"""Tests: asking Practo live on every search while the person waits (tools/doctor_refresh.py).

No network: a stand-in answers instead of Apify (test_provider_import.Apify). The list and the
spending record are temporary files. Dates are counted from the real day, as the fetch stamps
its searches with it."""

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.tools import doctor_refresh
from backend.tools.doctor_refresh import (
    DAILY_LIMIT_SETTING, DEFAULT_DAILY_LIMIT_USD, FETCH_PER_SEARCH, RETRY_AFTER_SECONDS, REUSE_SECONDS,
    WORST_COST_USD, daily_limit_usd, refresh_doctors,
)
from backend.tools.geocode import GeocodeError
from backend.tools.provider_import import KEY_NAME, PRICE_PER_RESULT, search_key, today_utc
from test_provider_import import FAKE_KEY, Apify, SlowApify, practo_row

KOREGAON_PARK = {"lat": 18.536, "lon": 73.893, "label": "Koregaon Park, Pune, Maharashtra", "city": "Pune",
                 "area": "Koregaon Park", "source": "request"}
KEY = search_key("Pune", "Koregaon Park", "cardiologist")
ROWS = {("Cardiologist", "female"): [practo_row(doctorId=1)], ("Cardiologist", "male"): [practo_row(doctorId=2)]}
LIVE_IDS = ["practo:1", "practo:2"]
IN_KORAMANGALA = {"lat": 12.935, "lon": 77.624, "label": "your current location", "source": "phone"}  # Bengaluru


class RefreshTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "doctors.json"
        self.spend = Path(folder.name) / "fetch_spend.json"
        self.today = today_utc()
        doctor_refresh._failed_at.clear()
        doctor_refresh._fetched.clear()
        self.area_near = MagicMock(return_value=None)  # Geoapify stand-in: the area is not known
        for patcher in (patch("backend.tools.provider_import.find_env_file", return_value=None),
                        patch.dict(os.environ, {KEY_NAME: FAKE_KEY}),
                        patch("backend.tools.doctor_refresh.area_near", self.area_near)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop(DAILY_LIMIT_SETTING, None)  # the default budget unless a test sets one

    def refresh(self, apify, place=KOREGAON_PARK):
        return refresh_doctors(place, "cardiologist", self.path, self.spend, client=apify.client())

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def spent(self):
        return json.loads(self.spend.read_text(encoding="utf-8"))

    def a_minute_passes(self):
        for key, (when, ids) in list(doctor_refresh._fetched.items()):
            doctor_refresh._fetched[key] = (when - REUSE_SECONDS - 1, ids)

    def test_a_search_is_fetched_live_and_names_the_doctors_it_brought(self):
        apify = Apify(ROWS)
        result = self.refresh(apify)
        self.assertEqual(result, {"status": "fetched", "area": "Koregaon Park, Pune", "results": 2,
                                  "cost_usd": 2 * PRICE_PER_RESULT, "doctor_ids": LIVE_IDS})
        for body in apify.bodies():
            self.assertEqual((body["cities"], body["localities"], body["maxItems"]),
                             (["Pune"], ["Koregaon Park"], FETCH_PER_SEARCH))
        self.assertEqual(apify.requests[0].url.params["timeout"], str(doctor_refresh.FETCH_TIMEOUT_SECONDS))
        stored = self.stored()  # saved: the backup for when Practo can't be asked
        self.assertEqual({d["doctor_id"] for d in stored["doctors"]}, set(LIVE_IDS))
        self.assertEqual(stored["searches"][KEY]["fetched_on"], self.today.isoformat())
        self.assertEqual(self.spent(), {"day": self.today.isoformat(), "usd": 2 * PRICE_PER_RESULT})

    def test_every_search_is_fetched_again_even_when_the_list_has_it(self):
        self.refresh(Apify(ROWS))  # the list now holds today's search for this area and specialty
        self.a_minute_passes()
        again = Apify({("Cardiologist", "female"): [practo_row(doctorId=3)], ("Cardiologist", "male"): []})
        result = self.refresh(again)
        self.assertEqual((result["status"], result["doctor_ids"]), ("fetched", ["practo:3"]))  # this fetch's only
        self.assertEqual(len(again.requests), 2)
        self.assertEqual({d["doctor_id"] for d in self.stored()["doctors"]}, {*LIVE_IDS, "practo:3"})
        self.assertEqual(self.spent()["usd"], round(3 * PRICE_PER_RESULT, 4))

    def test_the_same_search_within_a_minute_is_answered_from_that_fetch(self):
        self.refresh(Apify(ROWS))
        again = Apify(ROWS)
        result = self.refresh(again)  # a double tap, or a second person asking the same thing
        self.assertEqual((result["status"], result["doctor_ids"], result["cost_usd"]), ("reused", LIVE_IDS, 0.0))
        self.assertEqual(again.requests, [])
        other = Apify(ROWS)  # another area is its own search
        self.assertEqual(self.refresh(other, {**KOREGAON_PARK, "area": None})["status"], "fetched")
        self.assertEqual(len(other.requests), 2)
        self.a_minute_passes()
        later = Apify(ROWS)
        self.assertEqual(self.refresh(later)["status"], "fetched")
        self.assertEqual(len(later.requests), 2)

    def test_the_female_and_male_searches_run_at_the_same_time(self):
        apify = SlowApify(ROWS, seconds=0.5)
        started = time.monotonic()
        self.assertEqual(self.refresh(apify)["status"], "fetched")
        self.assertEqual(apify.most_at_once, 2)
        self.assertLess(time.monotonic() - started, 0.95)  # about one search's time, not two

    def test_practo_listing_nobody_is_a_live_answer_with_no_doctors(self):
        result = self.refresh(Apify())
        self.assertEqual((result["status"], result["results"], result["doctor_ids"]), ("fetched", 0, []))

    def test_near_me_gets_the_area_name_from_geoapify(self):
        self.area_near.return_value = {"city": "Bengaluru", "area": "Koramangala"}
        apify = Apify(ROWS)
        result = self.refresh(apify, IN_KORAMANGALA)
        self.assertEqual((result["status"], result["area"]), ("fetched", "Koramangala, Bengaluru"))
        self.area_near.assert_called_once_with(IN_KORAMANGALA)
        for request in apify.requests:  # Apify gets names, never coordinates
            body = json.loads(request.content)
            self.assertEqual((body["cities"], body["localities"]), (["Bengaluru"], ["Koramangala"]))
            self.assertNotIn("12.93", request.content.decode() + str(request.url))
        self.area_near.return_value = {"city": "Bengaluru", "area": None}  # no suburb near: the whole city
        whole_city = Apify(ROWS)
        self.assertEqual(self.refresh(whole_city, IN_KORAMANGALA)["area"], "Bengaluru")
        self.assertEqual(whole_city.bodies()[0]["localities"], [])

    def test_when_the_area_is_not_known_nothing_is_fetched(self):
        apify = Apify(ROWS)
        self.assertEqual(self.refresh(apify, IN_KORAMANGALA),  # Geoapify knew nothing
                         {"status": "no_area", "area": None, "results": 0, "cost_usd": 0.0, "doctor_ids": []})
        self.area_near.side_effect = GeocodeError("Area lookup failed (HTTP 503)")
        self.assertEqual(self.refresh(apify, IN_KORAMANGALA)["status"], "no_area")
        self.assertEqual(apify.requests, [])

    def test_a_whole_city_is_searched_without_a_neighbourhood(self):
        apify = Apify(ROWS)
        pune = {**KOREGAON_PARK, "label": "Pune, Maharashtra", "area": None}
        self.assertEqual(self.refresh(apify, pune)["area"], "Pune")
        self.assertEqual(apify.bodies()[0]["localities"], [])

    def test_the_daily_budget_is_2_dollars_unless_set(self):
        self.assertEqual((DEFAULT_DAILY_LIMIT_USD, daily_limit_usd()), (2.00, 2.00))
        for setting, limit in (("3.5", 3.5), (" 0 ", 0.0), ("lots", 2.00), ("-1", 2.00), ("inf", 2.00),
                               ("nan", 2.00), ("", 2.00)):
            with self.subTest(setting=setting), patch.dict(os.environ, {DAILY_LIMIT_SETTING: setting}):
                self.assertEqual(daily_limit_usd(), limit)

    def test_spending_stops_at_the_daily_limit(self):
        limit = DEFAULT_DAILY_LIMIT_USD
        just_under = round(limit - WORST_COST_USD, 4)
        cases = [({"day": self.today.isoformat(), "usd": just_under + 0.001}, "over_budget"),
                 ("not a record", "over_budget"),  # no spending without a record of it
                 ({"day": (self.today - timedelta(days=1)).isoformat(), "usd": limit}, "fetched"),
                 ({"day": self.today.isoformat(), "usd": just_under}, "fetched")]
        for spend, status in cases:
            with self.subTest(spend=spend):
                doctor_refresh._fetched.clear()
                self.spend.write_text(spend if isinstance(spend, str) else json.dumps(spend), encoding="utf-8")
                apify = Apify(ROWS)
                result = self.refresh(apify)
                self.assertEqual((result["status"], len(apify.requests)), (status, 2 if status == "fetched" else 0))
                if status == "over_budget":
                    self.assertEqual(result["doctor_ids"], [])  # the search shows saved doctors instead
        self.assertEqual(self.spent()["usd"], round(just_under + 2 * PRICE_PER_RESULT, 4))

    def test_the_budget_setting_is_used(self):
        with patch.dict(os.environ, {DAILY_LIMIT_SETTING: "0.04"}):  # less than one fetch's worst case
            apify = Apify(ROWS)
            self.assertEqual(self.refresh(apify)["status"], "over_budget")
            self.assertEqual(apify.requests, [])
        self.spend.write_text(json.dumps({"day": self.today.isoformat(), "usd": 2.5}), encoding="utf-8")
        with patch.dict(os.environ, {DAILY_LIMIT_SETTING: "3"}):  # raised for a demo day
            self.assertEqual(self.refresh(Apify(ROWS))["status"], "fetched")

    def test_the_worst_case_is_written_down_before_apify_is_asked(self):
        seen = []

        class Watching(Apify):
            def __call__(inner, request):
                seen.append(json.loads(self.spend.read_text(encoding="utf-8"))["usd"])
                return Apify.__call__(inner, request)

        self.refresh(Watching(ROWS))
        self.assertEqual(seen, [WORST_COST_USD, WORST_COST_USD])

    def test_a_failed_search_is_not_tried_again_for_a_while(self):
        failing = Apify(ROWS, fail=[("Cardiologist", "female")])  # half worked: not a complete search
        result = self.refresh(failing)
        self.assertEqual((result["status"], result["doctor_ids"]), ("failed", []))  # saved doctors are shown
        self.assertNotIn(KEY, self.stored().get("searches", {}))
        self.assertEqual({d["doctor_id"] for d in self.stored()["doctors"]}, {"practo:2"})  # what came is kept
        waiting = Apify(ROWS)
        self.assertEqual(self.refresh(waiting)["status"], "failed_recently")
        self.assertEqual(waiting.requests, [])
        doctor_refresh._failed_at[KEY] -= RETRY_AFTER_SECONDS + 1  # as if the wait were over
        self.assertEqual(self.refresh(Apify(ROWS))["status"], "fetched")

    def test_two_requests_at_once_pay_for_one_search(self):
        class Slow(Apify):
            def __call__(inner, request):
                time.sleep(0.2)  # the second request arrives while the first is fetching
                return Apify.__call__(inner, request)

        apify, results = Slow(ROWS), []
        threads = [threading.Thread(target=lambda: results.append(self.refresh(apify))) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(result["status"] for result in results), ["fetched", "reused"])
        self.assertEqual([result["doctor_ids"] for result in results], [LIVE_IDS, LIVE_IDS])  # both live
        self.assertEqual(len(apify.requests), 2)  # female and male, once

    def test_without_a_key_nothing_is_sent_or_counted(self):
        apify = Apify(ROWS)
        with patch.dict(os.environ):
            os.environ.pop(KEY_NAME, None)
            self.assertEqual(self.refresh(apify)["status"], "no_key")
        self.assertEqual(apify.requests, [])
        self.assertFalse(self.spend.exists())

    def test_a_list_that_cant_be_read_is_never_fetched_into(self):
        for broken in ("not a list", '{"doctors": "x"}', "  {"):
            with self.subTest(broken=broken):
                doctor_refresh._failed_at.clear()
                self.path.write_text(broken, encoding="utf-8")
                apify = Apify(ROWS)
                self.assertEqual(self.refresh(apify)["status"], "failed")
                self.assertEqual(apify.requests, [])
                self.assertEqual(self.path.read_text(encoding="utf-8"), broken)

    def test_an_empty_list_file_is_fetched_into_like_a_missing_one(self):
        # Seen live: a 0-byte doctors.json stopped every doctor search, as it was never refreshed.
        for empty in ("", "  \n"):
            with self.subTest(empty=repr(empty)):
                self.path.write_text(empty, encoding="utf-8")
                doctor_refresh._failed_at.clear()
                doctor_refresh._fetched.clear()
                apify = Apify(ROWS)
                self.assertEqual(self.refresh(apify)["status"], "fetched")
                self.assertEqual({d["doctor_id"] for d in self.stored()["doctors"]}, set(LIVE_IDS))
                self.spend.unlink()


if __name__ == "__main__":
    unittest.main()

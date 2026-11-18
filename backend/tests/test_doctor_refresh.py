"""Tests: fetching a missing or out-of-date search while the person waits (tools/doctor_refresh.py).

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
    DAILY_LIMIT_USD, FETCH_PER_SEARCH, FRESH_DAYS, RETRY_AFTER_SECONDS, WORST_COST_USD, refresh_doctors,
)
from backend.tools.geocode import GeocodeError
from backend.tools.provider_import import KEY_NAME, PRICE_PER_RESULT, search_key, today_utc
from test_doctor_search import ANDHERI, doctor
from test_provider_import import FAKE_KEY, Apify, SlowApify, practo_row

KOREGAON_PARK = {"lat": 18.536, "lon": 73.893, "label": "Koregaon Park, Pune, Maharashtra", "city": "Pune",
                 "area": "Koregaon Park", "source": "request"}
KEY = search_key("Pune", "Koregaon Park", "cardiologist")
ROWS = {("Cardiologist", "female"): [practo_row(doctorId=1)], ("Cardiologist", "male"): [practo_row(doctorId=2)]}


def stored_clinic(number, km):
    """A stored doctor whose clinic Practo places in Andheri West, Mumbai, km north of ANDHERI."""
    record = doctor(number, km)
    return {**record, "clinic": {**record["clinic"], "city": "Mumbai", "locality": "Andheri West"}}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "doctors.json"
        self.spend = Path(folder.name) / "fetch_spend.json"
        self.today = today_utc()
        doctor_refresh._failed_at.clear()
        self.area_near = MagicMock(return_value=None)  # Geoapify stand-in: the area is not known
        for patcher in (patch("backend.tools.provider_import.find_env_file", return_value=None),
                        patch.dict(os.environ, {KEY_NAME: FAKE_KEY}),
                        patch("backend.tools.doctor_refresh.area_near", self.area_near)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def refresh(self, apify, place=KOREGAON_PARK, doctors=()):
        return refresh_doctors(place, "cardiologist", list(doctors), self.path, self.spend, client=apify.client())

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def spent(self):
        return json.loads(self.spend.read_text(encoding="utf-8"))

    def remember(self, days_old):
        searches = {KEY: {"city": "Pune", "locality": "Koregaon Park", "specialty": "cardiologist",
                          "fetched_on": (self.today - timedelta(days=days_old)).isoformat()}}
        self.path.write_text(json.dumps({"doctors": [], "searches": searches}), encoding="utf-8")

    def test_a_missing_search_is_fetched_once_and_remembered(self):
        apify = Apify(ROWS)
        result = self.refresh(apify)
        self.assertEqual(result, {"status": "fetched", "area": "Koregaon Park, Pune", "results": 2,
                                  "cost_usd": 2 * PRICE_PER_RESULT})
        for body in apify.bodies():
            self.assertEqual((body["cities"], body["localities"], body["maxItems"]),
                             (["Pune"], ["Koregaon Park"], FETCH_PER_SEARCH))
        self.assertEqual(apify.requests[0].url.params["timeout"], str(doctor_refresh.FETCH_TIMEOUT_SECONDS))
        stored = self.stored()
        self.assertEqual({d["doctor_id"] for d in stored["doctors"]}, {"practo:1", "practo:2"})
        self.assertEqual(stored["searches"][KEY]["fetched_on"], self.today.isoformat())
        self.assertEqual(self.spent(), {"day": self.today.isoformat(), "usd": 2 * PRICE_PER_RESULT})
        again = Apify(ROWS)
        self.assertEqual(self.refresh(again)["status"], "fresh")
        self.assertEqual(again.requests, [])

    def test_the_female_and_male_searches_run_at_the_same_time(self):
        apify = SlowApify(ROWS, seconds=0.5)
        started = time.monotonic()
        self.assertEqual(self.refresh(apify)["status"], "fetched")
        self.assertEqual(apify.most_at_once, 2)
        self.assertLess(time.monotonic() - started, 0.95)  # about one search's time, not two

    def test_a_search_older_than_30_days_is_fetched_again(self):
        for days_old, status in ((FRESH_DAYS, "fresh"), (FRESH_DAYS + 1, "fetched")):
            with self.subTest(days_old=days_old):
                self.remember(days_old)
                apify = Apify(ROWS)
                self.assertEqual(self.refresh(apify)["status"], status)
                self.assertEqual(len(apify.requests), 0 if status == "fresh" else 2)

    def test_an_empty_search_is_remembered_and_not_paid_for_again(self):
        self.assertEqual(self.refresh(Apify())["status"], "fetched")  # Practo had nobody there
        again = Apify(ROWS)
        self.assertEqual(self.refresh(again)["status"], "fresh")
        self.assertEqual(again.requests, [])

    def test_near_me_uses_the_area_practo_gives_the_nearest_stored_clinic(self):
        phone = {**ANDHERI, "label": "your current location", "source": "phone"}
        apify = Apify(ROWS)
        result = self.refresh(apify, phone, [stored_clinic(7, 8.0), stored_clinic(8, 2.5)])
        self.assertEqual((result["status"], result["area"]), ("fetched", "Andheri West, Mumbai"))
        for request in apify.requests:  # the phone's coordinates never leave the machine
            body = json.loads(request.content)
            self.assertEqual((body["cities"], body["localities"]), (["Mumbai"], ["Andheri West"]))
            self.assertNotIn(str(ANDHERI["lat"]), request.content.decode() + str(request.url))
        self.area_near.assert_not_called()  # a stored clinic named it: nothing sent to name the area
        far = Apify(ROWS)
        self.assertEqual(self.refresh(far, phone, [stored_clinic(7, 3.5)])["status"], "no_area")
        self.assertEqual(self.refresh(far, phone, [])["status"], "no_area")
        self.assertEqual(far.requests, [])
        self.assertEqual(self.area_near.call_count, 2)  # asked, and Geoapify did not know either

    def test_near_me_far_from_stored_clinics_gets_the_area_name_from_geoapify(self):
        phone = {"lat": 12.935, "lon": 77.624, "label": "your current location", "source": "phone"}  # Bengaluru
        self.area_near.return_value = {"city": "Bengaluru", "area": "Koramangala"}
        apify = Apify(ROWS)
        result = self.refresh(apify, phone, [stored_clinic(7, 1.0)])  # Mumbai: far away
        self.assertEqual((result["status"], result["area"]), ("fetched", "Koramangala, Bengaluru"))
        self.area_near.assert_called_once_with(phone)
        for request in apify.requests:  # Apify gets names, never coordinates
            body = json.loads(request.content)
            self.assertEqual((body["cities"], body["localities"]), (["Bengaluru"], ["Koramangala"]))
            self.assertNotIn("12.93", request.content.decode() + str(request.url))
        self.area_near.return_value = {"city": "Bengaluru", "area": None}  # no suburb near: the whole city
        self.path.unlink()
        whole_city = Apify(ROWS)
        self.assertEqual(self.refresh(whole_city, phone)["area"], "Bengaluru")
        self.assertEqual(whole_city.bodies()[0]["localities"], [])

    def test_when_the_area_lookup_fails_nothing_is_fetched(self):
        self.area_near.side_effect = GeocodeError("Area lookup failed (HTTP 503)")
        apify = Apify(ROWS)
        phone = {"lat": 12.935, "lon": 77.624, "label": "your current location", "source": "phone"}
        self.assertEqual(self.refresh(apify, phone)["status"], "no_area")
        self.assertEqual(apify.requests, [])

    def test_a_whole_city_is_searched_without_a_neighbourhood(self):
        apify = Apify(ROWS)
        pune = {**KOREGAON_PARK, "label": "Pune, Maharashtra", "area": None}
        self.assertEqual(self.refresh(apify, pune)["area"], "Pune")
        self.assertEqual(apify.bodies()[0]["localities"], [])

    def test_spending_stops_at_the_daily_limit(self):
        just_under = round(DAILY_LIMIT_USD - WORST_COST_USD, 4)
        cases = [({"day": self.today.isoformat(), "usd": just_under + 0.001}, "over_budget"),
                 ("not a record", "over_budget"),  # no spending without a record of it
                 ({"day": (self.today - timedelta(days=1)).isoformat(), "usd": DAILY_LIMIT_USD}, "fetched"),
                 ({"day": self.today.isoformat(), "usd": just_under}, "fetched")]
        for spend, status in cases:
            with self.subTest(spend=spend):
                self.path.unlink(missing_ok=True)
                self.spend.write_text(spend if isinstance(spend, str) else json.dumps(spend), encoding="utf-8")
                apify = Apify(ROWS)
                self.assertEqual(self.refresh(apify)["status"], status)
                self.assertEqual(len(apify.requests), 2 if status == "fetched" else 0)
        self.assertEqual(self.spent()["usd"], round(just_under + 2 * PRICE_PER_RESULT, 4))

    def test_the_worst_case_is_written_down_before_apify_is_asked(self):
        seen = []

        class Watching(Apify):
            def __call__(inner, request):
                seen.append(json.loads(self.spend.read_text(encoding="utf-8"))["usd"])
                return Apify.__call__(inner, request)

        self.refresh(Watching(ROWS))
        self.assertEqual(seen, [WORST_COST_USD, WORST_COST_USD])

    def test_a_failed_search_is_not_remembered_or_tried_again_for_a_while(self):
        failing = Apify(ROWS, fail=[("Cardiologist", "female")])  # half worked: not a complete search
        self.assertEqual(self.refresh(failing)["status"], "failed")
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

        apify, statuses = Slow(ROWS), []
        threads = [threading.Thread(target=lambda: statuses.append(self.refresh(apify)["status"]))
                   for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(statuses), ["fetched", "fresh"])
        self.assertEqual(len(apify.requests), 2)  # female and male, once

    def test_without_a_key_nothing_is_sent_or_counted(self):
        apify = Apify(ROWS)
        with patch.dict(os.environ):
            os.environ.pop(KEY_NAME, None)
            self.assertEqual(self.refresh(apify)["status"], "no_key")
        self.assertEqual(apify.requests, [])
        self.assertFalse(self.spend.exists())

    def test_a_list_that_cant_be_read_is_never_fetched_into(self):
        self.path.write_text("not a list", encoding="utf-8")
        apify = Apify(ROWS)
        self.assertEqual(self.refresh(apify)["status"], "failed")
        self.assertEqual(apify.requests, [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not a list")


if __name__ == "__main__":
    unittest.main()

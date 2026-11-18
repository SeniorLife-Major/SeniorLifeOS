"""Tests: finding where to look for a doctor (tools/geocode.py).

No network: httpx's MockTransport answers instead of Geoapify and records each request. The
answers below copy the shape (and, for "Andheri", the places) of real answers seen live.
"""

import logging
import os
import unittest
from unittest.mock import MagicMock, patch

import httpx

from backend.tools import geocode
from backend.tools.geocode import (
    ATTRIBUTION, KEY_NAME, GeocodeError, clean_place_text, device_location, distance_km, find_place,
    search_near_from_text,
)

FAKE_KEY = "fake-geoapify-key-123"
REAL_CLIENT = httpx.Client
MUMBAI_PHONE = {"lat": 19.1201, "lon": 72.8409}  # a phone in Andheri, Mumbai


def result(name, lat, lon, result_type="city", confidence=1.0, city=None, state=None, suburb=None):
    return {"name": name, "lat": lat, "lon": lon, "result_type": result_type, "city": city, "state": state,
            "suburb": suburb, "formatted": f"{name}, India", "rank": {"confidence": confidence}}


# Live, 2026-11-17: "Andheri" -> a village in Punjab first, then Andheri East and West in Mumbai.
ANDHERI = [
    result("Andheri", 30.2699, 75.813, "city", city="Andheri", state="Punjab"),
    result("Andheri East", 19.1163, 72.8466, "amenity", city="Mumbai", state="Maharashtra"),
    result("Andheri West", 19.1172, 72.834, "suburb", city="Mumbai", state="Maharashtra"),
]
PUNE = [result("Pune", 18.5214, 73.8545, "city", city="Pune", state="Maharashtra")]


class Recorder:
    """MockTransport handler: answers with fixed results and keeps the requests."""

    def __init__(self, results=None, status=200, body=None, busy=0):
        self.results, self.status, self.body, self.busy, self.requests = results or [], status, body, busy, []

    def __call__(self, request):
        self.requests.append(request)
        if self.busy:
            self.busy -= 1
            return httpx.Response(429, json={"message": "busy"})
        if self.body is not None:
            return httpx.Response(self.status, content=self.body)
        return httpx.Response(self.status, json={"results": self.results, "query": {}})

    def client(self):
        return REAL_CLIENT(transport=httpx.MockTransport(self))

    def params(self, index=0):
        return dict(self.requests[index].url.params)


class GeocodeTestCase(unittest.TestCase):
    def setUp(self):
        geocode._cache.clear()
        # The real .env must not decide these tests, and fake requests need no spacing.
        for patcher in (patch("backend.tools.geocode.find_env_file", return_value=None),
                        patch("backend.tools.geocode.rate_limiter", lambda *a: MagicMock()),
                        patch.dict(os.environ, {KEY_NAME: FAKE_KEY})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def find(self, text, recorder, near=None):
        return find_place(text, near=near, client=recorder.client())


class FindPlaceTests(GeocodeTestCase):
    def test_one_place_is_found_rounded_with_its_label_and_credit(self):
        recorder = Recorder(PUNE)
        answer = self.find("Pune", recorder)
        self.assertEqual(answer, {"status": "found", "place": {
            "lat": 18.521, "lon": 73.855, "label": "Pune, Maharashtra", "city": "Pune", "area": None,
            "attribution": ATTRIBUTION}})
        params = recorder.params()
        self.assertEqual(params["text"], "Pune")
        self.assertEqual(params["filter"], "countrycode:in")
        self.assertEqual(params["format"], "json")
        self.assertNotIn("bias", params)

    def test_far_apart_places_with_one_name_are_ambiguous(self):
        answer = self.find("Andheri", Recorder(ANDHERI))
        self.assertEqual(answer["status"], "ambiguous")
        # Mumbai's two answers are one place, named by the area (suburb), not the amenity.
        self.assertEqual([o["label"] for o in answer["options"]],
                         ["Andheri, Punjab", "Andheri West, Mumbai, Maharashtra"])

    def test_labels_skip_repeats_and_same_named_places_are_offered_once(self):
        # Seen live: "Andheri, Andheri West, Mumbai, Maharashtra" and two "Rampur, Shahdol" options.
        andheri = result("Andheri", 19.1172, 72.834, "suburb", suburb="Andheri West", city="Mumbai",
                         state="Maharashtra")
        self.assertEqual(geocode.place_label(andheri), "Andheri West, Mumbai, Maharashtra")
        rampur = [result("Rampur", 28.8014, 79.0284, city="Rampur", state="Uttar Pradesh"),
                  result("Rampur", 23.2238, 81.7025, "suburb", city="Shahdol", state="Madhya Pradesh"),
                  result("Rampur", 23.46, 81.4255, "suburb", city="Shahdol", state="Madhya Pradesh")]
        answer = self.find("Rampur", Recorder(rampur))
        self.assertEqual([o["label"] for o in answer["options"]],
                         ["Rampur, Uttar Pradesh", "Rampur, Shahdol, Madhya Pradesh"])
        self.assertEqual(answer["options"][1]["lat"], 23.224)  # the best-ranked of the two

    def test_each_place_says_its_city_and_neighbourhood(self):
        # The names doctors are searched by (doctor_refresh): none for a whole city.
        cases = [(result("Andheri West", 19.1172, 72.834, "suburb", city="Mumbai"), ("Mumbai", "Andheri West")),
                 (result("Demo Hospital", 19.12, 72.84, "amenity", city="Mumbai", suburb="Juhu"), ("Mumbai", "Juhu")),
                 (result("Pune", 18.52, 73.85, "city", city="Pune"), ("Pune", None)),
                 (result("Pune", 18.52, 73.85, "city"), ("Pune", None)),  # the city's own name
                 # Live, 2026-11-18: Geoapify calls Koregaon Park a "city", inside the city of Pune.
                 (result("Koregaon Park", 18.536, 73.893, "city", city="Pune", suburb="Ghorpuri"),
                  ("Pune", "Koregaon Park")),
                 (result("Pune City Subdistrict", 18.52, 73.85, "county", city="Pune"), ("Pune", None)),
                 (result("Demo Road", 19.12, 72.84, "street"), (None, None))]
        for answer, names in cases:
            with self.subTest(name=answer["name"]):
                self.assertEqual(geocode.city_and_area(answer), names)
        options = self.find("Andheri", Recorder(ANDHERI))["options"]
        self.assertEqual([(o["city"], o["area"]) for o in options], [("Andheri", None), ("Mumbai", "Andheri West")])

    def test_a_match_near_the_phone_settles_the_name(self):
        recorder = Recorder(ANDHERI)
        answer = self.find("Andheri", recorder, near=MUMBAI_PHONE)
        self.assertEqual(answer["status"], "found")
        self.assertEqual(answer["place"]["label"], "Andheri West, Mumbai, Maharashtra")
        self.assertEqual(recorder.params()["bias"], "proximity:72.84,19.12")  # the phone's point, to ~1 km

    def test_a_far_named_place_is_kept_even_with_the_phone_elsewhere(self):
        # Asking from Mumbai for a doctor in Pune (say, for a parent there).
        answer = self.find("Pune", Recorder(PUNE), near=MUMBAI_PHONE)
        self.assertEqual(answer["place"]["label"], "Pune, Maharashtra")

    def test_unsure_and_foreign_answers_are_dropped(self):
        answers = [result("Andheri", 19.12, 72.84, confidence=0.0), result("Dhaka", 23.8, 90.4 + 10, "city")]
        self.assertEqual(self.find("Andheri", Recorder(answers)), {"status": "not_found"})
        self.assertEqual(self.find("asdfghjkl", Recorder([])), {"status": "not_found"})

    def test_nothing_is_sent_for_empty_text(self):
        recorder = Recorder(PUNE)
        self.assertEqual(self.find("  ", recorder), {"status": "not_found"})
        self.assertEqual(recorder.requests, [])

    def test_identifiers_are_removed_before_sending(self):
        recorder = Recorder(PUNE)
        self.find("Pune, call 9876543210 or meera@example.com", recorder)
        self.assertEqual(recorder.params()["text"], "Pune, call or")

    def test_the_same_place_is_looked_up_once(self):
        recorder = Recorder(PUNE)
        first = self.find("Pune", recorder)
        first["place"]["label"] = "changed by a caller"
        second = self.find("pune", recorder)
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(second["place"]["label"], "Pune, Maharashtra")  # the cache keeps its own copy


class FailureTests(GeocodeTestCase):
    def assert_no_key_in(self, exc):
        self.assertNotIn(FAKE_KEY, str(exc))
        self.assertIsNone(exc.__cause__)  # an httpx error (it holds the URL) is not chained...
        self.assertTrue(exc.__context__ is None or exc.__suppress_context__)  # ...nor shown as context

    def test_without_a_key_nothing_is_sent(self):
        recorder = Recorder(PUNE)
        with patch.dict(os.environ):
            os.environ.pop(KEY_NAME, None)
            with self.assertRaisesRegex(GeocodeError, KEY_NAME):
                self.find("Pune", recorder)
        self.assertEqual(recorder.requests, [])

    def test_service_errors_never_show_the_key(self):
        for recorder in (Recorder(status=401, body=f'{{"error": "bad key {FAKE_KEY}"}}'.encode()),
                         Recorder(body=b"not json"), Recorder(body=b'{"results": "x"}')):
            with self.subTest(status=recorder.status, body=recorder.body[:15]):
                geocode._cache.clear()
                with self.assertRaises(GeocodeError) as caught:
                    self.find("Pune", recorder)
                self.assert_no_key_in(caught.exception)

    def test_network_errors_never_show_the_key(self):
        def refuse(request):
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        with self.assertRaisesRegex(GeocodeError, r"could not be reached \(ConnectError\)") as caught:
            find_place("Pune", client=REAL_CLIENT(transport=httpx.MockTransport(refuse)))
        self.assert_no_key_in(caught.exception)

    @patch("backend.tools.geocode.time.sleep")
    def test_busy_once_is_tried_again_busy_twice_gives_up(self, sleep):
        self.assertEqual(self.find("Pune", Recorder(PUNE, busy=1))["status"], "found")
        sleep.assert_called_once_with(2.0)
        geocode._cache.clear()
        with self.assertRaisesRegex(GeocodeError, "HTTP 429"):
            self.find("Pune", Recorder(PUNE, busy=2))

    def test_the_key_is_hidden_in_httpx_log_lines(self):
        with self.assertLogs("httpx", level="INFO") as logs:
            self.find("Pune", Recorder(PUNE))
        joined = "\n".join(logs.output)
        self.assertIn("apiKey=[hidden]", joined)
        self.assertNotIn(FAKE_KEY, joined)


class AreaService:
    """MockTransport handler for the area lookup: nearby places, and the reverse lookup."""

    def __init__(self, nearby=(), cities=(), status=200):
        self.nearby, self.cities, self.status, self.requests = list(nearby), list(cities), status, []

    def __call__(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": f"bad key {FAKE_KEY}"})
        if request.url.path == "/v2/places":
            return httpx.Response(200, json={"type": "FeatureCollection",
                                             "features": [{"properties": p} for p in self.nearby]})
        return httpx.Response(200, json={"results": [{"city": city} for city in self.cities]})

    def client(self):
        return REAL_CLIENT(transport=httpx.MockTransport(self))


class AreaNearTests(GeocodeTestCase):
    def area(self, service, point=MUMBAI_PHONE):
        return geocode.area_near(point, client=service.client())

    def test_the_nearest_suburb_names_the_area_and_only_a_rough_point_is_sent(self):
        service = AreaService(nearby=[{"name": "Andheri West", "city": "Mumbai"}])
        self.assertEqual(self.area(service), {"city": "Mumbai", "area": "Andheri West"})
        self.assertEqual(len(service.requests), 1)
        params = dict(service.requests[0].url.params)
        self.assertEqual((params["filter"], params["bias"]), ("circle:72.84,19.12,3000", "proximity:72.84,19.12"))
        self.assertEqual(params["categories"], geocode.AREA_CATEGORIES)
        for precise in ("19.1201", "72.8409", "72.841", "19.120"):  # the phone's point, never closer than ~1 km
            self.assertNotIn(precise, str(service.requests[0].url))

    def test_with_no_suburb_near_the_city_alone(self):
        service = AreaService(nearby=[], cities=["Pune"])
        self.assertEqual(self.area(service, {"lat": 18.5404, "lon": 73.8912}), {"city": "Pune", "area": None})
        self.assertEqual([r.url.path for r in service.requests], ["/v2/places", "/v1/geocode/reverse"])
        self.assertEqual(dict(service.requests[1].url.params)["lat"], "18.54")

    def test_a_town_that_is_its_own_city_has_no_neighbourhood(self):
        service = AreaService(nearby=[{"name": "Nashik", "city": "Nashik"}])
        self.assertEqual(self.area(service, {"lat": 20.0, "lon": 73.79}), {"city": "Nashik", "area": None})

    def test_unknown_or_outside_india_is_none(self):
        service = AreaService()
        self.assertIsNone(self.area(service, {"lat": 51.5, "lon": -0.12}))  # London: nothing sent
        self.assertEqual(service.requests, [])
        self.assertIsNone(self.area(service))  # nothing near, no city either
        self.assertEqual(len(service.requests), 2)

    def test_the_same_rough_point_is_looked_up_once(self):
        service = AreaService(nearby=[{"name": "Andheri West", "city": "Mumbai"}])
        self.area(service)
        self.assertEqual(self.area(service, {"lat": 19.1238, "lon": 72.8371}),  # rounds to the same point
                         {"city": "Mumbai", "area": "Andheri West"})
        self.assertEqual(len(service.requests), 1)

    def test_errors_never_show_the_key(self):
        with self.assertRaisesRegex(GeocodeError, r"Area lookup failed \(HTTP 401\)") as caught:
            self.area(AreaService(status=401))
        FailureTests.assert_no_key_in(self, caught.exception)

        def refuse(request):
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        geocode._cache.clear()
        with self.assertRaisesRegex(GeocodeError, r"could not be reached \(ConnectError\)") as caught:
            geocode.area_near(MUMBAI_PHONE, client=REAL_CLIENT(transport=httpx.MockTransport(refuse)))
        FailureTests.assert_no_key_in(self, caught.exception)
        with patch.dict(os.environ):
            os.environ.pop(KEY_NAME, None)
            with self.assertRaisesRegex(GeocodeError, KEY_NAME):
                self.area(AreaService())


class LocationWordsTests(unittest.TestCase):
    def test_near_me_in_three_ways_of_writing(self):
        for text in ("find a heart doctor near me", "any doctor nearby?", "mere paas koi aankh ka doctor",
                     "yahan paas mein dentist", "आसपास कोई डॉक्टर", "doctor close to where I am"):
            with self.subTest(text=text):
                self.assertEqual(search_near_from_text(text), "current_location")

    def test_near_my_home(self):
        for text in ("a doctor near my home", "ghar ke paas haddi ka doctor", "घर के पास डॉक्टर"):
            with self.subTest(text=text):
                self.assertEqual(search_near_from_text(text), "home")

    def test_anything_else_is_unknown_never_a_guess(self):
        for text in ("Pune mein dil ka doctor", "Agar doctor mile toh batao", "knee doctor", ""):
            with self.subTest(text=text):
                self.assertEqual(search_near_from_text(text), "unknown")

    def test_phone_location_is_checked_and_rounded(self):
        self.assertEqual(device_location({"lat": 19.12014, "lon": 72.84093}), {"lat": 19.12, "lon": 72.841})
        for bad in (None, "19.1,72.8", {"lat": "19.1", "lon": 72.8}, {"lat": True, "lon": 72.8}, {"lat": 19.1},
                    {"lat": float("nan"), "lon": 72.8}, {"lat": 51.5, "lon": -0.12}):  # London: not India
            with self.subTest(bad=bad):
                self.assertIsNone(device_location(bad))

    def test_distance_and_cleaning(self):
        self.assertAlmostEqual(distance_km({"lat": 19.076, "lon": 72.8777}, {"lat": 18.5204, "lon": 73.8567}),
                               120, delta=5)  # Mumbai to Pune, straight line
        self.assertEqual(clean_place_text("  Andheri ,  "), "Andheri")
        self.assertEqual(len(clean_place_text("Pune " * 100)), geocode.MAX_PLACE_CHARS - 1)


if __name__ == "__main__":
    unittest.main()

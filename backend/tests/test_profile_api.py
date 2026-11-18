"""Tests: the profile API (api/profile.py). Place search is patched: nothing is sent out.

Uses the real AccountService on the test database (TEST_DATABASE_URL, like test_storage_db.py).
"""

import unittest
from unittest.mock import patch

from backend.tools.geocode import ATTRIBUTION, GeocodeError
from test_auth_api import ApiCase

ANDHERI = {"lat": 19.136, "lon": 72.829, "label": "Andheri West, Mumbai", "city": "Mumbai", "area": "Andheri West",
           "attribution": ATTRIBUTION}
FOUND = {"status": "found", "place": ANDHERI}
AMBIGUOUS = {"status": "ambiguous", "options": [
    {**ANDHERI, "label": "Model Town, Delhi", "lat": 28.716, "lon": 77.19},
    {**ANDHERI, "label": "Model Town, Jalandhar", "lat": 31.309, "lon": 75.6}]}


class ProfileApiCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.headers = self.bearer(self.token())

    def patch_profile(self, body, headers=None):
        return self.client.patch("/api/v1/me/profile", json=body, headers=headers or self.headers)

    def get_profile(self, headers=None):
        response = self.client.get("/api/v1/me/profile", headers=headers or self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]


class ProfileApiTests(ProfileApiCase):
    def test_defaults_then_only_what_is_sent_changes(self):
        self.assertEqual(self.get_profile(), {
            "language": "en", "homeLocation": None,
            "accessibility": {"fontScale": 1.0, "highContrast": False, "voice": False},
            "consents": {"useLlm": False, "medicalCloud": False, "cloudOcr": False},
            "consentsUpdatedAt": None})
        response = self.patch_profile({"language": "hi", "accessibility": {"fontScale": 1.5},
                                       "consents": {"useLlm": True}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.patch_profile({"accessibility": {"voice": True}})
        profile = self.get_profile()
        self.assertEqual(profile["language"], "hi")
        self.assertEqual(profile["accessibility"], {"fontScale": 1.5, "highContrast": False, "voice": True})
        self.assertEqual(profile["consents"], {"useLlm": True, "medicalCloud": False, "cloudOcr": False})
        self.assertIsNotNone(profile["consentsUpdatedAt"])

    def test_each_person_sees_only_their_own(self):
        self.patch_profile({"language": "mr"})
        other = self.bearer(self.token(login="9123456789", name="Ravi"))
        self.assertEqual(self.get_profile(other)["language"], "en")
        self.assertEqual(self.get_profile()["language"], "mr")

    def test_bad_values_are_refused(self):
        for body in ({"language": "xx"}, {"language": "a-very-long-tag"}, {"accessibility": {"fontScale": 5}},
                     {"consents": {"useLlm": "yes"}}, {"consents": {"medicalCloud": 1}},
                     {"accessibility": {"highContrast": "true"}}):
            with self.subTest(body=body):
                self.assertEqual(self.patch_profile(body).status_code, 422)
        self.assertEqual(self.get_profile()["language"], "en")

    def test_sign_in_is_needed_and_an_account(self):
        self.assertEqual(self.client.get("/api/v1/me/profile").status_code, 401)
        response = self.client.get("/api/v1/me/profile", headers={"X-Actor-Id": "senior-without-account"})
        self.assertEqual(response.status_code, 404)


class HomeTests(ProfileApiCase):
    def test_home_from_a_place_name(self):
        with patch("backend.api.profile.find_place", return_value=FOUND) as search:
            response = self.patch_profile({"homeLocation": {"place": "Andheri West, Mumbai"}})
        self.assertEqual(response.status_code, 200, response.text)
        search.assert_called_once_with("Andheri West, Mumbai")
        self.assertEqual(self.get_profile()["homeLocation"],
                         {"label": "Andheri West, Mumbai", "lat": 19.136, "lon": 72.829})

    def test_identifiers_never_reach_place_search(self):
        with patch("backend.api.profile.find_place", return_value=FOUND) as search:
            self.patch_profile({"homeLocation": {"place": "Andheri West, call 9876543210"}})
        self.assertNotIn("9876543210", search.call_args.args[0])

    def test_a_name_that_fits_far_apart_places(self):
        with patch("backend.api.profile.find_place", return_value=AMBIGUOUS):
            response = self.patch_profile({"homeLocation": {"place": "Model Town"}})
        self.assertEqual(response.status_code, 422)
        options = response.json()["error"]["details"]["options"]
        self.assertEqual([option["label"] for option in options], ["Model Town, Delhi", "Model Town, Jalandhar"])
        self.assertIsNone(self.get_profile()["homeLocation"])
        chosen = self.patch_profile({"homeLocation": {"place": "Model Town, Delhi", "lat": 28.716, "lon": 77.19}})
        self.assertEqual(chosen.json()["data"]["homeLocation"], {"label": "Model Town, Delhi", "lat": 28.716,
                                                                 "lon": 77.19})

    def test_not_found_and_search_down(self):
        with patch("backend.api.profile.find_place", return_value={"status": "not_found"}):
            self.assertEqual(self.patch_profile({"homeLocation": {"place": "Nowhere Nagar"}}).status_code, 422)
        with patch("backend.api.profile.find_place", side_effect=GeocodeError("Place search needs GEOAPIFY_API_KEY")):
            response = self.patch_profile({"homeLocation": {"place": "Andheri West"}})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "PLACE_SEARCH_UNAVAILABLE")
        self.assertNotIn("GEOAPIFY", response.text)

    def test_home_from_the_phone_is_rounded_and_can_be_forgotten(self):
        with patch("backend.api.profile.find_place") as search:
            response = self.patch_profile({"homeLocation": {"lat": 19.1361234, "lon": 72.8294567}})
        search.assert_not_called()
        self.assertEqual(response.json()["data"]["homeLocation"], {"label": None, "lat": 19.136, "lon": 72.829})
        response = self.patch_profile({"language": "hi"})  # home not sent: kept
        self.assertIsNotNone(response.json()["data"]["homeLocation"])
        self.assertIsNone(self.patch_profile({"homeLocation": None}).json()["data"]["homeLocation"])

    def test_points_outside_india_or_half_a_point(self):
        for home in ({"lat": 51.5, "lon": -0.12}, {"lat": 19.1}, {}, {"place": "  "}):
            with self.subTest(home=home):
                self.assertEqual(self.patch_profile({"homeLocation": home}).status_code, 422)


if __name__ == "__main__":
    unittest.main()

"""The web app served by the backend (api/web_app.py, frontend Decision 76)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch("backend.utils.env.find_env_file", return_value=None):  # keep the real .env out of tests
    import backend.main as main
from backend.api.web_app import WEB_DIR_SETTING, built_web_app, install_web_app
from backend.errors import register_global_error_handlers

INDEX = "<!doctype html><title>SeniorLife</title><div id=root></div>"


class WebAppTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dist = self.root / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text(INDEX, encoding="utf-8")
        (self.dist / "assets" / "index-AbC123.js").write_text("console.log(1)", encoding="utf-8")
        (self.dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        (self.root / "secret.txt").write_text("not for the web", encoding="utf-8")

        app = FastAPI()
        register_global_error_handlers(app)

        @app.get("/api/v1/thing")
        def thing():
            return {"ok": True}

        self.assertTrue(install_web_app(app, built_web_app(self.dist)))

        @app.get("/later/")  # added after the web app, like Pyctuator's /actuator/
        def later():
            return {"later": True}

        self.client = TestClient(app, raise_server_exceptions=False)

    def assert_app_page(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, INDEX)
        self.assertEqual(response.headers["cache-control"], "no-cache")  # a new version shows at once

    def test_every_page_address_gets_the_app(self):
        for path in ("/", "/doctor/abc-123", "/documents/add", "/settings/home?x=1", "/documents.json"):
            with self.subTest(path=path):
                self.assert_app_page(self.client.get(path))

    def test_built_files_are_sent_and_the_hashed_ones_kept_a_year(self):
        asset = self.client.get("/assets/index-AbC123.js")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.text, "console.log(1)")
        self.assertEqual(asset.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(self.client.get("/assets/gone-123.js").status_code, 404)  # never the app instead
        icon = self.client.get("/favicon.svg")
        self.assertEqual((icon.status_code, icon.text), (200, "<svg/>"))

    def test_the_api_comes_first_and_its_unknown_addresses_stay_404(self):
        self.assertEqual(self.client.get("/api/v1/thing").json(), {"ok": True})
        for path in ("/api/v1/nothing-here", "/api", "/actuator/env", "/redoc/x", "/metrics"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")
        self.assertIn("swagger", self.client.get("/docs").text.lower())  # FastAPI's own docs still work

    def test_routes_added_later_and_slash_redirects_still_come_first(self):
        self.assertEqual(self.client.get("/later/").json(), {"later": True})
        moved = self.client.get("/later", follow_redirects=False)  # as /actuator goes to /actuator/
        self.assertEqual((moved.status_code, moved.headers["location"]), (307, "http://testserver/later/"))
        self.assertEqual(self.client.post("/api/v1/thing").status_code, 405)  # wrong method, not the app

    def test_only_reading_a_page_gets_the_app(self):
        self.assertEqual(self.client.post("/doctor/abc").status_code, 404)
        self.assertEqual(self.client.head("/doctor/abc").status_code, 200)

    def test_nothing_outside_the_build_is_sent(self):
        for path in ("/../secret.txt", "/%2E%2E/secret.txt", "/assets/../../secret.txt"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotIn("not for the web", response.text)

    def test_nothing_is_added_without_a_build(self):
        self.assertIsNone(built_web_app(self.root / "missing"))
        (self.root / "empty").mkdir()
        self.assertIsNone(built_web_app(self.root / "empty"))  # a folder without index.html
        self.assertFalse(install_web_app(FastAPI(), None))
        with patch.dict(os.environ, {WEB_DIR_SETTING: str(self.dist)}):
            self.assertEqual(built_web_app(), self.dist.resolve())

    def test_the_real_app_opens_the_web_app_at_its_root_when_built_else_the_docs(self):
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(main, "WEB_APP", None):
            response = client.get("/", follow_redirects=False)
            self.assertEqual((response.status_code, response.headers["location"]), (307, "/docs"))
        with patch.object(main, "WEB_APP", self.dist.resolve()):
            self.assert_app_page(client.get("/"))


if __name__ == "__main__":
    unittest.main()

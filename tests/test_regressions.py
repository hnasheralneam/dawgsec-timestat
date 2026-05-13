import os
import re
import tempfile
import unittest

import app as app_module


def extract_csrf(html: bytes) -> str:
    match = re.search(rb'name="csrf-token" content="([^"]*)"', html)
    if not match:
        raise AssertionError("CSRF token not found in HTML response")
    return match.group(1).decode()


class TimeStatRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        app_module.DB_PATH = self.db_path
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _register_and_sign_in(self, username: str):
        register_page = self.client.get("/register")
        csrf = extract_csrf(register_page.data)
        response = self.client.post(
            "/register",
            data={"username": username, "csrf_token": csrf},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Account ready", response.data)
        return response

    def test_new_user_can_start_session_immediately_after_register(self):
        register_response = self._register_and_sign_in("newuser")
        csrf = extract_csrf(register_response.data)

        start_response = self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "first task"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(start_response.get_json(), {"ok": True})

        status_response = self.client.get("/api/status")
        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()
        self.assertEqual(payload["current_session"]["category_name"], "Other")

    def test_api_status_uses_category_name_without_category_id_join(self):
        # User 1 starts a running session.
        register_user1 = self._register_and_sign_in("runner")
        csrf_user1 = extract_csrf(register_user1.data)
        start_response = self.client.post(
            "/api/session/start",
            json={"category_name": "Research", "note": "presence check"},
            headers={"X-CSRF-Token": csrf_user1},
        )
        self.assertEqual(start_response.status_code, 200)

        # User 2 signs in and calls /api/status, which must not error and should include presence.
        self.client.post("/logout", data={"csrf_token": csrf_user1}, follow_redirects=False)
        self._register_and_sign_in("observer")
        status_response = self.client.get("/api/status")
        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()

        self.assertIn("team_presence", payload)
        self.assertEqual(len(payload["team_presence"]), 1)
        self.assertEqual(payload["team_presence"][0]["username"], "runner")
        self.assertEqual(payload["team_presence"][0]["category_name"], "Research")


if __name__ == "__main__":
    unittest.main()

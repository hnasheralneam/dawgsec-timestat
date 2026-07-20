import os
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import app as app_module
import config
from services import queries
import db
import reset

def extract_csrf(html: bytes) -> str:
    match = re.search(rb'name="csrf-token" content="([^"]*)"', html)
    if not match:
        raise AssertionError("CSRF token not found in HTML response")
    return match.group(1).decode()

class FixesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        app_module.DB_PATH = self.db_path
        self.env_patch = patch.dict(
            os.environ, {
                "ADMIN_CODE": "test-admin-code-12345",
                "STORE_LOGIN_CODE_PLAINTEXT": "1"
            }
        )
        self.env_patch.start()
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.app.config["STORE_LOGIN_CODE_PLAINTEXT"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.env_patch.stop()
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
        return extract_csrf(response.data)

    def test_authenticated_user_login_register_redirects(self):
        # Visit when not authenticated -> should render forms (status 200)
        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        register_page = self.client.get("/register")
        self.assertEqual(register_page.status_code, 200)

        # Register/sign in
        self._register_and_sign_in("redirectuser")

        # Visit GET /login and GET /register while authenticated -> should redirect to dashboard
        resp_login = self.client.get("/login")
        self.assertEqual(resp_login.status_code, 302)
        self.assertIn("/dashboard", resp_login.headers["Location"])

        resp_register = self.client.get("/register")
        self.assertEqual(resp_register.status_code, 302)
        self.assertIn("/dashboard", resp_register.headers["Location"])

    @patch("builtins.input")
    def test_reset_script_updates_plaintext_code(self, mock_input):
        self._register_and_sign_in("resetuser")
        
        # Mock CLI inputs for reset script
        mock_input.side_effect = [self.db_path, "resetuser"]
        
        with sqlite3.connect(self.db_path) as conn:
            user_row = conn.execute("SELECT login_code FROM users WHERE username = 'resetuser'").fetchone()
            original_code = user_row[0]
            self.assertIsNotNone(original_code)

        # Run reset script
        with patch("sys.stdout"):
            reset.main()

        # Check that both hash and plaintext are updated
        with sqlite3.connect(self.db_path) as conn:
            user_row = conn.execute("SELECT login_code, code_hash FROM users WHERE username = 'resetuser'").fetchone()
            new_code = user_row[0]
            new_hash = user_row[1]
            self.assertIsNotNone(new_code)
            self.assertNotEqual(original_code, new_code)
            from werkzeug.security import check_password_hash
            self.assertTrue(check_password_hash(new_hash, new_code))

    def test_activity_grid_filtering_range(self):
        self._register_and_sign_in("griduser")
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM users WHERE username = 'griduser'").fetchone()
            user_id = row[0]
        
        current_ts = int(time.time())
        # Add a session from 15 days ago
        fifteen_days_ago = current_ts - (15 * 24 * 3600)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions(user_id, category_name, note, start_ts, end_ts, paused_seconds, status, created_ts)
                VALUES(?, 'Other', 'old task', ?, ?, 0, 'completed', ?)
                """,
                (user_id, fifteen_days_ago - 3600, fifteen_days_ago, fifteen_days_ago)
            )
            conn.commit()

        # Retrieve grid with 14 days limit -> old task should be filtered out
        with self.app.app_context():
            grid = queries.user_activity_grid(user_id, current_ts, days=14)
            # Sum up seconds
            total_seconds = sum(d["seconds"] for d in grid)
            self.assertEqual(total_seconds, 0)

    def test_auto_pause_triggers_at_limit(self):
        self._register_and_sign_in("pauseuser")
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM users WHERE username = 'pauseuser'").fetchone()
            user_id = row[0]

        # Start active session
        csrf = extract_csrf(self.client.get("/dashboard").data)
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "auto-pause test"},
            headers={"X-CSRF-Token": csrf}
        )

        with sqlite3.connect(self.db_path) as conn:
            active_row = conn.execute("SELECT id FROM sessions WHERE status = 'running'").fetchone()
            session_id = active_row[0]

        # Artificially set start_ts to 9 hours ago
        nine_hours_ago = int(time.time()) - (9 * 3600)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET start_ts = ? WHERE id = ?", (nine_hours_ago, session_id))
            conn.commit()

        # Call /api/status endpoint and verify it triggers auto-pause and reports the alert
        status_resp = self.client.get("/api/status")
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.get_json()
        self.assertTrue(status_data["auto_paused_alert"])

        # Check that the database status was updated correctly
        with sqlite3.connect(self.db_path) as conn:
            active_row = conn.execute("SELECT status, pause_started_ts FROM sessions WHERE id = ?", (session_id,)).fetchone()
            self.assertEqual(active_row[0], "paused")
            expected_pause_ts = nine_hours_ago + (config.MAX_SESSION_RUNNING_HOURS * 3600)
            self.assertEqual(active_row[1], expected_pause_ts)

        # Subsequent status call should have popped the alert (so false/None)
        status_resp2 = self.client.get("/api/status")
        self.assertEqual(status_resp2.status_code, 200)
        status_data2 = status_resp2.get_json()
        self.assertFalse(status_data2["auto_paused_alert"])

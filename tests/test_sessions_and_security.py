import os
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import app as app_module
import config


def extract_csrf(html: bytes) -> str:
    match = re.search(rb'name="csrf-token" content="([^"]*)"', html)
    if not match:
        raise AssertionError("CSRF token not found in HTML response")
    return match.group(1).decode()


class TimeStatTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        app_module.DB_PATH = self.db_path
        self.env_patch = patch.dict(
            os.environ, {"ADMIN_USERNAME": "root", "ADMIN_PASSWORD": "secret-pass"}
        )
        self.env_patch.start()
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
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

    def _set_session_start_offset(self, session_id: int, seconds_ago: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET start_ts = ? WHERE id = ?",
                (int(time.time()) - seconds_ago, session_id),
            )
            conn.commit()

    def _set_pause_started_offset(self, session_id: int, seconds_ago: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET pause_started_ts = ? WHERE id = ?",
                (int(time.time()) - seconds_ago, session_id),
            )
            conn.commit()

    def _active_session_id(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE status IN ('running', 'paused') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            return int(row[0])


class SessionElapsedTimeTests(TimeStatTestCase):
    def test_pause_then_resume_excludes_paused_time_from_elapsed(self):
        csrf = self._register_and_sign_in("clockuser")
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "timing"},
            headers={"X-CSRF-Token": csrf},
        )
        session_id = self._active_session_id()
        self._set_session_start_offset(session_id, seconds_ago=120)

        pause_resp = self.client.post("/api/session/pause", headers={"X-CSRF-Token": csrf})
        self.assertEqual(pause_resp.status_code, 200)
        self._set_pause_started_offset(session_id, seconds_ago=30)

        resume_resp = self.client.post("/api/session/resume", headers={"X-CSRF-Token": csrf})
        self.assertEqual(resume_resp.status_code, 200)

        status = self.client.get("/api/status").get_json()
        elapsed = status["current_session"]["elapsed_seconds"]
        # ~120s wall-clock minus the ~30s spent paused.
        self.assertTrue(85 <= elapsed <= 95, f"expected ~90s elapsed, got {elapsed}")

    def test_adjust_rejects_removing_more_than_elapsed(self):
        csrf = self._register_and_sign_in("adjustuser")
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "adjust"},
            headers={"X-CSRF-Token": csrf},
        )
        session_id = self._active_session_id()
        self._set_session_start_offset(session_id, seconds_ago=50)

        too_much = self.client.post(
            "/api/session/adjust", json={"seconds": 600}, headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(too_much.status_code, 400)
        self.assertIn("Not enough elapsed time", too_much.get_json()["error"])

        ok = self.client.post(
            "/api/session/adjust", json={"seconds": 10}, headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(ok.status_code, 200)
        payload = ok.get_json()
        self.assertEqual(payload["removed_seconds"], 10)
        self.assertTrue(35 <= payload["remaining_seconds"] <= 45)


class CsrfAndRateLimitTests(TimeStatTestCase):
    def test_api_post_without_csrf_token_is_rejected(self):
        self._register_and_sign_in("nocsrfuser")
        response = self.client.post(
            "/api/session/start", json={"category_name": "Other", "note": "x"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Invalid CSRF token")

    def test_login_is_rate_limited_after_max_failed_attempts(self):
        self._register_and_sign_in("ratelimituser")
        self.client.post("/logout")

        login_page = self.client.get("/login")
        csrf = extract_csrf(login_page.data)
        for _ in range(config.LOGIN_MAX_ATTEMPTS + 1):
            response = self.client.post(
                "/login",
                data={"username": "ratelimituser", "code": "000000", "csrf_token": csrf},
                follow_redirects=True,
            )
        self.assertIn(b"Too many login attempts", response.data)


class AdminCategoryTests(TimeStatTestCase):
    def _admin_login(self):
        login_page = self.client.get("/admin/login")
        csrf = extract_csrf(login_page.data)
        response = self.client.post(
            "/admin/login",
            data={"username": "root", "password": "secret-pass", "csrf_token": csrf},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        dashboard = self.client.get("/admin")
        return extract_csrf(dashboard.data)

    def test_add_and_delete_category(self):
        csrf = self._admin_login()
        add_resp = self.client.post(
            "/admin/categories",
            data={"name": "Custom Category", "csrf_token": csrf},
            follow_redirects=True,
        )
        self.assertIn(b"Custom Category", add_resp.data)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM categories WHERE name = 'Custom Category'"
            ).fetchone()
        category_id = row[0]

        delete_resp = self.client.post(
            f"/admin/categories/{category_id}/delete",
            data={"csrf_token": csrf},
            follow_redirects=True,
        )
        self.assertIn(b"Removed category", delete_resp.data)

    def test_cannot_delete_last_remaining_category(self):
        csrf = self._admin_login()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT id FROM categories ORDER BY id").fetchall()
            keep_id = rows[0][0]
            conn.execute("DELETE FROM categories WHERE id != ?", (keep_id,))
            conn.commit()

        response = self.client.post(
            f"/admin/categories/{keep_id}/delete",
            data={"csrf_token": csrf},
            follow_redirects=True,
        )
        self.assertIn(b"At least one category must remain available", response.data)


class SessionOwnershipTests(TimeStatTestCase):
    def test_user_cannot_modify_another_users_completed_session(self):
        csrf1 = self._register_and_sign_in("owner")
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "mine"},
            headers={"X-CSRF-Token": csrf1},
        )
        self.client.post("/api/session/finish", headers={"X-CSRF-Token": csrf1})
        with sqlite3.connect(self.db_path) as conn:
            session_id = conn.execute(
                "SELECT id FROM sessions WHERE status = 'completed'"
            ).fetchone()[0]

        self.client.post("/logout", data={"csrf_token": csrf1})
        csrf2 = self._register_and_sign_in("intruder")

        delete_resp = self.client.post(
            "/api/session/delete",
            json={"session_id": session_id},
            headers={"X-CSRF-Token": csrf2},
        )
        self.assertEqual(delete_resp.status_code, 404)

        update_resp = self.client.post(
            "/api/session/update",
            json={"session_id": session_id, "category_name": "Other", "note": "hijacked"},
            headers={"X-CSRF-Token": csrf2},
        )
        self.assertEqual(update_resp.status_code, 404)


class RecentSessionsQueryTests(TimeStatTestCase):
    def _seed_sessions(self, username: str) -> int:
        self._register_and_sign_in(username)
        with sqlite3.connect(self.db_path) as conn:
            user_id = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()[0]
            for idx in range(5):
                start_ts = 1_700_000_000 + (idx * 120)
                conn.execute(
                    """
                    INSERT INTO sessions(
                        user_id, category_name, note, start_ts, end_ts,
                        paused_seconds, status, pause_started_ts, created_ts
                    )
                    VALUES(?, ?, ?, ?, ?, 0, 'completed', NULL, ?)
                    """,
                    (
                        user_id,
                        "Research" if idx % 2 == 0 else "Other",
                        f"note-{idx}",
                        start_ts,
                        start_ts + 60,
                        start_ts,
                    ),
                )
            conn.commit()
        return user_id

    def test_dashboard_recent_sessions_pagination_and_category_filter(self):
        self._seed_sessions("paginator")

        page1 = self.client.get("/api/recent-sessions?limit=2&offset=0").get_json()
        self.assertEqual(len(page1["sessions"]), 2)
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["total"], 5)

        filtered = self.client.get("/api/recent-sessions?category=Research").get_json()
        self.assertEqual(filtered["total"], 3)
        self.assertTrue(all(s["category_name"] == "Research" for s in filtered["sessions"]))

    def test_user_profile_recent_sessions_matches_dashboard_behavior(self):
        user_id = self._seed_sessions("profileviewer")

        profile_page = self.client.get(
            f"/api/users/{user_id}/recent-sessions?limit=2&offset=0"
        ).get_json()
        self.assertEqual(len(profile_page["sessions"]), 2)
        self.assertTrue(profile_page["has_more"])

        search = self.client.get(
            f"/api/users/{user_id}/recent-sessions?query=note-4"
        ).get_json()
        self.assertEqual(search["total"], 1)
        self.assertEqual(search["sessions"][0]["note"], "note-4")


if __name__ == "__main__":
    unittest.main()

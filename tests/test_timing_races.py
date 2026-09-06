"""Regression tests for session timing/lost-update races.

Covers the finish-vs-adjust lost update: the finish endpoints used to write
``paused_seconds`` absolutely from a snapshot read, so a concurrent adjust
committing between the snapshot and the finish UPDATE was silently
overwritten, inflating the session's credited duration. Also covers the
paused-session tail arithmetic of the relative-UPDATE form.
"""

import os
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import app as app_module
from services import queries


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
            os.environ, {"ADMIN_CODE": "test-admin-code-12345"}
        )
        self.env_patch.start()
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _register_and_login(self, username: str) -> str:
        page = self.client.get("/register")
        csrf = extract_csrf(page.data)
        resp = self.client.post(
            "/register",
            data={"username": username, "csrf_token": csrf},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)
        code = re.search(rb"(\d{6})", resp.data).group(1).decode()
        # Registration signs the user in; if it didn't (or the session was
        # lost), fall back to the login flow.
        dashboard = self.client.get("/dashboard")
        if dashboard.status_code == 200:
            return extract_csrf(dashboard.data)
        login_page = self.client.get("/login")
        login_csrf = extract_csrf(login_page.data)
        self.client.post(
            "/login",
            data={"username": username, "code": code, "csrf_token": login_csrf},
            follow_redirects=True,
        )
        return extract_csrf(self.client.get("/dashboard").data)


class FinishAdjustRaceTests(TimeStatTestCase):
    def test_finish_does_not_clobber_concurrent_adjust(self):
        csrf = self._register_and_login("racer")
        resp = self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "race"},
            headers={"X-CSRF-Token": csrf, "X-TimeStat-Client-State": "1"},
        )
        self.assertEqual(resp.status_code, 200)
        session_id = resp.get_json()["status"]["current_session"]["id"]

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "UPDATE sessions SET start_ts=? WHERE id=?",
                (int(time.time()) - 3600, session_id),
            )
            stale = conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            self.assertEqual(stale["paused_seconds"], 0)
            conn.commit()

        # Simulate an adjust committing in the window between finish's
        # snapshot read (queries.get_active_session) and its UPDATE: the
        # paused_seconds increment lands after `stale` was taken.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET paused_seconds = paused_seconds + 600 WHERE id=?",
                (session_id,),
            )
            conn.commit()

        with patch.object(queries, "get_active_session", return_value=stale):
            resp = self.client.post(
                "/api/session/finish", headers={"X-CSRF-Token": csrf}
            )
        self.assertEqual(resp.status_code, 200)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT paused_seconds, status FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        # The adjust's 600s must survive the finish; writing the snapshot's
        # paused_seconds (0) absolutely would have erased it.
        self.assertEqual(row[0], 600)
        self.assertEqual(row[1], "completed")

    def test_finish_of_paused_session_credits_pause_tail(self):
        csrf = self._register_and_login("pausedtail")
        resp = self.client.post(
            "/api/session/start",
            json={"category_name": "Other"},
            headers={"X-CSRF-Token": csrf, "X-TimeStat-Client-State": "1"},
        )
        self.assertEqual(resp.status_code, 200)
        session_id = resp.get_json()["status"]["current_session"]["id"]

        now = int(time.time())
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Started 1h ago; paused 15 minutes ago and still paused now.
            conn.execute(
                "UPDATE sessions SET start_ts=?, status='paused', pause_started_ts=? WHERE id=?",
                (now - 3600, now - 900, session_id),
            )
            stale = conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            conn.commit()

        with patch.object(queries, "get_active_session", return_value=stale):
            resp = self.client.post(
                "/api/session/finish", headers={"X-CSRF-Token": csrf}
            )
        self.assertEqual(resp.status_code, 200)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT paused_seconds, end_ts, start_ts, status FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        self.assertEqual(row[3], "completed")
        # paused_seconds = 0 + (finish_ts - pause_started_ts): the 15-minute
        # pause tail, derived from the row's own pause_started_ts.
        self.assertEqual(row[0], row[1] - (now - 900))
        # Credited time = wall clock minus paused.
        self.assertEqual(row[1] - row[2] - row[0], 3600 - 900)

    def test_admin_finish_does_not_clobber_concurrent_adjust(self):
        csrf = self._register_and_login("adminracer")
        resp = self.client.post(
            "/api/session/start",
            json={"category_name": "Other"},
            headers={"X-CSRF-Token": csrf, "X-TimeStat-Client-State": "1"},
        )
        self.assertEqual(resp.status_code, 200)
        session_id = resp.get_json()["status"]["current_session"]["id"]

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "UPDATE sessions SET start_ts=? WHERE id=?",
                (int(time.time()) - 3600, session_id),
            )
            stale = conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            conn.commit()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET paused_seconds = paused_seconds + 300 WHERE id=?",
                (session_id,),
            )
            conn.commit()

        admin_client = self.app.test_client()
        page = admin_client.get("/admin/login")
        admin_csrf = extract_csrf(page.data)
        resp = admin_client.post(
            "/admin/login",
            data={"code": "test-admin-code-12345", "csrf_token": admin_csrf},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        admin_csrf = extract_csrf(admin_client.get("/admin").data)
        with patch.object(queries, "get_active_session", return_value=stale):
            resp = admin_client.post(
                f"/admin/api/users/{stale['user_id']}/session/finish",
                headers={"X-CSRF-Token": admin_csrf},
            )
        self.assertEqual(resp.status_code, 200)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT paused_seconds, status FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        self.assertEqual(row[0], 300)
        self.assertEqual(row[1], "completed")


if __name__ == "__main__":
    unittest.main()

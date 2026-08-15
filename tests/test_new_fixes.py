"""Tests for the bug fixes, security hardening, perf work, and new features.

Covers:
  * S1  - account-scoped rate limiting (independent of source IP)
  * S3  - CSRF enforced on anonymous login/register (login-CSRF)
  * B3  - atomic session state transitions (status guards on UPDATEs)
  * P1  - SQL windowed aggregation parity vs the Python proration helper
  * F1  - SSE live stream (/api/stream)
  * F8  - admin analytics page + JSON endpoint (admin-only)
"""

import os
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import app as app_module
import config
from services import queries
from utils import helpers


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

    def _register(self, username: str):
        """Register and return the rendered 6-digit code + a fresh csrf token."""
        page = self.client.get("/register")
        csrf = extract_csrf(page.data)
        resp = self.client.post(
            "/register",
            data={"username": username, "csrf_token": csrf},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)
        code_match = re.search(rb"(\d{6})", resp.data)
        self.assertIsNotNone(code_match, "login code should be shown after register")
        return code_match.group(1).decode()

    def _login(self, username: str, code: str):
        page = self.client.get("/login")
        csrf = extract_csrf(page.data)
        resp = self.client.post(
            "/login",
            data={"username": username, "code": code, "csrf_token": csrf},
            follow_redirects=True,
        )
        return resp

    def _logout(self, csrf: str):
        self.client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)

    def _active_session_id(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE status IN ('running','paused') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            return int(row[0])


class AccountScopedRateLimitTests(TimeStatTestCase):
    def test_account_lockout_independent_of_ip(self):
        code = self._register("lockme")
        # We're logged in as lockme; log out to attempt logins.
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        self._logout(csrf)

        # Rotate the source IP on every call so each attempt comes from a
        # "fresh" IP. The per-IP bucket therefore never fills, and only the
        # account-scoped bucket should lock the account.
        counter = {"n": 0}

        def rotating_addr():
            counter["n"] += 1
            return f"10.0.{counter['n'] // 250}.{counter['n'] % 250}"

        with patch.object(helpers, "client_addr", side_effect=rotating_addr):
            for _ in range(config.LOGIN_ACCOUNT_MAX_ATTEMPTS):
                page = self.client.get("/login")
                csrf = extract_csrf(page.data)
                self.client.post(
                    "/login",
                    data={"username": "lockme", "code": "000000", "csrf_token": csrf},
                    follow_redirects=True,
                )
            # The next attempt from yet another fresh IP must be locked out
            # by the account-scoped bucket.
            page = self.client.get("/login")
            csrf = extract_csrf(page.data)
            resp = self.client.post(
                "/login",
                data={"username": "lockme", "code": code, "csrf_token": csrf},
                follow_redirects=True,
            )
        self.assertIn(b"Too many login attempts", resp.data)


class LoginCsrfTests(TimeStatTestCase):
    def test_login_without_csrf_token_is_rejected(self):
        self._register("csrfuser")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        self._logout(csrf)

        # Correct username + code but NO csrf_token -> must be bounced by the
        # CSRF guard, not processed by the credential check.
        resp = self.client.post(
            "/login",
            data={"username": "csrfuser", "code": "000000"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid request token", resp.data)
        with self.client.session_transaction() as sess:
            self.assertNotIn("user_id", sess)


class FrontendResilienceTests(unittest.TestCase):
    """Guard the shared-script failure path that browser tests may miss."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_base_template_provides_shared_runtime_fallbacks(self):
        base = (self.ROOT / "templates" / "base.html").read_text()
        self.assertIn('window.MEDALS = ["🥇", "🥈", "🥉"];', base)
        self.assertIn("window.postJson = window.postJson ||", base)
        self.assertIn("window.renderOrUpdateChart = window.renderOrUpdateChart || (() => null);", base)
        self.assertIn("Some optional enhancements are unavailable", base)
        self.assertIn("__timestatCommonLoadFailed", base)

    def test_leaderboards_do_not_index_missing_medals_global(self):
        for name in (
            "dashboard.html",
            "weekly_leaderboard.html",
            "all_time_stats.html",
            "admin_analytics.html",
        ):
            page = (self.ROOT / "templates" / name).read_text()
            self.assertIn("Array.isArray(window.MEDALS)", page, name)
            self.assertNotIn("window.MEDALS[row.rank - 1]", page, name)

    def test_dashboard_bootstrap_handles_widget_failure(self):
        dashboard = (self.ROOT / "templates" / "dashboard.html").read_text()
        self.assertIn("bootstrap().catch", dashboard)
        self.assertIn('console.error("Dashboard bootstrap failed", err)', dashboard)


class AtomicTransitionTests(TimeStatTestCase):
    def test_pause_after_racing_finish_is_rejected(self):
        self._register("racer")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "race"},
            headers={"X-CSRF-Token": csrf},
        )
        session_id = self._active_session_id()

        # Capture the 'running' row as the handler reads it, then flip the DB
        # row to 'completed' underneath it (simulating a racing finish landing
        # in the check-then-act window between the read and the UPDATE).
        now = int(time.time())
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            stale = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            conn.execute(
                "UPDATE sessions SET status='completed', end_ts=?, pause_started_ts=NULL WHERE id=?",
                (now, session_id),
            )
            conn.commit()

        with patch.object(queries, "get_active_session", return_value=stale):
            resp = self.client.post("/api/session/pause", headers={"X-CSRF-Token": csrf})
        self.assertEqual(resp.status_code, 409)
        # The session must remain completed (pause must NOT resurrect it).
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
        self.assertEqual(row[0], "completed")

    def test_adjust_after_racing_finish_is_rejected(self):
        self._register("racer2")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "race"},
            headers={"X-CSRF-Token": csrf},
        )
        session_id = self._active_session_id()
        now = int(time.time())
        with sqlite3.connect(self.db_path) as conn:
            # Backdate start so there's plenty of elapsed time to "adjust".
            conn.execute("UPDATE sessions SET start_ts=? WHERE id=?", (now - 3600, session_id))
            conn.row_factory = sqlite3.Row
            stale = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            conn.execute(
                "UPDATE sessions SET status='completed', end_ts=?, pause_started_ts=NULL WHERE id=?",
                (now, session_id),
            )
            conn.commit()

        with patch.object(queries, "get_active_session", return_value=stale):
            resp = self.client.post(
                "/api/session/adjust",
                json={"seconds": 60},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(resp.status_code, 409)

    def test_concurrent_adjust_same_status_is_rejected(self):
        """Two adjusts racing against the same stale elapsed value must not
        both apply. Before the paused_seconds optimistic-lock fix, this
        endpoint was guarded only by `status IN (...)`, so a second adjust
        reading the same pre-first-adjust snapshot (e.g. an offline-queue
        retry racing a fresh click) would pass validation and double-apply,
        over-subtracting and corrupting the session's duration."""
        self._register("racer3")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "race"},
            headers={"X-CSRF-Token": csrf},
        )
        session_id = self._active_session_id()
        now = int(time.time())
        with sqlite3.connect(self.db_path) as conn:
            # Backdate start so there's plenty of elapsed time for two adjusts.
            conn.execute("UPDATE sessions SET start_ts=? WHERE id=?", (now - 3600, session_id))
            conn.commit()
            conn.row_factory = sqlite3.Row
            stale = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()

        # First adjust applies for real, moving paused_seconds to 60.
        resp1 = self.client.post(
            "/api/session/adjust",
            json={"seconds": 60},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp1.status_code, 200)

        # Second adjust races against the *stale* pre-first-adjust snapshot
        # (paused_seconds still 0) - simulating a concurrent request that
        # read state before the first adjust committed.
        with patch.object(queries, "get_active_session", return_value=stale):
            resp2 = self.client.post(
                "/api/session/adjust",
                json={"seconds": 60},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(resp2.status_code, 409)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT paused_seconds FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        # Only the first adjust's 60s applied - not 120 from a double-apply.
        self.assertEqual(row[0], 60)


class AutoPauseAlertTests(TimeStatTestCase):
    def test_auto_pause_alert_delivered_exactly_once(self):
        """The auto-pause alert is a DB-backed atomic claim
        (auto_pause_pending_alert), not a one-shot Flask-session cookie
        flag, specifically so it survives being read from an SSE response
        (whose cookie writes don't reliably persist). Two reads right after
        the auto-pause transition (simulating a REST poll landing alongside
        an SSE tick) must show the alert exactly once between them, not
        zero times (lost) or twice (a stale re-delivery)."""
        self._register("sleepy")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "long haul"},
            headers={"X-CSRF-Token": csrf},
        )
        session_id = self._active_session_id()
        now = int(time.time())
        limit_seconds = config.MAX_SESSION_RUNNING_HOURS * 3600
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET start_ts=? WHERE id=?",
                (now - limit_seconds - 60, session_id),
            )
            conn.commit()

        first = self.client.get("/api/status").get_json()
        second = self.client.get("/api/status").get_json()

        self.assertEqual(first["current_session"]["status"], "paused")
        self.assertEqual(second["current_session"]["status"], "paused")
        self.assertTrue(first["auto_paused_alert"])
        self.assertFalse(second["auto_paused_alert"])


class WindowedAggregationParityTests(TimeStatTestCase):
    def test_weekly_leaderboard_matches_proration_helper(self):
        self._register("prorata")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "straddle"},
            headers={"X-CSRF-Token": csrf},
        )
        session_id = self._active_session_id()

        now = int(time.time())
        since = now - config.WEEK_SECONDS
        # A session that started before the 7-day cutoff and ended after it,
        # with some paused time, so the window proration actually bites.
        start_ts = since - 3 * 3600
        end_ts = now - 60
        paused = 600
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "UPDATE sessions SET start_ts=?, end_ts=?, paused_seconds=?, status='completed', pause_started_ts=NULL WHERE id=?",
                (start_ts, end_ts, paused, session_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()

        expected = helpers.elapsed_seconds_in_window(row, now, since)

        resp = self.client.get("/api/leaderboard/weekly").get_json()
        total = sum(r["seconds"] for r in resp["leaderboard"])
        self.assertEqual(total, expected)


class SseStreamTests(TimeStatTestCase):
    def test_stream_pushes_status_events(self):
        self._register("streamer")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        # Start a session so the status event must carry current_session.
        self.client.post(
            "/api/session/start",
            json={"category_name": "Other", "note": "live"},
            headers={"X-CSRF-Token": csrf},
        )
        rv = self.client.get("/api/stream", buffered=False)
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.mimetype, "text/event-stream")
        status_payload = None
        for chunk in rv.response:
            text = chunk.decode("utf-8", "replace")
            if text.startswith("event: status"):
                payload = text.split("data: ", 1)[1]
                import json as _json
                status_payload = _json.loads(payload)
                break
        rv.close()
        self.assertIsNotNone(status_payload, "expected at least one status event")
        self.assertIn("current_session", status_payload)
        self.assertIsNotNone(status_payload["current_session"])
        self.assertEqual(status_payload["current_session"]["status"], "running")

    def test_stream_requires_login(self):
        resp = self.client.get("/api/stream")
        # Not authenticated -> admin_required? no, login_required. /api/ -> 401
        self.assertEqual(resp.status_code, 401)


class AdminAnalyticsTests(TimeStatTestCase):
    def _admin_login(self):
        page = self.client.get("/admin/login")
        csrf = extract_csrf(page.data)
        self.client.post(
            "/admin/login",
            data={"code": "test-admin-code-12345", "csrf_token": csrf},
            follow_redirects=False,
        )

    def test_analytics_page_renders_for_admin(self):
        self._admin_login()
        resp = self.client.get("/admin/analytics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Team Analytics", resp.data)

    def test_analytics_json_has_expected_shape(self):
        self._admin_login()
        resp = self.client.get("/admin/api/analytics")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for key in (
            "hours_per_day",
            "hour_of_day",
            "active_users_per_day",
            "top_users_week",
            "categories_all_time",
            "categories_week",
            "server_ts",
        ):
            self.assertIn(key, data)
        self.assertEqual(len(data["hour_of_day"]), 24)

    def test_analytics_blocked_for_non_admin(self):
        # Register a normal user (is not admin) and try to hit analytics.
        self._register("normaluser")
        page = self.client.get("/dashboard")
        csrf = extract_csrf(page.data)
        # Non-admin user hitting the admin analytics page is redirected to admin login.
        resp = self.client.get("/admin/analytics", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login", resp.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main()

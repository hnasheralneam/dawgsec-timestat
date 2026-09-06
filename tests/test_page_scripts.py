"""Syntax-check every rendered page's inline scripts.

A duplicate ``const``/``let`` declaration at the top level of a classic
inline <script> is a SyntaxError that kills the ENTIRE script block for that
page - no event handlers, no timer, no SSE handling - while the page
otherwise renders and passes HTML-level checks. This actually happened (a
duplicated TIMER_RESYNC_TOLERANCE_SECONDS constant), so the suite now
compiles each page's inline scripts.

``node --check`` performs the real parse. If Node.js is not installed the
tests skip rather than fail: the dev tooling is optional at runtime.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_fixes import FixesTestCase, extract_csrf

INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)


def _node_available() -> bool:
    return shutil.which("node") is not None


class PageScriptSyntaxTestCase(FixesTestCase):
    """Render authenticated pages and compile their inline scripts."""

    def _syntax_check_page(self, path: str, csrf: str | None = None) -> None:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, f"GET {path} failed")
        html = response.data.decode("utf-8")
        scripts = INLINE_SCRIPT_RE.findall(html)
        self.assertTrue(scripts, f"no inline scripts found on {path}")

        with tempfile.TemporaryDirectory() as tmp:
            for i, script in enumerate(scripts):
                script_file = os.path.join(tmp, f"inline_{i}.js")
                with open(script_file, "w", encoding="utf-8") as fh:
                    fh.write(script)
                result = subprocess.run(
                    ["node", "--check", script_file],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"inline script #{i} on {path} has a syntax error "
                    f"(this kills the whole script block):\n"
                    f"{result.stderr.strip()}",
                )

    def _admin_csrf(self) -> str:
        admin_login = self.client.get("/admin/login")
        return extract_csrf(admin_login.data)

    def _sign_in_admin(self) -> str:
        csrf = self._admin_csrf()
        response = self.client.post(
            "/admin/login",
            data={"code": "test-admin-code-12345", "csrf_token": csrf},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return extract_csrf(response.data)

    @unittest.skipUnless(_node_available(), "Node.js not installed")
    def test_user_pages_inline_scripts_parse(self):
        csrf = self._register_and_sign_in("scriptcheck")
        # seed one completed session so pages render with data
        self.client.post(
            "/api/session/start",
            json={"category_name": "Research", "note": "t"},
            headers={"X-CSRF-Token": csrf},
        )
        self.client.post(
            "/api/session/finish", json={}, headers={"X-CSRF-Token": csrf}
        )
        for path in (
            "/dashboard",
            "/weekly-leaderboard",
            "/all-time-stats",
            "/users/1",
        ):
            with self.subTest(page=path):
                self._syntax_check_page(path)

    @unittest.skipUnless(_node_available(), "Node.js not installed")
    def test_admin_pages_inline_scripts_parse(self):
        self._sign_in_admin()
        for path in ("/admin", "/admin/analytics"):
            with self.subTest(page=path):
                self._syntax_check_page(path)

    @unittest.skipUnless(_node_available(), "Node.js not installed")
    def test_anonymous_pages_inline_scripts_parse(self):
        for path in ("/login", "/register"):
            with self.subTest(page=path):
                self._syntax_check_page(path)

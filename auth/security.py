import secrets
from functools import wraps
from hmac import compare_digest

from flask import current_app, jsonify, redirect, request, session, url_for

import config
import db
from utils import helpers


def auth_limit_key(scope: str, username: str) -> str:
    normalized_username = (username or "").strip().lower()
    return f"{scope}:{helpers.client_addr()}:{normalized_username}"


def auth_is_limited(scope: str, key: str, max_attempts: int) -> bool:
    conn = db.get_db()
    row = conn.execute(
        "SELECT first_ts, last_ts, failures FROM auth_attempts WHERE scope = ? AND key = ?",
        (scope, key),
    ).fetchone()
    if not row:
        return False
    now = db.now_ts()
    if now - int(row["first_ts"]) > config.AUTH_WINDOW_SECONDS:
        conn.execute("DELETE FROM auth_attempts WHERE scope = ? AND key = ?", (scope, key))
        conn.commit()
        return False
    return int(row["failures"]) >= max_attempts


def auth_record_failure(scope: str, key: str) -> None:
    conn = db.get_db()
    now = db.now_ts()
    row = conn.execute(
        "SELECT first_ts, failures FROM auth_attempts WHERE scope = ? AND key = ?",
        (scope, key),
    ).fetchone()
    if not row or now - int(row["first_ts"]) > config.AUTH_WINDOW_SECONDS:
        conn.execute(
            """
            INSERT INTO auth_attempts(scope, key, first_ts, last_ts, failures)
            VALUES(?, ?, ?, ?, 1)
            ON CONFLICT(scope, key) DO UPDATE SET
                first_ts = excluded.first_ts,
                last_ts = excluded.last_ts,
                failures = excluded.failures
            """,
            (scope, key, now, now),
        )
    else:
        conn.execute(
            """
            UPDATE auth_attempts
            SET failures = failures + 1,
                last_ts = ?
            WHERE scope = ? AND key = ?
            """,
            (now, scope, key),
        )
    conn.commit()


def auth_clear_failures(scope: str, key: str) -> None:
    conn = db.get_db()
    conn.execute("DELETE FROM auth_attempts WHERE scope = ? AND key = ?", (scope, key))
    conn.commit()


# Scopes for the two-layer user-login rate limiting. The IP+username scope
# (LOGIN_IP_SCOPE) limits a single device/IP, while the username-only scope
# (LOGIN_ACCOUNT_SCOPE) is a global per-account lockout so an attacker rotating
# source IPs still can't exceed LOGIN_ACCOUNT_MAX_ATTEMPTS per window.
LOGIN_IP_SCOPE = "user-login"
LOGIN_ACCOUNT_SCOPE = "user-login-account"


def _account_key(username: str) -> str:
    return f"{LOGIN_ACCOUNT_SCOPE}:{(username or '').strip().lower()}"


def user_login_is_limited(username: str) -> bool:
    ip_key = auth_limit_key(LOGIN_IP_SCOPE, username)
    if auth_is_limited(LOGIN_IP_SCOPE, ip_key, config.LOGIN_MAX_ATTEMPTS):
        return True
    return auth_is_limited(
        LOGIN_ACCOUNT_SCOPE, _account_key(username), config.LOGIN_ACCOUNT_MAX_ATTEMPTS
    )


def user_login_record_failure(username: str) -> None:
    ip_key = auth_limit_key(LOGIN_IP_SCOPE, username)
    auth_record_failure(LOGIN_IP_SCOPE, ip_key)
    auth_record_failure(LOGIN_ACCOUNT_SCOPE, _account_key(username))


def user_login_clear_failures(username: str) -> None:
    ip_key = auth_limit_key(LOGIN_IP_SCOPE, username)
    auth_clear_failures(LOGIN_IP_SCOPE, ip_key)
    auth_clear_failures(LOGIN_ACCOUNT_SCOPE, _account_key(username))


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapped


def admin_credentials_configured() -> bool:
    return bool(current_app.config["ADMIN_USERNAME"] and current_app.config["ADMIN_PASSWORD"])


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)

    return wrapped


def valid_admin_credentials(username: str, password: str) -> bool:
    configured_username = current_app.config["ADMIN_USERNAME"]
    configured_password = current_app.config["ADMIN_PASSWORD"]
    return compare_digest(username, configured_username) and compare_digest(
        password, configured_password
    )


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def rotate_csrf_token() -> None:
    session["_csrf_token"] = secrets.token_urlsafe(32)


def validate_csrf_request() -> bool:
    expected = session.get("_csrf_token")
    if not expected:
        return False
    provided = request.headers.get("X-CSRF-Token")
    if not provided:
        provided = request.form.get("csrf_token")
    if not provided:
        return False
    return compare_digest(str(expected), str(provided))

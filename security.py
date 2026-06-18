import secrets
from functools import wraps
from hmac import compare_digest

from flask import current_app, jsonify, redirect, request, session, url_for

import config
import db
import helpers


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

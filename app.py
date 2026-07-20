import logging
import os
import secrets
import sys
from datetime import timedelta

from flask import Flask, flash, jsonify, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import db
from auth import security
from config import BASE_DIR, DB_PATH

import routes.admin as routes_admin
import routes.auth as routes_auth
import routes.pages as routes_pages
import routes.session_api as routes_session_api
import routes.stats_api as routes_stats_api
import routes.stream_api as routes_stream_api
import routes.user_api as routes_user_api

logger = logging.getLogger("timestat")


def create_app() -> Flask:
    config.load_env_file(os.path.join(BASE_DIR, ".env"))
    config.load_env_file("/etc/timestat/timestat.env")

    app = Flask(__name__)

    debug_mode = os.environ.get("FLASK_DEBUG", "").strip() == "1"
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if not debug_mode:
            raise RuntimeError(
                "SECRET_KEY is not set. Refusing to start outside of debug mode. "
                "Set SECRET_KEY in the environment (see deploy/timestat.env.example), "
                "or set FLASK_DEBUG=1 for local development only."
            )
        secret_key = secrets.token_urlsafe(32)
        warning = (
            "SECRET_KEY is not set; falling back to a random key because "
            "FLASK_DEBUG=1. Sessions and CSRF tokens will not survive a restart. "
            "This is not safe outside of local development."
        )
        logger.warning(warning)
        print(f"WARNING: {warning}", file=sys.stderr)
    app.secret_key = secret_key

    app.config["DATABASE"] = DB_PATH
    admin_code = (os.environ.get("ADMIN_CODE") or "").strip()
    if not admin_code:
        # No ADMIN_CODE configured - generate one and persist it to the
        # first writable env file (see config.ensure_admin_code). We refuse
        # to fall back to an in-memory only code because that would rotate
        # on every restart and silently lock the admin out as soon as the
        # process dies; persisting it is what makes it a real credential.
        admin_code = config.generate_admin_code()
        written_path = config.ensure_admin_code(admin_code)
        warning = (
            f"No ADMIN_CODE found - generated a new one and wrote it to "
            f"{written_path}. The admin code is: {admin_code}  "
            f"Save it now; this message will not be repeated. "
            f"(Login at /admin/login with this single code.)"
        )
        logger.warning(warning)
        print(f"WARNING: {warning}", file=sys.stderr)
    app.config["ADMIN_CODE"] = admin_code
    app.config["STORE_LOGIN_CODE_PLAINTEXT"] = (
        os.environ.get("STORE_LOGIN_CODE_PLAINTEXT", "").strip().lower()
        in {"1", "true", "yes"}
    )
    if app.config["STORE_LOGIN_CODE_PLAINTEXT"]:
        warning = (
            "STORE_LOGIN_CODE_PLAINTEXT is enabled: 6-digit login codes are being "
            "stored verbatim in the users.login_code column. Any read-only DB "
            "leak (backup file, SQL dump, etc.) will expose directly-usable "
            "credentials, bypassing the code_hash protection. Disable this in "
            "production unless the 'reveal my code' UX is required."
        )
        logger.warning(warning)
        print(f"WARNING: {warning}", file=sys.stderr)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # SESSION_COOKIE_SECURE intentionally defaults to False so the app keeps
    # working over plain HTTP (e.g. LAN-only deployments / local testing),
    # where browsers reject cookies marked Secure. When serving over HTTPS
    # with a valid certificate, set SESSION_COOKIE_SECURE=true in the env so
    # the session cookie is only ever transmitted over TLS.
    session_cookie_secure = os.environ.get("SESSION_COOKIE_SECURE", "").strip()
    if session_cookie_secure:
        app.config["SESSION_COOKIE_SECURE"] = session_cookie_secure not in {"0", "false", "no", "False"}
    else:
        app.config["SESSION_COOKIE_SECURE"] = False
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)

    trusted_proxy_count_raw = os.environ.get("TRUSTED_PROXY_COUNT", "").strip()
    try:
        trusted_proxy_count = (
            int(trusted_proxy_count_raw)
            if trusted_proxy_count_raw
            else config.DEFAULT_TRUSTED_PROXY_COUNT
        )
    except ValueError:
        trusted_proxy_count = config.DEFAULT_TRUSTED_PROXY_COUNT
    trusted_proxy_count = max(0, trusted_proxy_count)
    app.config["TRUSTED_PROXY_COUNT"] = trusted_proxy_count
    if trusted_proxy_count > 0:
        # Only trust X-Forwarded-For when we know exactly how many reverse
        # proxy hops sit in front of us; ProxyFix validates and strips
        # exactly that many entries so the header can't be spoofed by a
        # client to bypass rate limiting (see utils.helpers.client_addr).
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_count)

    @app.context_processor
    def inject_template_context():
        return {
            "csrf_token": security.csrf_token(),
            "is_admin": bool(session.get("is_admin")),
        }

    @app.before_request
    def enforce_daily_maintenance():
        db.run_daily_maintenance()

    @app.before_request
    def enforce_csrf():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        # CSRF is enforced for ALL state-changing requests, including
        # unauthenticated ones (login, register, admin login). This prevents
        # login-CSRF / forced-registration attacks. The CSRF token is generated
        # lazily by csrf_token() (injected into every template and auto-added
        # to every POST form by base.html), so legitimate pre-auth forms carry it.
        if security.validate_csrf_request():
            return None

        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
            return jsonify({"error": "Invalid CSRF token"}), 400

        flash("Invalid request token. Refresh and try again.", "error")
        if session.get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        # Anonymous state-changing request with a bad token: bounce to the
        # nearest relevant entry point rather than the dashboard (which would
        # itself redirect to login, obscuring the cause).
        if request.path.startswith("/admin"):
            return redirect(url_for("admin_login"))
        return redirect(url_for("login"))

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'",
        )
        return response

    @app.teardown_appcontext
    def close_db(exception):
        db.close_db(exception)

    routes_auth.register_routes(app)
    routes_admin.register_routes(app)
    routes_pages.register_routes(app)
    routes_session_api.register_routes(app)
    routes_user_api.register_routes(app)
    routes_stats_api.register_routes(app)
    routes_stream_api.register_routes(app)

    with app.app_context():
        db.init_db()
        db.run_daily_maintenance()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "").strip() == "1")

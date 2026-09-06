import logging
import os
import secrets
import sys
import gzip
import hashlib
import threading
import time
from datetime import timedelta

from flask import Flask, flash, jsonify, redirect, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import db
from auth import security
from config import BASE_DIR, DB_PATH
from services import live_cache
from services import queries

import routes.admin as routes_admin
import routes.auth as routes_auth
import routes.pages as routes_pages
import routes.session_api as routes_session_api
import routes.stats_api as routes_stats_api
import routes.stream_api as routes_stream_api
import routes.user_api as routes_user_api

logger = logging.getLogger("timestat")


def _compute_static_version(static_dir: str) -> str:
    """Content-hash every static asset so a deploy automatically busts
    browser and service-worker caches.

    Templates append this version to static URLs (?v=...), which lets
    /static/ be served with a year-long immutable cache without ever
    shipping stale assets. This replaces the old scheme where a hand-edited
    CACHE_VERSION string in the service worker had to be bumped in lockstep
    with every asset change. Hashing ~300KB of assets at startup is
    negligible, and each Gunicorn worker computes the same value.
    """
    digest = hashlib.sha256()
    found = False
    for root, _dirs, files in os.walk(static_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            found = True
            digest.update(os.path.relpath(path, static_dir).encode("utf-8"))
            digest.update(data)
    return digest.hexdigest()[:12] if found else "0"


def create_app() -> Flask:
    config.load_env_file(os.path.join(BASE_DIR, ".env"))
    config.load_env_file("/etc/timestat/timestat.env")
    # Day/week boundaries for activity grids and analytics are computed
    # against the server process's local timezone (SQLite's 'localtime'
    # modifier, and Python's own local-time conversions). Without this,
    # setting TZ in the env file has no effect on an already-running
    # process's C-library timezone state, and sessions worked late at night
    # can silently land on the "wrong" day if the server's default
    # (frequently UTC) doesn't match where the team actually works.
    if hasattr(time, "tzset"):
        time.tzset()

    app = Flask(__name__)
    app.config["STATIC_VERSION"] = _compute_static_version(app.static_folder)

    @app.template_global()
    def static_v(filename):
        """Static URL with a content-hash version parameter (?v=...), so the
        year-long immutable cache on /static/ is safe: any asset change
        produces new URLs everywhere it is referenced."""
        return url_for("static", filename=filename, v=app.config["STATIC_VERSION"])

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
            # All frontend assets (including Chart.js and the icon font) are
            # self-hosted, so no third-party origins are needed. 'unsafe-inline'
            # remains for the per-page inline <script> blocks and Tailwind.
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'",
        )
        if request.path.startswith("/static/"):
            # Static URLs carry a content-hash version parameter (see
            # static_v), so a new deploy means new URLs. Content is therefore
            # effectively immutable and can be cached for a year, by both the
            # browser and any reverse proxy in front of the app. Set (not
            # setdefault): send_file already emits "Cache-Control: no-cache"
            # for the versioned asset, which must be overridden.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.after_request
    def compress_response(response):
        """Compress sizeable text responses when the client supports gzip.

        SSE must remain uncompressed and flushable. Binary assets such as the
        local font are already compressed, while JSON and HTML benefit greatly
        on slow links without adding a runtime dependency.
        """
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if response.headers.get("Content-Encoding"):
            return response
        if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
            return response

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type == "text/event-stream":
            return response
        if content_type not in {
            "application/json",
            "application/javascript",
            "text/javascript",
            "text/css",
            "text/html",
            "text/plain",
            "image/svg+xml",
        }:
            return response

        # get_data() materializes a response. The allowlist above excludes the
        # streaming endpoint, and the remaining responses are bounded assets
        # or API/template bodies.
        if response.direct_passthrough:
            response.direct_passthrough = False
        body = response.get_data()
        if len(body) < 500:
            return response
        compressed = gzip.compress(body, compresslevel=6)
        if len(compressed) >= len(body):
            return response
        # The original send_file ETag identifies the uncompressed bytes, but
        # this response is now the compressed byte stream - replace it with a
        # weak ETag over the compressed bytes (deterministic for a given body
        # at this fixed compression level). Without this the old code dropped
        # the ETag entirely and no compressed response could ever 304, forcing
        # full re-downloads on every hard reload.
        gzip_etag = f'W/"gzip-{hashlib.md5(compressed).hexdigest()}"'
        if (
            request.method in {"GET", "HEAD"}
            and response.status_code == 200
            and gzip_etag in request.headers.get("If-None-Match", "")
        ):
            # Revalidation hit: the client already holds exactly these
            # compressed bytes. The 304 decision normally happens inside
            # send_file against the uncompressed ETag, which can never match,
            # so it is handled here instead - after the compressed bytes are
            # known but before they are sent.
            response.status_code = 304
            response.set_data(b"")
            response.headers["ETag"] = gzip_etag
            response.headers.pop("Content-Encoding", None)
            response.headers.pop("Content-Type", None)
            response.headers["Content-Length"] = "0"
            return response
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        response.headers["ETag"] = gzip_etag
        vary = response.headers.get("Vary")
        response.headers["Vary"] = (
            f"{vary}, Accept-Encoding" if vary and "accept-encoding" not in vary.lower()
            else vary or "Accept-Encoding"
        )
        return response

    @app.errorhandler(Exception)
    def handle_unexpected_error(err):
        # HTTPException subclasses (404, the CSRF 400 above, abort(...), etc.)
        # already carry their own correct status/body - only unexpected,
        # truly-unhandled exceptions (e.g. a SQLite "database is locked"
        # under write contention) should hit this path.
        if isinstance(err, HTTPException):
            return err
        logger.exception("Unhandled exception on %s %s", request.method, request.path)
        # Without this, an unhandled exception on an API route returned
        # Flask's default HTML error page. postJson()/fetch callers parsing
        # that as JSON got a cryptic SyntaxError instead of any real message.
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
            return jsonify({"error": "Something went wrong. Please try again."}), 500
        return "Internal Server Error", 500

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

    _start_auto_pause_sweep(app)

    return app


def _start_auto_pause_sweep(app: Flask) -> None:
    """Run a daemon thread that auto-pauses running sessions past the 8h cap
    and drives daily maintenance off the request path.

    The cap used to be enforced inside get_active_session(), which is called
    from read paths (/api/status, the SSE stream, build_status_payload) - so
    a "status poll" mutated the DB. Moving it to a background sweep keeps
    every read path pure while still capping sessions even when a team is
    idle (no mutation ever fires). The sweep invalidates the live cache when
    it pauses anything, so open SSE clients pick up the change on their next
    tick.

    Daily maintenance (backup + auth-attempt pruning) rides the same thread
    via db.run_daily_maintenance()'s lock-file + marker-file coordination, so
    at most one worker performs it per day and it never blocks a request.
    Each Gunicorn worker runs its own copy of this thread; both sweep
    operations are safe to run concurrently across workers.
    """
    db_path = app.config["DATABASE"]
    interval = config.AUTO_PAUSE_SWEEP_INTERVAL_SECONDS

    def _sweep() -> None:
        while True:
            time.sleep(interval)
            try:
                with app.app_context():
                    db.run_daily_maintenance()
            except Exception:
                logger.exception("Daily maintenance failed")
            try:
                paused = queries.pause_overdue_running_sessions(db_path)
                if paused:
                    live_cache.invalidate()
            except Exception:
                logger.exception("Auto-pause sweep failed")

    threading.Thread(target=_sweep, name="auto-pause-sweep", daemon=True).start()


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "").strip() == "1")

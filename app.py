import os
import secrets

from flask import Flask, flash, jsonify, redirect, request, session, url_for

import config
import db
from auth import security
from config import BASE_DIR, DB_PATH

import routes.admin as routes_admin
import routes.auth as routes_auth
import routes.pages as routes_pages
import routes.session_api as routes_session_api
import routes.stats_api as routes_stats_api
import routes.user_api as routes_user_api


def create_app() -> Flask:
    config.load_env_file(os.path.join(BASE_DIR, ".env"))
    config.load_env_file("/etc/timestat/timestat.env")

    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
    app.config["DATABASE"] = DB_PATH
    app.config["ADMIN_USERNAME"] = (os.environ.get("ADMIN_USERNAME") or "").strip()
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD") or ""
    app.config["STORE_LOGIN_CODE_PLAINTEXT"] = (
        os.environ.get("STORE_LOGIN_CODE_PLAINTEXT", "").strip().lower()
        in {"1", "true", "yes"}
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    )

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
        if "user_id" not in session and not session.get("is_admin"):
            return None
        if security.validate_csrf_request():
            return None

        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
            return jsonify({"error": "Invalid CSRF token"}), 400

        flash("Invalid request token. Refresh and try again.", "error")
        if session.get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

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

    with app.app_context():
        db.init_db()
        db.run_daily_maintenance()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "").strip() == "1")

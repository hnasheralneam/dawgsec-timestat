import sqlite3

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db
from utils import helpers
from utils import parsing
from auth import security


def register_routes(app):
    @app.get("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render_template("register.html")

        username, username_error = parsing.parse_username(request.form.get("username"))
        if username_error:
            flash(username_error, "error")
            return redirect(url_for("register"))

        six_digit_code = helpers.generate_login_code()
        stored_login_code = (
            six_digit_code if app.config["STORE_LOGIN_CODE_PLAINTEXT"] else None
        )
        conn = db.get_db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO users(username, code_hash, login_code, created_ts)
                VALUES(?, ?, ?, ?)
                """,
                (
                    username,
                    generate_password_hash(six_digit_code),
                    stored_login_code,
                    db.now_ts(),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
            return redirect(url_for("register"))

        session.clear()
        session["user_id"] = int(cursor.lastrowid)
        security.rotate_csrf_token()
        return render_template(
            "register_success.html", username=username, six_digit_code=six_digit_code
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")

        username, username_error = parsing.parse_username(request.form.get("username"))
        code = request.form.get("code", "").strip()
        rate_key = security.auth_limit_key("user-login", request.form.get("username") or "")
        if security.auth_is_limited("user-login", rate_key, config.LOGIN_MAX_ATTEMPTS):
            flash("Too many login attempts. Please wait a few minutes and try again.", "error")
            return redirect(url_for("login"))
        if username_error:
            security.auth_record_failure("user-login", rate_key)
            flash("Invalid username or 6-digit code.", "error")
            return redirect(url_for("login"))
        conn = db.get_db()
        user = conn.execute(
            "SELECT id, username, code_hash FROM users WHERE username = ?", (username,)
        ).fetchone()

        if not user or not check_password_hash(user["code_hash"], code):
            security.auth_record_failure("user-login", rate_key)
            flash("Invalid username or 6-digit code.", "error")
            return redirect(url_for("login"))

        security.auth_clear_failures("user-login", rate_key)
        session.clear()
        session["user_id"] = user["id"]
        security.rotate_csrf_token()
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

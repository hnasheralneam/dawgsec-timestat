import sqlite3

from flask import current_app, jsonify, request, session
from werkzeug.security import generate_password_hash

import db
import helpers
import parsing
import security


def register_routes(app):
    @app.get("/api/user/settings")
    @security.login_required
    def api_user_settings():
        user_id = int(session["user_id"])
        conn = db.get_db()
        user = conn.execute(
            "SELECT id, username, login_code, notify_on_collab_starts FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404
        login_code = user["login_code"] if current_app.config["STORE_LOGIN_CODE_PLAINTEXT"] else None
        return jsonify(
            {
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "login_code": login_code,
                    "notify_on_collab_starts": bool(user["notify_on_collab_starts"]),
                }
            }
        )

    @app.post("/api/user/settings")
    @security.login_required
    def api_user_settings_update():
        payload = request.get_json(silent=True) or {}
        username, username_error = parsing.parse_username(payload.get("username"))
        if username_error:
            return jsonify({"error": username_error}), 400
        notify_on_collab_starts = payload.get("notify_on_collab_starts")
        if not isinstance(notify_on_collab_starts, bool):
            return jsonify({"error": "notify_on_collab_starts must be a boolean"}), 400

        user_id = int(session["user_id"])
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE users
                SET username = ?, notify_on_collab_starts = ?
                WHERE id = ?
                """,
                (username, int(notify_on_collab_starts), user_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "That username is already taken"}), 400
        return jsonify(
            {
                "ok": True,
                "user": {
                    "id": user_id,
                    "username": username,
                    "notify_on_collab_starts": notify_on_collab_starts,
                },
            }
        )

    @app.post("/api/user/settings/reset-login-code")
    @security.login_required
    def api_user_settings_reset_login_code():
        user_id = int(session["user_id"])
        new_login_code = helpers.generate_login_code()
        stored_login_code = (
            new_login_code if current_app.config["STORE_LOGIN_CODE_PLAINTEXT"] else None
        )
        conn = db.get_db()
        conn.execute(
            """
            UPDATE users
            SET code_hash = ?, login_code = ?
            WHERE id = ?
            """,
            (generate_password_hash(new_login_code), stored_login_code, user_id),
        )
        conn.commit()
        return jsonify({"ok": True, "login_code": new_login_code})

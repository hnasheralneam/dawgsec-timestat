import sqlite3

from flask import flash, jsonify, redirect, render_template, request, session, url_for

import config
import db
from utils import helpers
from utils import parsing
from auth import security


def register_routes(app):
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        if request.method == "GET":
            return render_template("admin_login.html")

        rate_key = security.auth_limit_key("admin-login", request.form.get("username") or "")
        if security.auth_is_limited(
            "admin-login", rate_key, config.ADMIN_LOGIN_MAX_ATTEMPTS
        ):
            flash(
                "Too many admin login attempts. Please wait a few minutes and try again.",
                "error",
            )
            return redirect(url_for("admin_login"))

        if not security.admin_credentials_configured():
            flash(
                "Admin login is disabled. Set ADMIN_USERNAME and ADMIN_PASSWORD in config.",
                "error",
            )
            return redirect(url_for("admin_login"))

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not security.valid_admin_credentials(username, password):
            security.auth_record_failure("admin-login", rate_key)
            flash("Invalid admin username or password.", "error")
            return redirect(url_for("admin_login"))

        security.auth_clear_failures("admin-login", rate_key)
        session.clear()
        session["is_admin"] = True
        security.rotate_csrf_token()
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/logout")
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    @app.get("/admin")
    @security.admin_required
    def admin_dashboard():
        conn = db.get_db()
        rows = conn.execute(
            """
            SELECT
                u.id,
                u.username,
                u.created_ts,
                COUNT(s.id) AS task_count,
                SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                SUM(CASE WHEN s.status IN ('running', 'paused') THEN 1 ELSE 0 END) AS active_count
            FROM users u
            LEFT JOIN sessions s ON s.user_id = u.id
            GROUP BY u.id
            ORDER BY LOWER(u.username)
            """
        ).fetchall()
        users = [
            {
                "id": row["id"],
                "username": row["username"],
                "created_ts": row["created_ts"],
                "task_count": int(row["task_count"] or 0),
                "completed_count": int(row["completed_count"] or 0),
                "active_count": int(row["active_count"] or 0),
            }
            for row in rows
        ]
        categories = conn.execute(
            "SELECT id, name FROM categories ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return render_template(
            "admin_dashboard.html",
            users=users,
            categories=categories,
            active_page="admin_dashboard",
        )

    @app.post("/admin/categories")
    @security.admin_required
    def admin_add_category():
        category_name, category_error = parsing.parse_category_name(request.form.get("name"))
        if category_error:
            flash(category_error, "error")
            return redirect(url_for("admin_dashboard"))

        conn = db.get_db()
        exists = conn.execute(
            "SELECT id FROM categories WHERE lower(name) = lower(?)",
            (category_name,),
        ).fetchone()
        if exists:
            flash("That category already exists.", "error")
            return redirect(url_for("admin_dashboard"))
        try:
            conn.execute("INSERT INTO categories(name) VALUES(?)", (category_name,))
            conn.commit()
        except sqlite3.IntegrityError:
            flash("That category already exists.", "error")
            return redirect(url_for("admin_dashboard"))

        flash(f"Added category '{category_name}'.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/categories/<int:category_id>/delete")
    @security.admin_required
    def admin_delete_category(category_id: int):
        conn = db.get_db()
        category = conn.execute(
            "SELECT id, name FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()
        if not category:
            flash("Category not found.", "error")
            return redirect(url_for("admin_dashboard"))

        remaining_count = int(
            conn.execute("SELECT COUNT(*) AS count FROM categories").fetchone()["count"]
        )
        if remaining_count <= 1:
            flash("At least one category must remain available.", "error")
            return redirect(url_for("admin_dashboard"))

        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()
        flash(f"Removed category '{category['name']}' from available task options.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.get("/admin/api/users/<int:user_id>/tasks")
    @security.admin_required
    def admin_user_tasks(user_id: int):
        conn = db.get_db()
        target_user = conn.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        current_ts = db.now_ts()
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.category_name,
                s.status,
                s.note,
                s.start_ts,
                s.end_ts,
                s.paused_seconds,
                s.pause_started_ts
            FROM sessions s
            WHERE s.user_id = ?
            ORDER BY s.id DESC
            """,
            (user_id,),
        ).fetchall()

        tasks = []
        for row in rows:
            tasks.append(
                {
                    "id": row["id"],
                    "category_name": row["category_name"] or "Other",
                    "note": row["note"] or "",
                    "status": row["status"],
                    "start_ts": row["start_ts"],
                    "end_ts": row["end_ts"],
                    "duration_seconds": helpers.elapsed_seconds(row, current_ts),
                }
            )

        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "tasks": tasks,
            }
        )

    @app.post("/admin/api/users/<int:user_id>/tasks/delete")
    @security.admin_required
    def admin_delete_user_tasks(user_id: int):
        payload = request.get_json(silent=True) or {}
        session_ids = payload.get("session_ids")
        if not isinstance(session_ids, list):
            return jsonify({"error": "session_ids must be an array of integers"}), 400

        normalized_ids = []
        for session_id in session_ids:
            if not isinstance(session_id, int):
                return jsonify({"error": "session_ids must be an array of integers"}), 400
            if session_id not in normalized_ids:
                normalized_ids.append(session_id)

        if not normalized_ids:
            return jsonify({"error": "Select at least one task to delete"}), 400

        conn = db.get_db()
        target_user = conn.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        placeholders = ",".join("?" for _ in normalized_ids)
        existing = conn.execute(
            f"""
            SELECT id
            FROM sessions
            WHERE user_id = ? AND id IN ({placeholders})
            """,
            (user_id, *normalized_ids),
        ).fetchall()
        existing_ids = [row["id"] for row in existing]
        if not existing_ids:
            return jsonify({"error": "No matching tasks found for this user"}), 404

        existing_placeholders = ",".join("?" for _ in existing_ids)
        conn.execute(
            f"DELETE FROM sessions WHERE user_id = ? AND id IN ({existing_placeholders})",
            (user_id, *existing_ids),
        )
        conn.commit()
        return jsonify({"ok": True, "deleted_count": len(existing_ids)})

    @app.post("/admin/users/<int:user_id>/delete")
    @security.admin_required
    def admin_delete_user(user_id: int):
        conn = db.get_db()
        target_user = conn.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not target_user:
            flash("User not found.", "error")
            return redirect(url_for("admin_dashboard"))

        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        if session.get("user_id") == user_id:
            session.pop("user_id", None)
        flash(f"Removed user '{target_user['username']}' and all associated tasks.", "success")
        return redirect(url_for("admin_dashboard"))

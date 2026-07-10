import sqlite3

from flask import jsonify, request, session

import config
import db
from utils import helpers
from utils import parsing
from services import queries
from services import payloads
from auth import security


def register_routes(app):
    @app.get("/api/status")
    @security.login_required
    def api_status():
        user_id = int(session["user_id"])
        current_ts = db.now_ts()
        collab_since_raw = request.args.get("collab_since")
        collab_since_ts = current_ts
        if collab_since_raw is not None:
            try:
                collab_since_ts = int(collab_since_raw)
            except ValueError:
                return jsonify({"error": "collab_since must be an integer"}), 400
        collab_since_ts = max(
            current_ts - config.COLLAB_SINCE_MAX_AGE_SECONDS,
            min(collab_since_ts, current_ts),
        )
        return jsonify(
            payloads.build_status_payload(user_id, current_ts, collab_since_ts, pop_alert=True)
        )

    @app.post("/api/session/start")
    @security.login_required
    def api_start_session():
        payload = request.get_json(silent=True) or {}
        category_name, category_error = parsing.parse_category_name(payload.get("category_name"))
        note, note_error = parsing.parse_note(payload.get("note"))

        if category_error:
            return jsonify({"error": category_error}), 400
        if note_error:
            return jsonify({"error": note_error}), 400

        conn = db.get_db()
        category = conn.execute(
            "SELECT name FROM categories WHERE lower(name) = lower(?)",
            (category_name,),
        ).fetchone()
        if not category:
            return jsonify({"error": "Unknown category"}), 400

        user_id = int(session["user_id"])
        if queries.get_active_session(user_id):
            return jsonify({"error": "Finish your current session first"}), 400

        ts = db.now_ts()
        try:
            conn.execute(
                """
                INSERT INTO sessions(user_id, category_name, note, start_ts, status, created_ts)
                VALUES(?, ?, ?, ?, 'running', ?)
                """,
                (user_id, category["name"], note, ts, ts),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return jsonify({"error": "session already active"}), 409
        return jsonify({"ok": True})

    @app.post("/api/session/pause")
    @security.login_required
    def api_pause_session():
        user_id = int(session["user_id"])
        active = queries.get_active_session(user_id)
        if not active or active["status"] != "running":
            return jsonify({"error": "No running session to pause"}), 400

        ts = db.now_ts()
        conn = db.get_db()
        try:
            cur = conn.execute(
                "UPDATE sessions SET status = 'paused', pause_started_ts = ? WHERE id = ? AND status = 'running'",
                (ts, active["id"]),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({"error": "session state changed"}), 409
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return jsonify({"error": "session already active"}), 409
        return jsonify({"ok": True})

    @app.post("/api/session/resume")
    @security.login_required
    def api_resume_session():
        user_id = int(session["user_id"])
        active = queries.get_active_session(user_id)
        if not active or active["status"] != "paused":
            return jsonify({"error": "No paused session to resume"}), 400

        ts = db.now_ts()
        extra_paused = ts - int(active["pause_started_ts"] or ts)
        conn = db.get_db()
        try:
            cur = conn.execute(
                """
                UPDATE sessions
                SET status = 'running',
                    paused_seconds = paused_seconds + ?,
                    pause_started_ts = NULL
                WHERE id = ? AND status = 'paused'
                """,
                (extra_paused, active["id"]),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({"error": "session state changed"}), 409
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return jsonify({"error": "session already active"}), 409
        return jsonify({"ok": True})

    @app.post("/api/session/finish")
    @security.login_required
    def api_finish_session():
        user_id = int(session["user_id"])
        active = queries.get_active_session(user_id)
        if not active:
            return jsonify({"error": "No active session to finish"}), 400

        ts = db.now_ts()
        paused_seconds = int(active["paused_seconds"])
        if active["status"] == "paused" and active["pause_started_ts"] is not None:
            paused_seconds += ts - int(active["pause_started_ts"])

        conn = db.get_db()
        cur = conn.execute(
            """
            UPDATE sessions
            SET status = 'completed',
                end_ts = ?,
                paused_seconds = ?,
                pause_started_ts = NULL
            WHERE id = ? AND status IN ('running', 'paused')
            """,
            (ts, paused_seconds, active["id"]),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "session state changed"}), 409
        conn.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/cancel")
    @security.login_required
    def api_cancel_session():
        user_id = int(session["user_id"])
        active = queries.get_active_session(user_id)
        if not active:
            return jsonify({"error": "No active session to cancel"}), 400

        conn = db.get_db()
        cur = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND status IN ('running', 'paused')",
            (active["id"],),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "session state changed"}), 409
        conn.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/adjust")
    @security.login_required
    def api_adjust_session():
        payload = request.get_json(silent=True) or {}
        seconds = payload.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            return jsonify({"error": "seconds must be an integer"}), 400
        if seconds <= 0:
            return jsonify({"error": "seconds must be greater than zero"}), 400

        user_id = int(session["user_id"])
        active = queries.get_active_session(user_id)
        if not active:
            return jsonify({"error": "No active session to adjust"}), 400

        current_ts = db.now_ts()
        available = helpers.elapsed_seconds(active, current_ts)
        if seconds > available:
            minutes = seconds // 60
            unit = "minutes" if minutes != 1 else "minute"
            label = f"{minutes} {unit}" if minutes else f"{seconds} seconds"
            return jsonify({"error": f"Not enough elapsed time to remove {label}."}), 400

        conn = db.get_db()
        cur = conn.execute(
            "UPDATE sessions SET paused_seconds = paused_seconds + ? WHERE id = ? AND status IN ('running', 'paused')",
            (seconds, active["id"]),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "session state changed"}), 409
        conn.commit()
        return jsonify(
            {"ok": True, "removed_seconds": seconds, "remaining_seconds": available - seconds}
        )

    @app.post("/api/session/delete")
    @security.login_required
    def api_delete_session():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id")
        if not isinstance(session_id, int) or isinstance(session_id, bool):
            return jsonify({"error": "session_id must be an integer"}), 400

        user_id = int(session["user_id"])
        conn = db.get_db()
        existing = conn.execute(
            """
            SELECT id FROM sessions
            WHERE id = ? AND user_id = ? AND status = 'completed'
            """,
            (session_id, user_id),
        ).fetchone()
        if not existing:
            return jsonify({"error": "Completed session not found"}), 404

        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/update")
    @security.login_required
    def api_update_session():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id")
        category_name, category_error = parsing.parse_category_name(payload.get("category_name"))
        note, note_error = parsing.parse_note(payload.get("note"))

        if not isinstance(session_id, int) or isinstance(session_id, bool):
            return jsonify({"error": "session_id must be an integer"}), 400
        if category_error:
            return jsonify({"error": category_error}), 400
        if note_error:
            return jsonify({"error": note_error}), 400

        user_id = int(session["user_id"])
        conn = db.get_db()
        existing = conn.execute(
            """
            SELECT id FROM sessions
            WHERE id = ? AND user_id = ? AND status = 'completed'
            """,
            (session_id, user_id),
        ).fetchone()
        if not existing:
            return jsonify({"error": "Completed session not found"}), 404

        category = conn.execute(
            "SELECT name FROM categories WHERE lower(name) = lower(?)",
            (category_name,),
        ).fetchone()
        if not category:
            return jsonify({"error": "Unknown category"}), 400

        conn.execute(
            """
            UPDATE sessions
            SET category_name = ?, note = ?
            WHERE id = ?
            """,
            (category["name"], note, session_id),
        )
        conn.commit()
        return jsonify({"ok": True})

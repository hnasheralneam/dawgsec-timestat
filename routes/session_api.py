from flask import jsonify, request, session

import db
from utils import helpers
from utils import parsing
from services import queries
from auth import security


def register_routes(app):
    @app.get("/api/status")
    @security.login_required
    def api_status():
        user_id = int(session["user_id"])
        active = queries.get_active_session(user_id)
        current_ts = db.now_ts()
        collab_since_raw = request.args.get("collab_since")
        collab_since_ts = current_ts
        if collab_since_raw is not None:
            try:
                collab_since_ts = int(collab_since_raw)
            except ValueError:
                return jsonify({"error": "collab_since must be an integer"}), 400
        collab_since_ts = max(0, min(collab_since_ts, current_ts))

        conn = db.get_db()
        user_settings = conn.execute(
            "SELECT notify_on_collab_starts FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        notify_on_collab_starts = (
            bool(user_settings["notify_on_collab_starts"]) if user_settings else True
        )
        team_presence = queries.collaborator_presence_rows(current_ts, exclude_user_id=user_id)
        new_starts = queries.started_session_events(collab_since_ts, exclude_user_id=user_id)
        if not active:
            return jsonify(
                {
                    "current_session": None,
                    "server_ts": current_ts,
                    "team_presence": team_presence,
                    "new_starts": new_starts,
                    "notify_on_collab_starts": notify_on_collab_starts,
                }
            )

        return jsonify(
            {
                "server_ts": current_ts,
                "team_presence": team_presence,
                "new_starts": new_starts,
                "notify_on_collab_starts": notify_on_collab_starts,
                "current_session": {
                    "id": active["id"],
                    "category_name": active["category_name"],
                    "note": active["note"] or "",
                    "status": active["status"],
                    "elapsed_seconds": helpers.elapsed_seconds(active, current_ts),
                    "start_ts": active["start_ts"],
                },
            }
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
        conn.execute(
            """
            INSERT INTO sessions(user_id, category_name, note, start_ts, status, created_ts)
            VALUES(?, ?, ?, ?, 'running', ?)
            """,
            (user_id, category["name"], note, ts, ts),
        )
        conn.commit()
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
        conn.execute(
            "UPDATE sessions SET status = 'paused', pause_started_ts = ? WHERE id = ?",
            (ts, active["id"]),
        )
        conn.commit()
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
        conn.execute(
            """
            UPDATE sessions
            SET status = 'running',
                paused_seconds = paused_seconds + ?,
                pause_started_ts = NULL
            WHERE id = ?
            """,
            (extra_paused, active["id"]),
        )
        conn.commit()
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
        conn.execute(
            """
            UPDATE sessions
            SET status = 'completed',
                end_ts = ?,
                paused_seconds = ?,
                pause_started_ts = NULL
            WHERE id = ?
            """,
            (ts, paused_seconds, active["id"]),
        )
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
        conn.execute("DELETE FROM sessions WHERE id = ?", (active["id"],))
        conn.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/adjust")
    @security.login_required
    def api_adjust_session():
        payload = request.get_json(silent=True) or {}
        seconds = payload.get("seconds")
        if not isinstance(seconds, int):
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
        conn.execute(
            "UPDATE sessions SET paused_seconds = paused_seconds + ? WHERE id = ?",
            (seconds, active["id"]),
        )
        conn.commit()
        return jsonify(
            {"ok": True, "removed_seconds": seconds, "remaining_seconds": available - seconds}
        )

    @app.post("/api/session/delete")
    @security.login_required
    def api_delete_session():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id")
        if not isinstance(session_id, int):
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

        if not isinstance(session_id, int):
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

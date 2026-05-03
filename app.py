import os
import random
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "timestat.db")

DEFAULT_CATEGORIES = [
    "Managing Infrastructure",
    "Working on Scripts",
    "Working on Wiki Documentation",
    "Working on Playbooks",
    "Practicing IR",
    "In Practice Competition",
    "Research",
    "TryHackMe",
    "Team Coordination",
    "Mentoring/Training Others",
    "Other",
]


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["DATABASE"] = DB_PATH

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    def now_ts() -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def elapsed_seconds(row: sqlite3.Row, current_ts: int) -> int:
        end_ts = row["end_ts"] if row["end_ts"] is not None else current_ts
        elapsed = end_ts - row["start_ts"] - row["paused_seconds"]
        if row["status"] == "paused" and row["pause_started_ts"] is not None:
            elapsed -= current_ts - row["pause_started_ts"]
        return max(0, int(elapsed))

    def init_db() -> None:
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                code_hash TEXT NOT NULL,
                created_ts INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                note TEXT,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER,
                paused_seconds INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN ('running', 'paused', 'completed')),
                pause_started_ts INTEGER,
                created_ts INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(category_id) REFERENCES categories(id)
            );
            """
        )

        for cat in DEFAULT_CATEGORIES:
            db.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (cat,))
        db.commit()

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Unauthorized"}), 401
                return redirect(url_for("login"))
            return fn(*args, **kwargs)

        return wrapped

    def get_current_user():
        if "user_id" not in session:
            return None
        db = get_db()
        return db.execute(
            "SELECT id, username FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()

    def get_active_session(user_id: int):
        db = get_db()
        return db.execute(
            """
            SELECT s.*, c.name AS category_name
            FROM sessions s
            JOIN categories c ON c.id = s.category_id
            WHERE s.user_id = ? AND s.status IN ('running', 'paused')
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    @app.teardown_appcontext
    def close_db(_exception):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.get("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render_template("register.html")

        username = request.form.get("username", "").strip()
        if not username:
            flash("Username is required.", "error")
            return redirect(url_for("register"))

        six_digit_code = f"{random.SystemRandom().randrange(0, 1_000_000):06d}"
        db = get_db()
        try:
            db.execute(
                """
                INSERT INTO users(username, code_hash, created_ts)
                VALUES(?, ?, ?)
                """,
                (username, generate_password_hash(six_digit_code), now_ts()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
            return redirect(url_for("register"))

        return render_template(
            "register_success.html", username=username, six_digit_code=six_digit_code
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")

        username = request.form.get("username", "").strip()
        code = request.form.get("code", "").strip()
        db = get_db()
        user = db.execute(
            "SELECT id, username, code_hash FROM users WHERE username = ?", (username,)
        ).fetchone()

        if not user or not check_password_hash(user["code_hash"], code):
            flash("Invalid username or 6-digit code.", "error")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        db = get_db()
        categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        user = get_current_user()
        return render_template("dashboard.html", categories=categories, user=user)

    @app.get("/api/status")
    @login_required
    def api_status():
        user_id = int(session["user_id"])
        active = get_active_session(user_id)
        current_ts = now_ts()
        if not active:
            return jsonify({"current_session": None, "server_ts": current_ts})

        return jsonify(
            {
                "server_ts": current_ts,
                "current_session": {
                    "id": active["id"],
                    "category_name": active["category_name"],
                    "note": active["note"] or "",
                    "status": active["status"],
                    "elapsed_seconds": elapsed_seconds(active, current_ts),
                    "start_ts": active["start_ts"],
                },
            }
        )

    @app.post("/api/session/start")
    @login_required
    def api_start_session():
        payload = request.get_json(silent=True) or {}
        category_id = payload.get("category_id")
        note = (payload.get("note") or "").strip()

        if not isinstance(category_id, int):
            return jsonify({"error": "category_id must be an integer"}), 400

        db = get_db()
        category = db.execute(
            "SELECT id FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not category:
            return jsonify({"error": "Unknown category"}), 400

        user_id = int(session["user_id"])
        if get_active_session(user_id):
            return jsonify({"error": "Finish your current session first"}), 400

        ts = now_ts()
        db.execute(
            """
            INSERT INTO sessions(user_id, category_id, note, start_ts, status, created_ts)
            VALUES(?, ?, ?, ?, 'running', ?)
            """,
            (user_id, category_id, note, ts, ts),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/pause")
    @login_required
    def api_pause_session():
        user_id = int(session["user_id"])
        active = get_active_session(user_id)
        if not active or active["status"] != "running":
            return jsonify({"error": "No running session to pause"}), 400

        ts = now_ts()
        db = get_db()
        db.execute(
            "UPDATE sessions SET status = 'paused', pause_started_ts = ? WHERE id = ?",
            (ts, active["id"]),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/resume")
    @login_required
    def api_resume_session():
        user_id = int(session["user_id"])
        active = get_active_session(user_id)
        if not active or active["status"] != "paused":
            return jsonify({"error": "No paused session to resume"}), 400

        ts = now_ts()
        extra_paused = ts - int(active["pause_started_ts"] or ts)
        db = get_db()
        db.execute(
            """
            UPDATE sessions
            SET status = 'running',
                paused_seconds = paused_seconds + ?,
                pause_started_ts = NULL
            WHERE id = ?
            """,
            (extra_paused, active["id"]),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/finish")
    @login_required
    def api_finish_session():
        user_id = int(session["user_id"])
        active = get_active_session(user_id)
        if not active:
            return jsonify({"error": "No active session to finish"}), 400

        ts = now_ts()
        paused_seconds = int(active["paused_seconds"])
        if active["status"] == "paused" and active["pause_started_ts"] is not None:
            paused_seconds += ts - int(active["pause_started_ts"])

        db = get_db()
        db.execute(
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
        db.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/cancel")
    @login_required
    def api_cancel_session():
        user_id = int(session["user_id"])
        active = get_active_session(user_id)
        if not active:
            return jsonify({"error": "No active session to cancel"}), 400

        db = get_db()
        db.execute("DELETE FROM sessions WHERE id = ?", (active["id"],))
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/leaderboard")
    @login_required
    def api_leaderboard():
        db = get_db()
        current_ts = now_ts()

        users = db.execute("SELECT id, username FROM users").fetchall()
        totals = {row["id"]: 0 for row in users}

        completed = db.execute(
            """
            SELECT user_id, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
            FROM sessions
            WHERE status = 'completed'
            GROUP BY user_id
            """
        ).fetchall()
        for row in completed:
            totals[row["user_id"]] = int(row["seconds"] or 0)

        active = db.execute(
            "SELECT * FROM sessions WHERE status IN ('running', 'paused')"
        ).fetchall()
        for row in active:
            totals[row["user_id"]] = totals.get(row["user_id"], 0) + elapsed_seconds(
                row, current_ts
            )

        rows = [
            {
                "user_id": user["id"],
                "username": user["username"],
                "seconds": totals.get(user["id"], 0),
            }
            for user in users
        ]
        rows.sort(key=lambda x: x["seconds"], reverse=True)

        for idx, row in enumerate(rows, start=1):
            row["rank"] = idx

        return jsonify({"leaderboard": rows, "server_ts": current_ts})

    @app.get("/api/stats")
    @login_required
    def api_stats():
        db = get_db()
        current_ts = now_ts()
        user_id = int(session["user_id"])

        categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        my_totals = {c["id"]: 0 for c in categories}
        team_totals = {c["id"]: 0 for c in categories}

        completed = db.execute(
            """
            SELECT user_id, category_id, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
            FROM sessions
            WHERE status = 'completed'
            GROUP BY user_id, category_id
            """
        ).fetchall()
        for row in completed:
            sec = int(row["seconds"] or 0)
            team_totals[row["category_id"]] += sec
            if row["user_id"] == user_id:
                my_totals[row["category_id"]] += sec

        active = db.execute(
            "SELECT * FROM sessions WHERE status IN ('running', 'paused')"
        ).fetchall()
        for row in active:
            sec = elapsed_seconds(row, current_ts)
            team_totals[row["category_id"]] += sec
            if row["user_id"] == user_id:
                my_totals[row["category_id"]] += sec

        my_rows = [
            {"name": c["name"], "seconds": my_totals[c["id"]]} for c in categories if my_totals[c["id"]] > 0
        ]
        team_rows = [
            {"name": c["name"], "seconds": team_totals[c["id"]]}
            for c in categories
            if team_totals[c["id"]] > 0
        ]

        return jsonify({"my_categories": my_rows, "team_categories": team_rows})

    @app.get("/api/recent-sessions")
    @login_required
    def api_recent_sessions():
        user_id = int(session["user_id"])
        db = get_db()
        rows = db.execute(
            """
            SELECT s.id, s.note, s.start_ts, s.end_ts, s.paused_seconds, c.name AS category_name
            FROM sessions s
            JOIN categories c ON c.id = s.category_id
            WHERE s.user_id = ? AND s.status = 'completed'
            ORDER BY s.id DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()

        sessions_payload = []
        for row in rows:
            duration = max(0, int(row["end_ts"] - row["start_ts"] - row["paused_seconds"]))
            sessions_payload.append(
                {
                    "id": row["id"],
                    "category_name": row["category_name"],
                    "note": row["note"] or "",
                    "start_ts": row["start_ts"],
                    "end_ts": row["end_ts"],
                    "duration_seconds": duration,
                }
            )
        return jsonify({"sessions": sessions_payload})

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)

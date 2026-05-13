import os
import random
import re
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
WEEK_SECONDS = 7 * 24 * 60 * 60


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

    def generate_login_code() -> str:
        return f"{random.SystemRandom().randrange(0, 1_000_000):06d}"

    def parse_username(raw_value: str | None) -> tuple[str | None, str | None]:
        username = " ".join((raw_value or "").split())
        if not username:
            return None, "Username is required."
        if len(username) > 50:
            return None, "Username must be 50 characters or fewer."
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9 -]*[A-Za-z0-9])?", username):
            return (
                None,
                "Username can only include letters, numbers, spaces, and hyphens.",
            )
        return username, None

    def elapsed_seconds(row: sqlite3.Row, current_ts: int) -> int:
        end_ts = row["end_ts"] if row["end_ts"] is not None else current_ts
        elapsed = end_ts - row["start_ts"] - row["paused_seconds"]
        if row["status"] == "paused" and row["pause_started_ts"] is not None:
            elapsed -= current_ts - row["pause_started_ts"]
        return max(0, int(elapsed))

    def elapsed_seconds_in_window(
        row: sqlite3.Row, current_ts: int, since_ts: int | None
    ) -> int:
        if since_ts is None:
            return elapsed_seconds(row, current_ts)

        start_ts = int(row["start_ts"])
        end_ts = int(row["end_ts"]) if row["end_ts"] is not None else current_ts
        if end_ts <= since_ts:
            return 0

        total_elapsed = elapsed_seconds(row, current_ts)
        total_span = max(1, end_ts - start_ts)
        overlap_span = end_ts - max(start_ts, since_ts)
        if overlap_span <= 0:
            return 0

        ratio = min(1.0, max(0.0, overlap_span / total_span))
        return int(total_elapsed * ratio)

    def init_db() -> None:
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                code_hash TEXT NOT NULL,
                login_code TEXT,
                notify_on_collab_starts INTEGER NOT NULL DEFAULT 1,
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

        user_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(users)").fetchall()
        }
        if "login_code" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN login_code TEXT")
        if "notify_on_collab_starts" not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN notify_on_collab_starts INTEGER NOT NULL DEFAULT 1"
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

    def get_user_by_id(user_id: int):
        db = get_db()
        return db.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()

    def collaborator_presence_rows(current_ts: int, exclude_user_id: int):
        db = get_db()
        rows = db.execute(
            """
            SELECT
                s.id,
                s.user_id,
                s.note,
                s.start_ts,
                s.end_ts,
                s.status,
                s.paused_seconds,
                s.pause_started_ts,
                u.username,
                c.name AS category_name
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN categories c ON c.id = s.category_id
            WHERE s.status IN ('running', 'paused')
              AND s.user_id != ?
            ORDER BY s.start_ts DESC
            """,
            (exclude_user_id,),
        ).fetchall()
        return [
            {
                "session_id": row["id"],
                "user_id": row["user_id"],
                "username": row["username"],
                "category_name": row["category_name"],
                "note": row["note"] or "",
                "status": row["status"],
                "start_ts": row["start_ts"],
                "elapsed_seconds": elapsed_seconds(row, current_ts),
            }
            for row in rows
        ]

    def started_session_events(since_ts: int, exclude_user_id: int):
        db = get_db()
        rows = db.execute(
            """
            SELECT
                s.id AS session_id,
                s.user_id,
                s.created_ts,
                s.note,
                u.username,
                c.name AS category_name
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN categories c ON c.id = s.category_id
            WHERE s.user_id != ?
              AND s.created_ts >= ?
            ORDER BY s.created_ts ASC, s.id ASC
            """,
            (exclude_user_id, since_ts),
        ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "username": row["username"],
                "category_name": row["category_name"],
                "note": row["note"] or "",
                "start_ts": row["created_ts"],
            }
            for row in rows
        ]

    def user_activity_grid(user_id: int, current_ts: int, days: int = 140):
        db = get_db()
        if days < 1:
            return []
        days = min(days, 366)
        today_iso = datetime.fromtimestamp(current_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        rows = db.execute(
            """
            WITH RECURSIVE dates(day) AS (
                SELECT date(?, ?)
                UNION ALL
                SELECT date(day, '+1 day') FROM dates WHERE day < date(?)
            ),
            totals AS (
                SELECT
                    date(end_ts, 'unixepoch') AS day,
                    SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
                FROM sessions
                WHERE user_id = ?
                  AND status = 'completed'
                  AND end_ts IS NOT NULL
                  AND date(end_ts, 'unixepoch') >= date(?, ?)
                GROUP BY date(end_ts, 'unixepoch')
            )
            SELECT dates.day, COALESCE(totals.seconds, 0) AS seconds
            FROM dates
            LEFT JOIN totals ON totals.day = dates.day
            ORDER BY dates.day ASC
            """,
            (today_iso, f"-{days - 1} days", today_iso, user_id, today_iso, f"-{days - 1} days"),
        ).fetchall()
        return [{"date": row["day"], "seconds": int(row["seconds"] or 0)} for row in rows]

    def category_rows_for_user(
        user_id: int | None, current_ts: int, since_ts: int | None = None
    ):
        db = get_db()
        categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        totals = {c["id"]: 0 for c in categories}

        if since_ts is None:
            if user_id is None:
                completed = db.execute(
                    """
                    SELECT category_id, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
                    FROM sessions
                    WHERE status = 'completed'
                    GROUP BY category_id
                    """
                ).fetchall()
                active = db.execute(
                    "SELECT * FROM sessions WHERE status IN ('running', 'paused')"
                ).fetchall()
            else:
                completed = db.execute(
                    """
                    SELECT category_id, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
                    FROM sessions
                    WHERE status = 'completed' AND user_id = ?
                    GROUP BY category_id
                    """,
                    (user_id,),
                ).fetchall()
                active = db.execute(
                    """
                    SELECT * FROM sessions
                    WHERE user_id = ? AND status IN ('running', 'paused')
                    """,
                    (user_id,),
                ).fetchall()

            for row in completed:
                totals[row["category_id"]] += int(row["seconds"] or 0)
            for row in active:
                totals[row["category_id"]] += elapsed_seconds(row, current_ts)
        else:
            if user_id is None:
                rows = db.execute(
                    """
                    SELECT category_id, start_ts, end_ts, paused_seconds, status, pause_started_ts
                    FROM sessions
                    WHERE status IN ('running', 'paused')
                       OR (status = 'completed' AND end_ts > ?)
                    """,
                    (since_ts,),
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT category_id, start_ts, end_ts, paused_seconds, status, pause_started_ts
                    FROM sessions
                    WHERE user_id = ?
                      AND (
                        status IN ('running', 'paused')
                        OR (status = 'completed' AND end_ts > ?)
                      )
                    """,
                    (user_id, since_ts),
                ).fetchall()

            for row in rows:
                totals[row["category_id"]] += elapsed_seconds_in_window(
                    row, current_ts, since_ts
                )

        return [
            {"name": c["name"], "seconds": totals[c["id"]]}
            for c in categories
            if totals[c["id"]] > 0
        ]

    def leaderboard_rows(current_ts: int, since_ts: int | None = None):
        db = get_db()
        users = db.execute("SELECT id, username FROM users").fetchall()
        totals = {row["id"]: 0 for row in users}

        if since_ts is None:
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
                """
                SELECT user_id, start_ts, end_ts, paused_seconds, status, pause_started_ts
                FROM sessions
                WHERE status IN ('running', 'paused')
                """
            ).fetchall()
            for row in active:
                totals[row["user_id"]] = totals.get(row["user_id"], 0) + elapsed_seconds(
                    row, current_ts
                )
        else:
            rows = db.execute(
                """
                SELECT user_id, start_ts, end_ts, paused_seconds, status, pause_started_ts
                FROM sessions
                WHERE status IN ('running', 'paused')
                   OR (status = 'completed' AND end_ts > ?)
                """,
                (since_ts,),
            ).fetchall()
            for row in rows:
                totals[row["user_id"]] = totals.get(row["user_id"], 0) + (
                    elapsed_seconds_in_window(row, current_ts, since_ts)
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
        return rows

    def recent_sessions_for_user(user_id: int, limit: int = 10):
        db = get_db()
        rows = db.execute(
            """
            SELECT
                s.id,
                s.note,
                s.start_ts,
                s.end_ts,
                s.paused_seconds,
                c.id AS category_id,
                c.name AS category_name
            FROM sessions s
            JOIN categories c ON c.id = s.category_id
            WHERE s.user_id = ? AND s.status = 'completed'
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

        sessions_payload = []
        for row in rows:
            duration = max(0, int(row["end_ts"] - row["start_ts"] - row["paused_seconds"]))
            sessions_payload.append(
                {
                    "id": row["id"],
                    "category_id": row["category_id"],
                    "category_name": row["category_name"],
                    "note": row["note"] or "",
                    "start_ts": row["start_ts"],
                    "end_ts": row["end_ts"],
                    "duration_seconds": duration,
                }
            )
        return sessions_payload

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

        username, username_error = parse_username(request.form.get("username"))
        if username_error:
            flash(username_error, "error")
            return redirect(url_for("register"))

        six_digit_code = generate_login_code()
        db = get_db()
        try:
            db.execute(
                """
                INSERT INTO users(username, code_hash, login_code, created_ts)
                VALUES(?, ?, ?, ?)
                """,
                (
                    username,
                    generate_password_hash(six_digit_code),
                    six_digit_code,
                    now_ts(),
                ),
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

        username, username_error = parse_username(request.form.get("username"))
        code = request.form.get("code", "").strip()
        if username_error:
            flash("Invalid username or 6-digit code.", "error")
            return redirect(url_for("login"))
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

    @app.get("/weekly-leaderboard")
    @login_required
    def weekly_leaderboard():
        user = get_current_user()
        return render_template("weekly_leaderboard.html", user=user)

    @app.get("/all-time-stats")
    @login_required
    def all_time_stats():
        user = get_current_user()
        return render_template("all_time_stats.html", user=user)

    @app.get("/users/<int:user_id>")
    @login_required
    def user_profile(user_id: int):
        target_user = get_user_by_id(user_id)
        if not target_user:
            flash("User not found.", "error")
            return redirect(url_for("dashboard"))
        current_user = get_current_user()
        db = get_db()
        categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        return render_template(
            "user.html",
            user=current_user,
            target_user=target_user,
            can_delete_sessions=current_user["id"] == target_user["id"],
            categories=categories,
        )

    @app.get("/api/status")
    @login_required
    def api_status():
        user_id = int(session["user_id"])
        active = get_active_session(user_id)
        current_ts = now_ts()
        collab_since_raw = request.args.get("collab_since")
        collab_since_ts = current_ts
        if collab_since_raw is not None:
            try:
                collab_since_ts = int(collab_since_raw)
            except ValueError:
                return jsonify({"error": "collab_since must be an integer"}), 400
        collab_since_ts = max(0, min(collab_since_ts, current_ts))

        db = get_db()
        user_settings = db.execute(
            "SELECT notify_on_collab_starts FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        notify_on_collab_starts = bool(user_settings["notify_on_collab_starts"]) if user_settings else True
        team_presence = collaborator_presence_rows(current_ts, exclude_user_id=user_id)
        new_starts = started_session_events(collab_since_ts, exclude_user_id=user_id)
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

    @app.post("/api/session/delete")
    @login_required
    def api_delete_session():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id")
        if not isinstance(session_id, int):
            return jsonify({"error": "session_id must be an integer"}), 400

        user_id = int(session["user_id"])
        db = get_db()
        existing = db.execute(
            """
            SELECT id FROM sessions
            WHERE id = ? AND user_id = ? AND status = 'completed'
            """,
            (session_id, user_id),
        ).fetchone()
        if not existing:
            return jsonify({"error": "Completed session not found"}), 404

        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        db.commit()
        return jsonify({"ok": True})

    @app.post("/api/session/update")
    @login_required
    def api_update_session():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id")
        category_id = payload.get("category_id")
        note = (payload.get("note") or "").strip()

        if not isinstance(session_id, int):
            return jsonify({"error": "session_id must be an integer"}), 400
        if not isinstance(category_id, int):
            return jsonify({"error": "category_id must be an integer"}), 400
        if len(note) > 200:
            return jsonify({"error": "note must be 200 characters or fewer"}), 400

        user_id = int(session["user_id"])
        db = get_db()
        existing = db.execute(
            """
            SELECT id FROM sessions
            WHERE id = ? AND user_id = ? AND status = 'completed'
            """,
            (session_id, user_id),
        ).fetchone()
        if not existing:
            return jsonify({"error": "Completed session not found"}), 404

        category = db.execute(
            "SELECT id FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()
        if not category:
            return jsonify({"error": "Unknown category"}), 400

        db.execute(
            """
            UPDATE sessions
            SET category_id = ?, note = ?
            WHERE id = ?
            """,
            (category_id, note, session_id),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/user/settings")
    @login_required
    def api_user_settings():
        user_id = int(session["user_id"])
        db = get_db()
        user = db.execute(
            "SELECT id, username, login_code, notify_on_collab_starts FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404
        return jsonify(
            {
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "login_code": user["login_code"],
                    "notify_on_collab_starts": bool(user["notify_on_collab_starts"]),
                }
            }
        )

    @app.post("/api/user/settings")
    @login_required
    def api_user_settings_update():
        payload = request.get_json(silent=True) or {}
        username, username_error = parse_username(payload.get("username"))
        if username_error:
            return jsonify({"error": username_error}), 400
        notify_on_collab_starts = payload.get("notify_on_collab_starts")
        if not isinstance(notify_on_collab_starts, bool):
            return jsonify({"error": "notify_on_collab_starts must be a boolean"}), 400

        user_id = int(session["user_id"])
        db = get_db()
        try:
            db.execute(
                """
                UPDATE users
                SET username = ?, notify_on_collab_starts = ?
                WHERE id = ?
                """,
                (username, int(notify_on_collab_starts), user_id),
            )
            db.commit()
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
    @login_required
    def api_user_settings_reset_login_code():
        user_id = int(session["user_id"])
        new_login_code = generate_login_code()
        db = get_db()
        db.execute(
            """
            UPDATE users
            SET code_hash = ?, login_code = ?
            WHERE id = ?
            """,
            (generate_password_hash(new_login_code), new_login_code, user_id),
        )
        db.commit()
        return jsonify({"ok": True, "login_code": new_login_code})

    @app.get("/api/leaderboard")
    @login_required
    def api_leaderboard():
        current_ts = now_ts()
        return jsonify({"leaderboard": leaderboard_rows(current_ts), "server_ts": current_ts})

    @app.get("/api/leaderboard/weekly")
    @login_required
    def api_weekly_leaderboard():
        current_ts = now_ts()
        since_ts = current_ts - WEEK_SECONDS
        limit_raw = request.args.get("limit")
        limit = None
        if limit_raw is not None:
            try:
                parsed_limit = int(limit_raw)
            except ValueError:
                return jsonify({"error": "limit must be an integer"}), 400
            if parsed_limit < 1:
                return jsonify({"error": "limit must be at least 1"}), 400
            limit = parsed_limit

        rows = leaderboard_rows(current_ts, since_ts=since_ts)
        if limit is not None:
            rows = rows[:limit]
        return jsonify({"leaderboard": rows, "server_ts": current_ts, "since_ts": since_ts})

    @app.get("/api/stats")
    @login_required
    def api_stats():
        current_ts = now_ts()
        user_id = int(session["user_id"])
        my_rows = category_rows_for_user(user_id, current_ts)
        team_rows = category_rows_for_user(None, current_ts)
        since_ts = current_ts - WEEK_SECONDS
        my_week_rows = category_rows_for_user(user_id, current_ts, since_ts=since_ts)
        team_week_rows = category_rows_for_user(None, current_ts, since_ts=since_ts)

        return jsonify(
            {
                "my_categories": my_rows,
                "team_categories": team_rows,
                "my_categories_week": my_week_rows,
                "team_categories_week": team_week_rows,
                "since_ts": since_ts,
            }
        )

    @app.get("/api/recent-sessions")
    @login_required
    def api_recent_sessions():
        user_id = int(session["user_id"])
        return jsonify({"sessions": recent_sessions_for_user(user_id)})

    @app.get("/api/users/<int:user_id>/stats")
    @login_required
    def api_user_stats(user_id: int):
        target_user = get_user_by_id(user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404
        rows = category_rows_for_user(user_id, now_ts())
        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "categories": rows,
            }
        )

    @app.get("/api/users/<int:user_id>/recent-sessions")
    @login_required
    def api_user_recent_sessions(user_id: int):
        target_user = get_user_by_id(user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404
        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "sessions": recent_sessions_for_user(user_id),
            }
        )

    @app.get("/api/users/<int:user_id>/activity-grid")
    @login_required
    def api_user_activity_grid(user_id: int):
        target_user = get_user_by_id(user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        days_raw = request.args.get("days")
        days = 140
        if days_raw is not None:
            try:
                days = int(days_raw)
            except ValueError:
                return jsonify({"error": "days must be an integer"}), 400
            if days < 1 or days > 366:
                return jsonify({"error": "days must be between 1 and 366"}), 400

        grid_rows = user_activity_grid(user_id, now_ts(), days=days)
        max_seconds = max((row["seconds"] for row in grid_rows), default=0)
        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "days": grid_rows,
                "max_seconds": max_seconds,
            }
        )

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)

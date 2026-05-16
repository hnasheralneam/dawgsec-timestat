import os
import random
import re
import secrets
import sqlite3
import shutil
from hmac import compare_digest
from datetime import datetime, timedelta, timezone
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
NOTE_MAX_LENGTH = 200
CATEGORY_MAX_LENGTH = 80
BACKUP_RETENTION_DAYS = 14
AUTH_WINDOW_SECONDS = 5 * 60
LOGIN_MAX_ATTEMPTS = 8
ADMIN_LOGIN_MAX_ATTEMPTS = 5


def load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in ("'", '"')
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def migrate_sessions_table_to_category_name(db: sqlite3.Connection) -> bool:
    table_exists = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
    ).fetchone()
    if not table_exists:
        return False

    session_columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
    needs_migration = "category_name" not in session_columns or "category_id" in session_columns
    if not needs_migration:
        return False

    foreign_keys_enabled = int(db.execute("PRAGMA foreign_keys").fetchone()[0])
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("BEGIN")
        db.execute(
            """
            CREATE TABLE sessions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category_name TEXT NOT NULL,
                note TEXT,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER,
                paused_seconds INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN ('running', 'paused', 'completed')),
                pause_started_ts INTEGER,
                created_ts INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )

        if "category_id" in session_columns and "category_name" in session_columns:
            category_expr = "COALESCE(NULLIF(TRIM(s.category_name), ''), c.name, 'Other')"
            join_clause = "LEFT JOIN categories c ON c.id = s.category_id"
        elif "category_id" in session_columns:
            category_expr = "COALESCE(c.name, 'Other')"
            join_clause = "LEFT JOIN categories c ON c.id = s.category_id"
        else:
            category_expr = "COALESCE(NULLIF(TRIM(s.category_name), ''), 'Other')"
            join_clause = ""

        db.execute(
            f"""
            INSERT INTO sessions_new(
                id,
                user_id,
                category_name,
                note,
                start_ts,
                end_ts,
                paused_seconds,
                status,
                pause_started_ts,
                created_ts
            )
            SELECT
                s.id,
                s.user_id,
                {category_expr},
                s.note,
                s.start_ts,
                s.end_ts,
                s.paused_seconds,
                s.status,
                s.pause_started_ts,
                s.created_ts
            FROM sessions s
            {join_clause}
            """
        )

        db.execute("DROP TABLE sessions")
        db.execute("ALTER TABLE sessions_new RENAME TO sessions")
        db.execute(
            """
            UPDATE sqlite_sequence
            SET seq = COALESCE((SELECT MAX(id) FROM sessions), 0)
            WHERE name = 'sessions'
            """
        )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        db.execute(f"PRAGMA foreign_keys = {foreign_keys_enabled}")

    return True


def run_daily_database_backup(db_path: str, base_dir: str) -> None:
    if not os.path.exists(db_path):
        return

    backup_dir = os.path.join(base_dir, "backups")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    now_utc = datetime.now(timezone.utc)
    today_prefix = now_utc.strftime("%Y%m%d")
    has_today_backup = False
    for entry in os.scandir(backup_dir):
        if not entry.is_file(follow_symlinks=False):
            continue
        if entry.name.startswith(f"timestat-{today_prefix}-") and entry.name.endswith(".db"):
            has_today_backup = True
            break

    if not has_today_backup:
        backup_filename = f"timestat-{now_utc.strftime('%Y%m%d-%H%M%S')}.db"
        backup_path = os.path.join(backup_dir, backup_filename)
        shutil.copy2(db_path, backup_path)
        os.chmod(backup_path, 0o600)

    cutoff = now_utc - timedelta(days=BACKUP_RETENTION_DAYS)
    cutoff_ts = cutoff.timestamp()
    for entry in os.scandir(backup_dir):
        if not entry.is_file(follow_symlinks=False):
            continue
        if not (entry.name.startswith("timestat-") and entry.name.endswith(".db")):
            continue
        if entry.stat(follow_symlinks=False).st_mtime < cutoff_ts:
            os.remove(entry.path)


def create_app() -> Flask:
    load_env_file(os.path.join(BASE_DIR, ".env"))
    load_env_file("/etc/timestat/timestat.env")

    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
    app.config["DATABASE"] = DB_PATH
    app.config["ADMIN_USERNAME"] = (os.environ.get("ADMIN_USERNAME") or "").strip()
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD") or ""
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    )

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    def now_ts() -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def client_addr() -> str:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()[:120]
        return (request.remote_addr or "unknown")[:120]

    def auth_limit_key(scope: str, username: str) -> str:
        normalized_username = (username or "").strip().lower()
        return f"{scope}:{client_addr()}:{normalized_username}"

    def auth_is_limited(scope: str, key: str, max_attempts: int) -> bool:
        db = get_db()
        row = db.execute(
            "SELECT first_ts, last_ts, failures FROM auth_attempts WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()
        if not row:
            return False
        now = now_ts()
        if now - int(row["first_ts"]) > AUTH_WINDOW_SECONDS:
            db.execute("DELETE FROM auth_attempts WHERE scope = ? AND key = ?", (scope, key))
            db.commit()
            return False
        return int(row["failures"]) >= max_attempts

    def auth_record_failure(scope: str, key: str) -> None:
        db = get_db()
        now = now_ts()
        row = db.execute(
            "SELECT first_ts, failures FROM auth_attempts WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()
        if not row or now - int(row["first_ts"]) > AUTH_WINDOW_SECONDS:
            db.execute(
                """
                INSERT INTO auth_attempts(scope, key, first_ts, last_ts, failures)
                VALUES(?, ?, ?, ?, 1)
                ON CONFLICT(scope, key) DO UPDATE SET
                    first_ts = excluded.first_ts,
                    last_ts = excluded.last_ts,
                    failures = excluded.failures
                """,
                (scope, key, now, now),
            )
        else:
            db.execute(
                """
                UPDATE auth_attempts
                SET failures = failures + 1,
                    last_ts = ?
                WHERE scope = ? AND key = ?
                """,
                (now, scope, key),
            )
        db.commit()

    def auth_clear_failures(scope: str, key: str) -> None:
        db = get_db()
        db.execute("DELETE FROM auth_attempts WHERE scope = ? AND key = ?", (scope, key))
        db.commit()

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

    def parse_note(raw_value: object) -> tuple[str | None, str | None]:
        if raw_value is None:
            note = ""
        elif isinstance(raw_value, str):
            note = raw_value.strip()
        else:
            return None, "note must be a string"

        if len(note) > NOTE_MAX_LENGTH:
            return None, f"note must be {NOTE_MAX_LENGTH} characters or fewer"
        return note, None

    def parse_category_name(raw_value: object) -> tuple[str | None, str | None]:
        if not isinstance(raw_value, str):
            return None, "category_name must be a string"
        category_name = " ".join(raw_value.split())
        if not category_name:
            return None, "category_name is required"
        if len(category_name) > CATEGORY_MAX_LENGTH:
            return None, f"category_name must be {CATEGORY_MAX_LENGTH} characters or fewer"
        return category_name, None

    def parse_bool_query_arg(
        raw_value: str | None, *, field_name: str
    ) -> tuple[bool | None, str | None]:
        if raw_value is None:
            return None, None
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True, None
        if normalized in {"0", "false", "no", "off"}:
            return False, None
        return None, f"{field_name} must be a boolean"

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
                category_name TEXT NOT NULL,
                note TEXT,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER,
                paused_seconds INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN ('running', 'paused', 'completed')),
                pause_started_ts INTEGER,
                created_ts INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS auth_attempts (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                first_ts INTEGER NOT NULL,
                last_ts INTEGER NOT NULL,
                failures INTEGER NOT NULL,
                PRIMARY KEY(scope, key)
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

        migrate_sessions_table_to_category_name(db)

        category_count = int(
            db.execute("SELECT COUNT(*) AS count FROM categories").fetchone()["count"]
        )
        if category_count == 0:
            for cat in DEFAULT_CATEGORIES:
                db.execute("INSERT INTO categories(name) VALUES(?)", (cat,))
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

    def admin_credentials_configured() -> bool:
        return bool(app.config["ADMIN_USERNAME"] and app.config["ADMIN_PASSWORD"])

    def admin_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("is_admin"):
                return redirect(url_for("admin_login"))
            return fn(*args, **kwargs)

        return wrapped

    def valid_admin_credentials(username: str, password: str) -> bool:
        configured_username = app.config["ADMIN_USERNAME"]
        configured_password = app.config["ADMIN_PASSWORD"]
        return compare_digest(username, configured_username) and compare_digest(
            password, configured_password
        )

    def csrf_token() -> str:
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    def rotate_csrf_token() -> None:
        session["_csrf_token"] = secrets.token_urlsafe(32)

    def validate_csrf_request() -> bool:
        expected = session.get("_csrf_token")
        if not expected:
            return False
        provided = request.headers.get("X-CSRF-Token")
        if not provided:
            provided = request.form.get("csrf_token")
        if not provided:
            return False
        return compare_digest(str(expected), str(provided))

    @app.context_processor
    def inject_template_context():
        return {"csrf_token": csrf_token()}

    @app.before_request
    def enforce_csrf():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if "user_id" not in session and not session.get("is_admin"):
            return None
        if validate_csrf_request():
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
            SELECT *
            FROM sessions
            WHERE user_id = ? AND status IN ('running', 'paused')
            ORDER BY id DESC
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
                s.category_name
            FROM sessions s
            JOIN users u ON u.id = s.user_id
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
                s.category_name
            FROM sessions s
            JOIN users u ON u.id = s.user_id
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
        categories = [
            row["name"]
            for row in db.execute("SELECT name FROM categories ORDER BY name").fetchall()
        ]
        totals = {name: 0 for name in categories}

        if since_ts is None:
            if user_id is None:
                completed = db.execute(
                    """
                    SELECT category_name, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
                    FROM sessions
                    WHERE status = 'completed'
                    GROUP BY category_name
                    """
                ).fetchall()
                active = db.execute(
                    "SELECT * FROM sessions WHERE status IN ('running', 'paused')"
                ).fetchall()
            else:
                completed = db.execute(
                    """
                    SELECT category_name, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
                    FROM sessions
                    WHERE status = 'completed' AND user_id = ?
                    GROUP BY category_name
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
                category_name = row["category_name"] or "Other"
                totals[category_name] = totals.get(category_name, 0) + int(row["seconds"] or 0)
            for row in active:
                category_name = row["category_name"] or "Other"
                totals[category_name] = totals.get(category_name, 0) + elapsed_seconds(
                    row, current_ts
                )
        else:
            if user_id is None:
                rows = db.execute(
                    """
                    SELECT category_name, start_ts, end_ts, paused_seconds, status, pause_started_ts
                    FROM sessions
                    WHERE status IN ('running', 'paused')
                       OR (status = 'completed' AND end_ts > ?)
                    """,
                    (since_ts,),
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT category_name, start_ts, end_ts, paused_seconds, status, pause_started_ts
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
                category_name = row["category_name"] or "Other"
                totals[category_name] = totals.get(category_name, 0) + elapsed_seconds_in_window(
                    row, current_ts, since_ts
                )

        known_names = set(categories)
        ordered_names = categories + sorted(
            [name for name in totals if name not in known_names]
        )
        return [{"name": name, "seconds": totals[name]} for name in ordered_names if totals[name] > 0]

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

    def recent_sessions_for_user(user_id: int, limit: int | None = 10):
        db = get_db()
        query = """
            SELECT
                s.id,
                s.category_name,
                s.note,
                s.start_ts,
                s.end_ts,
                s.paused_seconds
            FROM sessions s
            WHERE s.user_id = ? AND s.status = 'completed'
            ORDER BY s.id DESC
        """
        params: tuple[int] | tuple[int, int]
        if limit is None:
            params = (user_id,)
        else:
            query = f"{query}\nLIMIT ?"
            params = (user_id, limit)
        rows = db.execute(query, params).fetchall()

        sessions_payload = []
        for row in rows:
            duration = max(0, int(row["end_ts"] - row["start_ts"] - row["paused_seconds"]))
            sessions_payload.append(
                {
                    "id": row["id"],
                    "category_name": row["category_name"] or "Other",
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
            cursor = db.execute(
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

        session.clear()
        session["user_id"] = int(cursor.lastrowid)
        rotate_csrf_token()
        return render_template(
            "register_success.html", username=username, six_digit_code=six_digit_code
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")

        username, username_error = parse_username(request.form.get("username"))
        code = request.form.get("code", "").strip()
        rate_key = auth_limit_key("user-login", request.form.get("username") or "")
        if auth_is_limited("user-login", rate_key, LOGIN_MAX_ATTEMPTS):
            flash("Too many login attempts. Please wait a few minutes and try again.", "error")
            return redirect(url_for("login"))
        if username_error:
            auth_record_failure("user-login", rate_key)
            flash("Invalid username or 6-digit code.", "error")
            return redirect(url_for("login"))
        db = get_db()
        user = db.execute(
            "SELECT id, username, code_hash FROM users WHERE username = ?", (username,)
        ).fetchone()

        if not user or not check_password_hash(user["code_hash"], code):
            auth_record_failure("user-login", rate_key)
            flash("Invalid username or 6-digit code.", "error")
            return redirect(url_for("login"))

        auth_clear_failures("user-login", rate_key)
        session.clear()
        session["user_id"] = user["id"]
        rotate_csrf_token()
        return redirect(url_for("dashboard"))

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        if request.method == "GET":
            return render_template("admin_login.html")

        rate_key = auth_limit_key("admin-login", request.form.get("username") or "")
        if auth_is_limited("admin-login", rate_key, ADMIN_LOGIN_MAX_ATTEMPTS):
            flash(
                "Too many admin login attempts. Please wait a few minutes and try again.",
                "error",
            )
            return redirect(url_for("admin_login"))

        if not admin_credentials_configured():
            flash(
                "Admin login is disabled. Set ADMIN_USERNAME and ADMIN_PASSWORD in config.",
                "error",
            )
            return redirect(url_for("admin_login"))

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not valid_admin_credentials(username, password):
            auth_record_failure("admin-login", rate_key)
            flash("Invalid admin username or password.", "error")
            return redirect(url_for("admin_login"))

        auth_clear_failures("admin-login", rate_key)
        session.clear()
        session["is_admin"] = True
        rotate_csrf_token()
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/logout")
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        db = get_db()
        rows = db.execute(
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
        categories = db.execute(
            "SELECT id, name FROM categories ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return render_template("admin_dashboard.html", users=users, categories=categories)

    @app.post("/admin/categories")
    @admin_required
    def admin_add_category():
        category_name, category_error = parse_category_name(request.form.get("name"))
        if category_error:
            flash(category_error, "error")
            return redirect(url_for("admin_dashboard"))

        db = get_db()
        exists = db.execute(
            "SELECT id FROM categories WHERE lower(name) = lower(?)",
            (category_name,),
        ).fetchone()
        if exists:
            flash("That category already exists.", "error")
            return redirect(url_for("admin_dashboard"))
        try:
            db.execute("INSERT INTO categories(name) VALUES(?)", (category_name,))
            db.commit()
        except sqlite3.IntegrityError:
            flash("That category already exists.", "error")
            return redirect(url_for("admin_dashboard"))

        flash(f"Added category '{category_name}'.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/categories/<int:category_id>/delete")
    @admin_required
    def admin_delete_category(category_id: int):
        db = get_db()
        category = db.execute(
            "SELECT id, name FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()
        if not category:
            flash("Category not found.", "error")
            return redirect(url_for("admin_dashboard"))

        remaining_count = int(
            db.execute("SELECT COUNT(*) AS count FROM categories").fetchone()["count"]
        )
        if remaining_count <= 1:
            flash("At least one category must remain available.", "error")
            return redirect(url_for("admin_dashboard"))

        db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        db.commit()
        flash(f"Removed category '{category['name']}' from available task options.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.get("/admin/api/users/<int:user_id>/tasks")
    @admin_required
    def admin_user_tasks(user_id: int):
        db = get_db()
        target_user = db.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        current_ts = now_ts()
        rows = db.execute(
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
                    "duration_seconds": elapsed_seconds(row, current_ts),
                }
            )

        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "tasks": tasks,
            }
        )

    @app.post("/admin/api/users/<int:user_id>/tasks/delete")
    @admin_required
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

        db = get_db()
        target_user = db.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        placeholders = ",".join("?" for _ in normalized_ids)
        existing = db.execute(
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
        db.execute(
            f"DELETE FROM sessions WHERE user_id = ? AND id IN ({existing_placeholders})",
            (user_id, *existing_ids),
        )
        db.commit()
        return jsonify({"ok": True, "deleted_count": len(existing_ids)})

    @app.post("/admin/users/<int:user_id>/delete")
    @admin_required
    def admin_delete_user(user_id: int):
        db = get_db()
        target_user = db.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not target_user:
            flash("User not found.", "error")
            return redirect(url_for("admin_dashboard"))

        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        if session.get("user_id") == user_id:
            session.pop("user_id", None)
        flash(f"Removed user '{target_user['username']}' and all associated tasks.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        db = get_db()
        categories = db.execute(
            "SELECT id, name FROM categories ORDER BY name COLLATE NOCASE"
        ).fetchall()
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
        categories = db.execute(
            "SELECT id, name FROM categories ORDER BY name COLLATE NOCASE"
        ).fetchall()
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
        category_name, category_error = parse_category_name(payload.get("category_name"))
        note, note_error = parse_note(payload.get("note"))

        if category_error:
            return jsonify({"error": category_error}), 400
        if note_error:
            return jsonify({"error": note_error}), 400

        db = get_db()
        category = db.execute(
            "SELECT name FROM categories WHERE lower(name) = lower(?)",
            (category_name,),
        ).fetchone()
        if not category:
            return jsonify({"error": "Unknown category"}), 400

        user_id = int(session["user_id"])
        if get_active_session(user_id):
            return jsonify({"error": "Finish your current session first"}), 400

        ts = now_ts()
        db.execute(
            """
            INSERT INTO sessions(user_id, category_name, note, start_ts, status, created_ts)
            VALUES(?, ?, ?, ?, 'running', ?)
            """,
            (user_id, category["name"], note, ts, ts),
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
        category_name, category_error = parse_category_name(payload.get("category_name"))
        note, note_error = parse_note(payload.get("note"))

        if not isinstance(session_id, int):
            return jsonify({"error": "session_id must be an integer"}), 400
        if category_error:
            return jsonify({"error": category_error}), 400
        if note_error:
            return jsonify({"error": note_error}), 400

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
            "SELECT name FROM categories WHERE lower(name) = lower(?)",
            (category_name,),
        ).fetchone()
        if not category:
            return jsonify({"error": "Unknown category"}), 400

        db.execute(
            """
            UPDATE sessions
            SET category_name = ?, note = ?
            WHERE id = ?
            """,
            (category["name"], note, session_id),
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
        include_full, full_error = parse_bool_query_arg(
            request.args.get("full"), field_name="full"
        )
        if full_error:
            return jsonify({"error": full_error}), 400
        limit = None if include_full else 10
        return jsonify({"sessions": recent_sessions_for_user(user_id, limit=limit)})

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
        include_full, full_error = parse_bool_query_arg(
            request.args.get("full"), field_name="full"
        )
        if full_error:
            return jsonify({"error": full_error}), 400
        limit = None if include_full else 10
        return jsonify(
            {
                "user": {"id": target_user["id"], "username": target_user["username"]},
                "sessions": recent_sessions_for_user(user_id, limit=limit),
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
        run_daily_database_backup(app.config["DATABASE"], BASE_DIR)
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "").strip() == "1")

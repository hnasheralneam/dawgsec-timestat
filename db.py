import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app, g

import config

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX dev environments
    fcntl = None


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # WAL allows concurrent readers alongside a writer (important with
        # multiple Gunicorn worker processes sharing one SQLite file), and
        # busy_timeout makes writers block-and-retry for up to 5s instead of
        # immediately raising "database is locked" under contention.
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(_exception=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()





def run_daily_database_backup(db_path: str, base_dir: str) -> None:
    if not os.path.exists(db_path):
        return

    backup_dir = os.path.join(base_dir, "backups")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    now_local = datetime.now().astimezone()
    today_prefix = now_local.strftime("%Y%m%d")
    has_today_backup = False
    with os.scandir(backup_dir) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.name.startswith(f"timestat-{today_prefix}-") and entry.name.endswith(".db"):
                has_today_backup = True
                break

    if not has_today_backup:
        backup_filename = f"timestat-{now_local.strftime('%Y%m%d-%H%M%S')}.db"
        backup_path = os.path.join(backup_dir, backup_filename)
        tmp_backup_path = backup_path + ".tmp"
        
        src_conn = sqlite3.connect(db_path)
        dst_conn = sqlite3.connect(tmp_backup_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()
            
        os.chmod(tmp_backup_path, 0o600)
        os.replace(tmp_backup_path, backup_path)

    cutoff = now_local - timedelta(days=config.BACKUP_RETENTION_DAYS)
    cutoff_ts = cutoff.timestamp()
    with os.scandir(backup_dir) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            if not (entry.name.startswith("timestat-") and entry.name.endswith(".db")):
                continue
            if entry.stat(follow_symlinks=False).st_mtime < cutoff_ts:
                os.remove(entry.path)


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
            theme_palette TEXT NOT NULL DEFAULT 'gruvbox',
            theme_custom_color TEXT,
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
    if "theme_palette" not in user_columns:
        db.execute(
            "ALTER TABLE users ADD COLUMN theme_palette TEXT NOT NULL DEFAULT 'gruvbox'"
        )
    if "theme_custom_color" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN theme_custom_color TEXT")

    session_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "auto_pause_pending_alert" not in session_columns:
        db.execute(
            "ALTER TABLE sessions ADD COLUMN auto_pause_pending_alert INTEGER NOT NULL DEFAULT 0"
        )

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_status_id ON sessions(user_id, status, id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_status_end_ts ON sessions(status, end_ts)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_end_ts ON sessions(user_id, end_ts)"
    )
    # Supports the collaboration "new starts" feed (started_session_events),
    # which filters on created_ts and is polled every few seconds. Without this
    # index that query table-scans sessions as the table grows.
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_created_ts ON sessions(created_ts)"
    )
    # DB-level guard against a user ending up with two concurrently active
    # (running/paused) sessions when two requests race each other (realistic
    # under Gunicorn's multiple worker processes). The check-then-insert in
    # routes/session_api.py is not itself atomic across processes; this
    # unique index makes the second concurrent insert/update fail with
    # sqlite3.IntegrityError instead of silently creating a duplicate.
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_one_active_per_user
        ON sessions(user_id)
        WHERE status IN ('running', 'paused')
        """
    )

    category_count = int(
        db.execute("SELECT COUNT(*) AS count FROM categories").fetchone()["count"]
    )
    if category_count == 0:
        for cat in config.DEFAULT_CATEGORIES:
            db.execute("INSERT INTO categories(name) VALUES(?)", (cat,))
    db.commit()


def prune_auth_attempts(cutoff_ts: int) -> None:
    db = get_db()
    db.execute("DELETE FROM auth_attempts WHERE last_ts < ?", (cutoff_ts,))
    db.commit()


def run_daily_maintenance() -> None:
    """Run the daily backup + auth-attempt prune at most once per local day,
    per machine.

    Coordination is a lock file plus a marker file in the backups directory,
    not per-process state: with Gunicorn's multiple worker processes (each
    running its own background sweep thread), only the process that holds the
    lock performs the work, and the marker prevents a restart from re-running
    it later on the same day. This used to be called from a before_request
    hook, which paid the backup latency inside the day's first request,
    raced between workers, and skipped the backup entirely on a day with no
    requests; the background sweep now calls this continuously instead.
    """
    backup_dir = os.path.join(config.BASE_DIR, "backups")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    today = datetime.now().astimezone().date().isoformat()
    lock_path = os.path.join(backup_dir, ".maintenance.lock")
    marker_path = os.path.join(backup_dir, ".maintenance-last-run")

    lock_file = open(lock_path, "a+")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                # Another worker process is running maintenance right now.
                return
        try:
            try:
                with open(marker_path, "r") as f:
                    if f.read().strip() == today:
                        return
            except FileNotFoundError:
                pass
            run_daily_database_backup(current_app.config["DATABASE"], config.BASE_DIR)
            prune_auth_attempts(now_ts() - config.AUTH_WINDOW_SECONDS)
            with open(marker_path, "w") as f:
                f.write(today)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
    finally:
        lock_file.close()

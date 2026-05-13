import argparse
import os
import sqlite3


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


def main() -> int:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Migrate sessions table to store category names directly."
    )
    parser.add_argument(
        "--db",
        default=os.path.join(base_dir, "timestat.db"),
        help="Path to SQLite database (default: ./timestat.db)",
    )
    args = parser.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        migrated = migrate_sessions_table_to_category_name(db)
        db.commit()
    finally:
        db.close()

    if migrated:
        print("Migration complete: sessions now store category_name directly.")
    else:
        print("No migration needed: sessions already store category_name directly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

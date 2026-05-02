import secrets
import sqlite3
from werkzeug.security import generate_password_hash


def main() -> None:
    db_path = input("Database path [/opt/timestat/timestat.db]: ").strip() or "/opt/timestat/timestat.db"
    username = input("Username to reset: ").strip()
    if not username:
        raise SystemExit("Username is required.")

    new_code = f"{secrets.randbelow(1_000_000):06d}"

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE users SET code_hash = ? WHERE username = ?",
            (generate_password_hash(new_code), username),
        )
        conn.commit()
    finally:
        conn.close()

    if cur.rowcount == 0:
        raise SystemExit("No user was updated. Check the username and database path.")

    print("rows_updated:", cur.rowcount)
    print("new_code:", new_code)


if __name__ == "__main__":
    main()

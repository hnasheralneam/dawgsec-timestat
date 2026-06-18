from datetime import datetime, timezone

from flask import session

import db
import helpers


def get_current_user():
    if "user_id" not in session:
        return None
    conn = db.get_db()
    return conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()


def get_active_session(user_id: int):
    conn = db.get_db()
    return conn.execute(
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
    conn = db.get_db()
    return conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def collaborator_presence_rows(current_ts: int, exclude_user_id: int):
    conn = db.get_db()
    rows = conn.execute(
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
            "elapsed_seconds": helpers.elapsed_seconds(row, current_ts),
        }
        for row in rows
    ]


def started_session_events(since_ts: int, exclude_user_id: int):
    conn = db.get_db()
    rows = conn.execute(
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
    conn = db.get_db()
    if days < 1:
        return []
    days = min(days, 366)
    today_iso = datetime.fromtimestamp(current_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
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
    conn = db.get_db()
    categories = [
        row["name"]
        for row in conn.execute("SELECT name FROM categories ORDER BY name").fetchall()
    ]
    totals = {name: 0 for name in categories}

    if since_ts is None:
        if user_id is None:
            completed = conn.execute(
                """
                SELECT category_name, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
                FROM sessions
                WHERE status = 'completed'
                GROUP BY category_name
                """
            ).fetchall()
            active = conn.execute(
                "SELECT * FROM sessions WHERE status IN ('running', 'paused')"
            ).fetchall()
        else:
            completed = conn.execute(
                """
                SELECT category_name, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
                FROM sessions
                WHERE status = 'completed' AND user_id = ?
                GROUP BY category_name
                """,
                (user_id,),
            ).fetchall()
            active = conn.execute(
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
            totals[category_name] = totals.get(category_name, 0) + helpers.elapsed_seconds(
                row, current_ts
            )
    else:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT category_name, start_ts, end_ts, paused_seconds, status, pause_started_ts
                FROM sessions
                WHERE status IN ('running', 'paused')
                   OR (status = 'completed' AND end_ts > ?)
                """,
                (since_ts,),
            ).fetchall()
        else:
            rows = conn.execute(
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
            totals[category_name] = totals.get(
                category_name, 0
            ) + helpers.elapsed_seconds_in_window(row, current_ts, since_ts)

    known_names = set(categories)
    ordered_names = categories + sorted(
        [name for name in totals if name not in known_names]
    )
    return [{"name": name, "seconds": totals[name]} for name in ordered_names if totals[name] > 0]


def leaderboard_rows(current_ts: int, since_ts: int | None = None):
    conn = db.get_db()
    users = conn.execute("SELECT id, username FROM users").fetchall()
    totals = {row["id"]: 0 for row in users}

    if since_ts is None:
        completed = conn.execute(
            """
            SELECT user_id, SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
            FROM sessions
            WHERE status = 'completed'
            GROUP BY user_id
            """
        ).fetchall()
        for row in completed:
            totals[row["user_id"]] = int(row["seconds"] or 0)

        active = conn.execute(
            """
            SELECT user_id, start_ts, end_ts, paused_seconds, status, pause_started_ts
            FROM sessions
            WHERE status IN ('running', 'paused')
            """
        ).fetchall()
        for row in active:
            totals[row["user_id"]] = totals.get(row["user_id"], 0) + helpers.elapsed_seconds(
                row, current_ts
            )
    else:
        rows = conn.execute(
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
                helpers.elapsed_seconds_in_window(row, current_ts, since_ts)
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


def recent_sessions_for_user(
    user_id: int,
    *,
    limit: int,
    offset: int = 0,
    query_text: str | None = None,
    category: str | None = None,
) -> tuple[list[dict], int]:
    conn = db.get_db()
    filters = ["s.user_id = ?", "s.status = 'completed'"]
    params: list[object] = [user_id]

    if category:
        filters.append("lower(s.category_name) = lower(?)")
        params.append(category)

    if query_text:
        like_value = f"%{query_text.lower()}%"
        filters.append(
            "(lower(COALESCE(s.note, '')) LIKE ? OR lower(s.category_name) LIKE ?)"
        )
        params.extend([like_value, like_value])

    where_clause = " AND ".join(filters)
    count_row = conn.execute(
        f"SELECT COUNT(*) AS count FROM sessions s WHERE {where_clause}",
        params,
    ).fetchone()
    total = int(count_row["count"] or 0)

    rows = conn.execute(
        f"""
        SELECT
            s.id,
            s.category_name,
            s.note,
            s.start_ts,
            s.end_ts,
            s.paused_seconds
        FROM sessions s
        WHERE {where_clause}
        ORDER BY s.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()

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
    return sessions_payload, total

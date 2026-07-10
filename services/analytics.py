"""Team-wide analytics queries for the admin analytics page.

All of these aggregate over *completed* sessions only. Local-time bucketing
mirrors services.queries.user_activity_grid so admin and per-user views agree
on day boundaries.
"""

from datetime import datetime, timedelta

import db


def _window_bounds(current_ts: int, days: int) -> tuple[str, str, int]:
    today_local = datetime.fromtimestamp(current_ts).astimezone()
    start_of_today = today_local.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff_date = start_of_today - timedelta(days=days - 1)
    today_iso = today_local.strftime("%Y-%m-%d")
    cutoff_ts = int(cutoff_date.timestamp())
    return today_iso, f"-{days - 1} days", cutoff_ts


def team_hours_per_day(current_ts: int, days: int = 30) -> list[dict]:
    """Total tracked seconds per local day across all users (completed only)."""
    conn = db.get_db()
    today_iso, offset, cutoff_ts = _window_bounds(current_ts, days)
    rows = conn.execute(
        """
        WITH RECURSIVE dates(day) AS (
            SELECT date(?, ?)
            UNION ALL
            SELECT date(day, '+1 day') FROM dates WHERE day < date(?)
        ),
        totals AS (
            SELECT
                date(end_ts, 'unixepoch', 'localtime') AS day,
                SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
            FROM sessions
            WHERE status = 'completed' AND end_ts >= ?
            GROUP BY date(end_ts, 'unixepoch', 'localtime')
        )
        SELECT dates.day, COALESCE(totals.seconds, 0) AS seconds
        FROM dates
        LEFT JOIN totals ON totals.day = dates.day
        ORDER BY dates.day ASC
        """,
        (today_iso, offset, today_iso, cutoff_ts),
    ).fetchall()
    return [{"date": row["day"], "seconds": int(row["seconds"] or 0)} for row in rows]


def team_hour_of_day(current_ts: int, days: int = 30) -> list[dict]:
    """Seconds tracked grouped by hour-of-day (0-23, local time). Surfaces the
    team's busiest hours."""
    conn = db.get_db()
    _today_iso, _offset, cutoff_ts = _window_bounds(current_ts, days)
    rows = conn.execute(
        """
        SELECT
            CAST(strftime('%H', start_ts, 'unixepoch', 'localtime') AS INTEGER) AS hour,
            SUM(MAX(0, end_ts - start_ts - paused_seconds)) AS seconds
        FROM sessions
        WHERE status = 'completed' AND start_ts >= ?
        GROUP BY hour
        """,
        (cutoff_ts,),
    ).fetchall()
    by_hour = {int(row["hour"]): int(row["seconds"] or 0) for row in rows}
    return [{"hour": h, "seconds": by_hour.get(h, 0)} for h in range(24)]


def active_users_per_day(current_ts: int, days: int = 14) -> list[dict]:
    """Distinct users who completed at least one session per local day."""
    conn = db.get_db()
    today_iso, offset, cutoff_ts = _window_bounds(current_ts, days)
    rows = conn.execute(
        """
        WITH RECURSIVE dates(day) AS (
            SELECT date(?, ?)
            UNION ALL
            SELECT date(day, '+1 day') FROM dates WHERE day < date(?)
        ),
        totals AS (
            SELECT
                date(end_ts, 'unixepoch', 'localtime') AS day,
                COUNT(DISTINCT user_id) AS active_users
            FROM sessions
            WHERE status = 'completed' AND end_ts >= ?
            GROUP BY date(end_ts, 'unixepoch', 'localtime')
        )
        SELECT dates.day, COALESCE(totals.active_users, 0) AS active_users
        FROM dates
        LEFT JOIN totals ON totals.day = dates.day
        ORDER BY dates.day ASC
        """,
        (today_iso, offset, today_iso, cutoff_ts),
    ).fetchall()
    return [{"date": row["day"], "active_users": int(row["active_users"] or 0)} for row in rows]


def team_top_users(current_ts: int, since_ts: int | None = None, limit: int = 10) -> list[dict]:
    """Top users by total seconds (all-time unless since_ts given)."""
    from services import queries

    rows = queries.leaderboard_rows(current_ts, since_ts=since_ts)
    return rows[:limit]


def team_category_breakdown(current_ts: int, since_ts: int | None = None) -> list[dict]:
    """Team-wide category breakdown (all-time unless since_ts given)."""
    from services import queries

    return queries.category_rows_for_user(None, current_ts, since_ts=since_ts)

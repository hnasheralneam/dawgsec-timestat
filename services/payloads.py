"""Shared response builders.

Kept in one place so the REST endpoints (``/api/status`` etc.) and the SSE
stream (``/api/stream``) produce identical payloads and can't drift apart.
"""

import config
import db
from flask import session as flask_session
from utils import helpers
from services import queries


def build_status_payload(user_id, current_ts, collab_since_ts, pop_alert=True):
    """Return the dict shape that ``GET /api/status`` emits.

    When ``pop_alert`` is true the one-shot ``auto_paused_alert`` flash flag is
    popped from the session (REST behaviour). The SSE stream passes ``False``
    because session writes don't reliably persist once a streaming response has
    started (the Set-Cookie header is sent with the first chunk).
    """
    active = queries.get_active_session(user_id)

    auto_paused_alert = False
    if pop_alert:
        try:
            auto_paused_alert = bool(flask_session.pop("auto_paused_alert", None))
        except RuntimeError:
            auto_paused_alert = False

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

    payload = {
        "server_ts": current_ts,
        "team_presence": team_presence,
        "new_starts": new_starts,
        "notify_on_collab_starts": notify_on_collab_starts,
        "auto_paused_alert": auto_paused_alert,
        "current_session": None,
    }
    if active:
        payload["current_session"] = {
            "id": active["id"],
            "category_name": active["category_name"],
            "note": active["note"] or "",
            "status": active["status"],
            "elapsed_seconds": helpers.elapsed_seconds(active, current_ts),
            "start_ts": active["start_ts"],
        }
    return payload


def build_weekly_digest(user_id, current_ts, limit=5):
    """Return a dashboard 'digest': the weekly leaderboard + weekly category
    breakdowns (self and team). Emitted periodically by the SSE stream so the
    leaderboard/charts update without the client polling."""
    since_ts = current_ts - config.WEEK_SECONDS
    weekly_rows = queries.leaderboard_rows(current_ts, since_ts=since_ts)
    if limit is not None:
        weekly_rows = weekly_rows[:limit]
    leaderboard = {"leaderboard": weekly_rows, "server_ts": current_ts, "since_ts": since_ts}
    my_week = queries.category_rows_for_user(user_id, current_ts, since_ts=since_ts)
    team_week = queries.category_rows_for_user(None, current_ts, since_ts=since_ts)
    stats = {
        "my_categories_week": my_week,
        "team_categories_week": team_week,
        "since_ts": since_ts,
    }
    return {"leaderboard": leaderboard, "stats": stats}

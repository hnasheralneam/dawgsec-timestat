"""Shared response builders.

Kept in one place so the REST endpoints (``/api/status`` etc.) and the SSE
stream (``/api/stream``) produce identical payloads and can't drift apart.
"""

import db
from flask import session as flask_session
from utils import helpers
from services import queries
from services import live_cache


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
    collaboration = live_cache.collaboration_snapshot(current_ts)
    team_presence = [
        row for row in collaboration["presence"] if row["user_id"] != user_id
    ]
    new_starts = [
        row
        for row in collaboration["starts"]
        if row["user_id"] != user_id and row["start_ts"] >= collab_since_ts
    ]

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
    base = live_cache.weekly_digest_base(current_ts, limit=limit)
    leaderboard = {
        "leaderboard": base["leaderboard"],
        "server_ts": current_ts,
        "since_ts": base["since_ts"],
    }
    my_week = live_cache.user_weekly_categories(user_id, current_ts)
    team_week = base["team_categories"]
    stats = {
        "my_categories_week": my_week,
        "team_categories_week": team_week,
        "since_ts": base["since_ts"],
    }
    return {"leaderboard": leaderboard, "stats": stats}

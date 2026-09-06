"""Shared response builders.

Kept in one place so the REST endpoints (``/api/status`` etc.) and the SSE
stream (``/api/stream``) produce identical payloads and can't drift apart.
"""

import db
from utils import helpers
from services import queries
from services import live_cache


def build_status_payload(user_id, current_ts, collab_since_ts):
    """Return the dict shape that ``GET /api/status`` emits.

    ``auto_paused_alert`` is delivered exactly once via an atomic DB claim
    (see ``auto_pause_pending_alert`` on the sessions table) rather than a
    Flask-session cookie flash flag - a cookie write isn't reliably
    persisted once an SSE response has started streaming (the Set-Cookie
    header is sent with the first chunk), which used to silently drop the
    alert whenever the auto-pause happened to occur inside the stream
    rather than during a plain REST request.
    """
    active = queries.get_active_session(user_id)

    conn = db.get_db()

    auto_paused_alert = False
    if active and active["auto_pause_pending_alert"]:
        cur = conn.execute(
            "UPDATE sessions SET auto_pause_pending_alert = 0 WHERE id = ? AND auto_pause_pending_alert = 1",
            (active["id"],),
        )
        # Whichever caller's UPDATE actually flips the row wins and shows
        # the alert; anyone else who was about to read it sees it already
        # cleared - exactly-once delivery regardless of REST vs SSE or
        # which worker process handles the request.
        auto_paused_alert = cur.rowcount > 0
        conn.commit()

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
            # Included so SSE change detection notices adjusts: an adjust only
            # moves paused_seconds, which shifts the baseline the client's
            # locally-derived elapsed timer counts up from. Without it the
            # status signature (which strips elapsed_seconds as locally
            # derivable) wouldn't change, and other devices would keep
            # counting from the pre-adjust baseline until the next forced
            # resync (~60s) - displaying time the server had already removed.
            "paused_seconds": int(active["paused_seconds"] or 0),
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

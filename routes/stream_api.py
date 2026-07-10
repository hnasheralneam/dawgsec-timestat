"""Server-Sent Events live-update stream.

Replaces the dashboard's high-frequency polling of ``/api/status`` (5s) and
the leaderboard/stats batch (30s) with a single push connection. The server
polls the database internally and pushes:

  * ``status`` events ~ every 2s  - active session + team presence + new
    teammate starts (same shape as ``GET /api/status``).
  * ``digest`` events ~ every 10s - weekly leaderboard (top 5) + weekly
    self/team category breakdowns.

Notes
-----
* Each tick opens a *fresh* SQLite connection (rather than holding one open for
  the lifetime of the stream) so long-lived readers don't block WAL
  checkpointing. The connection is briefly swapped into ``flask.g`` so the
  shared query layer (which calls ``db.get_db()``) sees it.
* The stream self-closes after ``MAX_TICKS`` so EventSource reconnects with a
  fresh authentication cookie (bounds the window in which an expired session
  keeps receiving data).
* Requires threaded Gunicorn workers (``--threads N``), because each open SSE
  connection occupies a thread for its lifetime. See ``deploy/timestat.service``.
"""

import json
import sqlite3
import time
from contextlib import contextmanager

from flask import Response, current_app, g, session, stream_with_context

import config
import db
from auth import security
from services import payloads

# Timing (seconds / ticks)
STATUS_INTERVAL = 2.0
SLEEP_STEP = 0.5
DIGEST_EVERY = 5       # digest every 5 status ticks -> ~10s
HEARTBEAT_EVERY = 8    # heartbeat comment every 8 status ticks -> ~16s
MAX_TICKS = 150        # ~5min, then close so the client reconnects (re-auths)


@contextmanager
def _fresh_db():
    """Bind a brand-new SQLite connection to ``flask.g.db`` for the duration of
    one snapshot, then close it. Restores any previously-bound connection."""
    conn = sqlite3.connect(current_app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    saved = g.pop("db", None)
    g.db = conn
    try:
        yield
    finally:
        g.pop("db", None)
        conn.close()
        if saved is not None:
            g.db = saved


def _encode(event_name, data):
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"


def _sleep_responsive(total):
    """Sleep in small increments so a client disconnect is noticed promptly
    (the next ``yield`` will raise and end the generator)."""
    slept = 0.0
    while slept < total:
        time.sleep(SLEEP_STEP)
        slept += SLEEP_STEP


def _generate(user_id):
    last_collab_since = None
    ticks = 0
    try:
        while True:
            with _fresh_db():
                current_ts = db.now_ts()
                # First tick: start at "now" so we don't replay old starts.
                collab_since = current_ts if last_collab_since is None else last_collab_since
                collab_since = max(
                    current_ts - config.COLLAB_SINCE_MAX_AGE_SECONDS,
                    min(collab_since, current_ts),
                )
                status_payload = payloads.build_status_payload(
                    user_id, current_ts, collab_since, pop_alert=False
                )
                last_collab_since = current_ts

            yield _encode("status", status_payload)
            ticks += 1

            if ticks % DIGEST_EVERY == 0:
                with _fresh_db():
                    current_ts = db.now_ts()
                    digest = payloads.build_weekly_digest(user_id, current_ts, limit=5)
                yield _encode("digest", digest)

            if ticks % HEARTBEAT_EVERY == 0:
                yield f": heartbeat {ticks}\n\n"

            if ticks >= MAX_TICKS:
                # Close cleanly; EventSource will reconnect (re-authenticating).
                yield "event: end\ndata: reconnect\n\n"
                return

            _sleep_responsive(STATUS_INTERVAL)
    except (GeneratorExit, ConnectionError, BrokenPipeError):
        # Client went away. Nothing to clean up beyond returning.
        return


def register_routes(app):
    @app.get("/api/stream")
    @security.login_required
    def api_stream():
        user_id = int(session["user_id"])
        headers = {
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        }
        return Response(
            stream_with_context(_generate(user_id)),
            mimetype="text/event-stream",
            headers=headers,
        )

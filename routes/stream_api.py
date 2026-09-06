"""Server-Sent Events live-update stream.

Replaces the dashboard's high-frequency polling of ``/api/status`` (5s) and
the leaderboard/stats batch (30s) with a single push connection. The server
checks a shared change revision and pushes:

  * ``status`` events when session data changes - active session + team presence + new
    teammate starts (same shape as ``GET /api/status``).
  * ``digest`` events when session data changes - weekly leaderboard (top 5) + weekly
    self/team category breakdowns.

Notes
-----
* One SQLite connection is opened per stream and reused across ticks (swapped
  into ``flask.g`` so the shared query layer sees it), then closed when the
  stream ends. It is reopened every ``DB_REOPEN_EVERY`` ticks so no single
  connection lives for the whole stream lifetime; this bounds connection age
  without the per-tick open/close churn a fresh connection every tick caused.
  SQLite's autocommit mode commits each read transaction, so reusing a
  connection does not pin a long-lived read snapshot that would block WAL
  checkpointing.
* The stream self-closes after ``MAX_TICKS`` so EventSource reconnects with a
  fresh authentication cookie (bounds the window in which an expired session
  keeps receiving data).
* Scaling limit: each open SSE connection occupies one Gunicorn thread for its
  whole ~5 min lifetime. With ``--workers 3 --threads 16`` that is a hard
  ceiling of 3 * 16 = 48 concurrent live clients; new requests queue beyond
  that. Raise ``--workers``/``--threads`` to fit your team size. See
  ``deploy/timestat.service``.
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
from services import live_cache

# Timing (seconds / ticks)
STATUS_INTERVAL = 1.0
SLEEP_STEP = 0.5
HEARTBEAT_EVERY = 8    # heartbeat comment every 8 status ticks -> ~16s
MAX_TICKS = 150        # ~5min, then close so the client reconnects (re-auths)
STATUS_RESYNC_EVERY = 60  # refresh the client-side timer about every 60s
# Re-push the digest about this often even when no DB write bumped the
# revision. Leaderboard "seconds" include the live elapsed of running
# sessions, which advances with wall-clock time, so a revision-only gate
# freezes the leaderboard while the team is idle until someone acts. The
# signature check still suppresses the push when nothing is actually moving
# (no running sessions), and the cost is bounded because live_cache
# TTL-buckets digest recomputation.
DIGEST_RESYNC_EVERY = 30
# Reopen the per-stream SQLite connection every this many ticks (~seconds) so
# no single connection lives for the whole stream lifetime (bounds connection
# age while avoiding a brand-new connection every tick).
DB_REOPEN_EVERY = 60
# A new connection's "new starts" lookback window starts this far before
# "now" rather than exactly at "now". Every reconnect (including the forced
# one every MAX_TICKS) otherwise resets the window, silently dropping any
# teammate-start event that lands during the brief reconnect gap. The
# client already de-dupes by session_id, so a small overlap can't cause a
# duplicate notification - it can only close this gap.
COLLAB_RECONNECT_OVERLAP_SECONDS = 15


def _open_db():
    """Open a standalone SQLite connection for a stream (not ``g.db``)."""
    conn = sqlite3.connect(current_app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def _bound_db(conn):
    """Bind an existing connection to ``flask.g.db`` for the duration of one
    snapshot, restoring any previously-bound connection. Does NOT close the
    connection - the stream owns it and reuses it across ticks."""
    saved = g.pop("db", None)
    g.db = conn
    try:
        yield
    finally:
        g.pop("db", None)
        if saved is not None:
            g.db = saved


def _encode(event_name, data):
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"


def _status_signature(payload):
    """Ignore values the browser can derive locally between state changes."""
    stable = dict(payload)
    stable.pop("server_ts", None)
    current = stable.get("current_session")
    if current:
        current = dict(current)
        current.pop("elapsed_seconds", None)
        stable["current_session"] = current
    return json.dumps(stable, sort_keys=True, separators=(",", ":"))


def _digest_signature(payload):
    """Ignore rolling timestamps when deciding whether a digest changed."""
    stable = json.loads(json.dumps(payload))
    stable.get("leaderboard", {}).pop("server_ts", None)
    stable.get("leaderboard", {}).pop("since_ts", None)
    stable.get("stats", {}).pop("since_ts", None)
    return json.dumps(stable, sort_keys=True, separators=(",", ":"))


def _sleep_responsive(total):
    """Sleep in small increments so a client disconnect is noticed promptly
    (the next ``yield`` will raise and end the generator)."""
    slept = 0.0
    while slept < total:
        time.sleep(SLEEP_STEP)
        slept += SLEEP_STEP


def _generate(user_id):
    last_collab_since = None
    last_status_signature = None
    last_digest_signature = None
    last_status_revision = None
    last_digest_revision = None
    ticks = 0
    conn = _open_db()
    try:
        while True:
            ticks += 1
            if ticks > 1 and ticks % DB_REOPEN_EVERY == 0:
                conn.close()
                conn = _open_db()
            current_ts = db.now_ts()
            current_revision = live_cache.revision()
            status_due = (
                last_status_revision is None
                or current_revision != last_status_revision
                or ticks % STATUS_RESYNC_EVERY == 0
            )
            digest_due = (
                last_digest_revision is None
                or current_revision != last_digest_revision
                or ticks % DIGEST_RESYNC_EVERY == 0
            )

            status_sent = False
            if status_due:
                with _bound_db(conn):
                    # First tick of a (re)connection: start slightly before
                    # "now" rather than exactly "now" - see
                    # COLLAB_RECONNECT_OVERLAP_SECONDS above.
                    collab_since = (
                        current_ts - COLLAB_RECONNECT_OVERLAP_SECONDS
                        if last_collab_since is None
                        else last_collab_since
                    )
                    collab_since = max(
                        current_ts - config.COLLAB_SINCE_MAX_AGE_SECONDS,
                        min(collab_since, current_ts),
                    )
                    status_payload = payloads.build_status_payload(
                        user_id, current_ts, collab_since
                    )
                last_collab_since = current_ts
                status_signature = _status_signature(status_payload)
                status_sent = (
                    ticks % STATUS_RESYNC_EVERY == 0
                    or status_signature != last_status_signature
                )
                last_status_revision = current_revision
                if status_sent:
                    yield _encode("status", status_payload)
                    last_status_signature = status_signature

            if digest_due:
                with _bound_db(conn):
                    digest = payloads.build_weekly_digest(user_id, current_ts, limit=5)
                digest_signature = _digest_signature(digest)
                last_digest_revision = current_revision
                if digest_signature != last_digest_signature:
                    yield _encode("digest", digest)
                    last_digest_signature = digest_signature

            if ticks % HEARTBEAT_EVERY == 0 and not status_sent:
                yield f": heartbeat {ticks}\n\n"

            if ticks >= MAX_TICKS:
                # Close cleanly; EventSource will reconnect (re-authenticating).
                yield "event: end\ndata: reconnect\n\n"
                return

            _sleep_responsive(STATUS_INTERVAL)
    except (GeneratorExit, ConnectionError, BrokenPipeError):
        # Client went away. Nothing to clean up beyond returning.
        return
    finally:
        conn.close()


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

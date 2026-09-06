"""Shared, change-aware snapshots for live dashboard data.

The dashboard may have many open SSE connections, but the team-wide presence
and aggregate queries are identical for all of them. Keep those snapshots in
the worker process and invalidate them when the app writes session data. A
small SQLite ``data_version`` check also catches writes made by another
Gunicorn worker without repeating the expensive aggregate queries per client.
"""

import atexit
import os
import sqlite3
import threading
import time

from flask import current_app

from services import queries
import config


_LOCK = threading.RLock()
_MONITOR_CONNECTION = None
_MONITOR_PATH = None
_MONITOR_INODE = None
_LAST_DATA_VERSION = None
_LAST_CHECK = 0.0
_REVISION = 0
_CHECK_INTERVAL = 0.5

_collaboration_revision = None
_collaboration_snapshot = None
_digest_revision = None
_digest_base = None
_digest_epoch = None
_user_digest_revisions = {}
_alltime_leaderboard_revision = None
_alltime_leaderboard_epoch = None
_alltime_leaderboard_rows = None
_alltime_team_categories_revision = None
_alltime_team_categories_epoch = None
_alltime_team_categories_rows = None
_user_alltime_revisions = {}

# The weekly digest's rolling 7-day window (since_ts) slides with wall-clock
# time even when no one writes, so a cache keyed only on the DB revision goes
# stale when the team is idle - sessions that aged out of the window stay on
# the leaderboard until the next write. Recompute the digest whenever this
# many seconds have elapsed, in addition to on revision change.
_DIGEST_TTL_SECONDS = 60


def _close_monitor() -> None:
    global _MONITOR_CONNECTION
    with _LOCK:
        if _MONITOR_CONNECTION is not None:
            _MONITOR_CONNECTION.close()
            _MONITOR_CONNECTION = None


atexit.register(_close_monitor)


def _clear_snapshots() -> None:
    global _collaboration_revision, _collaboration_snapshot
    global _digest_revision, _digest_base, _digest_epoch
    global _alltime_leaderboard_revision, _alltime_leaderboard_epoch
    global _alltime_team_categories_revision, _alltime_team_categories_epoch
    _collaboration_revision = None
    _collaboration_snapshot = None
    _digest_revision = None
    _digest_base = None
    _digest_epoch = None
    _user_digest_revisions.clear()
    _alltime_leaderboard_revision = None
    _alltime_leaderboard_rows = None
    _alltime_team_categories_revision = None
    _alltime_team_categories_rows = None
    _user_alltime_revisions.clear()


def invalidate() -> None:
    """Mark all live snapshots stale after a successful application write."""
    global _LAST_DATA_VERSION, _REVISION
    with _LOCK:
        _REVISION += 1
        # Establish a new monitor baseline on the next check. This avoids
        # counting the same local commit twice.
        _LAST_DATA_VERSION = None
        _clear_snapshots()


def _ensure_monitor_locked(path: str):
    global _MONITOR_CONNECTION, _MONITOR_PATH, _MONITOR_INODE

    try:
        inode = os.stat(path).st_ino
    except OSError:
        inode = None

    if (
        _MONITOR_CONNECTION is not None
        and _MONITOR_PATH == path
        and _MONITOR_INODE == inode
    ):
        return _MONITOR_CONNECTION

    if _MONITOR_CONNECTION is not None:
        _MONITOR_CONNECTION.close()

    _MONITOR_CONNECTION = sqlite3.connect(
        path, isolation_level=None, check_same_thread=False
    )
    _MONITOR_CONNECTION.execute("PRAGMA query_only = ON")
    _MONITOR_CONNECTION.execute("PRAGMA busy_timeout = 5000")
    _MONITOR_PATH = path
    _MONITOR_INODE = inode
    return _MONITOR_CONNECTION


def _revision_locked() -> int:
    global _LAST_CHECK, _LAST_DATA_VERSION, _REVISION

    now = time.monotonic()
    if now - _LAST_CHECK < _CHECK_INTERVAL:
        return _REVISION

    monitor = _ensure_monitor_locked(current_app.config["DATABASE"])
    data_version = int(monitor.execute("PRAGMA data_version").fetchone()[0])
    if _LAST_DATA_VERSION is not None and data_version != _LAST_DATA_VERSION:
        _REVISION += 1
        _clear_snapshots()
    _LAST_DATA_VERSION = data_version
    _LAST_CHECK = now
    return _REVISION


def revision() -> int:
    """Return a cheap process-shared revision, noticing external DB commits."""
    with _LOCK:
        return _revision_locked()


def collaboration_snapshot(current_ts: int):
    """Return all active users and recent starts for the current DB revision."""
    global _collaboration_revision, _collaboration_snapshot
    with _LOCK:
        current_revision = _revision_locked()
        if _collaboration_revision != current_revision:
            _collaboration_snapshot = {
                "presence": queries.collaborator_presence_rows(None),
                "starts": queries.started_session_events(
                    current_ts - config.COLLAB_SINCE_MAX_AGE_SECONDS, None
                ),
            }
            _collaboration_revision = current_revision
        return _collaboration_snapshot


def weekly_digest_base(current_ts: int, limit: int | None = 5):
    """Return leaderboard/team totals once per revision OR time bucket, shared
    by SSE clients. Recomputes when the DB revision changes or when the
    coarse time bucket rolls over, so the rolling 7-day window (since_ts)
    actually rolls even while the team is idle."""
    global _digest_revision, _digest_base, _digest_epoch
    with _LOCK:
        current_revision = _revision_locked()
        epoch = int(current_ts) // _DIGEST_TTL_SECONDS
        if _digest_revision != current_revision or _digest_epoch != epoch:
            since_ts = current_ts - config.WEEK_SECONDS
            _digest_base = {
                "leaderboard": queries.leaderboard_rows(current_ts, since_ts=since_ts),
                "team_categories": queries.category_rows_for_user(
                    None, current_ts, since_ts=since_ts
                ),
                "since_ts": since_ts,
            }
            _digest_revision = current_revision
            _digest_epoch = epoch
            _user_digest_revisions.clear()

        rows = _digest_base["leaderboard"]
        return {
            "leaderboard": rows if limit is None else rows[:limit],
            "team_categories": _digest_base["team_categories"],
            "since_ts": _digest_base["since_ts"],
        }


def user_weekly_categories(user_id: int, current_ts: int):
    """Return one user's weekly category totals once per DB revision or time
    bucket (kept consistent with weekly_digest_base's rolling window)."""
    with _LOCK:
        current_revision = _revision_locked()
        epoch = int(current_ts) // _DIGEST_TTL_SECONDS
        cached = _user_digest_revisions.get(user_id)
        if cached and cached[0] == current_revision and cached[1] == epoch:
            return cached[2]

        since_ts = current_ts - config.WEEK_SECONDS
        rows = queries.category_rows_for_user(user_id, current_ts, since_ts=since_ts)
        _user_digest_revisions[user_id] = (current_revision, epoch, rows)
        return rows


def all_time_leaderboard(current_ts: int):
    """All-time leaderboard rows, cached per DB revision + TTL bucket.

    The all-time aggregate groups over every completed session - with no
    since_ts bound there is no index range to stop at, so the scan grows
    linearly with history. /api/leaderboard serves this per request (and the
    all-time page polls it), so cache it exactly like the weekly digest:
    recompute on revision change or when the TTL bucket rolls over, which
    also keeps the live elapsed of running sessions moving.
    """
    global _alltime_leaderboard_revision, _alltime_leaderboard_epoch
    global _alltime_leaderboard_rows
    with _LOCK:
        current_revision = _revision_locked()
        epoch = int(current_ts) // _DIGEST_TTL_SECONDS
        if (
            _alltime_leaderboard_revision != current_revision
            or _alltime_leaderboard_epoch != epoch
        ):
            _alltime_leaderboard_rows = queries.leaderboard_rows(current_ts)
            _alltime_leaderboard_revision = current_revision
            _alltime_leaderboard_epoch = epoch
        return _alltime_leaderboard_rows


def team_all_time_categories(current_ts: int):
    """Team all-time category totals, cached like all_time_leaderboard."""
    global _alltime_team_categories_revision, _alltime_team_categories_epoch
    global _alltime_team_categories_rows
    with _LOCK:
        current_revision = _revision_locked()
        epoch = int(current_ts) // _DIGEST_TTL_SECONDS
        if (
            _alltime_team_categories_revision != current_revision
            or _alltime_team_categories_epoch != epoch
        ):
            _alltime_team_categories_rows = queries.category_rows_for_user(
                None, current_ts
            )
            _alltime_team_categories_revision = current_revision
            _alltime_team_categories_epoch = epoch
        return _alltime_team_categories_rows


def user_all_time_categories(user_id: int, current_ts: int):
    """One user's all-time category totals, cached per user + revision +
    TTL bucket."""
    with _LOCK:
        current_revision = _revision_locked()
        epoch = int(current_ts) // _DIGEST_TTL_SECONDS
        cached = _user_alltime_revisions.get(user_id)
        if cached and cached[0] == current_revision and cached[1] == epoch:
            return cached[2]

        rows = queries.category_rows_for_user(user_id, current_ts)
        _user_alltime_revisions[user_id] = (current_revision, epoch, rows)
        return rows

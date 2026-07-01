import random
import sqlite3

from flask import request


def client_addr() -> str:
    # request.remote_addr is intentionally the only source of truth here.
    # X-Forwarded-For is attacker-controlled unless a trusted reverse proxy
    # is in front of us; when TRUSTED_PROXY_COUNT (config.py / app.py) is set
    # to a positive value, ProxyFix rewrites request.remote_addr itself from
    # a validated number of X-Forwarded-For hops, so this still does the
    # right thing without ever trusting the header directly.
    return (request.remote_addr or "unknown")[:120]


def generate_login_code() -> str:
    return f"{random.SystemRandom().randrange(0, 1_000_000):06d}"


def elapsed_seconds(row: sqlite3.Row, current_ts: int) -> int:
    end_ts = row["end_ts"] if row["end_ts"] is not None else current_ts
    elapsed = end_ts - row["start_ts"] - row["paused_seconds"]
    if row["status"] == "paused" and row["pause_started_ts"] is not None:
        elapsed -= current_ts - row["pause_started_ts"]
    return max(0, int(elapsed))


def elapsed_seconds_in_window(
    row: sqlite3.Row, current_ts: int, since_ts: int | None
) -> int:
    # NOTE on precision: this is a wall-clock-overlap approximation, not an
    # exact intersection of active (non-paused) time with [since_ts, now).
    # An exact calculation would require knowing the start/end timestamp of
    # *every* individual pause interval for the session, but the `sessions`
    # table (see db.py) only stores an aggregate `paused_seconds` total plus
    # `pause_started_ts` for whatever pause is currently in progress -
    # individual past pause/resume events are not persisted anywhere. So we
    # cannot reconstruct exactly when within [start_ts, end_ts] the paused
    # time occurred, and therefore cannot compute an exact overlap with an
    # arbitrary window boundary (since_ts). We instead assume active time is
    # spread evenly across the session's wall-clock span and prorate by the
    # fraction of that span which falls inside the window. This is exact
    # when since_ts <= start_ts (the whole session is in the window) and
    # exact for sessions with no pauses; it is only an approximation for
    # completed/paused sessions whose pauses are unevenly distributed and
    # whose window boundary falls strictly inside the session's span.
    #
    # Making this exact would require storing per-pause intervals (e.g. a
    # `session_pauses(session_id, paused_at, resumed_at)` table) - a schema
    # change that is out of scope here since it wasn't requested and no such
    # data currently exists to compute from.
    if since_ts is None:
        return elapsed_seconds(row, current_ts)

    start_ts = int(row["start_ts"])
    end_ts = int(row["end_ts"]) if row["end_ts"] is not None else current_ts
    if end_ts <= since_ts:
        return 0

    total_elapsed = elapsed_seconds(row, current_ts)

    # Exact case: the window fully contains the session, so no proration
    # is needed - all of the session's active time falls in the window.
    if since_ts <= start_ts:
        return total_elapsed

    total_span = max(1, end_ts - start_ts)
    overlap_span = end_ts - max(start_ts, since_ts)
    if overlap_span <= 0:
        return 0

    ratio = min(1.0, max(0.0, overlap_span / total_span))
    return int(total_elapsed * ratio)

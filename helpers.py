import random
import sqlite3

from flask import request


def client_addr() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()[:120]
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
    if since_ts is None:
        return elapsed_seconds(row, current_ts)

    start_ts = int(row["start_ts"])
    end_ts = int(row["end_ts"]) if row["end_ts"] is not None else current_ts
    if end_ts <= since_ts:
        return 0

    total_elapsed = elapsed_seconds(row, current_ts)
    total_span = max(1, end_ts - start_ts)
    overlap_span = end_ts - max(start_ts, since_ts)
    if overlap_span <= 0:
        return 0

    ratio = min(1.0, max(0.0, overlap_span / total_span))
    return int(total_elapsed * ratio)

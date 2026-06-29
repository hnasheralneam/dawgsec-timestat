import re

import config


def parse_username(raw_value: str | None) -> tuple[str | None, str | None]:
    username = " ".join((raw_value or "").split())
    if not username:
        return None, "Username is required."
    if len(username) > 50:
        return None, "Username must be 50 characters or fewer."
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9 -]*[A-Za-z0-9])?", username):
        return (
            None,
            "Username can only include letters, numbers, spaces, and hyphens.",
        )
    return username, None


def parse_note(raw_value: object) -> tuple[str | None, str | None]:
    if raw_value is None:
        note = ""
    elif isinstance(raw_value, str):
        note = raw_value.strip()
    else:
        return None, "note must be a string"

    if len(note) > config.NOTE_MAX_LENGTH:
        return None, f"note must be {config.NOTE_MAX_LENGTH} characters or fewer"
    return note, None


def parse_category_name(raw_value: object) -> tuple[str | None, str | None]:
    if not isinstance(raw_value, str):
        return None, "category_name must be a string"
    category_name = " ".join(raw_value.split())
    if not category_name:
        return None, "category_name is required"
    if len(category_name) > config.CATEGORY_MAX_LENGTH:
        return None, f"category_name must be {config.CATEGORY_MAX_LENGTH} characters or fewer"
    return category_name, None


def parse_bool_query_arg(
    raw_value: str | None, *, field_name: str
) -> tuple[bool | None, str | None]:
    if raw_value is None:
        return None, None
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True, None
    if normalized in {"0", "false", "no", "off"}:
        return False, None
    return None, f"{field_name} must be a boolean"


def parse_int_query_arg(
    raw_value: str | None,
    *,
    field_name: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> tuple[int | None, str | None]:
    if raw_value is None:
        return None, None
    try:
        value = int(raw_value)
    except ValueError:
        return None, f"{field_name} must be an integer"
    if minimum is not None and value < minimum:
        return None, f"{field_name} must be at least {minimum}"
    if maximum is not None and value > maximum:
        return None, f"{field_name} must be at most {maximum}"
    return value, None


def parse_text_query_arg(
    raw_value: str | None,
    *,
    field_name: str,
    maximum_length: int,
) -> tuple[str | None, str | None]:
    if raw_value is None:
        return None, None
    normalized = " ".join(raw_value.split())
    if not normalized:
        return None, None
    if len(normalized) > maximum_length:
        return None, f"{field_name} must be {maximum_length} characters or fewer"
    return normalized, None


def parse_recent_sessions_query_args(args) -> tuple[dict, str | None]:
    """Shared query-arg parsing for the recent-sessions endpoints.

    Returns (parsed, error). On error, parsed is empty and error holds the message.
    """
    include_full, full_error = parse_bool_query_arg(args.get("full"), field_name="full")
    if full_error:
        return {}, full_error

    limit, limit_error = parse_int_query_arg(
        args.get("limit"),
        field_name="limit",
        minimum=1,
        maximum=config.MAX_RECENT_LIMIT,
    )
    if limit_error:
        return {}, limit_error

    offset, offset_error = parse_int_query_arg(args.get("offset"), field_name="offset", minimum=0)
    if offset_error:
        return {}, offset_error

    query_text, query_error = parse_text_query_arg(
        args.get("query"), field_name="query", maximum_length=120
    )
    if query_error:
        return {}, query_error

    category, category_error = parse_text_query_arg(
        args.get("category"),
        field_name="category",
        maximum_length=config.CATEGORY_MAX_LENGTH,
    )
    if category_error:
        return {}, category_error

    limit = limit or (config.MAX_RECENT_LIMIT if include_full else config.DEFAULT_RECENT_LIMIT)
    offset = offset or 0
    return {
        "limit": limit,
        "offset": offset,
        "query_text": query_text,
        "category": category,
    }, None

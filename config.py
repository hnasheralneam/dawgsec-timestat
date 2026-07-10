import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "timestat.db")

DEFAULT_CATEGORIES = [
    "Managing Infrastructure",
    "Working on Scripts",
    "Working on Wiki Documentation",
    "Working on Playbooks",
    "Practicing IR",
    "In Practice Competition",
    "Research",
    "TryHackMe",
    "Team Coordination",
    "Mentoring/Training Others",
    "Other",
]
WEEK_SECONDS = 7 * 24 * 60 * 60
NOTE_MAX_LENGTH = 200
CATEGORY_MAX_LENGTH = 80
BACKUP_RETENTION_DAYS = 14
AUTH_WINDOW_SECONDS = 5 * 60
LOGIN_MAX_ATTEMPTS = 8
ADMIN_LOGIN_MAX_ATTEMPTS = 5
# Per-account lockout threshold (independent of source IP) so that rotating IPs
# (botnets / proxy pools) can't bypass per-IP rate limiting. Keyed on username
# alone, so a known account can't be brute-forced faster than this many failed
# attempts within AUTH_WINDOW_SECONDS regardless of how many IPs the attacker
# uses. Kept higher than LOGIN_MAX_ATTEMPTS to give legitimate users headroom
# across multiple devices.
LOGIN_ACCOUNT_MAX_ATTEMPTS = 12
DEFAULT_RECENT_LIMIT = 10
MAX_RECENT_LIMIT = 200
MAX_SESSION_RUNNING_HOURS = 8
# How far back the collaboration "new starts" feed (/api/status?collab_since=)
# is allowed to look. A client can otherwise request a huge window and force a
# large result set on every poll. Also bounded in rows by COLLAB_EVENT_LIMIT.
COLLAB_SINCE_MAX_AGE_SECONDS = 24 * 60 * 60
COLLAB_EVENT_LIMIT = 50

# Number of trusted reverse proxy hops in front of this app. When 0 (default),
# the app is assumed to receive connections directly and X-Forwarded-For is
# never trusted for things like rate-limit keys. Only set this to a positive
# number when a known, trusted number of reverse proxies (e.g. nginx) sit in
# front of the app and are configured to overwrite (not append to)
# X-Forwarded-For.
DEFAULT_TRUSTED_PROXY_COUNT = 0


def load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in ("'", '"')
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)

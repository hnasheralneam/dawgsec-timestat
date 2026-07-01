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
DEFAULT_RECENT_LIMIT = 10
MAX_RECENT_LIMIT = 200

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

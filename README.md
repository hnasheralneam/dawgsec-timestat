# TimeStat

![TimeStat logo](static/logo.svg)

Minimal Flask + SQLite web app for tracking cyber competition preparation time, with a live leaderboard and category breakdown charts.

## Quick start (local)

```bash
./setup.sh
```

This creates a `.venv`, installs dependencies, and writes a `.env` file with a
generated `SECRET_KEY`. Edit `.env` to set `ADMIN_USERNAME` + `ADMIN_PASSWORD`
if you need `/admin/login`.

Then run:

```bash
source .venv/bin/activate
python app.py
```

Open: http://127.0.0.1:5000

## Regression tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## Environment variables

All supported env values are in `deploy/timestat.env.example`.

## Features

- Account creation with generated 6-digit login code
- Login/logout
- Start, pause, resume, and finish tracking sessions
- Built-in activity categories for leadership visibility
- Live leaderboard (near-real-time polling) on a dedicated all-time stats page
- Weekly leaderboard preview (top 5) + full weekly leaderboard page
- Clickable leaderboard profiles with user charts and recent sessions
- Personal and team category breakdown charts (all-time page + last 7 days on dashboard, Chart.js)
- Recent session history with search/category filters
- Edit past completed sessions (category + note)
- Settings modal for username updates, login-code reveal-on-hover, and login-code reset
- Collaboration presence strip showing teammates currently tracking
- Optional notifications when teammates start a session (toggle in settings)
- User profile activity grid (GitHub-style daily heatmap over recent weeks)
- Mobile-friendly responsive tables and empty-state messaging
- Remove past completed sessions

| Variable | Required | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | Yes | Flask session signing key |
| `ADMIN_USERNAME` | No | Enables admin login when paired with `ADMIN_PASSWORD` |
| `ADMIN_PASSWORD` | No | Enables admin login when paired with `ADMIN_USERNAME` |
| `SESSION_COOKIE_SECURE` | No | Set `true`/`1` to send session cookies only over HTTPS |
| `FLASK_DEBUG` | No | Set `1` for debug mode when running `python app.py` |

## Deploy (systemd + Gunicorn)

**Linux with systemd only** — this script relies on `systemctl` and will not work on macOS or non-systemd distros.

```bash
./deploy.sh
```

This installs the app to `/opt/timestat`, creates a `timestat` system user,
writes `/etc/timestat/timestat.env` with a generated `SECRET_KEY` (edit it to
set `ADMIN_USERNAME`/`ADMIN_PASSWORD`), and installs + starts the
`timestat` systemd service. Requires `sudo`.

Useful:

```bash
sudo journalctl -u timestat -f --no-pager
sudo systemctl restart timestat
```

Service binds `127.0.0.1:8000` (put behind nginx/Caddy).

## Ops notes

- DB file: `timestat.db` (auto-created)
- Daily automatic DB backups: `backups/timestat-YYYYMMDD-HHMMSS.db` (UTC)
- Backups older than 14 days are auto-removed
- Sessions store `category_name` directly (stable historical labels)

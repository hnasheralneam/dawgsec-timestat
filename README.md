# TimeStat

![TimeStat logo](static/logo.svg)

Minimal Flask + SQLite web app for tracking cyber competition preparation time, with a live leaderboard and category breakdown charts.

## Quick start (local)

1. Create venv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create your local env file:

```bash
cp deploy/timestat.env.example .env
```

3. Edit `.env` and set at least:
- `SECRET_KEY` (required)
- `ADMIN_USERNAME` + `ADMIN_PASSWORD` (required for `/admin/login`)

4. Run:

```bash
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

1. Install app and dependencies:

```bash
sudo mkdir -p /opt/timestat /etc/timestat
sudo rsync -a ./ /opt/timestat/
cd /opt/timestat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create runtime user + env file:

```bash
sudo useradd --system --home /opt/timestat --shell /usr/sbin/nologin timestat || true
sudo cp deploy/timestat.env.example /etc/timestat/timestat.env
sudo chmod 640 /etc/timestat/timestat.env
sudo chown root:timestat /etc/timestat/timestat.env
sudo chown -R timestat:www-data /opt/timestat
```

3. Install and start service:

```bash
sudo cp deploy/timestat.service /etc/systemd/system/timestat.service
sudo systemctl daemon-reload
sudo systemctl enable --now timestat
sudo systemctl status timestat --no-pager
```

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

If your DB still has `sessions.category_id`, run:

```bash
python migrate_sessions_category_name.py --db /path/to/timestat.db
```

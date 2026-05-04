# TimeStat

Minimal Flask + SQLite web app for tracking cyber competition preparation time, with a live leaderboard and category breakdown charts.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

## Features

- Account creation with generated 6-digit login code
- Login/logout
- Start, pause, resume, and finish tracking sessions
- Built-in activity categories for leadership visibility
- Live leaderboard (near-real-time polling) on a dedicated all-time stats page
- Weekly leaderboard preview (top 5) + full weekly leaderboard page
- Clickable leaderboard profiles with user charts and recent sessions
- Personal and team category breakdown charts (all-time page + last 7 days on dashboard, Chart.js)
- Recent session history
- Remove past completed sessions

## Notes

- SQLite DB file: `timestat.db` (created automatically)
- This app is intentionally lightweight and low-complexity

## Deploy with systemd + Gunicorn

1. Copy app to server and install dependencies:

```bash
sudo mkdir -p /opt/timestat /etc/timestat
sudo rsync -a ./ /opt/timestat/
cd /opt/timestat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create runtime user and env file:

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

4. Useful commands:

```bash
sudo journalctl -u timestat -f --no-pager
sudo systemctl restart timestat
```

Service listens on `127.0.0.1:8000` (intended for reverse proxy via nginx/Caddy).

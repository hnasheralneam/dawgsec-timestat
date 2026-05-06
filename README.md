# TimeStat

Lightweight Flask + SQLite app for tracking team time and viewing leaderboard/stats.

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

## Environment variables

All supported env values are in `deploy/timestat.env.example`.

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

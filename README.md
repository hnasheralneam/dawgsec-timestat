# TimeStat

![TimeStat logo](static/logo.svg)

Minimal Flask + SQLite web app for tracking cyber competition preparation time, with a live leaderboard and category breakdown charts.

## Quick start (local)

```bash
./setup.sh
```

This creates a `.venv`, installs dependencies, and writes a `.env` file with a
generated `SECRET_KEY`. An `ADMIN_CODE` for `/admin/login` is auto-generated on
first startup and written back to `.env` (the value is printed to stderr once).

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

## Frontend build (Tailwind)

`static/app.css` is committed — a single stylesheet concatenating the compiled
Tailwind output (`static/tailwind.css`) and the hand-written `static/style.css`
— so the server needs no Node/npm at runtime and pages make one CSS request.
Tailwind is compiled from `static/input.css` using the dev-only config in
`tailwind.config.js` (content scanned from `templates/**/*.html`). If you add
or change Tailwind utility classes in a template **or edit `style.css`**,
rebuild it:

```bash
npm run css        # one-off: compile Tailwind + regenerate app.css
npm run css:watch  # recompile Tailwind on change (re-run css:merge for style.css edits)
```

Charts (Chart.js) are lazy-loaded only on pages that render a `<canvas>`, and
the "Material Icons Round" webfont is self-hosted (`static/fonts/`); both are
optional and degrade gracefully offline.

Static asset URLs carry a content-hash version parameter (`?v=...`) computed
from the static directory at startup, so `/static/` is served with a
year-long immutable cache and deploys always reach clients; the service
worker's cache name is derived from the same hash — no manual cache-version
bumping.

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
| `ADMIN_CODE` | No | Single admin login code. Auto-generated on first startup if unset, written back to the same env file as `SECRET_KEY`, and printed to stderr/journal once. |
| `SESSION_COOKIE_SECURE` | No | Set `true`/`1` to send session cookies only over HTTPS. **Leave unset or `false` for HTTP-only deployments (e.g., local testing, HTTP-accessible LAN) to prevent mobile login issues.** |
| `FLASK_DEBUG` | No | Set `1` for debug mode when running `python app.py` |
| `TZ` | No | IANA timezone name (e.g. `America/New_York`). Day/week boundaries in activity grids and analytics follow the **server's** timezone, not a per-user one - set this to match your team if the server's OS default (often UTC) doesn't. |

## Deploy (systemd + Gunicorn)

**Linux with systemd only** — this script relies on `systemctl` and will not work on macOS or non-systemd distros.

```bash
./deploy.sh
```

This installs the app to `/opt/timestat`, creates a `timestat` system user,
writes `/etc/timestat/timestat.env` with both a generated `SECRET_KEY` and a
generated `ADMIN_CODE`, and installs + starts the `timestat` systemd service.
The admin code is printed once and lives in the same env file as `SECRET_KEY`,
so credentials stay in one place. Requires `sudo`.

Useful:

```bash
sudo journalctl -u timestat -f --no-pager
sudo systemctl restart timestat
```

Service binds `127.0.0.1:8000` (put behind nginx/Caddy). See
`deploy/Caddyfile` for a ready-to-adapt Caddy example — it enables response
compression and, importantly, sets `flush_interval -1` on the proxy so the
SSE live-update stream isn't buffered.

## Ops notes

- DB file: `timestat.db` (auto-created)
- Daily automatic DB backups: `backups/timestat-YYYYMMDD-HHMMSS.db` (local
  server time), produced by the background sweep thread (never blocks a
  request; runs once per day per machine via a lock file, even with multiple
  Gunicorn workers and on days with no requests)
- Backups older than 14 days are auto-removed
- Sessions store `category_name` directly (stable historical labels)
- **SSE scaling limit:** each live dashboard client holds one open stream
  (`/api/stream`) that occupies a Gunicorn thread for its ~5 min lifetime.
  The default service runs `--workers 3 --threads 16`, a hard ceiling of 48
  concurrent live clients before new requests queue. Raise
  `--workers`/`--threads` in `deploy/timestat.service` to fit your team size.

## Troubleshooting

### Mobile users getting logged out repeatedly

If mobile users (especially on Android) experience frequent logouts or can't stay logged in:

1. **Check if you're using HTTP (not HTTPS):** If your app is accessed via `http://...` (no TLS/SSL), ensure `SESSION_COOKIE_SECURE` is unset or set to `false`. Modern browsers reject secure cookies on HTTP connections.

2. **Verify the environment variable:** Set `SESSION_COOKIE_SECURE=false` in your `.env` file or `/etc/timestat/timestat.env` for production.

3. **HTTPS deployments only:** Only set `SESSION_COOKIE_SECURE=true` when your app is served exclusively over HTTPS with a valid TLS certificate.

4. **Clear browser cookies:** After changing this setting, affected users should clear their browser cookies and log in again.

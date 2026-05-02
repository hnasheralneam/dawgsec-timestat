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
- Live leaderboard (near-real-time polling)
- Personal and team category breakdown charts (Chart.js)
- Recent session history

## Notes

- SQLite DB file: `timestat.db` (created automatically)
- This app is intentionally lightweight and low-complexity

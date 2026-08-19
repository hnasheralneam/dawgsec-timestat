#!/usr/bin/env bash
# Production deploy: installs TimeStat as a systemd + Gunicorn service.
# Linux with systemd only. Run from a checkout of this repo. Requires sudo.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found - this script requires a systemd-based Linux system." >&2
    exit 1
fi

sudo mkdir -p /opt/timestat /etc/timestat
sudo rsync -a --exclude .venv --exclude .git --exclude backups --exclude node_modules ./ /opt/timestat/

(
    cd /opt/timestat
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -q -r requirements.txt
)

sudo useradd --system --home /opt/timestat --shell /usr/sbin/nologin timestat || true

if [ ! -f /etc/timestat/timestat.env ]; then
    sudo cp deploy/timestat.env.example /etc/timestat/timestat.env
    secret_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    admin_code="$(python3 -c 'import secrets; print(secrets.token_urlsafe(15))')"
    sudo sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${secret_key}/" /etc/timestat/timestat.env
    sudo sed -i "s/^ADMIN_CODE=.*/ADMIN_CODE=${admin_code}/" /etc/timestat/timestat.env
    echo "Created /etc/timestat/timestat.env with a generated SECRET_KEY and ADMIN_CODE."
    echo "Your admin code is: ${admin_code} (also saved in /etc/timestat/timestat.env)."
else
    echo "/etc/timestat/timestat.env already exists, leaving it untouched."
fi
# Own the env file by the service user so the app can persist a regenerated
# ADMIN_CODE into the SAME file (keeping SECRET_KEY and ADMIN_CODE together,
# never split across two files). root can still read/write it via sudo.
sudo chmod 640 /etc/timestat/timestat.env
sudo chown timestat:timestat /etc/timestat/timestat.env
sudo chown -R timestat:www-data /opt/timestat

sudo cp deploy/timestat.service /etc/systemd/system/timestat.service
sudo systemctl daemon-reload
sudo systemctl enable --now timestat
sudo systemctl status timestat --no-pager

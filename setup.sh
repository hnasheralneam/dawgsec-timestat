#!/usr/bin/env bash
# One-time local setup: venv, dependencies, and .env file.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f .env ]; then
    cp deploy/timestat.env.example .env
    secret_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${secret_key}/" .env
    rm -f .env.bak
    echo "Created .env with a generated SECRET_KEY."
    echo "Edit .env to set ADMIN_USERNAME / ADMIN_PASSWORD before using /admin/login."
else
    echo ".env already exists, leaving it untouched."
fi

echo "Setup complete. Run: source .venv/bin/activate && python app.py"

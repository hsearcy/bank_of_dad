#!/usr/bin/env bash
# Bank of Dad — start the dashboard locally.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# Also install newly added dependencies when upgrading an existing checkout.
.venv/bin/pip install -q -r requirements.txt

exec .venv/bin/python app.py

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || { echo "Error: .venv not found. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"; exit 1; }
source .venv/bin/activate
exec python -m app.server "$@"

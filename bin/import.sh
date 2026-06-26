#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || { echo "Error: .venv not found. Run ./install.sh first"; exit 1; }
source .venv/bin/activate
exec python -m app.cli "$@"

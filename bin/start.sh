#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || { echo "Error: .venv not found. Run: bash bin/install.sh"; exit 1; }

# Load local runtime configuration (embedding provider, model cache, ...).
# Kept out of version control because it is host-specific; see AGENTS.md.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

source .venv/bin/activate
export MEMORY_STORAGE="${MEMORY_STORAGE:-dual}"
exec python -m app.server "$@"

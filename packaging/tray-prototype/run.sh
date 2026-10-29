#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV
export PYSTRAY_BACKEND=appindicator
exec "$ROOT/runtime/python/bin/python3" -I -B "$ROOT/prototype.py" "$@"

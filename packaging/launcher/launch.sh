#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ ! -e "$ROOT/.launcher-build-incomplete" ]] || {
    echo "Launcher build is incomplete: $ROOT" >&2; exit 1;
}
unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV
export PYSTRAY_BACKEND=appindicator
exec "$ROOT/runtime/python/bin/python3" -I -B "$ROOT/launcher/main.py" "$@"

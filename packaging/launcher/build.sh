#!/usr/bin/env bash
# Compose the existing Runner and GUI inputs without another shipped interpreter.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT="$ROOT/dist/remember-me-launcher-linux-x86_64"
CACHE="$ROOT/cache/tray-s2b/build"
RUNNER="" GUI="" MODEL=""
while (($#)); do
    case "$1" in
        --output|--cache-dir|--runner-source|--gui-source|--model-source)
            (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
            case "$1" in
                --output) OUTPUT="$2" ;; --cache-dir) CACHE="$2" ;;
                --runner-source) RUNNER="$2" ;; --gui-source) GUI="$2" ;;
                --model-source) MODEL="$2" ;;
            esac
            shift 2 ;;
        -h|--help)
            echo "Usage: bash packaging/launcher/build.sh [--output DIR] [--cache-dir DIR]"
            echo "       [--runner-source VERIFIED_RUNNER] [--gui-source VERIFIED_GUI_PROBE] [--model-source SNAPSHOT]"
            echo "Existing output is refused. Omit source options to run the existing build tasks."
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
OUTPUT="$(realpath -m -- "$OUTPUT")"
CACHE="$(realpath -m -- "$CACHE")"
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || { echo "Refusing existing output: $OUTPUT" >&2; exit 1; }
[[ "$CACHE/" != "$OUTPUT/"* ]] || { echo "Cache must be outside output" >&2; exit 1; }
for tree in app web launcher packaging; do
    for destination in "$OUTPUT" "$CACHE"; do
        [[ "$destination/" != "$ROOT/$tree/"* ]] || { echo "Build paths must be outside source $tree/" >&2; exit 1; }
    done
done
[[ -z "$RUNNER" || -z "$MODEL" ]] || { echo "--model-source is for a fresh Runner build; omit it with --runner-source" >&2; exit 2; }
for file in __init__.py main.py desktop.py controller.py child.py; do
    [[ -f "$ROOT/launcher/$file" ]] || { echo "Launcher source missing: $file" >&2; exit 1; }
done
for source in "$RUNNER" "$GUI"; do
    [[ -n "$source" ]] || continue
    source="$(realpath -e -- "$source")"
    for destination in "$OUTPUT" "$CACHE"; do
        [[ "$destination/" != "$source/"* ]] || {
            echo "Build output/cache cannot modify a reused source: $source" >&2; exit 1;
        }
    done
done
mkdir -p -- "$CACHE" "$(dirname -- "$OUTPUT")"
export PIP_CACHE_DIR="$CACHE/pip"
if [[ -z "$RUNNER" ]]; then
    arguments=(--output "$OUTPUT" --cache-dir "$ROOT/cache/standalone")
    [[ -z "$MODEL" ]] || arguments+=(--model-source "$MODEL")
    bash "$ROOT/packaging/standalone/build.sh" "${arguments[@]}"
    RUNNER="$OUTPUT"
else
    RUNNER="$(realpath -e -- "$RUNNER")"
fi
if [[ -z "$GUI" ]]; then
    GUI="$(mktemp -d "$CACHE/gui-output-XXXXXX")"
    rmdir -- "$GUI"
    bash "$ROOT/packaging/launcher/build_gui.sh" --output "$GUI" --cache-dir "$ROOT/cache/tray-s3-r1/build"
else
    GUI="$(realpath -e -- "$GUI")"
fi
PYTHON="$RUNNER/runtime/python/bin/python3"
unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -I -B "$ROOT/packaging/launcher/assemble.py" \
    --root "$ROOT" --runner "$RUNNER" --gui "$GUI" --output "$OUTPUT"
PYTHON="$OUTPUT/runtime/python/bin/python3"
mkdir -p "$CACHE/tmp"
TMPDIR="$CACHE/tmp" "$PYTHON" -I -B "$ROOT/packaging/standalone/verify_runtime.py" --runtime "$OUTPUT/runtime/python"
TMPDIR="$CACHE/tmp" "$PYTHON" -I -B "$ROOT/bin/verify_cpu_runtime.py" \
    --standalone-root "$OUTPUT" --model-cache "$OUTPUT/models/sentence-transformers" \
    --expected-profile-id ep_6134c0b15d2bb471
"$PYTHON" -I -B - "$OUTPUT" <<'PY'
import importlib.metadata, pathlib, sys
root=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(root/'launcher/vendor'))
import gi
gi.require_version('Gtk','3.0'); gi.require_version('AyatanaAppIndicator3','0.1')
from gi.repository import Gtk, AyatanaAppIndicator3
assert pathlib.Path(gi.__file__).resolve().is_relative_to(root/'launcher/vendor')
print('GUI import passed:', gi.__version__, Gtk.get_major_version(), Gtk.get_minor_version())
PY
PYTHONPATH="$OUTPUT/launcher/vendor" "$PYTHON" -B -m pip check
rm -- "$OUTPUT/.launcher-build-incomplete"
echo "Built Launcher directory: $OUTPUT"
du -sh -- "$OUTPUT"

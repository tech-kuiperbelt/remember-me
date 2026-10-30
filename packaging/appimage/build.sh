#!/usr/bin/env bash
# Package a verified Launcher directory using fixed official Type 2 tooling.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PAYLOAD="$ROOT/dist/remember-me-launcher-linux-x86_64-p3-s2"
WORK="$ROOT/cache/tray-p3-s2/appimage-build"
OUTPUT="$ROOT/dist/remember-me-linux-x86_64-s2.AppImage"
TOOLS="$ROOT/cache/tray-p3-s2/tools"
COMP=zstd
while (($#)); do
    case "$1" in
        --payload|--work-dir|--output|--tools-dir|--compression)
            (($# >= 2)) || { echo "Missing value: $1" >&2; exit 2; }
            case "$1" in
                --payload) PAYLOAD="$2" ;; --work-dir) WORK="$2" ;;
                --output) OUTPUT="$2" ;; --tools-dir) TOOLS="$2" ;;
                --compression) COMP="$2" ;;
            esac
            shift 2 ;;
        -h|--help)
            echo 'Usage: bash packaging/appimage/build.sh [--payload DIR] [--work-dir DIR] [--output FILE] [--tools-dir DIR] [--compression zstd]'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
[[ "$COMP" == zstd ]] || { echo 'Pinned appimagetool supports zstd only' >&2; exit 2; }
PAYLOAD="$(realpath -e -- "$PAYLOAD")"
WORK="$(realpath -m -- "$WORK")"
OUTPUT="$(realpath -m -- "$OUTPUT")"
TOOLS="$(realpath -m -- "$TOOLS")"
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" && ! -e "$OUTPUT.partial" ]] || { echo 'Output already exists' >&2; exit 1; }
[[ ! -e "$WORK/AppDir" && ! -L "$WORK/AppDir" ]] || { echo 'AppDir already exists; select a new work directory' >&2; exit 1; }
for destination in "$WORK" "$OUTPUT" "$TOOLS"; do
    [[ "$destination/" != "$PAYLOAD/"* ]] || { echo 'Build paths must not be inside the input payload' >&2; exit 1; }
done
mkdir -p -- "$WORK" "$TOOLS" "$(dirname -- "$OUTPUT")" "$WORK/tmp"
PYTHON="$PAYLOAD/runtime/python/bin/python3"
monitor=""
cleanup() { if [[ -n "$monitor" ]]; then kill "$monitor" 2>/dev/null || true; wait "$monitor" 2>/dev/null || true; fi; }
trap cleanup EXIT
"$PYTHON" -I -B -u -c 'import os,sys,time; paths=sys.argv[1:];
while True:
 values=[os.statvfs(p) for p in paths]; print(time.time_ns(),*(s.f_bavail*s.f_frsize for s in values),flush=True); time.sleep(1)' "$WORK" "$(dirname -- "$OUTPUT")" "$TOOLS" > "$WORK/disk-samples.txt" &
monitor=$!
date -u +%FT%TZ > "$WORK/started-utc.txt"
"$PYTHON" -I -B "$ROOT/packaging/appimage/verify_payload.py" "$ROOT" "$PAYLOAD" > "$WORK/payload-verification.json"
TOOL="$TOOLS/appimagetool-1.9.1-x86_64.AppImage"
RUNTIME="$TOOLS/runtime-20251108-x86_64"
fetch() {
    local path="$1" digest="$2" url="$3"
    if [[ ! -f "$path" ]]; then
        curl -fL --retry 1 --connect-timeout 15 --max-time 180 --output "$path.partial" "$url"
        printf '%s  %s\n' "$digest" "$path.partial" | sha256sum --check --status
        mv -- "$path.partial" "$path"
    fi
    printf '%s  %s\n' "$digest" "$path" | sha256sum --check
}
fetch "$TOOL" ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0 \
    https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
fetch "$RUNTIME" 2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d \
    https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64
chmod 755 "$TOOL"
"$PYTHON" -I -B - "$ROOT" "$WORK" "$COMP" <<'PY'
import hashlib, json, pathlib, sys
root, work = map(pathlib.Path, sys.argv[1:3])
names = ('build.sh', 'AppRun', 'remember-me.desktop', 'verify_payload.py', 'tree_hash.py')
sources = {f'packaging/appimage/{name}': hashlib.sha256((root/'packaging/appimage'/name).read_bytes()).hexdigest() for name in names}
info = {'sources': sources, 'compression': sys.argv[3], 'squashfs_processors': 4,
        'appimagetool': {'version': '1.9.1', 'sha256': 'ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0',
                         'url': 'https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage'},
        'type2_runtime': {'version': '20251108', 'sha256': '2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d',
                          'url': 'https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64'}}
(work/'build-inputs.json').write_text(json.dumps(info, indent=2)+'\n')
PY
mkdir "$WORK/AppDir"
cp -a --reflink=auto -- "$PAYLOAD" "$WORK/AppDir/payload"
cp -- "$ROOT/packaging/appimage/AppRun" "$WORK/AppDir/AppRun"
chmod 755 "$WORK/AppDir/AppRun"
cp -- "$ROOT/packaging/appimage/remember-me.desktop" "$WORK/AppDir/"
cp -- "$PAYLOAD/web/static/img/brand/logo-neutral-128.png" "$WORK/AppDir/remember-me.png"
ln -s remember-me.png "$WORK/AppDir/.DirIcon"
desktop-file-validate "$WORK/AppDir/remember-me.desktop"
"$PYTHON" -I -B "$ROOT/packaging/appimage/tree_hash.py" "$WORK/AppDir" > "$WORK/appdir-before.json"
unset VERSION NO_CLEANUP
export ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 TMPDIR="$WORK/tmp"
"$TOOL" --verbose --no-appstream --comp "$COMP" --runtime-file "$RUNTIME" \
    --mksquashfs-opt=-processors --mksquashfs-opt=4 "$WORK/AppDir" "$OUTPUT.partial"
"$PYTHON" -I -B "$ROOT/packaging/appimage/tree_hash.py" "$WORK/AppDir" > "$WORK/appdir-after.json"
"$PYTHON" -I -B - "$WORK" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
if json.loads((p/'appdir-before.json').read_text()) != json.loads((p/'appdir-after.json').read_text()):
    raise SystemExit('appimagetool modified the AppDir; output retained as partial')
PY
mv -- "$OUTPUT.partial" "$OUTPUT"
sha256sum "$OUTPUT" > "$WORK/appimage.sha256"
date -u +%FT%TZ > "$WORK/finished-utc.txt"
stat -c '%s' "$OUTPUT" > "$WORK/appimage-bytes.txt"
cleanup
monitor=""
echo "AppImage built: $OUTPUT"

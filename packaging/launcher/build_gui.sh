#!/usr/bin/env bash
# Build fixed GUI wheels against Ubuntu 22.04, never against the host desktop.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT="$ROOT/dist/remember-me-gui-ubuntu2204"
CACHE="$ROOT/cache/tray-gui-ubuntu2204"
IMAGE="ubuntu@sha256:8a2321a92b438238f93b4346fff61bc4259c99fdd4d9b52ee86f153fee0c344f"
while (($#)); do
    case "$1" in
        --output|--cache-dir|--base-image)
            (($# >= 2)) || { echo "Missing value: $1" >&2; exit 2; }
            case "$1" in --output) OUTPUT="$2" ;; --cache-dir) CACHE="$2" ;; --base-image) IMAGE="$2" ;; esac
            shift 2 ;;
        -h|--help)
            echo 'Usage: bash packaging/launcher/build_gui.sh [--output DIR] [--cache-dir DIR] [--base-image PINNED_IMAGE]'
            echo 'A reused Ubuntu22.04 build image must have an immutable sha256 reference; identity/packages are recorded.'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
OUTPUT="$(realpath -m -- "$OUTPUT")"
CACHE="$(realpath -m -- "$CACHE")"
[[ "$IMAGE" == *@sha256:* || "$IMAGE" == sha256:* ]] || { echo 'Base image must be pinned by digest/image ID' >&2; exit 2; }
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || { echo "Refusing existing output: $OUTPUT" >&2; exit 1; }
[[ "$CACHE/" != "$OUTPUT/"* && "$OUTPUT/" != "$CACHE/"* ]] || { echo 'Output and build cache must be separate' >&2; exit 1; }
for directory in app web launcher packaging; do
    for destination in "$OUTPUT" "$CACHE"; do
        [[ "$destination/" != "$ROOT/$directory/"* ]] || { echo 'Build paths must be outside source directories' >&2; exit 1; }
    done
done
source "$ROOT/packaging/standalone/runtime.env"
mkdir -p "$CACHE/inputs" "$CACHE/evidence" "$CACHE/system-debs" "$OUTPUT"
touch "$OUTPUT/.build-incomplete"
container=""
runtime="$(mktemp -d "$CACHE/portable-runtime-XXXXXX")"
cleanup() {
    [[ -z "$container" ]] || docker rm -f "$container" >/dev/null 2>&1 || true
    rm -rf -- "$runtime"
}
trap cleanup EXIT
archive="$CACHE/$PYTHON_ARCHIVE"
if [[ ! -f "$archive" ]]; then
    if [[ -f "$ROOT/cache/standalone/$PYTHON_ARCHIVE" ]]; then
        cp -- "$ROOT/cache/standalone/$PYTHON_ARCHIVE" "$archive"
    else
        curl -fL --retry 1 --connect-timeout 10 --max-time 180 "$PYTHON_URL" -o "$archive"
    fi
fi
printf '%s  %s\n' "$PYTHON_SHA256" "$archive" | sha256sum -c -
# A unique empty tree prevents extra files from an older build surviving extraction.
tar -xzf "$archive" -C "$runtime"
python="$runtime/python/bin/python3"
"$python" -I -B - "$ROOT" "$CACHE" <<'PY'
import hashlib,json,pathlib,shutil,subprocess,sys
root,cache=map(pathlib.Path,sys.argv[1:])
for item in json.loads((root/'packaging/tray-prototype/inputs.json').read_text()):
    target=cache/'inputs'/item['filename']
    if not target.exists():
        previous=root/'cache/tray-s2a/build/inputs'/item['filename']
        if previous.exists(): shutil.copy2(previous,target)
        else: subprocess.run(['curl','-fsSL','--retry','1','--connect-timeout','10','--max-time','120',item['url'],'-o',str(target)],check=True)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:
        raise SystemExit('Input hash mismatch: '+str(target))
PY
docker image inspect "$IMAGE" > "$CACHE/evidence/base-image-inspect.json" 2>/dev/null || {
    docker pull --platform linux/amd64 "$IMAGE"
    docker image inspect "$IMAGE" > "$CACHE/evidence/base-image-inspect.json"
}
container="remember-me-gui-build-$$"
docker create --name "$container" --platform linux/amd64 --network host \
    --mount "type=bind,src=$ROOT,dst=/repo,readonly" \
    --mount "type=bind,src=$CACHE,dst=/work" \
    --mount "type=bind,src=$runtime,dst=/portable,readonly" \
    --mount "type=bind,src=$CACHE/system-debs,dst=/var/cache/apt/archives" \
    --mount "type=bind,src=$OUTPUT,dst=/output" \
    --env "BUILD_UID=$(id -u)" --env "BUILD_GID=$(id -g)" \
    --env HTTP_PROXY --env HTTPS_PROXY --env http_proxy --env https_proxy \
    --entrypoint /bin/bash "$IMAGE" /repo/packaging/launcher/build_gui_native.sh > "$CACHE/evidence/container-id.txt"
docker start -a "$container"
[[ ! -e "$OUTPUT/.build-incomplete" ]] || { echo 'GUI build did not complete' >&2; exit 1; }
echo "Built Ubuntu22.04 GUI inputs: $OUTPUT"

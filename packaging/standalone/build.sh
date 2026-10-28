#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=runtime.env
source "$ROOT/packaging/standalone/runtime.env"
OUTPUT="$ROOT/dist/remember-me-standalone-linux-x86_64"
CACHE="$ROOT/cache/standalone"
MODEL_SOURCE=""
while (($#)); do
    case "$1" in
        --output|--cache-dir|--model-source)
            (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
            case "$1" in
                --output) OUTPUT="$2" ;;
                --cache-dir) CACHE="$2" ;;
                --model-source) MODEL_SOURCE="$2" ;;
            esac
            shift 2 ;;
        -h|--help)
            echo "Usage: bash packaging/standalone/build.sh [--output DIR] [--cache-dir DIR] [--model-source SNAPSHOT]"
            echo "Existing output is never overwritten. Omit --model-source to download the pinned manifest files."
            exit 0 ;;
        *) echo "Unknown build option: $1" >&2; exit 2 ;;
    esac
done
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
    echo "This build targets Linux x86_64 only" >&2; exit 1;
}
OUTPUT="$(realpath -m -- "$OUTPUT")"
CACHE="$(realpath -m -- "$CACHE")"
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || {
    echo "Refusing to overwrite existing output: $OUTPUT" >&2; exit 1;
}
[[ "$CACHE/" != "$OUTPUT/"* ]] || {
    echo "Build cache must be outside the output" >&2; exit 1;
}
for source_tree in app web; do
    for destination in "$OUTPUT" "$CACHE"; do
        [[ "$destination/" != "$ROOT/$source_tree/"* ]] || {
            echo "Output/cache must be outside source $source_tree/: $destination" >&2; exit 1;
        }
    done
done
if [[ -n "$MODEL_SOURCE" ]]; then
    MODEL_SOURCE="$(realpath -e -- "$MODEL_SOURCE")"
fi
unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV
unset PIP_INDEX_URL PIP_EXTRA_INDEX_URL PIP_FIND_LINKS PIP_NO_INDEX
unset PIP_TARGET PIP_PREFIX PIP_USER PIP_ROOT PIP_CONSTRAINT PIP_REQUIREMENT
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p -- "$CACHE" "$(dirname -- "$OUTPUT")"
mkdir -p -- "$CACHE/tmp" "$CACHE/metadata"
export TMPDIR="$CACHE/tmp" XDG_CACHE_HOME="$CACHE/metadata"
export HF_HOME="$CACHE/metadata/huggingface" TORCH_HOME="$CACHE/metadata/torch"
export HF_HUB_CACHE="$HF_HOME/hub" HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
unset SENTENCE_TRANSFORMERS_CACHE_DIR FASTEMBED_CACHE_DIR TRANSFORMERS_CACHE
ARCHIVE="$CACHE/$PYTHON_ARCHIVE"
fetch_verified() {
    local path="$CACHE/$1" url="$2" digest="$3"
    if [[ ! -f "$path" ]]; then
        curl --fail --location --retry 3 --output "$path.partial" "$url"
        printf '%s  %s\n' "$digest" "$path.partial" | sha256sum --check --status
        mv -- "$path.partial" "$path"
    fi
    printf '%s  %s\n' "$digest" "$path" | sha256sum --check
}
fetch_verified "$PYTHON_ARCHIVE" "$PYTHON_URL" "$PYTHON_SHA256"
fetch_verified "$PYTHON_LICENSE_FILE" "$PYTHON_LICENSE_URL" "$PYTHON_LICENSE_SHA256"
fetch_verified "$OPENSSL_LICENSE_FILE" "$OPENSSL_LICENSE_URL" "$OPENSSL_LICENSE_SHA256"
export PYTHON_LICENSE_URL PYTHON_LICENSE_SHA256 PYTHON_LICENSE_GIT_BLOB
export OPENSSL_LICENSE_URL OPENSSL_LICENSE_SHA256 OPENSSL_LICENSE_GIT_BLOB
mkdir -- "$OUTPUT"
touch "$OUTPUT/.build-incomplete"
trap 'echo "Build interrupted; incomplete output retained at: $OUTPUT" >&2' ERR
mkdir -- "$OUTPUT/runtime"
tar -xzf "$ARCHIVE" -C "$OUTPUT/runtime"
PYTHON="$OUTPUT/runtime/python/bin/python3"
"$PYTHON" -I "$ROOT/packaging/standalone/verify_runtime.py" --runtime "$OUTPUT/runtime/python"

# No resolution/compilation: install the existing production wheels and bootstrap lock.
"$PYTHON" -I -m pip install --require-hashes --only-binary=:all: --no-compile \
    --cache-dir "$CACHE/pip" -r "$ROOT/docker/pip-bootstrap.lock"
"$PYTHON" -I -m pip install --require-hashes --only-binary=:all: --no-compile \
    --cache-dir "$CACHE/pip" -r "$ROOT/requirements/docker-cpu-py312-linux-amd64.lock"
"$PYTHON" -I -m pip check

# Preserve the application's existing resource layout; copy no developer caches.
"$PYTHON" -I - "$ROOT" "$OUTPUT" "$CACHE/$PYTHON_LICENSE_FILE" "$CACHE/$OPENSSL_LICENSE_FILE" <<'PY'
from pathlib import Path
from hashlib import sha1
import os
import shutil
import sys
source, output = map(Path, sys.argv[1:3])
for name in ("app", "web"):
    shutil.copytree(source / name, output / name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
for name in ("release.jsonc", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(source / name, output / name)
shutil.copy2(source / "packaging/standalone/start.sh", output / "start.sh")
(output / "start.sh").chmod(0o755)
licenses = output / "licenses/runtime"
licenses.mkdir(parents=True)
for path, name, source_oid in (
        (Path(sys.argv[3]), "PBS-THIRD-PARTY.rst", os.environ["PYTHON_LICENSE_GIT_BLOB"]),
        (Path(sys.argv[4]), "OPENSSL-3-LICENSE.txt", os.environ["OPENSSL_LICENSE_GIT_BLOB"])):
    data = path.read_bytes()
    if sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != source_oid:
        raise SystemExit(f"Official license Git blob mismatch: {path.name}")
    shutil.copy2(path, licenses / name)
(licenses / "NOTICE.txt").write_text(
    "License texts from astral-sh/python-build-standalone release 20261020.\n"
    "PBS-THIRD-PARTY.rst is the upstream catalog, including components not used here;\n"
    "it is not an inventory of this runtime's linked components. Its legacy OpenSSL\n"
    "section does not describe the bundled OpenSSL 3: use OPENSSL-3-LICENSE.txt\n"
    "(Apache-2.0) for OpenSSL 3. CPython's own license remains at\n"
    "runtime/python/lib/python3.12/LICENSE.txt. Exact source URLs and integrity\n"
    "hashes are recorded in build-manifest.json.\n")
PY
"$PYTHON" -I "$ROOT/bin/verify_cpu_runtime.py" --standalone-root "$OUTPUT"
if [[ -n "$MODEL_SOURCE" ]]; then
    "$PYTHON" -I "$ROOT/docker/package_model.py" --source "$MODEL_SOURCE" --output "$OUTPUT/models"
else
    "$PYTHON" -I "$ROOT/docker/package_model.py" --download-cache "$CACHE/model" --output "$OUTPUT/models"
fi
TMPDIR="$CACHE/tmp" "$PYTHON" -I "$ROOT/bin/verify_cpu_runtime.py" \
    --standalone-root "$OUTPUT" --model-cache "$OUTPUT/models/sentence-transformers" \
    --expected-profile-id ep_6134c0b15d2bb471

# Record controlled inputs and the exact shipped app/resources, including dirty source.
"$PYTHON" -I - "$ROOT" "$OUTPUT" "$PYTHON_URL" "$PYTHON_SHA256" <<'PY'
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
root, output = map(Path, sys.argv[1:3])
def digest(path):
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()
def git(*args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).rstrip("\n")
inputs = ["packaging/standalone/build.sh", "packaging/standalone/start.sh",
          "packaging/standalone/runtime.env", "packaging/standalone/verify_runtime.py",
          "bin/verify_cpu_runtime.py", "docker/pip-bootstrap.lock",
          "requirements/docker-cpu-py312-linux-amd64.lock", "docker/package_model.py",
          "docker/bge-m3-manifest.json"]
resources = [p for directory in ("app", "web", "licenses") for p in (output / directory).rglob("*") if p.is_file()]
resources += [output / name for name in ("start.sh", "release.jsonc", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")]
model = json.loads((root / "docker/bge-m3-manifest.json").read_text())
manifest = {
    "format": 1, "built_at": datetime.now(timezone.utc).isoformat(),
    "source_commit": git("rev-parse", "HEAD"),
    "source_status": git("status", "--porcelain", "--untracked-files=normal").splitlines(),
    "source_diff_sha256": sha256(subprocess.check_output(["git", "-C", str(root), "diff", "HEAD", "--binary"])).hexdigest(),
    "platform": "linux-x86_64", "python_version": platform.python_version(),
    "python_source": {"url": sys.argv[3], "sha256": sys.argv[4]},
    "runtime_licenses": {
        "licenses/runtime/PBS-THIRD-PARTY.rst": {
            "url": os.environ["PYTHON_LICENSE_URL"],
            "sha256": os.environ["PYTHON_LICENSE_SHA256"],
            "git_blob": os.environ["PYTHON_LICENSE_GIT_BLOB"]},
        "licenses/runtime/OPENSSL-3-LICENSE.txt": {
            "url": os.environ["OPENSSL_LICENSE_URL"],
            "sha256": os.environ["OPENSSL_LICENSE_SHA256"],
            "git_blob": os.environ["OPENSSL_LICENSE_GIT_BLOB"]}},
    "input_sha256": {name: digest(root / name) for name in inputs},
    "packages": dict(sorted((d.metadata["Name"], d.version) for d in distributions())),
    "model": {"id": model["model"], "revision": model["revision"],
              "manifest_sha256": digest(output / "models/bge-m3-manifest.json")},
    "resource_sha256": {str(p.relative_to(output)): digest(p) for p in sorted(resources)},
}
(output / "build-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
PY
rm -- "$OUTPUT/.build-incomplete"
trap - ERR
printf 'Built: %s\n' "$OUTPUT"
du -sh -- "$OUTPUT"

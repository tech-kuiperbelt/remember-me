#!/usr/bin/env bash
# S2-A GUI-only experiment. Never installs packages into the host or service.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="$ROOT/packaging/tray-prototype"
OUTPUT="$ROOT/dist/remember-me-tray-s2a"
CACHE="$ROOT/cache/tray-s2a/build"
while (($#)); do
  case "$1" in
    --output) OUTPUT="$(realpath -m -- "$2")"; shift 2 ;;
    --cache-dir) CACHE="$(realpath -m -- "$2")"; shift 2 ;;
    *) echo "Usage: $0 [--output DIR] [--cache-dir DIR]" >&2; exit 2 ;;
  esac
done
[[ ! -e "$OUTPUT" ]] || { echo "Refusing existing output: $OUTPUT" >&2; exit 1; }
for executable in gcc pkg-config curl tar dpkg-deb sha256sum; do
  command -v "$executable" >/dev/null || { echo "Missing build tool: $executable" >&2; exit 1; }
done
pkg-config --exists glib-2.0 gobject-2.0 gio-2.0 cairo libffi
source "$ROOT/packaging/standalone/runtime.env"
mkdir -p "$CACHE/inputs" "$OUTPUT/runtime"
touch "$OUTPUT/.build-incomplete"
ARCHIVE="$CACHE/$PYTHON_ARCHIVE"
if [[ ! -f "$ARCHIVE" ]]; then
  if [[ -f "$ROOT/cache/standalone/$PYTHON_ARCHIVE" ]]; then
    cp -- "$ROOT/cache/standalone/$PYTHON_ARCHIVE" "$ARCHIVE"
  else
    curl -fL --retry 2 --connect-timeout 10 --max-time 180 "$PYTHON_URL" -o "$ARCHIVE"
  fi
fi
printf '%s  %s\n' "$PYTHON_SHA256" "$ARCHIVE" | sha256sum -c -
tar -xzf "$ARCHIVE" -C "$OUTPUT/runtime"
PYTHON="$OUTPUT/runtime/python/bin/python3"
mkdir -p "$OUTPUT/licenses/runtime"
for item in "$PYTHON_LICENSE_FILE" "$OPENSSL_LICENSE_FILE"; do
  if [[ "$item" == "$PYTHON_LICENSE_FILE" ]]; then
    url="$PYTHON_LICENSE_URL"; digest="$PYTHON_LICENSE_SHA256"
  else
    url="$OPENSSL_LICENSE_URL"; digest="$OPENSSL_LICENSE_SHA256"
  fi
  if [[ ! -f "$CACHE/$item" ]]; then
    if [[ -f "$ROOT/cache/standalone/$item" ]]; then
      cp -- "$ROOT/cache/standalone/$item" "$CACHE/$item"
    else
      curl -fsSL --retry 2 --connect-timeout 10 --max-time 90 "$url" -o "$CACHE/$item"
    fi
  fi
  printf '%s  %s\n' "$digest" "$CACHE/$item" | sha256sum -c -
  cp -- "$CACHE/$item" "$OUTPUT/licenses/runtime/"
done
unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PIP_CACHE_DIR="$CACHE/pip"
# PBS was built with clang; use the existing host compiler for extensions.
export CC=gcc CXX=g++
# Fixed official downloads; the manifest also retains upstream provenance.
"$PYTHON" -I -B - "$SOURCE/inputs.json" "$CACHE/inputs" <<'PY'
import hashlib,json,pathlib,subprocess,sys
for item in json.loads(pathlib.Path(sys.argv[1]).read_text()):
    target=pathlib.Path(sys.argv[2])/item['filename']
    if not target.exists():
        subprocess.run(['curl','-fsSL','--retry','2','--retry-all-errors','--connect-timeout','10','--max-time','90',item['url'],'-o',str(target)],check=True)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:
        raise SystemExit('Input hash mismatch: '+str(target))
PY
DEB="$CACHE/libgirepository-1.0-dev_1.80.1-1_amd64.deb"
if [[ ! -f "$DEB" ]]; then
  curl -fsSL --retry 2 --retry-all-errors --connect-timeout 10 --max-time 90 \
    https://archive.ubuntu.com/ubuntu/pool/main/g/gobject-introspection/libgirepository-1.0-dev_1.80.1-1_amd64.deb -o "$DEB"
fi
printf '%s  %s\n' 0abe9ac32fd2209ebcd8b4c7f3b1a780cc90bf41f125d02b34c4ce67e9407e1a "$DEB" | sha256sum -c -
PREFIX="$CACHE/gi-dev"
mkdir -p "$PREFIX"
dpkg-deb -x "$DEB" "$PREFIX"
# Build-only pkg-config metadata links the installed runtime SONAME directly.
"$PYTHON" -I -B - "$PREFIX" <<'PY'
import pathlib,sys
p=pathlib.Path(sys.argv[1])
lib=p/'usr/lib/x86_64-linux-gnu'
(lib/'pkgconfig').mkdir(parents=True,exist_ok=True)
# This Ubuntu split package has headers only. This explicit build-only .pc
# describes the extracted headers and already installed runtime; no source patch.
(lib/'pkgconfig/gobject-introspection-1.0.pc').write_text(
    f'Name: gobject-introspection\nDescription: S2-A isolated GI headers\nVersion: 1.80.1\n'
    f'Requires: gobject-2.0 gmodule-2.0 libffi\nLibs: -l:libgirepository-1.0.so.1\n'
    f'Cflags: -I{p}/usr/include/gobject-introspection-1.0\n')
PY
export PKG_CONFIG_PATH="$PREFIX/usr/lib/x86_64-linux-gnu/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
pkg-config --modversion gobject-introspection-1.0
TOOLS="$CACHE/tools"
mkdir -p "$TOOLS" "$CACHE/wheels" "$OUTPUT/vendor"
"$PYTHON" -I -B -m pip install --no-index --no-deps --require-hashes --no-compile \
  --upgrade --find-links "$CACHE/inputs" --target "$TOOLS" -r "$SOURCE/requirements-build.lock"
export PATH="$TOOLS/bin:$PATH"
export PYTHONPATH="$TOOLS:$OUTPUT/vendor"
"$PYTHON" -B -m pip wheel --no-cache-dir --no-index --no-deps --no-build-isolation \
  --wheel-dir "$CACHE/wheels" "$CACHE/inputs/pycairo-1.26.1.tar.gz"
"$PYTHON" -I -B -m pip install --no-index --no-deps --no-compile --target "$OUTPUT/vendor" "$CACHE"/wheels/pycairo-1.26.1-*.whl
"$PYTHON" -B -m pip wheel --no-cache-dir --no-index --no-deps --no-build-isolation \
  --wheel-dir "$CACHE/wheels" "$CACHE/inputs/pygobject-3.48.2.tar.gz"
"$PYTHON" -I -B -m pip install --no-index --no-deps --no-compile --target "$OUTPUT/vendor" "$CACHE"/wheels/pygobject-3.48.2-*.whl
"$PYTHON" -I -B -m pip install --no-index --no-deps --require-hashes --no-compile \
  --find-links "$CACHE/inputs" --target "$OUTPUT/vendor" -r "$SOURCE/requirements-runtime.lock"
unset PYTHONPATH PKG_CONFIG_PATH
PYTHONPATH="$OUTPUT/vendor" "$PYTHON" -B -m pip check
cp -- "$SOURCE/prototype.py" "$SOURCE/run.sh" "$SOURCE/README.md" "$OUTPUT/"
chmod 755 "$OUTPUT/run.sh"
cp -- "$SOURCE/inputs.json" "$SOURCE"/requirements-*.lock "$OUTPUT/"
"$PYTHON" -I -B - "$OUTPUT" "$CACHE" <<'PY'
import hashlib,importlib.metadata,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); cache=pathlib.Path(sys.argv[2])
sys.path.insert(0,str(root/'vendor'))
import gi
gi.require_version('Gtk','3.0'); gi.require_version('AyatanaAppIndicator3','0.1')
from gi.repository import Gtk,AyatanaAppIndicator3,GLib
manifest={'python':sys.version,'prefix':sys.prefix,'gi':gi.__file__,
          'gtk':[Gtk.get_major_version(),Gtk.get_minor_version(),Gtk.get_micro_version()],
          'glib':[GLib.MAJOR_VERSION,GLib.MINOR_VERSION,GLib.MICRO_VERSION],
          'packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions(path=[str(root/'vendor')])},
          'native_wheels':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (cache/'wheels').glob('*.whl')},
          'system_native_libraries':True,'desktop_gui_validated':False}
(root/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
PY
rm -- "$OUTPUT/.build-incomplete"
echo "GUI-only prototype assembled: $OUTPUT"

#!/usr/bin/env bash
# Internal half of build_gui.sh; paths are the isolated build container mounts.
set -euo pipefail
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 22.04 ]] || { echo 'GUI baseline must be Ubuntu22.04' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
export http_proxy="${http_proxy:-${HTTP_PROXY:-}}" https_proxy="${https_proxy:-${HTTPS_PROXY:-}}"
# Docker's stock apt hook deletes cached .deb files even when apt's retention
# option is set. Disable only that build-container hook before updating indices.
rm -f /etc/apt/apt.conf.d/docker-clean
apt_options=(-o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)
timeout 300 apt-get "${apt_options[@]}" update > /work/evidence/apt-update.log 2>&1
packages=(gcc pkg-config libcairo2-dev libgirepository1.0-dev libffi-dev gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1)
apt-get "${apt_options[@]}" --print-uris --yes install --no-install-recommends "${packages[@]}" > /work/evidence/apt-input-uris.txt
timeout 300 apt-get "${apt_options[@]}" -o Binary::apt-get::APT::Keep-Downloaded-Packages=true \
    install --yes --no-install-recommends "${packages[@]}" > /work/evidence/apt-install.log 2>&1
dpkg-query -W -f='${Package}\t${Version}\n' > /work/evidence/system-packages.tsv
find /var/cache/apt/archives -name '*.deb' -type f -exec sha256sum '{}' + > /work/evidence/system-deb-sha256.txt
cp /etc/os-release /work/evidence/os-release
cp /etc/apt/sources.list /work/evidence/apt-sources.list
gcc --version > /work/evidence/compiler.txt
pkg-config --modversion glib-2.0 cairo gobject-introspection-1.0 > /work/evidence/native-build-versions.txt
PYTHON=/portable/python/bin/python3
unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV LD_LIBRARY_PATH LD_PRELOAD GI_TYPELIB_PATH
export HOME=/work/home PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PIP_CACHE_DIR=/work/pip
export CC=gcc CXX=g++
mkdir -p /work/home /work/tools /work/wheels /output/vendor /output/sources
"$PYTHON" -I -B -m pip install --no-index --no-deps --require-hashes --no-compile --upgrade \
    --find-links /work/inputs --target /work/tools -r /repo/packaging/tray-prototype/requirements-build.lock
export PATH="/work/tools/bin:$PATH" PYTHONPATH=/work/tools:/output/vendor
# Remove old wheel outputs so an earlier Ubuntu24 build cannot be selected.
rm -f /work/wheels/pycairo-1.26.1-*.whl /work/wheels/pygobject-3.48.2-*.whl
"$PYTHON" -B -m pip wheel --no-index --no-cache-dir --no-deps --no-build-isolation \
    --wheel-dir /work/wheels /work/inputs/pycairo-1.26.1.tar.gz
"$PYTHON" -I -B -m pip install --no-index --no-deps --no-compile --target /output/vendor /work/wheels/pycairo-1.26.1-*.whl
"$PYTHON" -B -m pip wheel --no-index --no-cache-dir --no-deps --no-build-isolation \
    --wheel-dir /work/wheels /work/inputs/pygobject-3.48.2.tar.gz
"$PYTHON" -I -B -m pip install --no-index --no-deps --no-compile --target /output/vendor /work/wheels/pygobject-3.48.2-*.whl
"$PYTHON" -I -B -m pip install --no-index --no-deps --require-hashes --no-compile \
    --find-links /work/inputs --target /output/vendor -r /repo/packaging/tray-prototype/requirements-runtime.lock
PYTHONPATH=/output/vendor "$PYTHON" -B -m pip check
for name in inputs.json requirements-runtime.lock requirements-build.lock; do
    cp "/repo/packaging/tray-prototype/$name" "/output/$name"
done
cp /work/inputs/pygobject-3.48.2.tar.gz /output/sources/
"$PYTHON" -I -B - <<'PY'
import ctypes,hashlib,importlib.metadata,json,pathlib,re,subprocess,sys
root=pathlib.Path('/output'); cache=pathlib.Path('/work')
sys.path.insert(0,str(root/'vendor'))
import gi,cairo
gi.require_version('Gtk','3.0');gi.require_version('AyatanaAppIndicator3','0.1')
from gi.repository import Gtk,GLib,AyatanaAppIndicator3
glib_library=ctypes.CDLL('libglib-2.0.so.0')
glib_runtime=[ctypes.c_uint.in_dll(glib_library,'glib_'+part+'_version').value for part in ('major','minor','micro')]
extensions=list((root/'vendor/gi').glob('*.so'))+list((root/'vendor/cairo').glob('*.so'))
native={}
for path in extensions:
    symbols=subprocess.check_output(['readelf','--dyn-syms','--wide',str(path)],text=True)
    dynamic=subprocess.check_output(['readelf','-d',str(path)],text=True)
    libraries=subprocess.check_output(['ldd',str(path)],text=True)
    if any(s in symbols for s in ('g_assertion_message_cmpint','cairo_font_options_get_color_mode')):
        raise SystemExit('New-baseline API reference remains: '+str(path))
    if 'RPATH' in dynamic or 'RUNPATH' in dynamic or 'not found' in libraries:
        raise SystemExit('Invalid native search/dependency: '+str(path))
    native[str(path.relative_to(root))]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'dynamic':dynamic,'ldd':libraries,'undefined_symbols':[line.strip() for line in symbols.splitlines() if ' UND ' in line],
        'glibc_versions':sorted(set(re.findall(r'GLIBC_[0-9.]+',symbols)))}
image=json.loads((cache/'evidence/base-image-inspect.json').read_text())[0]
base_identity={key:image.get(key) for key in ('Id','RepoDigests','Parent','Architecture','Os','RootFS')}
deb_hashes={line.split(maxsplit=1)[1].rsplit('/',1)[-1]:line.split()[0] for line in
    (cache/'evidence/system-deb-sha256.txt').read_text().splitlines()}
manifest={'python':sys.version,'packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions(path=[str(root/'vendor')])},
    'native_wheels':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (cache/'wheels').glob('*.whl')},
    'build_baseline':'Ubuntu22.04','base_image':image['Id'],'base_image_identity':base_identity,
    'glib':glib_runtime,'glib_typelib_constants':[GLib.MAJOR_VERSION,GLib.MINOR_VERSION,GLib.MICRO_VERSION],
    'gtk':[Gtk.get_major_version(),Gtk.get_minor_version(),Gtk.get_micro_version()],'cairo':cairo.cairo_version_string(),
    'input_sha256':{str(p.relative_to(pathlib.Path('/repo'))):hashlib.sha256(p.read_bytes()).hexdigest() for p in
        [pathlib.Path('/repo/packaging/launcher/build_gui.sh'),pathlib.Path('/repo/packaging/launcher/build_gui_native.sh'),
         pathlib.Path('/repo/packaging/tray-prototype/inputs.json'),pathlib.Path('/repo/packaging/tray-prototype/requirements-build.lock'),
         pathlib.Path('/repo/packaging/tray-prototype/requirements-runtime.lock'),pathlib.Path('/repo/packaging/standalone/runtime.env')]},
    'system_packages':(cache/'evidence/system-packages.tsv').read_text().splitlines(),
    'compiler':(cache/'evidence/compiler.txt').read_text(),
    'system_deb_sha256':deb_hashes,
    'apt_sources':(cache/'evidence/apt-sources.list').read_text(),
    'apt_input_uris':(cache/'evidence/apt-input-uris.txt').read_text().splitlines(),
    'native_build_versions':(cache/'evidence/native-build-versions.txt').read_text().splitlines(),
    'native_evidence':native,'system_native_libraries':True,'desktop_gui_validated':False}
(root/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(cache/'evidence/native-import-and-abi.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'gui_import':'PASS','glib':manifest['glib'],'cairo':manifest['cairo'],'native_wheels':manifest['native_wheels']},indent=2))
PY
rm /output/.build-incomplete
chown -R "$BUILD_UID:$BUILD_GID" /output /work

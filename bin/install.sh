#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

VENV_DIR=".venv"
CPU_INDEX="https://download.pytorch.org/whl/cpu"
# Large wheels may arrive through a slow proxy; callers can override this value.
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"

case "$(uname -s)" in
    Linux) TORCH_INDEX="$CPU_INDEX" ;;
    Darwin) TORCH_INDEX="https://pypi.org/simple" ;;
    *) echo "Unsupported platform for this installer. Use Linux or macOS." >&2; exit 1 ;;
esac

python3 -c 'import sys; sys.exit("Python 3.10 or newer is required.") if sys.version_info < (3, 10) else None'

if [[ -e "$VENV_DIR" || -L "$VENV_DIR" ]]; then
    if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
        echo "Existing .venv is not functional. Preserve it and create a clean environment before retrying." >&2
        exit 1
    fi
else
    python3 -m venv "$VENV_DIR"
fi
PYTHON="$VENV_DIR/bin/python"

check_cpu_environment() {
    "$PYTHON" - "$1" <<'PY'
import sys
from importlib.metadata import distributions, distribution, PackageNotFoundError

if sys.version_info < (3, 10) or sys.prefix == sys.base_prefix:
    sys.exit("A project virtual environment with Python >=3.10 is required.")
packages = {d.metadata['Name'].lower().replace('_', '-') for d in distributions()}
gpu_packages = sorted(name for name in packages if name.startswith('nvidia-'))
if gpu_packages:
    sys.exit("Existing NVIDIA packages detected. Preserve .venv and create a clean CPU environment: "
             + ', '.join(gpu_packages))
try:
    installed = distribution('torch')
except PackageNotFoundError:
    if sys.argv[1] == 'allow-missing':
        sys.exit(0)
    sys.exit("Torch installation is missing.")
try:
    import torch
except Exception as exc:
    sys.exit(f"Existing Torch cannot be imported. Create a clean environment: {exc}")
if torch.version.cuda is not None or getattr(torch.version, 'hip', None) is not None:
    sys.exit("Existing GPU Torch detected. Preserve .venv and create a clean CPU environment.")
if sys.platform.startswith('linux') and '+cpu' not in installed.version:
    sys.exit("Linux Torch is not identified as an official CPU build. Create a clean CPU environment.")
print(f"CPU Torch: {installed.version}")
PY
}

# Check before installing anything into an existing environment.
check_cpu_environment allow-missing

# Ignore alternate index configuration for this step so CUDA candidates cannot
# enter the Torch selection. Standard HTTP(S) proxy variables remain available.
env -u PIP_INDEX_URL -u PIP_EXTRA_INDEX_URL -u PIP_FIND_LINKS -u PIP_NO_INDEX \
    -u PIP_TARGET -u PIP_PREFIX -u PIP_USER -u PIP_ROOT \
    PIP_CONFIG_FILE=/dev/null "$PYTHON" -m pip install \
    --only-binary=:all: --index-url "$TORCH_INDEX" 'torch>=2.6,<3'
check_cpu_environment required

CONSTRAINT_FILE="$(mktemp)"
trap 'rm -f "$CONSTRAINT_FILE"' EXIT
"$PYTHON" -c 'from importlib.metadata import version; print("torch==" + version("torch"))' > "$CONSTRAINT_FILE"
# Preserve the complete version, including +cpu, throughout dependency solving.
env -u PIP_TARGET -u PIP_PREFIX -u PIP_USER -u PIP_ROOT \
    PIP_CONFIG_FILE=/dev/null "$PYTHON" -m pip install \
    --constraint "$CONSTRAINT_FILE" -r requirements.txt
"$PYTHON" -m pip check
check_cpu_environment required
"$PYTHON" - "$CONSTRAINT_FILE" <<'PY'
import sys
from importlib.metadata import version
from pathlib import Path
assert Path(sys.argv[1]).read_text().strip() == 'torch==' + version('torch'), 'Torch version changed'
import sentence_transformers
print(f"Sentence Transformers: {sentence_transformers.__version__}")
PY

echo "CPU installation complete. Run: bash bin/start.sh"

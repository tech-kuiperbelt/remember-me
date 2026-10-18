#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

VENV_DIR=".venv"

check_venv_valid() {
    "$VENV_DIR/bin/python3" --version &>/dev/null && "$VENV_DIR/bin/pip" --version &>/dev/null
}

if [ -d "$VENV_DIR" ]; then
    if check_venv_valid; then
        echo "$VENV_DIR already exists and looks functional, skipping venv creation."
    else
        echo "WARNING: $VENV_DIR exists but seems broken (python/pip not working)."
        echo "This can happen if the venv was copied from another project."
        read -r -p "Delete and recreate $VENV_DIR? [Y/n] " answer
        case "${answer:-Y}" in
            [Yy]*)
                rm -rf "$VENV_DIR"
                python3 -m venv "$VENV_DIR"
                echo "Recreated $VENV_DIR"
                ;;
            *)
                echo "Proceeding with existing $VENV_DIR (may fail)."
                ;;
        esac
    fi
else
    python3 -m venv "$VENV_DIR"
    echo "Created $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install -r requirements.txt
echo "Done. Run bin/start.sh to start the server."

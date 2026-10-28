#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Use this distribution's interpreter and model, never a developer's .env or Python.
unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV
unset MEMORY_DEPLOYMENT_MODE REMEMBER_ME_INSTALL_METHOD
unset SENTENCE_TRANSFORMERS_CACHE_DIR FASTEMBED_CACHE_DIR
unset HF_HOME HF_HUB_CACHE HUGGINGFACE_HUB_CACHE TRANSFORMERS_CACHE TORCH_HOME
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export MEMORY_MODEL_DIR="$ROOT/models"
export MEMORY_STORAGE="${MEMORY_STORAGE:-dual}"
export EMBED_PROVIDER=sentence_transformers
export SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-m3 SENTENCE_TRANSFORMERS_DEVICE=cpu
# The pinned bin weights are complete; avoid an optional online conversion thread
# that can outlive the service and block foreground Ctrl+C shutdown.
export DISABLE_SAFETENSORS_CONVERSION=1
exec "$ROOT/runtime/python/bin/python3" -m app.server "$@"

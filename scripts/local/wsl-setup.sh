#!/usr/bin/env bash
# One-time WSL setup for the local GPU model services used by scripts/dev.mjs:
#   1) $BASE/.venv-vllm -> vLLM (HaS Text semantic NER on port 8080)
#   2) $BASE/.venv      -> Paddle PP-StructureV3 OCR wrapper (port 8082)
#   3) HaS Text HF bf16 weights downloaded through hf-mirror
#
# Safe to re-run: every step is skipped when already satisfied.
set -uo pipefail

BASE="${DATAINFRA_BASE:-$HOME/.cache/datainfra-redaction}"
REPO="${DATAINFRA_REPO:-/mnt/c/Work/WSY/DataInfra-RedactionEverything}"
PIP_MIRROR="${PIP_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
PADDLE_INDEX="${PADDLE_INDEX:-https://www.paddlepaddle.org.cn/packages/stable/cu129/}"
VLLM_SPEC="${VLLM_SPEC:-vllm==0.19.1}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
HAS_MODEL_DIR="$BASE/models/HaS_Text_0209_0.6B"

log() { printf '\n[setup] %s %s\n' "$(date +%H:%M:%S)" "$*"; }

log "base=$BASE repo=$REPO python=$(python3 -V)"
mkdir -p "$BASE"

# ---------- 1) vLLM venv (serves HaS Text) ----------
if [ -x "$BASE/.venv-vllm/bin/vllm" ]; then
  log "venv-vllm already installed: $("$BASE/.venv-vllm/bin/vllm" --version 2>/dev/null | tail -1)"
else
  log "creating venv-vllm and installing $VLLM_SPEC"
  python3 -m venv "$BASE/.venv-vllm"
  "$BASE/.venv-vllm/bin/pip" install -q -U -i "$PIP_MIRROR" pip setuptools wheel
  "$BASE/.venv-vllm/bin/pip" install -i "$PIP_MIRROR" "$VLLM_SPEC" ||
    "$BASE/.venv-vllm/bin/pip" install -i "$PIP_MIRROR" vllm ||
    { log "FAILED vllm install"; exit 1; }
fi

# ---------- 2) App / OCR venv (PP-StructureV3 wrapper) ----------
if "$BASE/.venv/bin/python" -c "import paddleocr, fastapi, uvicorn" >/dev/null 2>&1; then
  log "app/ocr venv already installed"
else
  log "creating app venv and installing backend/requirements-ocr.lock"
  python3 -m venv "$BASE/.venv"
  "$BASE/.venv/bin/pip" install -q -U -i "$PIP_MIRROR" pip setuptools wheel
  "$BASE/.venv/bin/pip" install -i "$PIP_MIRROR" --extra-index-url "$PADDLE_INDEX" \
    -r "$REPO/backend/requirements-ocr.lock" || { log "FAILED ocr deps install"; exit 1; }
  # ocr_server.py imports app.* helpers through PYTHONPATH=backend.
  "$BASE/.venv/bin/pip" install -i "$PIP_MIRROR" nvidia-ml-py python-dotenv || true
fi

# ---------- 3) HaS Text HF weights ----------
if [ -f "$HAS_MODEL_DIR/config.json" ]; then
  log "HaS model already present: $HAS_MODEL_DIR"
else
  log "downloading $HAS_MODEL_REPO via $HF_ENDPOINT"
  HAS_MODEL_DIR="$HAS_MODEL_DIR" HF_ENDPOINT="$HF_ENDPOINT" \
    "$BASE/.venv-vllm/bin/python" - <<'PY' || { log "FAILED model download"; exit 1; }
import os
from huggingface_hub import snapshot_download

dst = os.environ["HAS_MODEL_DIR"]
path = snapshot_download("xuanwulab/HaS_Text_0209_0.6B", local_dir=dst, max_workers=8)
print("model at", path)
PY
fi

log "done"
echo "VENV_DIR=$BASE/.venv"
echo "VLLM_VENV_DIR=$BASE/.venv-vllm"
echo "HAS_TEXT_HF_MODEL_PATH=$HAS_MODEL_DIR"

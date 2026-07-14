#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${PORT:-8501}"
DATA_DIR="${CS2_DATA_DIR:-/data/cs2_engine}"

# Railway volumes are normally mounted at /data. Local runs may not have it.
mkdir -p "${DATA_DIR}" 2>/dev/null || true

# Fail early with a clear deployment log if the uploaded file is invalid.
python -m py_compile app.py

exec python -m streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port="${APP_PORT}" \
  --server.headless=true \
  --browser.gatherUsageStats=false

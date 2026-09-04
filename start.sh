#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${PORT:-8501}"
DATA_DIR="${CS2_DATA_DIR:-/data/cs2_engine}"

# Full-data settings. These control collection/gating only; projection math is untouched.
export CS2_DATA_DIR="${DATA_DIR}"
export CS2_ASSISTED_OFFICIAL="${CS2_ASSISTED_OFFICIAL:-false}"
export CS2_AUTO_HARVEST_HISTORY="${CS2_AUTO_HARVEST_HISTORY:-true}"
export CS2_COLLECT_PROJECTIONS="${CS2_COLLECT_PROJECTIONS:-true}"
export CS2_AUTO_GRADE="${CS2_AUTO_GRADE:-true}"
export CS2_DEEP_DATA="${CS2_DEEP_DATA:-true}"
export CS2_BO3_PROFILES_PER_REFRESH="${CS2_BO3_PROFILES_PER_REFRESH:-180}"
export CS2_AUTOFEED_DIRECT_PROFILE_BATCH="${CS2_AUTOFEED_DIRECT_PROFILE_BATCH:-60}"
export CS2_AUTOFEED_DIRECT_WORKERS="${CS2_AUTOFEED_DIRECT_WORKERS:-4}"
export CS2_EMBEDDED_COLLECTOR="${CS2_EMBEDDED_COLLECTOR:-true}"

mkdir -p "${DATA_DIR}" 2>/dev/null || true

# Web health must not depend on a data-provider patch or recovery request.
# The committed app is syntax-checked and Streamlit starts immediately.
python -m py_compile app.py collector.py

# Full-gas data collection runs independently after the web server has had time
# to become healthy. collector.py patches an isolated temporary copy of app.py,
# never the live Streamlit source file.
if [[ "${CS2_EMBEDDED_COLLECTOR}" =~ ^(1|true|TRUE|True|yes|YES|Yes|on|ON|On)$ ]]; then
  (
    sleep 60
    while true; do
      python collector.py || true
      sleep 600
    done
  ) &
fi

exec python -m streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port="${APP_PORT}" \
  --server.headless=true \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false

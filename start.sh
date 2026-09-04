#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${PORT:-8501}"
DATA_DIR="${CS2_DATA_DIR:-/data/cs2_engine}"

# Full verified-data settings. These change data acquisition/gating only.
export PYTHONUNBUFFERED=1
export CS2_DATA_DIR="${DATA_DIR}"
export CS2_ASSISTED_OFFICIAL="${CS2_ASSISTED_OFFICIAL:-false}"
export CS2_AUTO_HARVEST_HISTORY="${CS2_AUTO_HARVEST_HISTORY:-true}"
export CS2_COLLECT_PROJECTIONS="${CS2_COLLECT_PROJECTIONS:-true}"
export CS2_AUTO_GRADE="${CS2_AUTO_GRADE:-true}"
export CS2_DEEP_DATA="${CS2_DEEP_DATA:-true}"
export CS2_BO3_PROFILES_PER_REFRESH="${CS2_BO3_PROFILES_PER_REFRESH:-180}"
export CS2_AUTOFEED_DIRECT_PROFILE_BATCH="${CS2_AUTOFEED_DIRECT_PROFILE_BATCH:-60}"
export CS2_AUTOFEED_DIRECT_WORKERS="${CS2_AUTOFEED_DIRECT_WORKERS:-4}"
export CS2_HLTV_BATCH_PAGES="${CS2_HLTV_BATCH_PAGES:-12}"
export CS2_HLTV_BATCH_WORKERS="${CS2_HLTV_BATCH_WORKERS:-4}"
export CS2_BRIDGE_REPO="${CS2_BRIDGE_REPO:-hernandezjh235-sudo/cS2}"
export CS2_BRIDGE_BRANCH="${CS2_BRIDGE_BRANCH:-data-cache}"
export CS2_EMBEDDED_COLLECTOR="${CS2_EMBEDDED_COLLECTOR:-true}"

mkdir -p "${DATA_DIR}" 2>/dev/null || true

# Build an isolated verified-data web runtime. If any overlay ever fails,
# prepare_web_app.py falls back to the known-good committed app.py so Railway
# health is not held hostage by the data layer.
WEB_APP_PATH="$(python prepare_web_app.py | tail -n 1)"
python -m py_compile "${WEB_APP_PATH}"

# Data collection is independent from web health. GitHub is the portable cache;
# Railway /data is the live persistent store. v5.5 merges both directions
# non-destructively, including SQLite projection/grading/audit rows.
if [[ "${CS2_EMBEDDED_COLLECTOR}" =~ ^(1|true|TRUE|True|yes|YES|Yes|on|ON|On)$ ]]; then
  (
    sleep 8
    python github_cache_sync_v55.py pull --data-dir "${DATA_DIR}" --repo "${CS2_BRIDGE_REPO}" --branch "${CS2_BRIDGE_BRANCH}" || true
    sleep 7
    while true; do
      python collector_v55.py || true
      sleep 585
      python github_cache_sync_v55.py pull --data-dir "${DATA_DIR}" --repo "${CS2_BRIDGE_REPO}" --branch "${CS2_BRIDGE_BRANCH}" || true
      sleep 15
    done
  ) &
fi

exec python -m streamlit run "${WEB_APP_PATH}" \
  --server.address=0.0.0.0 \
  --server.port="${APP_PORT}" \
  --server.headless=true \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false

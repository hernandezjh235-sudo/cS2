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
export CS2_AUTOFEED_DIRECT_PROFILE_BATCH="${CS2_AUTOFEED_DIRECT_PROFILE_BATCH:-120}"
export CS2_AUTOFEED_DIRECT_WORKERS="${CS2_AUTOFEED_DIRECT_WORKERS:-4}"
export CS2_HLTV_BATCH_PAGES="${CS2_HLTV_BATCH_PAGES:-12}"
export CS2_HLTV_BATCH_WORKERS="${CS2_HLTV_BATCH_WORKERS:-4}"
export CS2_BRIDGE_REPO="${CS2_BRIDGE_REPO:-hernandezjh235-sudo/cS2}"
export CS2_BRIDGE_BRANCH="${CS2_BRIDGE_BRANCH:-data-cache}"
export CS2_EMBEDDED_COLLECTOR="${CS2_EMBEDDED_COLLECTOR:-true}"
export CS2_WEB_FAST_REFRESH="${CS2_WEB_FAST_REFRESH:-true}"
# Browser refreshes stay cache-first/fast. The background collector explicitly
# overrides this to true for verified provider recovery.
export CS2_WEB_ALLOW_PROVIDER_NETWORK="${CS2_WEB_ALLOW_PROVIDER_NETWORK:-false}"
export CS2_COLLECTOR_INTERVAL_SECONDS="${CS2_COLLECTOR_INTERVAL_SECONDS:-180}"

mkdir -p "${DATA_DIR}" 2>/dev/null || true

# Build an isolated verified-data web runtime. If any overlay ever fails,
# prepare_web_app.py falls back to the known-good committed app.py so Railway
# health is not held hostage by the data layer.
WEB_APP_PATH="$(python prepare_web_app.py | tail -n 1)"
python -m py_compile "${WEB_APP_PATH}"

# Web refreshes are cache-first. The collector owns slow provider work so the
# browser does not hang while profiles, identities, maps and rosters fill.
# IMPORTANT: the GitHub data-cache branch is only a cold-start bootstrap.
# Once Railway's persistent volume has real data, never poll an older remote
# snapshot back into the live volume. The Railway volume + collector are the
# freshness authority until GitHub successfully publishes a newer cache.
if [[ "${CS2_EMBEDDED_COLLECTOR}" =~ ^(1|true|TRUE|True|yes|YES|Yes|on|ON|On)$ ]]; then
  (
    sleep 2
    if [[ ! -s "${DATA_DIR}/cs2_provider_cache.json" && ! -s "${DATA_DIR}/player_database.json" ]]; then
      python github_cache_sync_v582.py pull --data-dir "${DATA_DIR}" --repo "${CS2_BRIDGE_REPO}" --branch "${CS2_BRIDGE_BRANCH}" || true
    fi
    while true; do
      CS2_WEB_ALLOW_PROVIDER_NETWORK=true \
      CS2_AUTOFEED_DIRECT_PROFILE_BATCH="${CS2_AUTOFEED_DIRECT_PROFILE_BATCH}" \
      python collector_v55.py || true
      sleep "${CS2_COLLECTOR_INTERVAL_SECONDS}"
    done
  ) &
fi

exec python -m streamlit run "${WEB_APP_PATH}" \
  --server.address=0.0.0.0 \
  --server.port="${APP_PORT}" \
  --server.headless=true \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false

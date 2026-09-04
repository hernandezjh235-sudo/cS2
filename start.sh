#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${PORT:-8501}"
DATA_DIR="${CS2_DATA_DIR:-/data/cs2_engine}"

# Shared Railway-volume defaults. These control data acquisition / gating only;
# they do not alter the protected projection math.
export CS2_DATA_DIR="${DATA_DIR}"
export CS2_ASSISTED_OFFICIAL="${CS2_ASSISTED_OFFICIAL:-false}"
export CS2_AUTO_HARVEST_HISTORY="${CS2_AUTO_HARVEST_HISTORY:-true}"
export CS2_COLLECT_PROJECTIONS="${CS2_COLLECT_PROJECTIONS:-true}"
export CS2_AUTO_GRADE="${CS2_AUTO_GRADE:-true}"
export CS2_DEEP_DATA="${CS2_DEEP_DATA:-true}"
export CS2_BO3_PROFILES_PER_REFRESH="${CS2_BO3_PROFILES_PER_REFRESH:-180}"

# Railway volumes are normally mounted at /data. Local runs may not have it.
mkdir -p "${DATA_DIR}" 2>/dev/null || true

# Apply the small idempotent data/identity/persistence patch before launch.
if [[ -f "autofeed_patch.py" ]]; then
  python autofeed_patch.py app.py
fi

# Fail early with a clear deployment log if the source is invalid.
python -m py_compile app.py

exec python -m streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port="${APP_PORT}" \
  --server.headless=true \
  --browser.gatherUsageStats=false

"""OneWayPickz CS2 v5.5.1 collector entrypoint.

Keeps protected projection math untouched while adding the complete verified-data
readiness overlays and exporting current roster context into the GitHub cache.
"""
from __future__ import annotations

import collector as base
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for patch in [ROOT / "autofeed_readiness_v55.py", ROOT / "autofeed_identity_v551.py"]:
    if patch not in base.PATCH_PATHS:
        base.PATCH_PATHS.append(patch)

_base_bridge_match_from_row = base._bridge_match_from_row

def _bridge_match_from_row(ns: dict, row: dict):
    rec = _base_bridge_match_from_row(ns, row)
    if not rec:
        return rec
    rec["lineup_groups"] = list(row.get("confirmed_lineup_groups") or row.get("lineup_groups") or [])
    rec["lineup_names"] = list(row.get("confirmed_lineup_names") or row.get("lineup_names") or [])
    rec["provider_team_verified"] = bool(row.get("provider_team_verified"))
    rec["identity_official_ready"] = bool(row.get("identity_official_ready"))
    rec["projection_data_ready"] = bool(row.get("projection_data_ready"))
    rec["official_data_ready"] = bool(row.get("official_data_ready"))
    rec["data_readiness_score"] = row.get("data_readiness_score")
    rec["source_freshness"] = row.get("source_freshness") or {}
    return rec

base._bridge_match_from_row = _bridge_match_from_row

if __name__ == "__main__":
    raise SystemExit(base.run_locked())

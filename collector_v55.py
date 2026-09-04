"""OneWayPickz CS2 v5.6.2 collector entrypoint.

Keeps protected projection math untouched while adding the complete verified-data
readiness overlays, real BO3 match recovery, durable current-team exports, and
final pre-model identity carry through all board wrappers.
"""
from __future__ import annotations

import collector as base
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for patch in [
    ROOT / "autofeed_readiness_v55.py",
    ROOT / "autofeed_identity_v551.py",
    ROOT / "autofeed_production_v56.py",
    ROOT / "autofeed_identity_v562.py",
]:
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

_base_export_provider_bridge = base.export_provider_bridge


def export_provider_bridge(ns: dict, board: list[dict], previous: dict | None = None) -> dict:
    bridge = dict(_base_export_provider_bridge(ns, board, previous) or {})
    profiles = dict(bridge.get("profiles") or {})
    teams = dict(bridge.get("teams") or {})

    try:
        player_db = ns["load_json"](ns["PLAYER_DATABASE_FILE"], {}) or {}
    except Exception:
        player_db = {}
    try:
        team_db = ns["load_json"](ns["TEAM_DATABASE_FILE"], {}) or {}
    except Exception:
        team_db = {}

    for key, rec in list(profiles.items()):
        dbrec = player_db.get(key) if isinstance(player_db, dict) else None
        if not isinstance(dbrec, dict):
            continue
        team = str(dbrec.get("team") or "").strip()
        if team:
            merged = dict(rec or {})
            merged["team"] = team
            merged["provider_team_verified"] = bool(dbrec.get("provider_team_verified", True))
            merged["identity_verified_at"] = dbrec.get("identity_verified_at")
            merged["identity_verified_source"] = dbrec.get("identity_verified_source") or "v5.6 collector"
            profiles[key] = merged

    if isinstance(team_db, dict):
        for key, rec in team_db.items():
            if isinstance(rec, dict) and str(rec.get("team") or "").strip():
                teams[key] = {**dict(teams.get(key) or {}), **rec}

    if callable(ns.get("_v49_build_team_index")):
        try:
            for key, rec in (ns["_v49_build_team_index"](profiles) or {}).items():
                teams[key] = {**dict(teams.get(key) or {}), **dict(rec or {})}
        except Exception:
            pass

    bridge["schema_version"] = max(8, int(bridge.get("schema_version") or 0))
    bridge["profiles"] = profiles
    bridge["teams"] = teams
    status = dict(bridge.get("source_status") or {})
    status.update({
        "autofeed_version": "5.6.2",
        "verified_profile_count": len(profiles),
        "team_count": len(teams),
        "match_count": len(bridge.get("matches") or []),
        "verified_team_profiles": sum(bool((x or {}).get("team")) for x in profiles.values()),
    })
    bridge["source_status"] = status

    path = Path(str(ns.get("V48_BRIDGE_LOCAL_FILE") or (Path(ns["STORAGE_DIR"]) / "cs2_provider_cache.json")))
    ns["save_json"](str(path), bridge, force=True)
    seed = ns.get("_v54_seed_databases_from_bridge")
    if callable(seed):
        try:
            bridge["source_status"]["database_seed_v56"] = seed(bridge)
            ns["save_json"](str(path), bridge, force=True)
        except Exception as exc:
            bridge["source_status"]["database_seed_v56_warning"] = str(exc)
    return bridge


base.export_provider_bridge = export_provider_bridge

if __name__ == "__main__":
    raise SystemExit(base.run_locked())

"""OneWayPickz CS2 v5.8.7 collector entrypoint.

Keeps protected projection math untouched while adding verified profile recovery,
real match/player IDs, five-player roster context, durable identity persistence,
strict CS2-only source classification, complete live-line visibility, pre-model
context handoff, full provider/deep-data recovery, pregame freeze, grading, and learning.
"""
from __future__ import annotations

import os
import collector as base
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for patch in [
    ROOT / "autofeed_readiness_v55.py",
    ROOT / "autofeed_identity_v551.py",
    ROOT / "autofeed_production_v56.py",
    ROOT / "autofeed_identity_v562.py",
    ROOT / "autofeed_context_v57.py",
    ROOT / "autofeed_liveboard_v58.py",
    ROOT / "autofeed_webfast_v581.py",
    ROOT / "autofeed_liveboard_v582.py",
    ROOT / "autofeed_liveboard_v583.py",
    ROOT / "autofeed_liveboard_v584.py",
    ROOT / "autofeed_premodel_v585.py",
    ROOT / "autofeed_verified_v586.py",
    ROOT / "autofeed_completion_v587.py",
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
    rec["identity_ids"] = dict(row.get("identity_ids") or {})
    rec["source_identity_ids"] = dict(row.get("source_identity_ids") or {})
    rec["source_lineup_groups"] = list(row.get("source_lineup_groups") or [])
    rec["source_roster_names"] = list(row.get("source_roster_names") or [])
    rec["source_match_verified"] = bool(row.get("source_match_verified"))
    rec["current_roster_names"] = list(row.get("current_roster_names") or [])
    rec["current_roster_verified"] = bool(row.get("current_roster_verified"))
    rec["player_in_lineup"] = bool(row.get("player_in_lineup"))
    rec["lineup_verified"] = bool(row.get("lineup_verified"))
    rec["v585_premodel_context"] = bool(row.get("v585_premodel_context"))
    rec["v586_premodel_context"] = bool(row.get("v586_premodel_context"))
    rec["v587_provider_context"] = bool(row.get("v587_provider_context"))
    rec["provider_match_url"] = str(row.get("provider_match_url") or "")
    rec["provider_match_id"] = str(row.get("provider_match_id") or (row.get("identity_ids") or {}).get("match_id") or rec.get("provider_match_id") or "")
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
            merged["identity_verified_source"] = dbrec.get("identity_verified_source") or "v5.8.7 collector"
            if dbrec.get("player_id") and not merged.get("player_id"):
                merged["player_id"] = dbrec.get("player_id")
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

    bridge["schema_version"] = max(12, int(bridge.get("schema_version") or 0))
    bridge["profiles"] = profiles
    bridge["teams"] = teams
    status = dict(bridge.get("source_status") or {})
    status.update({
        "autofeed_version": "5.8.7",
        "verified_profile_count": len(profiles),
        "team_count": len(teams),
        "match_count": len(bridge.get("matches") or []),
        "verified_team_profiles": sum(bool((x or {}).get("team")) for x in profiles.values()),
        "exact_id_rows": sum(bool(((x or {}).get("identity_ids") or {}).get("match_id") and ((x or {}).get("identity_ids") or {}).get("player_id")) for x in board),
        "five_player_lineup_rows": sum(len(list((x or {}).get("current_roster_names") or [])) == 5 for x in board),
        "source_exact_match_rows": sum(bool((x or {}).get("source_match_verified")) for x in board),
        "source_five_player_rows": sum(bool((x or {}).get("source_five_player_lineup")) for x in board),
        "premodel_context_rows": sum(bool((x or {}).get("v586_premodel_context") or (x or {}).get("v585_premodel_context")) for x in board),
        "provider_context_rows": sum(bool((x or {}).get("v587_provider_context")) for x in board),
        "real_provider_match_rows": sum(str((x or {}).get("match_url") or "").startswith(("bo3://", "pandascore://", "https://bo3.gg/", "https://www.hltv.org/")) for x in board),
        "deep_team_map_rows": sum((int((x or {}).get("team_recent_maps") or 0) > 0 and int((x or {}).get("opponent_mapstats_samples") or 0) > 0) for x in board),
        "projection_ready_rows": sum(bool((x or {}).get("projection_data_ready")) for x in board),
        "official_ready_rows": sum(bool((x or {}).get("official_data_ready")) for x in board),
        "non_cs2_rows_visible": sum(not bool(ns.get("_v586_is_cs2", lambda _: True)(x)) for x in board),
    })
    try:
        context = ns["load_json"](ns.get("V57_CONTEXT_HEALTH_FILE"), {}) if ns.get("V57_CONTEXT_HEALTH_FILE") else {}
        readiness = ns["load_json"](ns.get("V55_READINESS_FILE"), {}) if ns.get("V55_READINESS_FILE") else {}
        if isinstance(context, dict):
            status["context_health_version"] = context.get("version")
            status["real_provider_match_rows"] = int(context.get("real_provider_match_rows") or status.get("real_provider_match_rows") or 0)
            status["real_source_match_rows"] = int(context.get("real_source_match_rows") or 0)
            status["verified_profile_rows"] = int(context.get("verified_profile_rows") or 0)
            status["core_kpr_rows"] = int(context.get("core_kpr_rows") or 0)
        if isinstance(readiness, dict):
            status["readiness_version"] = readiness.get("version")
            status["missing_projection_requirements"] = dict(readiness.get("missing_projection_requirements") or {})
    except Exception:
        pass
    bridge["source_status"] = status

    path = Path(str(ns.get("V48_BRIDGE_LOCAL_FILE") or (Path(ns["STORAGE_DIR"]) / "cs2_provider_cache.json")))
    ns["save_json"](str(path), bridge, force=True)
    seed = ns.get("_v54_seed_databases_from_bridge")
    if callable(seed):
        try:
            bridge["source_status"]["database_seed_v587"] = seed(bridge)
            ns["save_json"](str(path), bridge, force=True)
        except Exception as exc:
            bridge["source_status"]["database_seed_v587_warning"] = str(exc)
    return bridge


base.export_provider_bridge = export_provider_bridge


def _force_fresh_cycle() -> None:
    """The process lock is authoritative; a stale success heartbeat must not block a new live-line cycle."""
    data_dir = Path(os.getenv("CS2_DATA_DIR", "/data/cs2_engine"))
    try:
        (data_dir / ".autofeed_collector.heartbeat").unlink(missing_ok=True)
    except Exception:
        pass


if __name__ == "__main__":
    _force_fresh_cycle()
    raise SystemExit(base.run_locked())

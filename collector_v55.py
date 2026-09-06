"""OneWayPickz CS2 v5.9.3 collector entrypoint.

Protected projection math stays untouched. This collector loads the verified
source-identity, provider, profile, team/map, freeze, grading, and persistence
layers used by the production app.
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
    ROOT / "autofeed_completion_v588.py",
    ROOT / "autofeed_handoff_v589.py",
    ROOT / "autofeed_provider_v590.py",
    ROOT / "autofeed_hltv_v591.py",
    ROOT / "autofeed_profiles_v592.py",
    ROOT / "autofeed_maps_v593.py",
]:
    if patch not in base.PATCH_PATHS:
        base.PATCH_PATHS.append(patch)

_base_bridge_match_from_row = base._bridge_match_from_row


def _bridge_match_from_row(ns: dict, row: dict):
    rec = _base_bridge_match_from_row(ns, row)
    if not rec: return rec
    for key in ["provider_team_verified","identity_official_ready","projection_data_ready","official_data_ready","source_match_verified","source_team_name_verified","source_identity_verified_v588","current_roster_verified","player_in_lineup","lineup_verified","v585_premodel_context","v586_premodel_context","v587_provider_context","v588_premodel_context"]:
        rec[key]=bool(row.get(key))
    for key in ["lineup_groups","lineup_names","source_lineup_groups","source_roster_names","current_roster_names"]:
        source={"lineup_groups":"confirmed_lineup_groups","lineup_names":"confirmed_lineup_names"}.get(key,key)
        rec[key]=list(row.get(source) or row.get(key) or [])
    rec["data_readiness_score"]=row.get("data_readiness_score")
    rec["source_freshness"]=row.get("source_freshness") or {}
    rec["identity_ids"]=dict(row.get("identity_ids") or {})
    rec["source_identity_ids"]=dict(row.get("source_identity_ids") or {})
    rec["source_team_name"]=str(row.get("source_team_name") or "")
    rec["source_opponent_name"]=str(row.get("source_opponent_name") or "")
    rec["provider_match_url"]=str(row.get("provider_match_url") or "")
    rec["provider_match_id"]=str(row.get("provider_match_id") or (row.get("identity_ids") or {}).get("match_id") or rec.get("provider_match_id") or "")
    return rec


base._bridge_match_from_row = _bridge_match_from_row
_base_export_provider_bridge = base.export_provider_bridge


def export_provider_bridge(ns: dict, board: list[dict], previous: dict | None = None) -> dict:
    bridge=dict(_base_export_provider_bridge(ns,board,previous) or {})
    profiles=dict(bridge.get("profiles") or {}); teams=dict(bridge.get("teams") or {})
    try: player_db=ns["load_json"](ns["PLAYER_DATABASE_FILE"],{}) or {}
    except Exception: player_db={}
    try: team_db=ns["load_json"](ns["TEAM_DATABASE_FILE"],{}) or {}
    except Exception: team_db={}
    for key,rec in list(profiles.items()):
        dbrec=player_db.get(key) if isinstance(player_db,dict) else None
        if not isinstance(dbrec,dict): continue
        team=str(dbrec.get("team") or "").strip()
        if team:
            merged=dict(rec or {}); merged["team"]=team; merged["provider_team_verified"]=bool(dbrec.get("provider_team_verified",True)); merged["identity_verified_at"]=dbrec.get("identity_verified_at"); merged["identity_verified_source"]=dbrec.get("identity_verified_source") or "v5.9.3 collector"
            if dbrec.get("player_id") and not merged.get("player_id"): merged["player_id"]=dbrec.get("player_id")
            profiles[key]=merged
    if isinstance(team_db,dict):
        for key,rec in team_db.items():
            if isinstance(rec,dict) and str(rec.get("team") or "").strip(): teams[key]={**dict(teams.get(key) or {}),**rec}
    if callable(ns.get("_v49_build_team_index")):
        try:
            for key,rec in (ns["_v49_build_team_index"](profiles) or {}).items(): teams[key]={**dict(teams.get(key) or {}),**dict(rec or {})}
        except Exception: pass
    bridge["schema_version"]=max(17,int(bridge.get("schema_version") or 0)); bridge["profiles"]=profiles; bridge["teams"]=teams
    status=dict(bridge.get("source_status") or {})
    status.update({
        "autofeed_version":"5.9.3","verified_profile_count":len(profiles),"team_count":len(teams),"match_count":len(bridge.get("matches") or []),"verified_team_profiles":sum(bool((x or {}).get("team")) for x in profiles.values()),
        "exact_id_rows":sum(bool(((x or {}).get("identity_ids") or {}).get("match_id") and ((x or {}).get("identity_ids") or {}).get("player_id")) for x in board),
        "exact_source_identity_rows":sum(bool((x or {}).get("source_identity_verified_v588")) for x in board),
        "five_player_lineup_rows":sum(len(list((x or {}).get("current_roster_names") or []))==5 for x in board),
        "source_exact_match_rows":sum(bool((x or {}).get("source_match_verified")) for x in board),"source_five_player_rows":sum(bool((x or {}).get("source_five_player_lineup")) for x in board),
        "premodel_context_rows":sum(bool((x or {}).get("v588_premodel_context") or (x or {}).get("v586_premodel_context") or (x or {}).get("v585_premodel_context")) for x in board),
        "provider_context_rows":sum(bool((x or {}).get("v587_provider_context")) for x in board),
        "real_provider_match_rows":sum(str((x or {}).get("provider_match_url") or (x or {}).get("match_url") or "").startswith(("bo3://","pandascore://","https://bo3.gg/","https://www.hltv.org/")) for x in board),
        "deep_team_map_rows":sum(int((x or {}).get("team_recent_maps") or 0)>0 and int((x or {}).get("opponent_mapstats_samples") or 0)>0 for x in board),
        "projection_ready_rows":sum(bool((x or {}).get("projection_data_ready")) for x in board),"official_ready_rows":sum(bool((x or {}).get("official_data_ready")) for x in board),
        "freeze_candidate_rows":sum(bool((x or {}).get("projection_data_ready") and (x or {}).get("lean") in {"OVER","UNDER"} and (x or {}).get("status") in {"OFFICIAL","PLAYABLE","TRACK"}) for x in board),
        "supported_exact_id_blank_team_rows":sum(bool((x or {}).get("model_supported") and (x or {}).get("market_scope_verified") and (x or {}).get("source_identity_ids") and (not (x or {}).get("team") or not (x or {}).get("opponent"))) for x in board),
        "non_cs2_rows_visible":sum(not bool(ns.get("_v586_is_cs2",lambda _:True)(x)) for x in board),
    })
    try:
        context=ns["load_json"](ns.get("V57_CONTEXT_HEALTH_FILE"),{}) if ns.get("V57_CONTEXT_HEALTH_FILE") else {}; readiness=ns["load_json"](ns.get("V55_READINESS_FILE"),{}) if ns.get("V55_READINESS_FILE") else {}
        ph=ns["load_json"](ns.get("V590_PROVIDER_HEALTH_FILE"),{}) if ns.get("V590_PROVIDER_HEALTH_FILE") else {}; hh=ns["load_json"](ns.get("V591_HEALTH_FILE"),{}) if ns.get("V591_HEALTH_FILE") else {}; pph=ns["load_json"](ns.get("V592_PROFILE_HEALTH_FILE"),{}) if ns.get("V592_PROFILE_HEALTH_FILE") else {}; mh=ns["load_json"](ns.get("V593_MAP_HEALTH_FILE"),{}) if ns.get("V593_MAP_HEALTH_FILE") else {}
        if isinstance(context,dict):
            for k in ["real_provider_match_rows","real_source_match_rows","verified_profile_rows","core_kpr_rows","exact_source_identity_rows","supported_exact_id_blank_team_rows","freeze_candidate_rows","deep_team_map_rows","projection_ready_rows","official_ready_rows"]:
                if k in context: status[k]=int(context.get(k) or 0)
            status["context_health_version"]=context.get("version")
        if isinstance(readiness,dict): status["readiness_version"]=readiness.get("version"); status["missing_projection_requirements"]=dict(readiness.get("missing_projection_requirements") or {})
        if isinstance(ph,dict): status["provider_discovery_version"]=ph.get("version"); status["provider_discovery"]=dict(ph.get("discovery") or {}); status["provider_index"]=dict(ph.get("index") or {})
        if isinstance(hh,dict): status["hltv_direct_version"]=hh.get("version"); status["hltv_direct_health"]=hh
        if isinstance(pph,dict): status["hltv_profile_version"]=pph.get("version"); status["hltv_profile_health"]=pph
        if isinstance(mh,dict): status["hltv_map_version"]=mh.get("version"); status["hltv_map_health"]=mh
    except Exception: pass
    bridge["source_status"]=status
    path=Path(str(ns.get("V48_BRIDGE_LOCAL_FILE") or (Path(ns["STORAGE_DIR"])/"cs2_provider_cache.json"))); ns["save_json"](str(path),bridge,force=True)
    seed=ns.get("_v54_seed_databases_from_bridge")
    if callable(seed):
        try: bridge["source_status"]["database_seed_v593"]=seed(bridge); ns["save_json"](str(path),bridge,force=True)
        except Exception as exc: bridge["source_status"]["database_seed_v593_warning"]=str(exc)
    return bridge


base.export_provider_bridge=export_provider_bridge


def _force_fresh_cycle():
    try: (Path(os.getenv("CS2_DATA_DIR","/data/cs2_engine"))/".autofeed_collector.heartbeat").unlink(missing_ok=True)
    except Exception: pass


if __name__=="__main__":
    _force_fresh_cycle(); raise SystemExit(base.run_locked())
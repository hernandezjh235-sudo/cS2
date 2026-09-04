from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ CS2 GITHUB DATA CACHE PATCH V5.4 ==="

OVERLAY = r'''
# === ONEWAYPICKZ CS2 GITHUB DATA CACHE PATCH V5.4 ===
# GitHub cache bootstrap/persistence only. Protected projection math is unchanged.
AUTOFEED_CACHE_VERSION = "5.4"
V54_DEFAULT_BRIDGE_REPO = os.getenv("CS2_DEFAULT_BRIDGE_REPO", "hernandezjh235-sudo/cS2").strip() or "hernandezjh235-sudo/cS2"
V54_CACHE_SYNC_STATUS_FILE = os.path.join(STORAGE_DIR, ".github_cache_sync.json")
if not str(globals().get("V48_BRIDGE_REPO") or "").strip():
    V48_BRIDGE_REPO = V54_DEFAULT_BRIDGE_REPO


def _v54_newer_record(remote: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
    remote = dict(remote or {})
    local = dict(local or {})
    exact = {"real_kills_div_rounds", "hltv_reported_kpr", "bo3_reported_kpr", "demo_full_round_kpr"}
    local_exact = str(local.get("kpr_source") or "") in exact
    remote_exact = str(remote.get("kpr_source") or "") in exact
    local_maps = safe_int(local.get("profile_maps") or local.get("maps"), 0) or 0
    remote_maps = safe_int(remote.get("profile_maps") or remote.get("maps"), 0) or 0
    if local_exact and not remote_exact and local_maps >= remote_maps:
        out = dict(remote)
        out.update(local)
        return out
    out = dict(local)
    for key, value in remote.items():
        if value not in (None, "", [], {}):
            out[key] = value
    return out


def _v54_seed_databases_from_bridge(payload: Dict[str, Any]) -> Dict[str, int]:
    if not isinstance(payload, dict):
        return {"profiles": 0, "teams": 0, "matches": 0, "rosters": 0, "maps": 0, "vetoes": 0, "aliases": 0}
    counts = {"profiles": 0, "teams": 0, "matches": 0, "rosters": 0, "maps": 0, "vetoes": 0, "aliases": 0}

    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    player_db = load_json(PLAYER_DATABASE_FILE, {})
    player_db = player_db if isinstance(player_db, dict) else {}
    aliases = load_json(PLAYER_ALIAS_FILE, {})
    aliases = aliases if isinstance(aliases, dict) else {}
    for raw_key, rec in profiles.items():
        if not isinstance(rec, dict):
            continue
        player = str(rec.get("player") or rec.get("nickname") or raw_key or "").strip()
        key = normalize_name(player)
        if not key:
            continue
        old = player_db.get(key) if isinstance(player_db.get(key), dict) else {}
        row = _v54_newer_record(rec, old)
        row["player"] = player
        row["profile_maps"] = safe_int(row.get("profile_maps") or row.get("maps"), 0) or 0
        row["updated_at"] = row.get("updated_at") or payload.get("generated_at") or now_iso()
        row["github_cache_seeded"] = True
        player_db[key] = row
        counts["profiles"] += 1
        team = str(row.get("team") or "").strip()
        slug = str(row.get("slug") or "").strip()
        pid = str(((row.get("identity_ids") or {}).get("player_id")) or row.get("player_id") or "").strip()
        old_alias = aliases.get(key) if isinstance(aliases.get(key), dict) else {}
        aliases[key] = {
            **old_alias,
            "alias": player,
            "bo3_slug": slug or old_alias.get("bo3_slug", ""),
            "player_id": pid or old_alias.get("player_id", ""),
            "team": team or old_alias.get("team", ""),
            "source": "GitHub verified provider cache",
            "saved_at": now_iso(),
        }
        counts["aliases"] += 1
        try:
            sqlite_store_entity_snapshot("player", key, row, source="GitHub provider cache", as_of=row.get("updated_at"))
        except Exception:
            pass
    if profiles:
        save_json(PLAYER_DATABASE_FILE, player_db, force=True)
        save_json(PLAYER_ALIAS_FILE, aliases, force=True)

    teams = payload.get("teams") if isinstance(payload.get("teams"), dict) else {}
    team_db = load_json(TEAM_DATABASE_FILE, {})
    team_db = team_db if isinstance(team_db, dict) else {}
    roster_db = load_json(ROSTER_DATABASE_FILE, {})
    roster_db = roster_db if isinstance(roster_db, dict) else {}
    for raw_key, rec in teams.items():
        if not isinstance(rec, dict):
            continue
        team = str(rec.get("team") or raw_key or "").strip()
        key = normalize_team(team)
        if not key:
            continue
        old = team_db.get(key) if isinstance(team_db.get(key), dict) else {}
        row = {**old, **rec, "team": team, "github_cache_seeded": True}
        team_db[key] = row
        counts["teams"] += 1
        candidates = list(row.get("current_roster") or row.get("roster_candidates") or [])
        if candidates:
            roster_db[f"cache|{key}"] = {
                "team": team,
                "roster_candidates": candidates,
                "current_roster_names": list(row.get("current_roster") or []),
                "lineup_verified": bool(row.get("current_roster")),
                "current_roster_verified": bool(row.get("current_roster")),
                "source": "GitHub provider cache",
                "updated_at": row.get("updated_at") or payload.get("generated_at") or now_iso(),
            }
            counts["rosters"] += 1
        try:
            sqlite_store_entity_snapshot("team", key, row, source="GitHub provider cache", as_of=row.get("updated_at"))
        except Exception:
            pass
    if teams:
        save_json(TEAM_DATABASE_FILE, team_db, force=True)
        save_json(ROSTER_DATABASE_FILE, roster_db, force=True)

    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    match_db = load_json(MATCH_DATABASE_FILE, {})
    match_db = match_db if isinstance(match_db, dict) else {}
    map_db = load_json(MAP_DATABASE_FILE, {})
    map_db = map_db if isinstance(map_db, dict) else {}
    veto_db = load_json(VETO_DATABASE_FILE, {})
    veto_db = veto_db if isinstance(veto_db, dict) else {}
    for idx, rec in enumerate(matches):
        if not isinstance(rec, dict):
            continue
        mid = str(rec.get("match_id") or rec.get("provider_match_id") or rec.get("match_url") or f"cache-match-{idx}").strip()
        if not mid:
            continue
        old = match_db.get(mid) if isinstance(match_db.get(mid), dict) else {}
        row = {**old, **rec, "github_cache_seeded": True}
        match_db[mid] = row
        counts["matches"] += 1
        confirmed_maps = [str(x).strip() for x in (row.get("confirmed_maps") or []) if str(x).strip()]
        for map_name in confirmed_maps:
            mkey = f"{mid}|{normalize_name(map_name)}"
            map_db[mkey] = {
                "match_id": mid, "map_name": map_name, "confirmed": True,
                "source": "GitHub provider cache", "updated_at": row.get("updated_at") or now_iso(),
            }
            counts["maps"] += 1
        veto_actions = list(row.get("veto_actions") or [])
        if veto_actions:
            veto_db[mid] = {
                "match_id": mid, "veto_actions": veto_actions, "confirmed_maps": confirmed_maps,
                "source": "GitHub provider cache", "updated_at": row.get("updated_at") or now_iso(),
            }
            counts["vetoes"] += 1
        try:
            sqlite_store_entity_snapshot("match", mid, row, source="GitHub provider cache", as_of=row.get("updated_at") or row.get("start_time"))
        except Exception:
            pass
    if matches:
        save_json(MATCH_DATABASE_FILE, match_db, force=True)
        save_json(MAP_DATABASE_FILE, map_db, force=True)
        save_json(VETO_DATABASE_FILE, veto_db, force=True)
    return counts


_v54_load_provider_bridge_base = load_provider_bridge

def load_provider_bridge(force: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    payload, status = _v54_load_provider_bridge_base(force=force)
    status = dict(status or {})
    if _v48_valid_bridge(payload):
        try:
            status["database_seed"] = _v54_seed_databases_from_bridge(payload)
        except Exception as exc:
            status["database_seed_warning"] = str(exc)
    status["default_repo"] = V48_BRIDGE_REPO or V54_DEFAULT_BRIDGE_REPO
    status["branch"] = V48_BRIDGE_BRANCH
    return payload, status


_v54_prefetch_provider_base = v48_prefetch_provider_data

def v48_prefetch_provider_data(players: Sequence[str], force: bool = False) -> Dict[str, Any]:
    bridge, bridge_status = load_provider_bridge(force=False)
    out = dict(_v54_prefetch_provider_base(players, force=force) or {})
    out["github_data_cache"] = bridge_status
    out["github_cache_profiles"] = len((bridge or {}).get("profiles") or {}) if isinstance(bridge, dict) else 0
    return out


try:
    APP_VERSION = "CS2 v5.4 — VERIFIED GITHUB DATA CACHE + AUTOFEED"
except Exception:
    pass
# === END ONEWAYPICKZ CS2 GITHUB DATA CACHE PATCH V5.4 ===
'''


def patch_text(source: str) -> str:
    text = source
    if PATCH_MARKER not in text:
        if MARKER not in text:
            raise RuntimeError("SESSION BOARD LOAD marker not found; refusing to patch unknown app layout")
        text = text.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)

    health_anchor = '    storage_ok = os.path.isdir(STORAGE_DIR) and os.access(STORAGE_DIR, os.W_OK)\n    return {'
    health_insert = '''    storage_ok = os.path.isdir(STORAGE_DIR) and os.access(STORAGE_DIR, os.W_OK)\n    github_cache_state = load_json(os.path.join(STORAGE_DIR, ".github_cache_sync.json"), {})\n    github_cache_state = github_cache_state if isinstance(github_cache_state, dict) else {}\n    heartbeat_path = os.path.join(STORAGE_DIR, ".autofeed_collector.heartbeat")\n    try:\n        collector_heartbeat_age = max(0.0, time.time() - os.path.getmtime(heartbeat_path)) if os.path.exists(heartbeat_path) else None\n    except Exception:\n        collector_heartbeat_age = None\n    return {'''
    if health_anchor in text and 'github_cache_state = load_json(os.path.join(STORAGE_DIR, ".github_cache_sync.json")' not in text:
        text = text.replace(health_anchor, health_insert, 1)

    old_fields = '''        "github_backup_configured": bool(get_secret("GITHUB_TOKEN") and get_secret("GITHUB_REPO")),\n        "github_auto_backup": str(get_secret("GITHUB_AUTO_BACKUP", "")).lower() in {"1", "true", "yes"},'''
    new_fields = '''        "github_backup_configured": bool(get_secret("GITHUB_TOKEN") and get_secret("GITHUB_REPO")),\n        "github_auto_backup": str(get_secret("GITHUB_AUTO_BACKUP", "")).lower() in {"1", "true", "yes"},\n        "github_data_cache_ready": bool(github_cache_state.get("ok")),\n        "github_data_cache_repo": str(github_cache_state.get("repo") or os.getenv("CS2_BRIDGE_REPO", "hernandezjh235-sudo/cS2")),\n        "github_data_cache_generated_at": github_cache_state.get("cache_generated_at"),\n        "collector_heartbeat_age_seconds": collector_heartbeat_age,'''
    if old_fields in text:
        text = text.replace(old_fields, new_fields, 1)

    old_info = '''    backup_label = "READY" if health.get("github_backup_configured") else "NOT CONFIGURED"\n    st.info(f"Storage: {health.get('storage_dir')} · {storage_label} · Core DB: {health.get('core_db_size_mb',0):.2f} MB · GitHub backup: {backup_label}")'''
    new_info = '''    cache_label = "CONNECTED" if health.get("github_data_cache_ready") else "SYNCING"\n    backup_label = "WRITE-BACK READY" if health.get("github_backup_configured") else "PUBLIC CACHE MODE"\n    st.info(f"Storage: {health.get('storage_dir')} · {storage_label} · Core DB: {health.get('core_db_size_mb',0):.2f} MB · GitHub data cache: {cache_label} · {backup_label}")'''
    if old_info in text:
        text = text.replace(old_info, new_info, 1)

    old_integrity = '''    q3.metric("GitHub Backup", "✅" if health.get("github_backup_configured") else "⚠️")\n    q4.metric("Auto Backup", "✅" if health.get("github_auto_backup") else "OFF")'''
    new_integrity = '''    q3.metric("GitHub Data Cache", "✅" if health.get("github_data_cache_ready") else "⏳")\n    hb = health.get("collector_heartbeat_age_seconds")\n    q4.metric("Auto Collector", "✅" if hb is not None and hb < 900 else "⏳")'''
    if old_integrity in text:
        text = text.replace(old_integrity, new_integrity, 1)

    text = text.replace(
        '{"Variable": "CS2_BRIDGE_REPO", "Required": "Optional", "Purpose": "Only for an optional GitHub data-cache branch"}',
        '{"Variable": "CS2_BRIDGE_REPO", "Required": "Automatic", "Purpose": "Defaults to hernandezjh235-sudo/cS2; public data-cache branch is loaded automatically"}'
    )
    text = text.replace(
        'st.caption("Fill the false rows first. The projection model can run without them, but best-win confidence improves most from graded results, mappings, odds, persistent storage, and demo/current-roster data.")',
        'st.caption("Automatic GitHub + Railway collection fills these checks over time. Manual uploads are optional recovery tools only; verified profiles, mappings, lines, grading, and persistent history are collected automatically.")'
    )
    return text


def patch_app(path: Path | str = "app.py") -> bool:
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    patched = patch_text(original)
    changed = patched != original
    if changed:
        tmp = p.with_suffix(p.suffix + ".autofeed54.tmp")
        tmp.write_text(patched, encoding="utf-8")
        os.replace(tmp, p)
    return changed


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    try:
        changed = patch_app(path)
        print(f"CS2 GitHub data-cache patch v5.4: {'updated' if changed else 'already applied'} -> {path}")
        return 0
    except Exception as exc:
        print(f"CS2 GitHub data-cache patch failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

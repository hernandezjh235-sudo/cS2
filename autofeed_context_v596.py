from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.6 VERIFIED TEAM MAP CONTEXT PROPAGATION ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.6 VERIFIED TEAM MAP CONTEXT PROPAGATION ===
# Data-context propagation only. Protected Maps 1-2 Kills projection math,
# probability math, side selection, confidence, and readiness thresholds are unchanged.
AUTOFEED_CONTEXT_V596_VERSION = "5.9.6"
V596_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_v596_context_health.json")
V596_HEALTH = {"rows_attempted": 0, "rows_hydrated": 0, "team_profiles": 0, "opponent_profiles": 0, "last_error": ""}


def _v596_save(extra=None):
    try:
        payload = {"version": "5.9.6", "updated_at": now_iso(), **dict(V596_HEALTH)}
        if isinstance(extra, dict): payload.update(extra)
        save_json(V596_HEALTH_FILE, payload, force=True)
    except Exception:
        pass


def _v596_slug(name):
    s = normalize_team(str(name or "")).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "team"


def _v596_hydrate_deep(row):
    out = dict(row or {})
    if not _v587_supported(out) or not bool(out.get("v587_provider_context")):
        return out
    ids = dict(out.get("identity_ids") or {})
    team_id = str(ids.get("team_id") or "").strip()
    opp_id = str(ids.get("opponent_id") or "").strip()
    team = str(out.get("team") or "").strip()
    opp = str(out.get("opponent") or "").strip()
    if not (team_id.isdigit() and opp_id.isdigit() and team and opp):
        return out
    V596_HEALTH["rows_attempted"] = int(V596_HEALTH.get("rows_attempted") or 0) + 1
    try:
        team_profile, team_status = build_team_deep_profile(team_id, _v596_slug(team), team)
        opp_profile, opp_status = build_team_deep_profile(opp_id, _v596_slug(opp), opp)
        team_profile = dict(team_profile or {}); opp_profile = dict(opp_profile or {})
        team_maps = int(safe_int(team_profile.get("recent_maps"), 0) or 0)
        team_samples = int(safe_int(team_profile.get("mapstats_samples"), 0) or 0)
        opp_maps = int(safe_int(opp_profile.get("recent_maps"), 0) or 0)
        opp_samples = int(safe_int(opp_profile.get("mapstats_samples"), 0) or 0)
        if team_maps > 0:
            V596_HEALTH["team_profiles"] = int(V596_HEALTH.get("team_profiles") or 0) + 1
            out["team_recent_maps"] = team_maps
            out["team_mapstats_samples"] = max(team_samples, team_maps)
            out["team_map_profiles"] = dict(team_profile.get("map_profiles") or {})
            out["team_map_context_verified"] = True
            out["team_map_context_source"] = str(team_profile.get("provider") or "HLTV verified public team page")
        if opp_samples > 0 or opp_maps > 0:
            V596_HEALTH["opponent_profiles"] = int(V596_HEALTH.get("opponent_profiles") or 0) + 1
            out["opponent_recent_maps"] = opp_maps
            out["opponent_mapstats_samples"] = max(opp_samples, opp_maps)
            out["opponent_map_profiles"] = dict(opp_profile.get("map_profiles") or {})
            out["opponent_map_context_verified"] = True
            out["opponent_map_context_source"] = str(opp_profile.get("provider") or "HLTV verified public team page")
        # Only copy roster facts when the public team page actually returned them.
        roster = list(team_profile.get("current_roster") or [])
        if len(roster) >= 5:
            out["provider_current_roster_names"] = roster
            out["provider_current_roster_verified"] = bool((team_status or {}).get("roster_fresh"))
        if team_maps > 0 and (opp_samples > 0 or opp_maps > 0):
            out["deep_team_map_verified"] = True
            out["v596_deep_context"] = True
            V596_HEALTH["rows_hydrated"] = int(V596_HEALTH.get("rows_hydrated") or 0) + 1
        out["v596_team_status"] = {"ok": bool((team_status or {}).get("ok")), "mapstats_samples": max(team_samples, team_maps)}
        out["v596_opponent_status"] = {"ok": bool((opp_status or {}).get("ok")), "mapstats_samples": max(opp_samples, opp_maps)}
        V596_HEALTH["last_error"] = ""
    except Exception as exc:
        V596_HEALTH["last_error"] = f"{type(exc).__name__}: {exc}"
    _v596_save()
    return out


# v5.8.7/v5.9.1 already recover verified provider identity. Attach the verified
# team-map profiles to that same row instead of merely fetching and discarding them.
_v596_provider_base = _v587_discover_provider

def _v587_discover_provider(row):
    out = _v596_provider_base(row)
    return _v596_hydrate_deep(out)


# Recompute readiness from the now-populated verified context after the board is
# assembled. This changes no gate definition; it only prevents real context from
# being lost between provider enrichment and readiness evaluation.
_v596_board_base = build_full_board

def build_full_board(props, deep_enabled=True):
    board, status = _v596_board_base(props, deep_enabled)
    board = [dict(x) for x in list(board or []) if isinstance(x, dict)]
    deep_rows = projection_ready = official_ready = 0
    for i, row in enumerate(board):
        if _v587_supported(row):
            row = _v587_discover_provider(row)
            if (safe_int(row.get("team_recent_maps"), 0) or 0) > 0 and (safe_int(row.get("opponent_mapstats_samples"), 0) or 0) > 0:
                deep_rows += 1
            try:
                rd = _v55_ready(row)
                row["data_readiness"] = rd
                row["projection_data_ready"] = bool(rd.get("projection_ready"))
                row["official_data_ready"] = bool(rd.get("official_ready"))
                row["data_readiness_score"] = rd.get("readiness_score")
            except Exception:
                pass
            projection_ready += int(bool(row.get("projection_data_ready")))
            official_ready += int(bool(row.get("official_data_ready")))
        board[i] = row
    status = dict(status or {})
    status["v596_verified_team_map_context"] = {
        "version": "5.9.6", "updated_at": now_iso(), "board_rows": len(board),
        "deep_team_map_rows": deep_rows, "projection_ready_rows": projection_ready,
        "official_ready_rows": official_ready, "projection_math_changed": False,
    }
    try:
        health = load_json(V57_CONTEXT_HEALTH_FILE, {}) or {}
        if isinstance(health, dict):
            health.update({"version": "5.9.6", "runtime_layer": "5.9.6", "updated_at": now_iso(),
                           "deep_team_map_rows": deep_rows, "projection_ready_rows": projection_ready,
                           "official_ready_rows": official_ready, "projection_math_changed": False})
            save_json(V57_CONTEXT_HEALTH_FILE, health, force=True)
        readiness = load_json(V55_READINESS_FILE, {}) or {}
        if isinstance(readiness, dict):
            readiness.update({"version": "5.9.6", "updated_at": now_iso(), "deep_team_map_rows": deep_rows,
                              "projection_ready_rows": projection_ready, "official_ready_rows": official_ready})
            save_json(V55_READINESS_FILE, readiness, force=True)
    except Exception as exc:
        status["v596_health_warning"] = f"{type(exc).__name__}: {exc}"
    _v596_save({"deep_team_map_rows": deep_rows, "projection_ready_rows": projection_ready, "official_ready_rows": official_ready})
    return board, status

try:
    APP_VERSION = "CS2 v5.9.6 — VERIFIED TEAM MAP CONTEXT PROPAGATION"
except Exception:
    pass
# === END ONEWAYPICKZ V5.9.6 VERIFIED TEAM MAP CONTEXT PROPAGATION ===
'''


def patch_text(source: str) -> str:
    if PATCH_MARKER in source: return source
    if MARKER not in source: raise RuntimeError("SESSION BOARD LOAD marker not found")
    return source.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)


def patch_app(path="app.py"):
    p=Path(path); old=p.read_text(encoding="utf-8"); new=patch_text(old); changed=new!=old
    if changed:
        tmp=p.with_suffix(p.suffix+".v596.tmp"); tmp.write_text(new,encoding="utf-8"); os.replace(tmp,p)
    return changed

if __name__ == "__main__":
    p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("app.py")
    changed=patch_app(p); compile(p.read_text(encoding="utf-8"),str(p),"exec")
    print(f"v5.9.6 patch {'applied' if changed else 'already present'}: {p}")

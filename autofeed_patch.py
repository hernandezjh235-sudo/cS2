from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ CS2 AUTOFEED ACCURACY PATCH V5.2 ==="

OVERLAY = r'''
# === ONEWAYPICKZ CS2 AUTOFEED ACCURACY PATCH V5.2 ===
# Data/identity/persistence hardening only. The protected projection math above is unchanged.
AUTOFEED_VERSION = "5.2"


def _autofeed_verified_profile_row(row: Dict[str, Any]) -> bool:
    return bool(
        safe_int(row.get("profile_maps"), 0) >= MIN_PROFILE_MAPS
        and safe_float(row.get("base_kpr"), None) is not None
        and safe_float(row.get("projection"), None) is not None
        and not bool(row.get("line_only_fallback"))
    )


def _line_only_fallback_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Display the real market line without manufacturing a player projection."""
    line = safe_float(row.get("line"), None)
    if line is None:
        return {**row, "status": "PASS", "status_label": "🚫 PASS — REAL LINE MISSING"}
    movement = line_movement(str(row.get("player") or ""), row.get("market", "Maps 1-2 Kills"), row.get("start_time", ""), float(line))
    flags = list(dict.fromkeys(list(row.get("flags") or []) + [
        "LINE-ONLY MARKET WATCH",
        "NO VERIFIED PLAYER PROFILE",
        "NO MODEL PROJECTION OR CONFIDENCE ASSIGNED",
        "NOT ELIGIBLE FOR OFFICIAL / PLAYABLE / BEST-WIN",
    ]))
    return {
        **row,
        "projection": None,
        "projection_before_learning": None,
        "median": None,
        "edge": None,
        "abs_edge": None,
        "lean": "PASS",
        "probability": None,
        "raw_probability": None,
        "over_probability": None,
        "under_probability": None,
        "push_probability": None,
        "expected_rounds": None,
        "adjusted_kpr": None,
        "base_kpr": None,
        "profile_maps": 0,
        "profile_rounds": 0,
        "profile_source": "REAL MARKET LINE — PROFILE PENDING",
        "profile_warnings": ["Verified historical player profile has not been recovered yet"],
        "role": "Unknown",
        "likely_maps": ["Unconfirmed", "Unconfirmed"],
        "map_confidence": 0.0,
        "current_roster_maps": 0,
        "roster_stability": 0.0,
        "data_score": min(safe_int(row.get("data_score"), 0) or 0, 20),
        "status": "PASS",
        "status_label": "⏳ DATA BUILDING — REAL LINE ONLY",
        "opening_line": movement.get("opening_line"),
        "line_move": movement.get("move"),
        "line_observations": movement.get("observations"),
        "core_kpr_verified": False,
        "player_source_fresh": False,
        "line_only_fallback": True,
        "assisted_official": False,
        "official_mode": "NONE",
        "confidence_grade": None,
        "best_win_tier": "PASS",
        "pick_action": "WAIT FOR VERIFIED PROFILE",
        "flags": flags,
        "risk_notes": flags[:10],
        "error": "Verified player data required before projection/confidence",
    }


def _promote_assisted_official_rows(board: List[Dict[str, Any]], enabled: bool = True, max_rows: int = ASSISTED_OFFICIAL_MAX_ROWS) -> Dict[str, Any]:
    """V5.2: never promote market-prior/zero-profile rows to Official."""
    strict_count = sum(1 for row in board if row.get("status") == "OFFICIAL" and _autofeed_verified_profile_row(row))
    for row in board:
        if not _autofeed_verified_profile_row(row):
            row["assisted_official"] = False
            if row.get("status") == "OFFICIAL" and safe_int(row.get("profile_maps"), 0) <= 0:
                row["status"] = "PASS"
                row["status_label"] = "⏳ DATA BUILDING — VERIFIED PROFILE REQUIRED"
                row["probability"] = None
                row["raw_probability"] = None
                row["projection"] = None
                row["edge"] = None
                row["data_score"] = min(safe_int(row.get("data_score"), 0) or 0, 20)
    return {
        "enabled": False,
        "promoted": 0,
        "strict_official": strict_count,
        "message": "Assisted market-prior promotion disabled. Official status requires verified player data.",
    }


def _autofeed_reconcile_identity(row: Dict[str, Any]) -> Dict[str, Any]:
    """Prefer matchup + verified player-team evidence over stale market team labels."""
    out = dict(row)
    player = str(out.get("player") or "").strip()
    matchup = str(out.get("matchup") or out.get("evidence") or "").strip()
    a, b = _teams_from_matchup(matchup)
    db = lookup_database_player(player) if player else {}
    profile_team = str((db or {}).get("team") or "").strip()
    current_team = str(out.get("team") or "").strip()
    current_opp = str(out.get("opponent") or "").strip()
    chosen_team, chosen_opp, source, warning = current_team, current_opp, "market", ""
    if a and b:
        if profile_team and _team_name_matches(profile_team, a):
            chosen_team, chosen_opp, source = a, b, "verified-profile + matchup"
        elif profile_team and _team_name_matches(profile_team, b):
            chosen_team, chosen_opp, source = b, a, "verified-profile + matchup"
        elif current_team and _team_name_matches(current_team, a):
            chosen_team, chosen_opp, source = a, b, "market-team + matchup"
        elif current_team and _team_name_matches(current_team, b):
            chosen_team, chosen_opp, source = b, a, "market-team + matchup"
        elif current_opp and _team_name_matches(current_opp, a):
            chosen_team, chosen_opp, source = b, a, "market-opponent + matchup"
        elif current_opp and _team_name_matches(current_opp, b):
            chosen_team, chosen_opp, source = a, b, "market-opponent + matchup"
        else:
            warning = "PLAYER TEAM NOT RECONCILED TO MATCHUP"
            chosen_team = current_team if current_team and (_team_name_matches(current_team, a) or _team_name_matches(current_team, b)) else ""
            chosen_opp = current_opp if current_opp and (_team_name_matches(current_opp, a) or _team_name_matches(current_opp, b)) else ""
    elif profile_team and not chosen_team:
        chosen_team, source = profile_team, "verified-profile"
    out["team"] = chosen_team
    out["opponent"] = chosen_opp
    out["identity_reconciled"] = bool(chosen_team and chosen_opp)
    out["identity_reconcile_source"] = source
    if warning:
        out["flags"] = list(dict.fromkeys(list(out.get("flags") or []) + [warning]))
    return out


def _autofeed_persist_context(row: Dict[str, Any]) -> None:
    """Persist verified player/team/match/map plus roster/veto context automatically."""
    if not isinstance(row, dict):
        return
    if safe_int(row.get("profile_maps"), 0) > 0:
        try:
            save_projection_entities(row)
        except Exception:
            pass
    match_key = str((row.get("identity_ids") or {}).get("match_id") or row.get("match_url") or "").strip()
    team = str(row.get("team") or "").strip()
    opponent = str(row.get("opponent") or "").strip()
    if not match_key:
        return
    roster_record = {
        "match_id": match_key, "match_url": row.get("match_url"), "team": team, "opponent": opponent,
        "player": row.get("player"), "confirmed_lineup_names": list(row.get("confirmed_lineup_names") or []),
        "current_roster_names": list(row.get("current_roster_names") or []), "lineup_verified": bool(row.get("lineup_verified")),
        "current_roster_verified": bool(row.get("current_roster_verified")), "roster_overlap": row.get("roster_overlap"),
        "roster_stability": row.get("roster_stability"), "start_time": row.get("start_time"),
        "source_freshness": row.get("source_freshness") or {},
    }
    if team or roster_record["confirmed_lineup_names"] or roster_record["current_roster_names"]:
        upsert_database_record(ROSTER_DATABASE_FILE, f"{match_key}|{normalize_team(team)}", roster_record)
    veto_record = {
        "match_id": match_key, "match_url": row.get("match_url"), "team": team, "opponent": opponent,
        "veto_state": row.get("veto_state"), "likely_maps": row.get("likely_maps"), "map_confidence": row.get("map_confidence"),
        "map_scenarios": row.get("map_scenarios") or [], "start_time": row.get("start_time"), "event": row.get("event"),
    }
    if row.get("veto_state") or row.get("likely_maps"):
        upsert_database_record(VETO_DATABASE_FILE, match_key, veto_record)


def auto_freeze_verified_pregame(board: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Automatically freeze every verified projected row before its match for later grading."""
    eligible: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for row in list(board or []):
        if not _autofeed_verified_profile_row(row) or row.get("lean") not in {"OVER", "UNDER"}:
            continue
        if row.get("status") not in {"OFFICIAL", "PLAYABLE", "TRACK"}:
            continue
        start = _parse_iso_datetime(row.get("start_time"))
        if start and start < now - timedelta(minutes=5):
            continue
        eligible.append(dict(row))
    if not eligible:
        return {"added": 0, "skipped": 0, "eligible": 0}
    out = save_official_snapshots(eligible, include_playable=True, include_track=True)
    return {**out, "eligible": len(eligible)}


_autofeed_base_build_full_board = build_full_board

def build_full_board(props: List[Dict[str, Any]], deep_enabled: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    reconciled = [_autofeed_reconcile_identity(p) for p in list(props or [])]
    board, status = _autofeed_base_build_full_board(reconciled, deep_enabled)
    verified = blocked = persisted = 0
    for idx, raw in enumerate(list(board)):
        row = _autofeed_reconcile_identity(raw)
        if safe_int(row.get("profile_maps"), 0) <= 0:
            if safe_float(row.get("projection"), None) is not None or row.get("status") == "OFFICIAL":
                blocked += 1
            row = _line_only_fallback_row(row)
        else:
            verified += 1
            row["assisted_official"] = False
            _autofeed_persist_context(row)
            persisted += 1
        board[idx] = row
    status = dict(status or {})
    status["autofeed_v52"] = {
        "enabled": True, "input_props": len(props or []), "board_rows": len(board),
        "verified_profiles": verified, "zero_profile_rows_blocked": blocked,
        "verified_rows_persisted": persisted, "assisted_market_prior_disabled": True,
        "auto_database_persistence": True,
    }
    status["assisted_official"] = _promote_assisted_official_rows(board, enabled=False)
    return board, status


_autofeed_base_simple_board = build_simple_line_only_board

def build_simple_line_only_board(props: Sequence[Dict[str, Any]], max_rows: int = 500) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows = [_line_only_fallback_row(_autofeed_reconcile_identity(dict(prop))) for prop in list(props or [])[:max(1, int(max_rows or 500))]
            if prop.get("player") and safe_float(prop.get("line"), None) is not None]
    return rows, {
        "mode": "simple_market_watch_only", "lines_loaded": len(props or []), "rows_built": len(rows),
        "autofeed_v52": {"verified_projection_mode": False, "warning": "Market-watch mode never assigns projection/confidence or Official status."},
        "assisted_official": {"enabled": False, "promoted": 0},
    }

# === END ONEWAYPICKZ CS2 AUTOFEED ACCURACY PATCH V5.2 ===
'''


def patch_text(source: str) -> str:
    text = source
    text = text.replace('ASSISTED_OFFICIAL_DEFAULT = os.getenv("CS2_ASSISTED_OFFICIAL", "true")',
                        'ASSISTED_OFFICIAL_DEFAULT = os.getenv("CS2_ASSISTED_OFFICIAL", "false")')
    text = text.replace('simple_line_only_mode = st.checkbox("Simple all-lines mode", value=True)',
                        'simple_line_only_mode = st.checkbox("Simple all-lines mode", value=False)')
    text = text.replace('fast_refresh_enabled = st.checkbox("Fast refresh / prevent hangs", value=True)',
                        'fast_refresh_enabled = st.checkbox("Fast refresh / prevent hangs", value=False)')
    text = text.replace('max_props_per_refresh = st.slider("Max props per refresh", 10, 250, 250, 10)',
                        'max_props_per_refresh = st.slider("Max props per refresh", 10, 500, 500, 10)')
    text = text.replace('assisted_official_enabled = st.checkbox("Assisted Official when profiles missing", value=ASSISTED_OFFICIAL_DEFAULT)',
                        'assisted_official_enabled = st.checkbox("Assisted Official when profiles missing", value=False, disabled=True, help="Disabled: Official status now requires verified player data.")')
    if PATCH_MARKER not in text:
        if MARKER not in text:
            raise RuntimeError("SESSION BOARD LOAD marker not found; refusing to patch an unknown app layout")
        text = text.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)
    freeze_anchor = 'st.session_state["cs2_board"] = board\n        save_asof_projection_history(board, source_status)'
    freeze_repl = 'st.session_state["cs2_board"] = board\n        save_asof_projection_history(board, source_status)\n        board_status["auto_freeze"] = auto_freeze_verified_pregame(board)'
    if freeze_anchor in text and 'board_status["auto_freeze"] = auto_freeze_verified_pregame(board)' not in text:
        text = text.replace(freeze_anchor, freeze_repl, 1)
    text = re.sub(r'APP_VERSION = "CS2 v5\.0 — DIRECT BO3 PROFILE CACHE \+ PROGRESSIVE BOARD RECOVERY"',
                  'APP_VERSION = "CS2 v5.2 — AUTOFEED VERIFIED DATA + PREGAME FREEZE"', text)
    return text


def patch_app(path: Path | str = "app.py") -> bool:
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    patched = patch_text(original)
    changed = patched != original
    if changed:
        tmp = p.with_suffix(p.suffix + ".autofeed.tmp")
        tmp.write_text(patched, encoding="utf-8")
        os.replace(tmp, p)
    return changed


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    try:
        changed = patch_app(path)
        print(f"CS2 autofeed patch v5.2: {'updated' if changed else 'already applied'} -> {path}")
        return 0
    except Exception as exc:
        print(f"CS2 autofeed patch failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

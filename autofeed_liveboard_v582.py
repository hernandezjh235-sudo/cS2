from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.2 PRIMARY-SOURCE LINE + CONTEXT FASTPATH ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.2 PRIMARY-SOURCE LINE + CONTEXT FASTPATH ===
# Data acquisition / identity-context / latency only.
# Protected Maps 1-2 kill projection math is unchanged.
AUTOFEED_LIVEBOARD_V582_VERSION = "5.8.2"
V582_LIVE_CATALOG_FILE = os.path.join(STORAGE_DIR, "cs2_live_line_catalog.json")


def _v582_market_kind(text, stat_name=""):
    raw = f"{stat_name} | {text}".lower()
    compact = re.sub(r"[^a-z0-9]+", " ", raw)
    headshots = "headshot" in raw
    kills = bool(re.search(r"\bkills?\b", raw)) and not headshots
    maps12 = bool(
        re.search(r"\bmaps?\s*1\s*[-+&/]\s*(?:maps?\s*)?2\b", raw)
        or re.search(r"\bmaps?\s*1\s+(?:and\s+)?2\b", raw)
        or "maps 1 2" in compact
        or "map 1 2" in compact
    )
    map1 = bool(re.search(r"\bmap\s*1\b", raw) or re.search(r"\bm1\b", raw))
    if kills and maps12:
        return "Maps 1-2 Kills", True
    if headshots and maps12:
        return "Headshots Maps 1-2", False
    if kills and map1:
        return "Map 1 Kills", False
    if headshots and map1:
        return "Map 1 Headshots", False
    clean = str(stat_name or "").strip()
    return clean or "Other CS2 Market", False


def _v582_player_name(player_obj, appearance=None, appearance_stat=None, over_under=None, line_obj=None):
    player_obj = player_obj if isinstance(player_obj, dict) else {}
    first = str(player_obj.get("first_name") or "").strip()
    last = str(player_obj.get("last_name") or "").strip()
    name = str(player_obj.get("full_name") or player_obj.get("display_name") or f"{first} {last}").strip()
    if name:
        return name
    try:
        return str(_extract_player_name(player_obj, appearance or {}, appearance_stat or {}, over_under or {}, line_obj or {}) or "").strip()
    except Exception:
        return ""


def _v582_parse_all_underdog_lines(data):
    root = _underdog_payload_root(data)
    if not root:
        return []
    line_items = _underdog_collection(root, "over_under_lines", "lines", "projections")
    appearances_list = _underdog_collection(root, "appearances")
    appearances = _record_map(appearances_list)
    players = _record_map(_underdog_collection(root, "players", "new_players"))
    games = _record_map(_underdog_collection(root, "games", "matches", "events") + _underdog_collection(root, "solo_games"))
    teams = _record_map(_underdog_collection(root, "teams"))
    sports = _record_map(_underdog_collection(root, "sports", "leagues"))
    if not line_items:
        return []

    roster_index = {}
    for ap in appearances_list:
        if not isinstance(ap, dict):
            continue
        gid = ap.get("match_id") or ap.get("game_id") or ap.get("event_id")
        pid = ap.get("player_id")
        tid = ap.get("team_id")
        pobj = players.get(str(pid), {}) if pid not in [None, ""] else {}
        pname = _v582_player_name(pobj, ap)
        if gid in [None, ""] or tid in [None, ""] or not pname:
            continue
        key = (str(gid), str(tid))
        bucket = roster_index.setdefault(key, [])
        if normalize_name(pname) not in {normalize_name(x) for x in bucket}:
            bucket.append(pname)

    rows = []
    for line_obj in line_items:
        if not isinstance(line_obj, dict):
            continue
        over_under = line_obj.get("over_under") if isinstance(line_obj.get("over_under"), dict) else {}
        appearance_stat = over_under.get("appearance_stat") if isinstance(over_under.get("appearance_stat"), dict) else {}
        appearance_id = appearance_stat.get("appearance_id") or line_obj.get("appearance_id") or over_under.get("appearance_id")
        appearance = appearances.get(str(appearance_id), {}) if appearance_id not in [None, ""] else {}
        player_id = appearance.get("player_id") or appearance_stat.get("player_id")
        player_obj = players.get(str(player_id), {}) if player_id not in [None, ""] else {}
        game_id = appearance.get("match_id") or appearance.get("game_id") or appearance.get("event_id")
        game_obj = games.get(str(game_id), {}) if game_id not in [None, ""] else {}
        team_id = appearance.get("team_id") or player_obj.get("team_id")
        team_obj = teams.get(str(team_id), {}) if team_id not in [None, ""] else {}
        sport_id = appearance.get("sport_id") or player_obj.get("sport_id") or game_obj.get("sport_id")
        sport_obj = sports.get(str(sport_id), {}) if sport_id not in [None, ""] else {}
        try:
            if _underdog_inactive(line_obj, over_under, appearance, game_obj):
                continue
        except Exception:
            pass
        stat_name = appearance_stat.get("stat") or over_under.get("stat") or line_obj.get("stat") or ""
        text = _object_text(line_obj, over_under, appearance_stat, appearance, player_obj, game_obj, team_obj, sport_obj)
        market, model_supported = _v582_market_kind(text, stat_name)
        specific_cs2 = False
        try:
            specific_cs2 = bool(_v58_specific_cs2(sport_obj, game_obj, appearance, player_obj, line_obj, over_under))
        except Exception:
            low = str(text or "").lower()
            specific_cs2 = any(x in low for x in ["counter strike", "counter-strike", "counterstrike", "cs2", "cs:go", "csgo"])
        if not (specific_cs2 or model_supported):
            continue
        player = _v582_player_name(player_obj, appearance, appearance_stat, over_under, line_obj)
        try:
            line = _extract_line(line_obj, over_under, appearance_stat)
        except Exception:
            line = None
        if not player or line is None:
            continue
        team = str(team_obj.get("name") or team_obj.get("display_name") or player_obj.get("team_name") or appearance.get("team_name") or "").strip()
        home_id = game_obj.get("home_team_id")
        away_id = game_obj.get("away_team_id")
        opponent_id = None
        if team_id not in [None, ""]:
            if str(home_id) == str(team_id): opponent_id = away_id
            elif str(away_id) == str(team_id): opponent_id = home_id
        opponent_obj = teams.get(str(opponent_id), {}) if opponent_id not in [None, ""] else {}
        opponent = str(opponent_obj.get("name") or opponent_obj.get("display_name") or appearance.get("opponent_name") or "").strip()
        matchup = str(game_obj.get("title") or game_obj.get("display_title") or game_obj.get("name") or appearance.get("matchup") or "").strip()
        if not matchup and team and opponent:
            matchup = f"{team} vs {opponent}"
        start_time = str(game_obj.get("scheduled_at") or game_obj.get("starts_at") or game_obj.get("start_time") or game_obj.get("begin_at") or game_obj.get("commence_time") or appearance.get("scheduled_at") or appearance.get("starts_at") or "").strip()
        if start_time:
            try:
                if not _current_line_time(start_time):
                    continue
            except Exception:
                pass
        own_roster = list(roster_index.get((str(game_id), str(team_id)), [])) if game_id not in [None, ""] and team_id not in [None, ""] else []
        opp_roster = list(roster_index.get((str(game_id), str(opponent_id)), [])) if game_id not in [None, ""] and opponent_id not in [None, ""] else []
        lineup_groups = []
        if team and own_roster: lineup_groups.append({"team": team, "team_id": str(team_id or ""), "players": own_roster})
        if opponent and opp_roster: lineup_groups.append({"team": opponent, "team_id": str(opponent_id or ""), "players": opp_roster})
        player_in_roster = bool(own_roster and max([name_similarity(player, x) for x in own_roster] or [0.0]) >= .84)
        exact_five = len(own_roster) == 5 and player_in_roster
        best_of = game_obj.get("best_of") or game_obj.get("bo") or game_obj.get("match_format") or game_obj.get("format")
        match_format = ""
        if best_of not in [None, ""]:
            raw_bo = str(best_of).strip().upper()
            if raw_bo.isdigit(): match_format = f"BO{raw_bo}"
            elif raw_bo.startswith("BO"): match_format = raw_bo
            else: match_format = raw_bo
        if not match_format and model_supported: match_format = "MULTI_MAP"
        source_line_id = str(line_obj.get("id") or "")
        source_player_id = str(player_id or "")
        source_match_id = str(game_id or "")
        source_team_id = str(team_id or "")
        source_opponent_id = str(opponent_id or "")
        rows.append({
            "source": "Underdog", "prop_id": source_line_id or hashlib.md5(f"{player}|{market}|{line}|{start_time}".encode()).hexdigest()[:12],
            "player": player, "team": team, "opponent": opponent, "matchup": matchup, "start_time": start_time,
            "market": market, "market_scope": "maps_1_2" if model_supported else "unsupported_model_market",
            "market_scope_verified": bool(model_supported), "model_supported": bool(model_supported), "projection_eligible_market": bool(model_supported),
            "source_line_id": source_line_id, "appearance_id": str(appearance_id or ""), "game_id": source_match_id,
            "sport_id": str(sport_id or ""), "stat_name": str(stat_name or ""), "line": float(line), "evidence": text[:900], "source_pulled_at": now_iso(),
            "underdog_player_id": source_player_id, "underdog_match_id": source_match_id, "underdog_team_id": source_team_id, "underdog_opponent_id": source_opponent_id,
            "source_identity_ids": {"player_id": source_player_id, "match_id": source_match_id, "team_id": source_team_id, "opponent_id": source_opponent_id},
            "source_lineup_groups": lineup_groups, "source_roster_names": own_roster, "source_player_in_lineup": player_in_roster,
            "source_five_player_lineup": exact_five, "source_match_verified": bool(source_player_id and source_match_id and team and opponent), "match_format": match_format,
        })
    dedup = {}
    for row in rows:
        line_id = str(row.get("source_line_id") or "")
        key = ("line", line_id) if line_id else (normalize_name(row.get("player")), normalize_name(row.get("market")), float(row.get("line")), str(row.get("start_time") or "")[:16])
        dedup[key] = row
    return list(dedup.values())


def _v582_source_context(row):
    out = dict(row or {})
    ids = dict(out.get("source_identity_ids") or {})
    player = str(out.get("player") or "").strip(); team = str(out.get("team") or "").strip(); opponent = str(out.get("opponent") or "").strip()
    roster = [str(x or "").strip() for x in list(out.get("source_roster_names") or []) if str(x or "").strip()]
    groups = [dict(x) for x in list(out.get("source_lineup_groups") or []) if isinstance(x, dict)]
    player_in = bool(roster and player and max([name_similarity(player, x) for x in roster] or [0.0]) >= .84)
    exact_five = len(roster) == 5 and player_in
    pid = str(ids.get("player_id") or out.get("underdog_player_id") or "").strip(); mid = str(ids.get("match_id") or out.get("underdog_match_id") or out.get("game_id") or "").strip()
    if not (pid and mid and team and opponent and exact_five): return out, False
    existing_ids = dict(out.get("identity_ids") or {})
    existing_ids.setdefault("player_id", f"ud:{pid}"); existing_ids.setdefault("match_id", f"ud:{mid}")
    if ids.get("team_id"): existing_ids.setdefault("team_id", f"ud:{ids.get('team_id')}")
    if ids.get("opponent_id"): existing_ids.setdefault("opponent_id", f"ud:{ids.get('opponent_id')}")
    if not out.get("match_url"): out["match_url"] = f"underdog://{mid}"
    out["match_format"] = out.get("match_format") or "MULTI_MAP"; out["identity_ids"] = existing_ids
    out["provider_team_verified"] = True; out["v55_preprojection_identity_verified"] = True; out["identity_reconciled"] = True
    out["identity_reconcile_source"] = "Underdog exact current game roster"; out["confirmed_lineup_groups"] = groups or out.get("confirmed_lineup_groups") or []
    out["current_roster_names"] = roster; out["current_roster_verified"] = True; out["lineup_verified"] = True; out["player_in_lineup"] = True
    out["roster_overlap"] = 5; out["source_match_verified"] = True; out["v57_match_context_verified"] = True; out["identity_official_ready"] = True
    return out, True


if "_v55_resolve_prop" in globals():
    _v582_resolve_prop_base = _v55_resolve_prop
    def _v55_resolve_prop(prop):
        source_row, ok = _v582_source_context(prop)
        if ok:
            try: _v55_save_team(source_row.get("player"), source_row.get("team"), source_row.get("opponent"), "Underdog exact current game roster")
            except Exception: pass
            return source_row
        return _v582_resolve_prop_base(prop)

if "_v57_enrich_row" in globals():
    _v582_v57_enrich_base = _v57_enrich_row
    def _v57_enrich_row(row):
        source_row, ok = _v582_source_context(row)
        if ok: return source_row
        return _v582_v57_enrich_base(row)

if "v48_prefetch_provider_data" in globals():
    _v582_prefetch_base = v48_prefetch_provider_data
    def v48_prefetch_provider_data(players, force=False):
        unique = list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
        if not force and callable(globals().get("_v50_profile_available")):
            missing = []
            for p in unique:
                try:
                    if not _v50_profile_available(p): missing.append(p)
                except Exception: missing.append(p)
            unique = missing
        if not unique:
            return {"ok": True, "provider": "v5.8.2 verified cache", "unique_players": 0, "verified_profiles": 0, "remaining": 0, "network_requests": 0, "message": "All requested players were already available in verified persistent cache."}
        return _v582_prefetch_base(unique, force=force)

if "_autofeed_direct_profile_recovery" in globals():
    _v582_direct_recovery_base = _autofeed_direct_profile_recovery
    def _autofeed_direct_profile_recovery(players, max_new=None):
        unique = list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
        missing = []
        if callable(globals().get("_v50_profile_available")):
            for p in unique:
                try:
                    if not _v50_profile_available(p): missing.append(p)
                except Exception: missing.append(p)
        else: missing = unique
        if not missing:
            return {"ok": True, "requested": len(unique), "attempted": 0, "recovered": 0, "skipped_verified": len(unique), "message": "No duplicate direct profile recovery needed."}
        return _v582_direct_recovery_base(missing, max_new=max_new)

if "fetch_underdog_cs2_board" in globals():
    _v582_ud_board_base = fetch_underdog_cs2_board
    def fetch_underdog_cs2_board():
        base_rows, meta = _v582_ud_board_base(); base_rows = list(base_rows or []); meta = dict(meta or {})
        payload = {}
        try: payload = (V58_LAST_UD_PAYLOAD or {}).get("data") or {}
        except Exception: payload = {}
        catalog = _v582_parse_all_underdog_lines(payload) if payload else []; supported = [x for x in catalog if x.get("model_supported")]
        by_key = {}
        for row in base_rows:
            line_id = str(row.get("source_line_id") or "")
            key = ("line", line_id) if line_id else (normalize_name(row.get("player")), float(row.get("line")) if safe_float(row.get("line"), None) is not None else None, str(row.get("start_time") or "")[:16])
            by_key[key] = dict(row)
        recovered = 0
        for row in supported:
            line_id = str(row.get("source_line_id") or ""); key = ("line", line_id) if line_id else (normalize_name(row.get("player")), float(row.get("line")), str(row.get("start_time") or "")[:16])
            old = by_key.get(key)
            if old is None: by_key[key] = dict(row); recovered += 1; continue
            for field in ["team","opponent","matchup","start_time","game_id","sport_id","appearance_id","source_line_id","evidence","underdog_player_id","underdog_match_id","underdog_team_id","underdog_opponent_id","source_identity_ids","source_lineup_groups","source_roster_names","source_player_in_lineup","source_five_player_lineup","source_match_verified","match_format"]:
                if (not old.get(field)) and row.get(field): old[field] = row.get(field)
            old["model_supported"] = True; old["projection_eligible_market"] = True; old["market_scope_verified"] = True; by_key[key] = old
        merged = list(by_key.values())
        if catalog:
            payload_out = {"version":"5.8.2","updated_at":now_iso(),"rows":catalog,"all_cs2_lines":len(catalog),"model_supported_lines":len(supported),"unsupported_visible_lines":max(0,len(catalog)-len(supported)),"recovered_supported_lines":recovered,"source_exact_five_rows":sum(bool(x.get("source_five_player_lineup")) for x in catalog),"source_exact_match_rows":sum(bool(x.get("source_match_verified")) for x in catalog)}
            try: save_json(V582_LIVE_CATALOG_FILE, payload_out, force=True)
            except Exception: pass
            try: st.session_state["cs2_all_live_lines"] = catalog
            except Exception: pass
            meta["v582_live_line_coverage"] = {k:v for k,v in payload_out.items() if k != "rows"}
        meta["rows"] = len(merged); return merged, meta

if "build_full_board" in globals():
    _v582_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        board, status = _v582_board_base(props, deep_enabled)
        provider_match_rows = source_match_rows = exact_ids = five = in_lineup = projection_ready = official_ready = 0
        for idx, item in enumerate(list(board or [])):
            row, _ = _v582_source_context(dict(item or {})); ids = row.get("identity_ids") if isinstance(row.get("identity_ids"), dict) else {}
            exact_ids += int(bool(ids.get("match_id") and ids.get("player_id"))); roster = list(row.get("current_roster_names") or []); five += int(len(roster) == 5); in_lineup += int(bool(row.get("player_in_lineup")))
            url = str(row.get("match_url") or ""); provider_match_rows += int(url.startswith(("bo3://","pandascore://","https://www.hltv.org/","https://bo3.gg/"))); source_match_rows += int(bool(row.get("source_match_verified") and url.startswith("underdog://")))
            projection_ready += int(bool(row.get("projection_data_ready"))); official_ready += int(bool(row.get("official_data_ready"))); board[idx] = row
        health = {"version":"5.7","runtime_layer":"5.8.2","updated_at":now_iso(),"board_rows":len(board or []),"exact_match_player_ids":exact_ids,"five_player_lineups":five,"players_in_lineup":in_lineup,"real_provider_match_rows":provider_match_rows,"real_source_match_rows":source_match_rows,"real_match_rows":provider_match_rows+source_match_rows,"projection_ready_rows":projection_ready,"official_ready_rows":official_ready}
        try: save_json(V57_CONTEXT_HEALTH_FILE, health, force=True)
        except Exception: pass
        status = dict(status or {}); status["v57_context_health"] = health; status["v582_primary_source_fastpath"] = {"exact_ids":exact_ids,"five_player_lineups":five,"provider_matches":provider_match_rows,"source_matches":source_match_rows}
        return board, status

try: APP_VERSION = "CS2 v5.8.2 — FAST PRIMARY-SOURCE DATA + COMPLETE LIVE LINES"
except Exception: pass
# === END ONEWAYPICKZ V5.8.2 PRIMARY-SOURCE LINE + CONTEXT FASTPATH ===
'''


def patch_text(source: str) -> str:
    text = source
    if PATCH_MARKER not in text:
        if MARKER not in text:
            raise RuntimeError("SESSION BOARD LOAD marker not found")
        text = text.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)
    text = text.replace('with st.expander(f"📋 All Current Underdog CS2 Lines ({len(_v58_catalog)})", expanded=False):','with st.expander(f"📋 ALL LIVE UNDERDOG CS2 LINES ({len(_v58_catalog)})", expanded=True):',1)
    text = text.replace('st.caption("Every real CS2 line is visible here. The projection model still runs only verified Maps 1–2 Kills; Headshots/Map 1/other markets are display-only until a separate model is built.")','st.caption("All current Underdog CS2 markets are shown here. Verified Maps 1–2 Kills feed the protected projection model; Headshots/Map 1/other markets remain visible as LINE ONLY until their own model is validated.")',1)
    return text


def patch_app(path="app.py"):
    p = Path(path); old = p.read_text(encoding="utf-8"); new = patch_text(old); changed = new != old
    if changed:
        tmp = p.with_suffix(p.suffix + ".v582.tmp"); tmp.write_text(new, encoding="utf-8"); os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p); compile(p.read_text(encoding="utf-8"), str(p), "exec"); print(f"v5.8.2 patch {'applied' if changed else 'already present'}: {p}")

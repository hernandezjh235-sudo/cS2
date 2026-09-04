from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8 FAST LIVE LINE COVERAGE ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8 FAST LIVE LINE COVERAGE ===
# Data acquisition / visibility / request-dedup only.
# Protected Maps 1-2 kill projection math is unchanged.
AUTOFEED_LIVEBOARD_VERSION = "5.8"
V58_LIVE_CATALOG_FILE = os.path.join(STORAGE_DIR, "cs2_live_line_catalog.json")
V58_LAST_UD_PAYLOAD = {}
V58_MATCH_DISCOVERY_CACHE = {}
V58_MATCH_CONTEXT_CACHE = {}
V58_COLLECTOR_MODE = "collector" in str(globals().get("__name__", "")).lower()


def _v58_market_kind(text, stat_name=""):
    raw = f"{stat_name} | {text}".lower()
    compact = normalize_name(raw)
    maps12 = bool(
        re.search(r"maps?\s*1\s*[-+&/]\s*(?:maps?\s*)?2", raw, re.I)
        or re.search(r"maps?\s*1\s+(?:and\s+)?2", raw, re.I)
        or "maps 1 2" in compact
        or "map 1 2" in compact
    )
    map1 = bool(re.search(r"\bmap\s*1\b", raw, re.I) or re.search(r"\bm1\b", raw, re.I))
    headshots = "headshot" in raw
    kills = bool(re.search(r"\bkills?\b", raw, re.I)) and not headshots
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


def _v58_specific_cs2(*objects):
    text = _object_text(*objects).lower()
    return any(term in text for term in ["counter strike", "counter-strike", "counterstrike", "cs2", "cs:go", "csgo"])


def _v58_parse_all_underdog_lines(data):
    root = _underdog_payload_root(data)
    if not root:
        return []
    line_items = _underdog_collection(root, "over_under_lines", "lines", "projections")
    appearances = _record_map(_underdog_collection(root, "appearances"))
    players = _record_map(_underdog_collection(root, "players", "new_players"))
    games = _record_map(
        _underdog_collection(root, "games", "matches", "events") +
        _underdog_collection(root, "solo_games")
    )
    teams = _record_map(_underdog_collection(root, "teams"))
    sports = _record_map(_underdog_collection(root, "sports", "leagues"))
    if not line_items or not appearances or not players:
        return []

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

        if _underdog_inactive(line_obj, over_under, appearance, game_obj):
            continue
        stat_name = appearance_stat.get("stat") or over_under.get("stat") or line_obj.get("stat") or ""
        text = _object_text(line_obj, over_under, appearance_stat, appearance, player_obj, game_obj, team_obj, sport_obj)
        market, model_supported = _v58_market_kind(text, stat_name)
        if not (_v58_specific_cs2(sport_obj, game_obj, appearance, player_obj, line_obj, over_under) or model_supported):
            continue

        first = str(player_obj.get("first_name") or "").strip()
        last = str(player_obj.get("last_name") or "").strip()
        player = str(player_obj.get("full_name") or player_obj.get("display_name") or f"{first} {last}").strip()
        if not player:
            player = _extract_player_name(player_obj, appearance, appearance_stat, over_under, line_obj)
        line = _extract_line(line_obj, over_under, appearance_stat)
        if not player or line is None:
            continue

        team = str(team_obj.get("name") or team_obj.get("display_name") or player_obj.get("team_name") or appearance.get("team_name") or "").strip()
        home_id = game_obj.get("home_team_id")
        away_id = game_obj.get("away_team_id")
        opponent_obj = {}
        if team_id not in [None, ""]:
            other_id = away_id if str(home_id) == str(team_id) else home_id if str(away_id) == str(team_id) else None
            if other_id not in [None, ""]:
                opponent_obj = teams.get(str(other_id), {})
        opponent = str(opponent_obj.get("name") or opponent_obj.get("display_name") or appearance.get("opponent_name") or "").strip()
        matchup = str(game_obj.get("title") or game_obj.get("display_title") or game_obj.get("name") or appearance.get("matchup") or "").strip()
        start_time = str(
            game_obj.get("scheduled_at") or game_obj.get("starts_at") or game_obj.get("start_time") or
            game_obj.get("begin_at") or game_obj.get("commence_time") or
            appearance.get("scheduled_at") or appearance.get("starts_at") or ""
        ).strip()
        if not _current_line_time(start_time):
            continue

        rows.append({
            "source": "Underdog",
            "prop_id": str(line_obj.get("id") or hashlib.md5(f"{player}|{market}|{line}|{start_time}".encode()).hexdigest()[:12]),
            "player": player,
            "team": team,
            "opponent": opponent,
            "matchup": matchup,
            "start_time": start_time,
            "market": market,
            "market_scope": "maps_1_2" if model_supported else "unsupported_model_market",
            "market_scope_verified": bool(model_supported),
            "model_supported": bool(model_supported),
            "projection_eligible_market": bool(model_supported),
            "source_line_id": str(line_obj.get("id") or ""),
            "appearance_id": str(appearance_id or ""),
            "game_id": str(game_id or ""),
            "sport_id": str(sport_id or ""),
            "stat_name": str(stat_name or ""),
            "line": float(line),
            "evidence": text[:900],
            "source_pulled_at": now_iso(),
        })

    dedup = {}
    for row in rows:
        key = (normalize_name(row.get("player")), normalize_name(row.get("market")), float(row.get("line")), str(row.get("start_time", ""))[:16])
        dedup[key] = row
    return list(dedup.values())


if "http_get_json" in globals():
    _v58_http_get_json_base = http_get_json
    def http_get_json(url, *args, **kwargs):
        data, status = _v58_http_get_json_base(url, *args, **kwargs)
        source = str(args[0] if args else kwargs.get("source", ""))
        if source.strip().lower() == "underdog" and data:
            V58_LAST_UD_PAYLOAD.clear()
            V58_LAST_UD_PAYLOAD.update({"data": data, "url": url, "captured_at": now_iso()})
        return data, status


if "fetch_underdog_cs2_board" in globals():
    _v58_ud_board_base = fetch_underdog_cs2_board
    def fetch_underdog_cs2_board():
        base_rows, meta = _v58_ud_board_base()
        base_rows = list(base_rows or [])
        meta = dict(meta or {})
        payload = V58_LAST_UD_PAYLOAD.get("data")
        catalog = _v58_parse_all_underdog_lines(payload) if payload else []
        supported = [x for x in catalog if x.get("model_supported")]

        by_key = {
            (normalize_name(x.get("player")), float(x.get("line")), str(x.get("start_time", ""))[:16]): dict(x)
            for x in base_rows if safe_float(x.get("line"), None) is not None
        }
        recovered = 0
        for row in supported:
            key = (normalize_name(row.get("player")), float(row.get("line")), str(row.get("start_time", ""))[:16])
            if key not in by_key:
                by_key[key] = dict(row)
                recovered += 1
            else:
                old = by_key[key]
                for field in ["team", "opponent", "matchup", "start_time", "game_id", "sport_id", "appearance_id", "source_line_id", "evidence"]:
                    if not old.get(field) and row.get(field):
                        old[field] = row.get(field)
                old["model_supported"] = True
                old["projection_eligible_market"] = True
                old["market_scope_verified"] = True
                by_key[key] = old
        merged = list(by_key.values())

        if catalog:
            payload_out = {
                "version": "5.8",
                "updated_at": now_iso(),
                "rows": catalog,
                "all_cs2_lines": len(catalog),
                "model_supported_lines": len(supported),
                "unsupported_visible_lines": max(0, len(catalog) - len(supported)),
                "recovered_supported_lines": recovered,
            }
            try:
                save_json(V58_LIVE_CATALOG_FILE, payload_out, force=True)
            except Exception:
                pass
            try:
                st.session_state["cs2_all_live_lines"] = catalog
            except Exception:
                pass
            meta["v58_live_line_coverage"] = {k: v for k, v in payload_out.items() if k != "rows"}
        meta["rows"] = len(merged)
        return merged, meta


# The web process should render from persistent verified data immediately while
# the independent collector performs slow provider recovery in the background.
# This removes network profile sweeps from the user-facing refresh path.
if "v48_prefetch_provider_data" in globals():
    _v58_prefetch_base = v48_prefetch_provider_data
    def v48_prefetch_provider_data(players, force=False):
        if V58_COLLECTOR_MODE:
            return _v58_prefetch_base(players, force=force)
        unique = list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
        loaded = 0
        if callable(globals().get("_v50_load_saved_profiles")):
            try:
                loaded = int(_v50_load_saved_profiles(unique) or 0)
            except Exception:
                loaded = 0
        covered = 0
        if callable(globals().get("_v50_profile_available")):
            for player in unique:
                try:
                    covered += int(bool(_v50_profile_available(player)))
                except Exception:
                    pass
        return {
            "ok": covered > 0,
            "provider": "v5.8 cache-first web runtime",
            "unique_players": len(unique),
            "verified_profiles": covered,
            "loaded_from_saved_cache": loaded,
            "remaining": max(0, len(unique) - covered),
            "network_requests": 0,
            "background_collector": True,
            "message": f"Web refresh used verified persistent cache for {covered}/{len(unique)} players; background collector is filling the rest.",
        }


# Deduplicate match discovery/context calls by matchup during one refresh. Later
# rows in the same match reuse the first verified provider response.
if "discover_bo3_match" in globals():
    _v58_bo3_discover_base = discover_bo3_match
    def discover_bo3_match(team, opponent, player=""):
        key = "bo3|" + "|".join(sorted([normalize_team(team), normalize_team(opponent)]))
        if key in V58_MATCH_DISCOVERY_CACHE:
            return V58_MATCH_DISCOVERY_CACHE[key]
        out = _v58_bo3_discover_base(team, opponent, player)
        V58_MATCH_DISCOVERY_CACHE[key] = out
        return out

if "discover_hltv_match" in globals():
    _v58_hltv_discover_base = discover_hltv_match
    def discover_hltv_match(team, opponent, player=""):
        key = "hltv|" + "|".join(sorted([normalize_team(team), normalize_team(opponent)]))
        if key in V58_MATCH_DISCOVERY_CACHE:
            return V58_MATCH_DISCOVERY_CACHE[key]
        out = _v58_hltv_discover_base(team, opponent, player)
        V58_MATCH_DISCOVERY_CACHE[key] = out
        return out

if "fetch_match_context" in globals():
    _v58_context_base = fetch_match_context
    def fetch_match_context(match_url):
        key = str(match_url or "")
        if key and key in V58_MATCH_CONTEXT_CACHE:
            return V58_MATCH_CONTEXT_CACHE[key]
        out = _v58_context_base(match_url)
        if key:
            V58_MATCH_CONTEXT_CACHE[key] = out
        return out

try:
    APP_VERSION = "CS2 v5.8 — FAST VERIFIED DATA + COMPLETE LIVE LINE COVERAGE"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8 FAST LIVE LINE COVERAGE ===
'''


def patch_text(source: str) -> str:
    text = source
    if PATCH_MARKER not in text:
        if MARKER not in text:
            raise RuntimeError("SESSION BOARD LOAD marker not found")
        text = text.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)

    # Make the browser refresh cache-first by default. The collector still runs
    # the full verified recovery path independently.
    old_fast = 'fast_refresh_enabled = st.checkbox("Fast refresh / prevent hangs", value=False)'
    new_fast = 'fast_refresh_enabled = st.checkbox("Fast refresh / prevent hangs", value=os.getenv("CS2_WEB_FAST_REFRESH", "true").strip().lower() not in {"0", "false", "no", "off"})'
    if old_fast in text:
        text = text.replace(old_fast, new_fast, 1)

    # Surface every real current CS2 line without feeding unsupported markets
    # into the Maps 1-2 Kills projection engine.
    live_anchor = '    st.caption("Ranked by Official → Playable → Track → Pass, then by best-win score, model probability, and projection edge.")'
    if live_anchor in text and 'v58_all_current_cs2_lines_expander' not in text:
        live_extra = live_anchor + r'''
    _v58_catalog = st.session_state.get("cs2_all_live_lines") or []
    if not _v58_catalog:
        try:
            _v58_saved_catalog = load_json(V58_LIVE_CATALOG_FILE, {}) or {}
            _v58_catalog = list(_v58_saved_catalog.get("rows") or []) if isinstance(_v58_saved_catalog, dict) else []
        except Exception:
            _v58_catalog = []
    if _v58_catalog:
        with st.expander(f"📋 All Current Underdog CS2 Lines ({len(_v58_catalog)})", expanded=False):
            st.caption("Every real CS2 line is visible here. The projection model still runs only verified Maps 1–2 Kills; Headshots/Map 1/other markets are display-only until a separate model is built.")
            _v58_line_df = pd.DataFrame([{
                "Player": x.get("player"), "Market": x.get("market"), "Line": x.get("line"),
                "Team": x.get("team"), "Opponent": x.get("opponent"), "Match": x.get("matchup"),
                "Start": x.get("start_time"), "Model": "Maps 1-2 Kills" if x.get("model_supported") else "DISPLAY ONLY",
            } for x in _v58_catalog])
            st.dataframe(_v58_line_df, use_container_width=True, hide_index=True, key="v58_all_current_cs2_lines_expander")
'''
        text = text.replace(live_anchor, live_extra, 1)

    return text


def patch_app(path="app.py"):
    p = Path(path)
    old = p.read_text(encoding="utf-8")
    new = patch_text(old)
    changed = new != old
    if changed:
        tmp = p.with_suffix(p.suffix + ".v58.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8 patch {'applied' if changed else 'already present'}: {p}")

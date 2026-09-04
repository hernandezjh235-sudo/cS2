from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.8 AUTHORITATIVE SOURCE IDENTITY + PROVIDER RECOVERY ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.8 AUTHORITATIVE SOURCE IDENTITY + PROVIDER RECOVERY ===
# Data acquisition / current identity / provider-context repair only.
# Protected Maps 1-2 Kills projection math, probability math, side selection,
# thresholds, and confidence are unchanged.
AUTOFEED_COMPLETION_V588_VERSION = "5.8.8"
V588_SOURCE_CONTEXT = {}


def _v588_fields(obj):
    if not isinstance(obj, dict):
        return {}
    out = dict(obj)
    try:
        a = attrs(obj)
        if isinstance(a, dict):
            out.update(a)
    except Exception:
        pass
    return out


def _v588_id(obj, *keys):
    raw = _v588_fields(obj)
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            if isinstance(value, dict):
                value = value.get("id") or value.get("uuid")
            if value not in (None, ""):
                return str(value)
    return ""


def _v588_name(obj):
    raw = _v588_fields(obj)
    for key in ("name", "display_name", "full_name", "title", "short_name", "abbreviation"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _v588_rel_id(obj, *keys):
    if not isinstance(obj, dict):
        return ""
    rels = obj.get("relationships") if isinstance(obj.get("relationships"), dict) else {}
    for key in keys:
        node = rels.get(key)
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if isinstance(data, dict) and data.get("id") not in (None, ""):
            return str(data.get("id"))
    return ""


def _v588_matchup_sides(text):
    try:
        a, b = _teams_from_matchup(str(text or ""))
        a, b = str(a or "").strip(), str(b or "").strip()
        if a and b and normalize_team(a) != normalize_team(b):
            return a, b
    except Exception:
        pass
    raw = str(text or "").strip()
    m = re.split(r"\s+(?:vs\.?|v\.?|versus)\s+", raw, maxsplit=1, flags=re.I)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        return m[0].strip(), m[1].strip()
    return "", ""


def _v588_game_name_maps(data):
    root = _underdog_payload_root(data)
    if not root:
        return {}, {}
    team_names = {}
    games_by_id = {}

    try:
        team_rows = _underdog_collection(root, "teams")
    except Exception:
        team_rows = []
    for raw in list(team_rows or []):
        if not isinstance(raw, dict):
            continue
        tid = _v588_id(raw, "id", "team_id")
        name = _v588_name(raw)
        if tid and name:
            team_names[tid] = name

    try:
        games = _underdog_collection(root, "games", "matches", "events") + _underdog_collection(root, "solo_games")
    except Exception:
        games = []

    for raw_game in list(games or []):
        if not isinstance(raw_game, dict):
            continue
        game = _v588_fields(raw_game)
        gid = _v588_id(raw_game, "id", "game_id", "match_id", "event_id")
        if not gid:
            continue
        home_id = _v588_id(game, "home_team_id", "home_id", "team1_id") or _v588_rel_id(raw_game, "home_team", "home", "team1")
        away_id = _v588_id(game, "away_team_id", "away_id", "team2_id") or _v588_rel_id(raw_game, "away_team", "away", "team2")

        for side_key, side_id in (("home_team", home_id), ("away_team", away_id), ("home", home_id), ("away", away_id), ("team1", home_id), ("team2", away_id)):
            node = game.get(side_key)
            if isinstance(node, dict):
                nid = _v588_id(node, "id", "team_id") or side_id
                nname = _v588_name(node)
                if nid and nname:
                    team_names[nid] = nname

        for list_key in ("teams", "participants", "competitors"):
            values = game.get(list_key)
            if not isinstance(values, list):
                continue
            for node in values:
                if not isinstance(node, dict):
                    continue
                nid = _v588_id(node, "id", "team_id")
                nname = _v588_name(node)
                if nid and nname:
                    team_names[nid] = nname

        title = str(game.get("title") or game.get("display_title") or game.get("name") or game.get("matchup") or "").strip()
        side_a, side_b = _v588_matchup_sides(title)
        # Underdog game titles are home-vs-away. The parser already uses these
        # same home/away IDs to derive opponent_id, so this is an ID-backed name
        # recovery rather than a player-team guess.
        if home_id and away_id and side_a and side_b:
            team_names.setdefault(home_id, side_a)
            team_names.setdefault(away_id, side_b)

        games_by_id[gid] = {
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team_name": team_names.get(home_id, "") if home_id else "",
            "away_team_name": team_names.get(away_id, "") if away_id else "",
            "title": title,
        }
    return team_names, games_by_id


def _v588_line_key(row):
    row = row if isinstance(row, dict) else {}
    lid = str(row.get("source_line_id") or row.get("prop_id") or "").strip()
    if lid:
        return ("line", lid)
    return (
        normalize_name(row.get("player") or ""),
        str(row.get("underdog_match_id") or row.get("game_id") or ""),
        safe_float(row.get("line"), None),
        str(row.get("start_time") or "")[:16],
    )


def _v588_correct_catalog_rows(rows, data=None):
    rows = [dict(x) for x in list(rows or []) if isinstance(x, dict)]
    team_names, games = _v588_game_name_maps(data or {}) if data else ({}, {})

    # Learn any already-explicit exact team names before applying title fallback.
    for row in rows:
        tid = str(row.get("underdog_team_id") or ((row.get("source_identity_ids") or {}).get("team_id") if isinstance(row.get("source_identity_ids"), dict) else "") or "").strip()
        oid = str(row.get("underdog_opponent_id") or ((row.get("source_identity_ids") or {}).get("opponent_id") if isinstance(row.get("source_identity_ids"), dict) else "") or "").strip()
        team = str(row.get("team") or "").strip()
        opp = str(row.get("opponent") or "").strip()
        if tid and team:
            team_names.setdefault(tid, team)
        if oid and opp:
            team_names.setdefault(oid, opp)

    # Roster aggregation is keyed only by the exact Underdog game/team IDs.
    roster_by_side = {}
    for row in rows:
        gid = str(row.get("underdog_match_id") or row.get("game_id") or "").strip()
        tid = str(row.get("underdog_team_id") or ((row.get("source_identity_ids") or {}).get("team_id") if isinstance(row.get("source_identity_ids"), dict) else "") or "").strip()
        if not (gid and tid):
            continue
        bucket = roster_by_side.setdefault((gid, tid), [])
        candidates = list(row.get("source_roster_names") or []) + [row.get("player")]
        for name in candidates:
            name = str(name or "").strip()
            if name and max([name_similarity(name, x) for x in bucket] or [0.0]) < .94:
                bucket.append(name)

    fixed = []
    for row in rows:
        gid = str(row.get("underdog_match_id") or row.get("game_id") or "").strip()
        src = dict(row.get("source_identity_ids") or {})
        pid = str(src.get("player_id") or row.get("underdog_player_id") or "").strip()
        mid = str(src.get("match_id") or row.get("underdog_match_id") or row.get("game_id") or "").strip()
        tid = str(src.get("team_id") or row.get("underdog_team_id") or "").strip()
        oid = str(src.get("opponent_id") or row.get("underdog_opponent_id") or "").strip()
        game = games.get(gid) or games.get(mid) or {}

        # If the raw teams collection is incomplete, use the game home/away IDs
        # plus title order. This directly repairs the blank-team defect seen in
        # the live audit without consulting stale player-team aliases.
        if tid and tid not in team_names and game:
            if tid == str(game.get("home_team_id") or "") and game.get("home_team_name"):
                team_names[tid] = str(game.get("home_team_name"))
            elif tid == str(game.get("away_team_id") or "") and game.get("away_team_name"):
                team_names[tid] = str(game.get("away_team_name"))
        if oid and oid not in team_names and game:
            if oid == str(game.get("home_team_id") or "") and game.get("home_team_name"):
                team_names[oid] = str(game.get("home_team_name"))
            elif oid == str(game.get("away_team_id") or "") and game.get("away_team_name"):
                team_names[oid] = str(game.get("away_team_name"))

        team = str(team_names.get(tid) or row.get("team") or "").strip()
        opponent = str(team_names.get(oid) or row.get("opponent") or "").strip()
        matchup = str(row.get("matchup") or game.get("title") or "").strip()
        side_a, side_b = _v588_matchup_sides(matchup)
        # Final consistency check: only accept names that correspond to this exact
        # matchup. It prevents a stale persistent team alias from becoming source truth.
        if team and opponent and side_a and side_b:
            valid = (
                (_team_name_matches(team, side_a) and _team_name_matches(opponent, side_b)) or
                (_team_name_matches(team, side_b) and _team_name_matches(opponent, side_a))
            )
            if not valid:
                team = opponent = ""

        own_roster = list(roster_by_side.get((mid, tid), roster_by_side.get((gid, tid), []))) if tid else []
        opp_roster = list(roster_by_side.get((mid, oid), roster_by_side.get((gid, oid), []))) if oid else []
        player = str(row.get("player") or "").strip()
        player_in = bool(own_roster and player and max([name_similarity(player, x) for x in own_roster] or [0.0]) >= .84)
        exact_five = bool(len(own_roster) == 5 and player_in)
        groups = []
        if team and own_roster:
            groups.append({"team": team, "team_id": tid, "players": own_roster})
        if opponent and opp_roster:
            groups.append({"team": opponent, "team_id": oid, "players": opp_roster})

        exact_identity = bool(player and team and opponent and pid and mid and tid and oid)
        if exact_identity:
            row.update({
                "team": team,
                "opponent": opponent,
                "source_team_name": team,
                "source_opponent_name": opponent,
                "source_team_name_verified": True,
                "source_identity_ids": {"player_id": pid, "match_id": mid, "team_id": tid, "opponent_id": oid},
                "source_match_verified": True,
                "source_identity_verified_v588": True,
                "identity_reconcile_source": "Underdog exact game/team IDs + game-side names",
            })
            if not row.get("matchup"):
                row["matchup"] = f"{team} vs {opponent}"
        if groups:
            row["source_lineup_groups"] = groups
        if own_roster:
            row["source_roster_names"] = own_roster
        row["source_player_in_lineup"] = player_in
        row["source_five_player_lineup"] = exact_five
        if exact_five:
            row["current_roster_names"] = own_roster
            row["current_roster_verified"] = True
            row["lineup_verified"] = True
            row["player_in_lineup"] = True
            row["roster_overlap"] = 5
            row["confirmed_lineup_groups"] = groups or list(row.get("confirmed_lineup_groups") or [])
        if row.get("model_supported") and row.get("market_scope_verified") and str(row.get("match_format") or "").upper() in {"", "UNKNOWN"}:
            row["match_format"] = "MULTI_MAP"

        V588_SOURCE_CONTEXT[_v588_line_key(row)] = dict(row)
        fixed.append(row)
    return fixed


if "_v582_parse_all_underdog_lines" in globals():
    _v588_parse_base = _v582_parse_all_underdog_lines
    def _v582_parse_all_underdog_lines(data):
        return _v588_correct_catalog_rows(_v588_parse_base(data), data)


def _v588_source_record(row):
    row = row if isinstance(row, dict) else {}
    src = V588_SOURCE_CONTEXT.get(_v588_line_key(row))
    return dict(src or {}) if isinstance(src, dict) else {}


def _v588_apply_authoritative_source(row):
    out = dict(row or {})
    src = _v588_source_record(out)
    # A row can itself carry the authoritative fields even if it did not pass
    # through this process's in-memory catalog (e.g. restored GitHub cache).
    if not src and out.get("source_team_name_verified"):
        src = dict(out)
    team = str(src.get("source_team_name") or "").strip()
    opponent = str(src.get("source_opponent_name") or "").strip()
    sids = dict(src.get("source_identity_ids") or out.get("source_identity_ids") or {})
    pid = str(sids.get("player_id") or out.get("underdog_player_id") or "").strip()
    mid = str(sids.get("match_id") or out.get("underdog_match_id") or out.get("game_id") or "").strip()
    tid = str(sids.get("team_id") or out.get("underdog_team_id") or "").strip()
    oid = str(sids.get("opponent_id") or out.get("underdog_opponent_id") or "").strip()
    exact = bool(team and opponent and pid and mid and tid and oid and (src.get("source_team_name_verified") or out.get("source_team_name_verified")))
    if not exact:
        return out

    # Source current-game IDs outrank stale persistent aliases for team/opponent.
    out["team"] = team
    out["opponent"] = opponent
    out["source_team_name"] = team
    out["source_opponent_name"] = opponent
    out["source_team_name_verified"] = True
    out["source_identity_verified_v588"] = True
    out["source_match_verified"] = True
    out["provider_team_verified"] = True
    out["v55_preprojection_identity_verified"] = True
    out["identity_reconciled"] = True
    out["identity_reconcile_source"] = "v5.8.8 authoritative Underdog game/team IDs"
    out["v585_premodel_context"] = True
    out["v586_premodel_context"] = True
    out["v588_premodel_context"] = True
    out["source_identity_ids"] = {"player_id": pid, "match_id": mid, "team_id": tid, "opponent_id": oid}

    ids = dict(out.get("identity_ids") or {})
    provider_url = str(out.get("provider_match_url") or out.get("match_url") or "").strip()
    provider_real = bool(callable(globals().get("_v587_provider_url")) and _v587_provider_url(provider_url))
    ids.setdefault("player_id", f"ud:{pid}")
    ids["team_id"] = ids.get("team_id") if provider_real and ids.get("team_id") and not str(ids.get("team_id")).startswith("ud:") else f"ud:{tid}"
    ids["opponent_id"] = ids.get("opponent_id") if provider_real and ids.get("opponent_id") and not str(ids.get("opponent_id")).startswith("ud:") else f"ud:{oid}"
    if not provider_real:
        ids["match_id"] = f"ud:{mid}"
    out["identity_ids"] = ids
    if not provider_real and (not str(out.get("match_url") or "").strip() or str(out.get("match_url") or "").startswith(("mirror://", "bridge://", "underdog://"))):
        out["match_url"] = f"underdog://{mid}"
    if str(out.get("match_format") or "").upper() in {"", "UNKNOWN"} and out.get("market_scope_verified"):
        out["match_format"] = "MULTI_MAP"

    for field in ("source_lineup_groups", "source_roster_names"):
        if src.get(field):
            out[field] = src.get(field)
    roster = [str(x or "").strip() for x in list(src.get("source_roster_names") or out.get("source_roster_names") or []) if str(x or "").strip()]
    player = str(out.get("player") or "").strip()
    player_in = bool(roster and player and max([name_similarity(player, x) for x in roster] or [0.0]) >= .84)
    if len(roster) == 5 and player_in:
        out["current_roster_names"] = roster
        out["current_roster_verified"] = True
        out["lineup_verified"] = True
        out["player_in_lineup"] = True
        out["roster_overlap"] = 5
        if src.get("source_lineup_groups"):
            out["confirmed_lineup_groups"] = list(src.get("source_lineup_groups") or [])

    try:
        if callable(globals().get("_v55_save_team")):
            _v55_save_team(player, team, opponent, "v5.8.8 exact Underdog current-game identity")
    except Exception:
        pass
    return out


if "_v586_source" in globals():
    _v588_v586_source_base = _v586_source
    def _v586_source(row):
        prepared = _v588_apply_authoritative_source(row)
        out, ok = _v588_v586_source_base(prepared)
        out = _v588_apply_authoritative_source(out)
        return out, bool(ok or out.get("source_identity_verified_v588"))

if "_v586_restore" in globals():
    _v588_v586_restore_base = _v586_restore
    def _v586_restore(row):
        return _v588_apply_authoritative_source(_v588_v586_restore_base(row))


# Give provider discovery one extra verified-context path after source identity is
# corrected. Only a BO3/HLTV/PandaScore provider URL is accepted; underdog://
# remains source identity and can never masquerade as provider context.
if "_v587_discover_provider" in globals():
    _v588_discover_base = _v587_discover_provider
    def _v587_discover_provider(row):
        prepared = _v588_apply_authoritative_source(row)
        out = _v588_discover_base(prepared)
        if _v587_provider_url(out.get("provider_match_url") or out.get("match_url")):
            return out
        player = str(out.get("player") or "").strip()
        team = str(out.get("team") or "").strip()
        opponent = str(out.get("opponent") or "").strip()
        if not (player and team and opponent):
            return out
        if callable(globals().get("_v57_real_context")):
            try:
                url, ctx, status = _v57_real_context({**out, "matchup": f"{team} vs {opponent}"})
                if _v587_provider_url(url) and isinstance(ctx, dict) and ctx:
                    temp = dict(out)
                    temp["provider_match_url"] = url
                    temp["match_url"] = url
                    # Seed the v5.8.7 per-match cache so the normal verified provider
                    # merge runs exactly once and remains the source of truth.
                    key = _v587_context_key(team, opponent)
                    V587_PROVIDER_CONTEXT[key] = {"url": url, "context": dict(ctx), "status": dict(status or {})}
                    return _v588_discover_base(temp)
            except Exception:
                pass
        return out


if "fetch_underdog_cs2_board" in globals():
    _v588_ud_base = fetch_underdog_cs2_board
    def fetch_underdog_cs2_board():
        rows, meta = _v588_ud_base()
        rows = [_v588_apply_authoritative_source(x) for x in list(rows or []) if isinstance(x, dict)]
        meta = dict(meta or {})
        exact = sum(bool(x.get("source_identity_verified_v588")) for x in rows)
        five = sum(bool(x.get("source_five_player_lineup") or (len(list(x.get("current_roster_names") or [])) == 5 and x.get("player_in_lineup"))) for x in rows)
        blanks = sum(bool(x.get("model_supported") and x.get("market_scope_verified") and ((x.get("underdog_team_id") and not x.get("team")) or (x.get("underdog_opponent_id") and not x.get("opponent")))) for x in rows)
        meta["v588_authoritative_source_identity"] = {
            "version": "5.8.8", "rows": len(rows), "exact_source_identity_rows": exact,
            "five_player_source_rows": five, "supported_exact_id_blank_team_rows": blanks,
        }
        return rows, meta


# Final board pass: authoritative source side first, then real provider context,
# then strict readiness. This changes no projection value or probability.
if "build_full_board" in globals():
    _v588_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        prepared = [_v588_apply_authoritative_source(x) for x in list(props or []) if isinstance(x, dict)]
        board, status = _v588_board_base(prepared, deep_enabled)
        board = [dict(x) for x in list(board or []) if isinstance(x, dict)]
        status = dict(status or {})
        exact_source = five = provider_rows = profile_rows = core_rows = deep_rows = projection_ready = official_ready = 0
        blank_source_names = 0
        missing = Counter()
        for i, row in enumerate(board):
            row = _v588_apply_authoritative_source(row)
            if row.get("model_supported") and row.get("market_scope_verified"):
                try:
                    row = _v587_discover_provider(row)
                except Exception:
                    pass
                try:
                    row = _v588_apply_authoritative_source(row)
                except Exception:
                    pass
                if callable(globals().get("_v55_ready")):
                    try:
                        rd = _v55_ready(row)
                        row["data_readiness"] = rd
                        row["projection_data_ready"] = bool(rd.get("projection_ready"))
                        row["official_data_ready"] = bool(rd.get("official_ready"))
                        row["data_readiness_score"] = rd.get("readiness_score")
                        projection_ready += int(bool(rd.get("projection_ready")))
                        official_ready += int(bool(rd.get("official_ready")))
                        if not rd.get("projection_ready"):
                            missing.update(rd.get("missing_projection") or [])
                    except Exception:
                        pass
            exact_source += int(bool(row.get("source_identity_verified_v588")))
            five += int(len(list(row.get("current_roster_names") or [])) == 5 and bool(row.get("player_in_lineup")))
            provider_rows += int(_v587_provider_url(row.get("provider_match_url") or row.get("match_url"))) if callable(globals().get("_v587_provider_url")) else 0
            profile_rows += int((safe_int(row.get("profile_maps"), 0) or 0) >= MIN_PROFILE_MAPS and safe_float(row.get("base_kpr"), None) is not None)
            core_rows += int(bool(row.get("core_kpr_verified")))
            deep_rows += int((safe_int(row.get("team_recent_maps"), 0) or 0) > 0 and (safe_int(row.get("opponent_mapstats_samples"), 0) or 0) > 0)
            if row.get("model_supported") and row.get("market_scope_verified") and row.get("source_identity_ids") and (not row.get("team") or not row.get("opponent")):
                blank_source_names += 1
            board[i] = row

        health = {
            "version": "5.8.8", "runtime_layer": "5.8.8", "updated_at": now_iso(),
            "board_rows": len(board), "exact_source_identity_rows": exact_source,
            "exact_match_player_ids": sum(bool((x.get("identity_ids") or {}).get("match_id") and (x.get("identity_ids") or {}).get("player_id")) for x in board if isinstance(x.get("identity_ids"), dict)),
            "five_player_lineups": five, "players_in_lineup": sum(bool(x.get("player_in_lineup")) for x in board),
            "real_provider_match_rows": provider_rows,
            "real_source_match_rows": sum(bool(x.get("source_match_verified") and str(x.get("match_url") or "").startswith("underdog://")) for x in board),
            "real_match_rows": sum(bool(str(x.get("match_url") or "") and not str(x.get("match_url") or "").startswith(("mirror://", "bridge://"))) for x in board),
            "verified_profile_rows": profile_rows, "core_kpr_rows": core_rows,
            "deep_team_map_rows": deep_rows, "projection_ready_rows": projection_ready,
            "official_ready_rows": official_ready, "premodel_context_rows": sum(bool(x.get("v588_premodel_context") or x.get("v586_premodel_context") or x.get("v585_premodel_context")) for x in board),
            "supported_exact_id_blank_team_rows": blank_source_names,
            "non_cs2_rows_visible": sum(not bool(_v586_is_cs2(x)) for x in board) if callable(globals().get("_v586_is_cs2")) else 0,
            "projection_math_changed": False,
        }
        readiness = {
            "version": "5.8.8", "updated_at": now_iso(), "board_rows": len(board),
            "projection_ready_rows": projection_ready, "official_ready_rows": official_ready,
            "verified_identity_rows": exact_source, "verified_profile_rows": profile_rows,
            "core_kpr_rows": core_rows, "real_provider_match_rows": provider_rows,
            "deep_team_map_rows": deep_rows, "supported_exact_id_blank_team_rows": blank_source_names,
            "missing_projection_requirements": dict(missing),
            "source_gate": "exact Underdog player/game/team IDs + ID-backed matchup side names",
        }
        try:
            save_json(V57_CONTEXT_HEALTH_FILE, health, force=True)
            save_json(V55_READINESS_FILE, readiness, force=True)
        except Exception:
            pass
        try:
            op = load_json(V56_OPERATIONAL_FILE, {}) or {}
            if isinstance(op, dict):
                op.update({
                    "version": "5.8.8", "runtime_layer": "5.8.8", "updated_at": now_iso(),
                    "board_rows": len(board), "verified_profile_rows": profile_rows,
                    "verified_identity_rows": exact_source, "real_match_rows": health["real_match_rows"],
                    "real_provider_match_rows": provider_rows, "deep_team_map_rows": deep_rows,
                    "projection_ready_rows": projection_ready, "official_ready_rows": official_ready,
                    "pipeline_ready": bool(projection_ready > 0), "projection_math_changed": False,
                })
                save_json(V56_OPERATIONAL_FILE, op, force=True)
        except Exception:
            pass
        status["v57_context_health"] = health
        status["v55_data_readiness"] = readiness
        status["v588_completion"] = health
        return board, status


try:
    APP_VERSION = "CS2 v5.8.8 — AUTHORITATIVE SOURCE IDENTITY + COMPLETE PROVIDER RECOVERY"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.8 AUTHORITATIVE SOURCE IDENTITY + PROVIDER RECOVERY ===
'''


def patch_text(source: str) -> str:
    if PATCH_MARKER in source:
        return source
    if MARKER not in source:
        raise RuntimeError("SESSION BOARD LOAD marker not found")
    return source.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)


def patch_app(path="app.py"):
    p = Path(path)
    old = p.read_text(encoding="utf-8")
    new = patch_text(old)
    changed = new != old
    if changed:
        tmp = p.with_suffix(p.suffix + ".v588.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8.8 patch {'applied' if changed else 'already present'}: {p}")
from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.7 VERIFIED MATCH + LINEUP CONTEXT ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.7 VERIFIED MATCH + LINEUP CONTEXT ===
# Identity/match/roster persistence only. Protected projection math is unchanged.
AUTOFEED_CONTEXT_VERSION = "5.7"
V57_CONTEXT_CACHE = {}
V57_CONTEXT_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_context_health.json")


def _v57_player_id(row):
    ids = row.get("identity_ids") if isinstance(row.get("identity_ids"), dict) else {}
    pid = str(ids.get("player_id") or "").strip()
    if pid:
        return pid
    player = str(row.get("player") or "").strip()
    try:
        rec = lookup_database_player(player) or {}
        pid = str((rec.get("identity_ids") or {}).get("player_id") or rec.get("player_id") or "").strip()
        if pid:
            return pid
    except Exception:
        pass
    try:
        alias = _alias_record(player) or {}
        pid = str(alias.get("hltv_player_id") or alias.get("player_id") or "").strip()
        if pid:
            return pid
    except Exception:
        pass
    try:
        aliases = load_json(PLAYER_ALIAS_FILE, {}) or {}
        rec = aliases.get(normalize_name(player)) if isinstance(aliases, dict) else {}
        if isinstance(rec, dict):
            pid = str(rec.get("hltv_player_id") or rec.get("player_id") or "").strip()
            if pid:
                return pid
    except Exception:
        pass
    try:
        profiles = (V48_RUNTIME.get("profiles") or {}) if isinstance(V48_RUNTIME, dict) else {}
        rec = profiles.get(normalize_name(player)) if isinstance(profiles, dict) else {}
        if isinstance(rec, dict):
            pid = str(rec.get("player_id") or (rec.get("identity_ids") or {}).get("player_id") or "").strip()
            if pid:
                return pid
    except Exception:
        pass
    return ""


def _v57_matchup(row):
    try:
        return _teams_from_matchup(str(row.get("matchup") or row.get("evidence") or row.get("event") or ""))
    except Exception:
        return "", ""


def _v57_real_context(row):
    player = str(row.get("player") or "").strip()
    a, b = _v57_matchup(row)
    if not player or not a or not b:
        return "", {}, {"ok": False, "warning": "player/matchup missing"}
    key = "|".join(sorted([normalize_team(a), normalize_team(b)]))
    if key in V57_CONTEXT_CACHE:
        return V57_CONTEXT_CACHE[key]

    url = ""
    meta = {}
    if callable(globals().get("discover_bo3_match")):
        try:
            url, meta = discover_bo3_match(a, b, player)
        except Exception:
            url, meta = "", {}
    if not url and callable(globals().get("discover_hltv_match")):
        try:
            candidate, cmeta = discover_hltv_match(a, b, player)
            if candidate and not str(candidate).startswith(("mirror://", "bridge://")):
                url, meta = candidate, cmeta or {}
        except Exception:
            pass
    if not url:
        out = ("", {}, {"ok": False, "warning": "real provider match not recovered"})
        V57_CONTEXT_CACHE[key] = out
        return out
    try:
        ctx, status = fetch_match_context(url)
    except Exception as exc:
        ctx, status = {}, {"ok": False, "warning": f"match context failed: {type(exc).__name__}: {exc}"}
    ctx = dict(ctx or {})
    status = dict(status or {})
    ctx.setdefault("match_url", url)
    if meta:
        status.setdefault("discovery", meta)
    out = (url, ctx, status)
    V57_CONTEXT_CACHE[key] = out
    return out


def _v57_team_record(ctx, team):
    best = (0.0, {})
    for rec in list((ctx or {}).get("teams") or []):
        if not isinstance(rec, dict):
            continue
        score = name_similarity(team, str(rec.get("name") or ""))
        if score > best[0]:
            best = (score, rec)
    return dict(best[1] or {}) if best[0] >= .80 else {}


def _v57_group_for_player(player, team, groups):
    best = (0.0, {})
    for group in list(groups or []):
        if not isinstance(group, dict):
            continue
        roster = [str(x or "").strip() for x in list(group.get("players") or group.get("roster") or []) if str(x or "").strip()]
        if not roster:
            continue
        player_score = max([name_similarity(player, x) for x in roster] or [0.0])
        team_score = name_similarity(team, str(group.get("team") or group.get("name") or "")) if team else 0.0
        score = player_score * .72 + team_score * .28
        if player_score >= .84 and score > best[0]:
            best = (score, {**group, "players": roster})
    return dict(best[1] or {})


def _v57_enrich_row(row):
    if not isinstance(row, dict):
        return row
    player = str(row.get("player") or "").strip()
    if not player or (safe_int(row.get("profile_maps"), 0) or 0) < MIN_PROFILE_MAPS:
        return row

    url, ctx, status = _v57_real_context(row)
    if not url or not ctx:
        row["v57_match_context_verified"] = False
        return row

    a, b = _v57_matchup(row)
    groups = list(ctx.get("lineup_groups") or [])
    if not groups:
        for team_rec in list(ctx.get("teams") or [])[:2]:
            if not isinstance(team_rec, dict):
                continue
            roster = list(team_rec.get("players") or team_rec.get("roster") or [])
            if roster:
                groups.append({"team": team_rec.get("name"), "team_id": team_rec.get("team_id"), "players": roster})

    known_team = str(row.get("team") or "").strip()
    group = _v57_group_for_player(player, known_team, groups)
    resolved_team = str(group.get("team") or group.get("name") or "").strip()
    if resolved_team and a and b:
        if _team_name_matches(resolved_team, a):
            team, opponent = a, b
        elif _team_name_matches(resolved_team, b):
            team, opponent = b, a
        else:
            team, opponent = known_team, str(row.get("opponent") or "")
    else:
        team, opponent = known_team, str(row.get("opponent") or "")

    team_rec = _v57_team_record(ctx, team)
    opp_rec = _v57_team_record(ctx, opponent)
    player_id = _v57_player_id(row)
    match_id = str(ctx.get("provider_match_id") or status.get("match_id") or "").strip()
    if not match_id and callable(globals().get("_match_id_from_url")):
        try:
            match_id = str(_match_id_from_url(url) or "").strip()
        except Exception:
            pass

    roster = [str(x or "").strip() for x in list(group.get("players") or []) if str(x or "").strip()]
    player_in_roster = bool(roster and max([name_similarity(player, x) for x in roster] or [0.0]) >= .84)
    five_player = len(roster) == 5
    exact_lineup = bool(status.get("exact_lineup"))

    ids = dict(row.get("identity_ids") or {})
    ids.update({
        "player_id": player_id or ids.get("player_id") or "",
        "match_id": match_id or ids.get("match_id") or "",
        "team_id": str(team_rec.get("team_id") or ids.get("team_id") or ""),
        "opponent_id": str(opp_rec.get("team_id") or ids.get("opponent_id") or ""),
    })

    row.update({
        "match_url": url,
        "match_format": ctx.get("format") or row.get("match_format") or "BO3",
        "event": ctx.get("event") or row.get("event"),
        "team": team,
        "opponent": opponent,
        "provider_team_verified": bool(team and opponent and group),
        "v55_preprojection_identity_verified": bool(team and opponent and group),
        "identity_reconciled": bool(team and opponent and group),
        "identity_reconcile_source": "v5.7 real provider match roster",
        "identity_ids": ids,
        "confirmed_lineup_groups": groups,
        "confirmed_lineup_names": list(ctx.get("lineup_names") or []),
        "lineup_source": ctx.get("lineup_source") or row.get("lineup_source") or "real provider roster",
        "current_roster_names": roster or list(row.get("current_roster_names") or []),
        "current_roster_verified": bool(five_player and player_in_roster),
        "lineup_verified": bool((exact_lineup or five_player) and player_in_roster),
        "player_in_lineup": bool(player_in_roster),
        "roster_overlap": 5 if five_player and player_in_roster else row.get("roster_overlap"),
        "provider_match_id": match_id,
        "v57_match_context_verified": bool(match_id and player_id and player_in_roster),
    })

    fresh = dict(row.get("source_freshness") or {})
    if status.get("age_seconds") is not None:
        fresh["match_age_seconds"] = status.get("age_seconds")
    row["source_freshness"] = fresh
    row["identity_official_ready"] = bool(
        ids.get("player_id") and ids.get("match_id") and team and opponent and five_player and player_in_roster
    )

    try:
        if callable(globals().get("_v55_save_team")) and team and opponent:
            _v55_save_team(player, team, opponent, "v5.7 verified provider roster")
    except Exception:
        pass
    try:
        if callable(globals().get("_autofeed_persist_context")):
            _autofeed_persist_context(row)
    except Exception:
        pass
    try:
        if callable(globals().get("save_projection_entities")):
            save_projection_entities(row)
    except Exception:
        pass
    return row


if "build_full_board" in globals():
    _v57_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        board, status = _v57_board_base(props, deep_enabled)
        exact_ids = five = in_lineup = real_match = projection_ready = official_ready = 0
        for idx, original in enumerate(list(board or [])):
            row = _v57_enrich_row(dict(original or {}))
            if callable(globals().get("_v55_ready")):
                try:
                    ready = _v55_ready(row)
                    row["data_readiness"] = ready
                    row["projection_data_ready"] = bool(ready.get("projection_ready"))
                    row["official_data_ready"] = bool(ready.get("official_ready"))
                    row["data_readiness_score"] = ready.get("readiness_score")
                except Exception:
                    pass
            ids = row.get("identity_ids") if isinstance(row.get("identity_ids"), dict) else {}
            exact_ids += int(bool(ids.get("match_id") and ids.get("player_id")))
            lineup = _select_team_lineup(row) if callable(globals().get("_select_team_lineup")) else []
            five += int(len(lineup) == 5)
            in_lineup += int(bool(lineup and any(normalize_name(x) == normalize_name(row.get("player")) for x in lineup)))
            real_match += int(bool(str(row.get("match_url") or "").startswith(("bo3://", "pandascore://", "https://www.hltv.org/"))))
            projection_ready += int(bool(row.get("projection_data_ready")))
            official_ready += int(bool(row.get("official_data_ready")))
            board[idx] = row

        health = {
            "version": "5.7",
            "updated_at": now_iso(),
            "board_rows": len(board or []),
            "exact_match_player_ids": exact_ids,
            "five_player_lineups": five,
            "players_in_lineup": in_lineup,
            "real_match_rows": real_match,
            "projection_ready_rows": projection_ready,
            "official_ready_rows": official_ready,
        }
        try:
            save_json(V57_CONTEXT_HEALTH_FILE, health, force=True)
        except Exception:
            pass
        status = dict(status or {})
        status["v57_context_health"] = health
        return board, status

try:
    APP_VERSION = "CS2 v5.7 — VERIFIED MATCH / LINEUP / AUDIT PIPELINE"
except Exception:
    pass
# === END ONEWAYPICKZ V5.7 VERIFIED MATCH + LINEUP CONTEXT ===
'''


def patch_text(source: str) -> str:
    text = source
    if PATCH_MARKER not in text:
        if MARKER not in text:
            raise RuntimeError("SESSION BOARD LOAD marker not found")
        text = text.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)

    # Keep the audit control at the top of the sidebar rather than buried below
    # the full controls list. The placeholder is created early; the audit is
    # rendered into it later after all audit helper functions have been defined.
    sidebar_anchor = 'with st.sidebar:\n    st.markdown("## 🎯 CS2 Controls")'
    sidebar_repl = 'with st.sidebar:\n    st.markdown("## 🎯 CS2 Controls")\n    _audit_sidebar_slot = st.empty()'
    if sidebar_anchor in text and '_audit_sidebar_slot = st.empty()' not in text:
        text = text.replace(sidebar_anchor, sidebar_repl, 1)

    audit_anchor = 'with st.sidebar:\n    st.markdown("---")\n    with st.expander("🧪 FULL LIVE AUDIT DOWNLOAD", expanded=True):'
    audit_repl = '_audit_sidebar_target = _audit_sidebar_slot.container() if "_audit_sidebar_slot" in globals() else st.sidebar\nwith _audit_sidebar_target:\n    st.markdown("---")\n    with st.expander("🧪 FULL LIVE AUDIT DOWNLOAD", expanded=True):'
    if audit_anchor in text:
        text = text.replace(audit_anchor, audit_repl, 1)

    text = text.replace(
        'value=False,\n            key="cs2_enable_full_live_audit_download",',
        'value=True,\n            key="cs2_enable_full_live_audit_download_v57",',
        1,
    )
    return text


def patch_app(path="app.py"):
    p = Path(path)
    old = p.read_text(encoding="utf-8")
    new = patch_text(old)
    changed = new != old
    if changed:
        tmp = p.with_suffix(p.suffix + ".v57.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.7 patch {'applied' if changed else 'already present'}: {p}")

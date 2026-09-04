from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.5 PREMODEL VERIFIED CONTEXT HANDOFF ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.5 PREMODEL VERIFIED CONTEXT HANDOFF ===
# Data/context handoff only. Protected Maps 1-2 Kills projection math,
# probability math, side selection, thresholds, and confidence are unchanged.
AUTOFEED_PREMODEL_V585_VERSION = "5.8.5"


def _v585_supported_market(row):
    if callable(globals().get("_v583_supported_market")):
        try:
            return bool(_v583_supported_market(row))
        except Exception:
            pass
    raw = f"{(row or {}).get('market','')} | {(row or {}).get('stat_name','')} | {(row or {}).get('evidence','')}".lower()
    if "headshot" in raw:
        return False
    kills = bool(re.search(r"\bkills?\b", raw))
    maps12 = bool(
        re.search(r"\bmaps?\s*1\s*[-+&/]\s*(?:maps?\s*)?2\b", raw)
        or re.search(r"\bmaps?\s*1\s+(?:and\s+)?2\b", raw)
        or "maps 1 2" in re.sub(r"[^a-z0-9]+", " ", raw)
    )
    return bool(kills and maps12)


def _v585_normalize_supported_market(row):
    out = dict(row or {})
    if _v585_supported_market(out):
        out["market"] = "Maps 1-2 Kills"
        out["market_scope"] = "maps_1_2"
        out["market_scope_verified"] = True
        out["model_supported"] = True
        out["projection_eligible_market"] = True
    return out


def _v585_real_match_url(url):
    raw = str(url or "").strip()
    return bool(raw and not raw.startswith(("mirror://", "bridge://")))


def _v585_group_for_player(player, groups):
    best = None
    for raw in list(groups or []):
        if not isinstance(raw, dict):
            continue
        roster = [str(x or "").strip() for x in list(raw.get("players") or raw.get("roster") or []) if str(x or "").strip()]
        if not roster:
            continue
        score = max([name_similarity(player, x) for x in roster] or [0.0])
        if score >= .84 and (best is None or score > best[0]):
            best = (score, dict(raw), roster)
    return (best[1], best[2]) if best else ({}, [])


def _v585_hydrate_verified_context(row):
    out = _v585_normalize_supported_market(row)
    player = str(out.get("player") or "").strip()
    team = str(out.get("team") or "").strip()
    opponent = str(out.get("opponent") or "").strip()

    if bool(out.get("source_match_verified")) and _v585_real_match_url(out.get("match_url")):
        return out

    identity_ok = bool(
        player and team and opponent and
        out.get("provider_team_verified") and
        out.get("v55_preprojection_identity_verified")
    )
    if not identity_ok:
        return out

    fmt = str(out.get("match_format") or "").upper().strip()
    if _v585_real_match_url(out.get("match_url")) and fmt not in {"", "UNKNOWN", "BO1"}:
        return out

    ctx, st = {}, {}
    if callable(globals().get("_v55_context")):
        try:
            ctx, st = _v55_context(team, opponent, player)
        except Exception as exc:
            st = {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}
    ctx = dict(ctx or {})
    st = dict(st or {})
    url = str(ctx.get("match_url") or "").strip()
    ctx_fmt = str(ctx.get("format") or ctx.get("match_format") or "").strip()

    if not _v585_real_match_url(url):
        flags = list(out.get("flags") or [])
        flags.append("V5.8.5 PREMODEL REAL MATCH CONTEXT PENDING")
        out["flags"] = list(dict.fromkeys(flags))
        return out

    groups = [dict(x) for x in list(ctx.get("lineup_groups") or []) if isinstance(x, dict)]
    group, roster = _v585_group_for_player(player, groups)
    group_team = str(group.get("team") or group.get("name") or "").strip()
    team_matches = bool(group_team and _team_name_matches(group_team, team))
    player_in = bool(roster and max([name_similarity(player, x) for x in roster] or [0.0]) >= .84)
    exact_five = bool(len(roster) == 5 and player_in and team_matches)

    ids = dict(out.get("identity_ids") or {})
    derived_match_id = ""
    if callable(globals().get("_match_id_from_url")):
        try:
            derived_match_id = str(_match_id_from_url(url) or "")
        except Exception:
            derived_match_id = ""
    match_id = str(
        st.get("match_id") or ctx.get("match_id") or ctx.get("provider_match_id") or
        derived_match_id or ids.get("match_id") or ""
    ).strip()
    if match_id:
        ids["match_id"] = ids.get("match_id") or match_id

    out.update({
        "match_url": url,
        "match_format": ctx_fmt or out.get("match_format") or "BO3",
        "event": ctx.get("event") or out.get("event"),
        "identity_ids": ids,
        "provider_match_id": match_id or out.get("provider_match_id") or "",
        "confirmed_lineup_groups": groups or list(out.get("confirmed_lineup_groups") or []),
        "confirmed_lineup_names": list(ctx.get("lineup_names") or out.get("confirmed_lineup_names") or []),
        "lineup_source": ctx.get("lineup_source") or out.get("lineup_source") or "verified provider match context",
        "v585_premodel_context": True,
    })

    if exact_five:
        out.update({
            "current_roster_names": roster,
            "current_roster_verified": True,
            "lineup_verified": True,
            "player_in_lineup": True,
            "roster_overlap": 5,
        })

    fresh = dict(out.get("source_freshness") or {})
    if st.get("age_seconds") is not None:
        fresh["match_age_seconds"] = st.get("age_seconds")
    out["source_freshness"] = fresh
    return out


if "_v55_resolve_prop" in globals():
    _v585_resolve_base = _v55_resolve_prop
    def _v55_resolve_prop(prop):
        out = _v585_resolve_base(_v585_normalize_supported_market(prop))
        return _v585_hydrate_verified_context(out)


if "build_full_board" in globals():
    _v585_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        prepared = [_v585_normalize_supported_market(x) for x in list(props or [])]
        board, status = _v585_board_base(prepared, deep_enabled)
        board = [dict(x) for x in list(board or []) if isinstance(x, dict)]
        status = dict(status or {})
        diag = {
            "rows": len(board),
            "premodel_context_rows": sum(bool(x.get("v585_premodel_context")) for x in board),
            "supported_market_rows": sum(_v585_supported_market(x) for x in board),
            "real_match_rows": sum(_v585_real_match_url(x.get("match_url")) for x in board),
            "projection_ready_rows": sum(bool(x.get("projection_data_ready")) for x in board),
            "official_ready_rows": sum(bool(x.get("official_data_ready")) for x in board),
            "projection_math_changed": False,
        }
        status["v585_premodel_context"] = diag
        try:
            health_path = globals().get("V57_CONTEXT_HEALTH_FILE")
            if health_path:
                health = load_json(health_path, {}) or {}
                if isinstance(health, dict):
                    health["runtime_layer"] = "5.8.5"
                    health["updated_at"] = now_iso()
                    health["board_rows"] = len(board)
                    health["premodel_context_rows"] = diag["premodel_context_rows"]
                    health["supported_rows_visible"] = diag["supported_market_rows"]
                    health["real_match_rows"] = diag["real_match_rows"]
                    health["projection_ready_rows"] = diag["projection_ready_rows"]
                    health["official_ready_rows"] = diag["official_ready_rows"]
                    save_json(health_path, health, force=True)
                    status["v57_context_health"] = health
        except Exception as exc:
            status["v585_context_stamp_warning"] = f"{type(exc).__name__}: {exc}"
        return board, status

try:
    APP_VERSION = "CS2 v5.8.5 — PREMODEL VERIFIED CONTEXT + ALL LIVE LINES"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.5 PREMODEL VERIFIED CONTEXT HANDOFF ===
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
        tmp = p.with_suffix(p.suffix + ".v585.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8.5 patch {'applied' if changed else 'already present'}: {p}")

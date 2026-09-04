from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.7 COMPLETE VERIFIED DATA RECOVERY ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.7 COMPLETE VERIFIED DATA RECOVERY ===
# Data acquisition/context/readiness only. Protected Maps 1-2 Kills projection
# math, probability math, side selection, thresholds, and confidence are unchanged.
AUTOFEED_COMPLETION_V587_VERSION = "5.8.7"
V587_PROFILE_PASSES = int(max(1, min(4, float(os.getenv("CS2_PROFILE_RECOVERY_PASSES", "3") or 3))))
V587_PROVIDER_CONTEXT = {}


def _v587_provider_url(url):
    raw = str(url or "").strip()
    return bool(raw.startswith((
        "bo3://", "pandascore://", "https://bo3.gg/", "https://www.hltv.org/"
    )))


# v5.8.5 used "not mirror/bridge" as its real-match test.  An underdog:// URL is
# a real SOURCE identity, but it is not a stats-provider match page.  Treating it
# as provider context prevented BO3/PandaScore/HLTV discovery and deep map pulls.
def _v585_real_match_url(url):
    return _v587_provider_url(url)


def _v587_supported(row):
    row = row if isinstance(row, dict) else {}
    if callable(globals().get("_v586_is_cs2")):
        try:
            if not _v586_is_cs2(row):
                return False
        except Exception:
            pass
    if callable(globals().get("_v585_supported_market")):
        try:
            return bool(_v585_supported_market(row))
        except Exception:
            pass
    return bool(row.get("model_supported") and row.get("market_scope_verified"))


def _v587_ensure_market_format(row):
    out = dict(row or {})
    if _v587_supported(out):
        out["market"] = "Maps 1-2 Kills"
        out["market_scope"] = "maps_1_2"
        out["market_scope_verified"] = True
        out["model_supported"] = True
        out["projection_eligible_market"] = True
        # The market itself explicitly defines a two-map scope. MULTI_MAP is a
        # scope label, not a guessed BO format and does not change projection math.
        if str(out.get("match_format") or "").upper().strip() in {"", "UNKNOWN"}:
            out["match_format"] = "MULTI_MAP"
    return out


def _v587_profile_covered(player):
    try:
        return bool(_v50_profile_available(player))
    except Exception:
        return False


if "_autofeed_direct_profile_recovery" in globals():
    _v587_profile_recovery_base = _autofeed_direct_profile_recovery

    def _autofeed_direct_profile_recovery(players, max_new=None):
        unique = list(dict.fromkeys(str(x or "").strip() for x in players if str(x or "").strip()))
        if not unique:
            return _v587_profile_recovery_base(unique, max_new=max_new)

        before = sum(_v587_profile_covered(p) for p in unique)
        pass_rows = []
        no_progress = 0
        batch = int(max(4, min(120, int(max_new if max_new is not None else os.getenv("CS2_AUTOFEED_DIRECT_PROFILE_BATCH", "60") or 60))))

        for pass_no in range(1, V587_PROFILE_PASSES + 1):
            missing = [p for p in unique if not _v587_profile_covered(p)]
            if not missing:
                break
            prior = len(missing)
            try:
                result = dict(_v587_profile_recovery_base(missing, max_new=min(batch, len(missing))) or {})
            except Exception as exc:
                result = {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}
            remaining = [p for p in unique if not _v587_profile_covered(p)]
            progress = prior - len(remaining)
            pass_rows.append({
                "pass": pass_no,
                "requested_missing": prior,
                "attempted": int(result.get("attempted") or 0),
                "loaded": int(result.get("loaded") or result.get("recovered") or 0),
                "verified_progress": progress,
                "remaining": len(remaining),
                "provider_circuit_open": bool(result.get("provider_circuit_open")),
                "warning": result.get("warning"),
            })
            if progress <= 0:
                no_progress += 1
            else:
                no_progress = 0
            if result.get("provider_circuit_open") or no_progress >= 2:
                break

        after = sum(_v587_profile_covered(p) for p in unique)
        return {
            "ok": after > 0,
            "provider": "v5.8.7 progressive verified profile recovery",
            "requested": len(unique),
            "verified_before": before,
            "verified_profiles": after,
            "loaded": max(0, after - before),
            "remaining": max(0, len(unique) - after),
            "passes": pass_rows,
            "batch_limit": batch,
            "message": f"Verified profile recovery {before}->{after}/{len(unique)}; remaining {max(0, len(unique)-after)}.",
        }


def _v587_team_record(ctx, name):
    best = (0.0, {})
    for rec in list((ctx or {}).get("teams") or []):
        if not isinstance(rec, dict):
            continue
        score = name_similarity(name, str(rec.get("name") or "")) if name else 0.0
        if score > best[0]:
            best = (score, rec)
    return dict(best[1] or {}) if best[0] >= .78 else {}


def _v587_context_key(team, opponent):
    return "|".join(sorted([normalize_team(team), normalize_team(opponent)]))


def _v587_discover_provider(row):
    out = _v587_ensure_market_format(row)
    if not _v587_supported(out):
        return out
    player = str(out.get("player") or "").strip()
    team = str(out.get("team") or "").strip()
    opponent = str(out.get("opponent") or "").strip()
    if not (player and team and opponent):
        return out

    existing = str(out.get("provider_match_url") or "").strip()
    if not _v587_provider_url(existing):
        current = str(out.get("match_url") or "").strip()
        if _v587_provider_url(current):
            existing = current

    key = _v587_context_key(team, opponent)
    cached = V587_PROVIDER_CONTEXT.get(key)
    if isinstance(cached, dict) and cached.get("url"):
        existing = existing or str(cached.get("url") or "")
        ctx = dict(cached.get("context") or {})
        status = dict(cached.get("status") or {})
    else:
        ctx, status = {}, {}

    if not _v587_provider_url(existing):
        url, discovery = "", {}
        if callable(globals().get("discover_bo3_match")):
            try:
                url, discovery = discover_bo3_match(team, opponent, player)
            except Exception as exc:
                discovery = {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}
        if not url and callable(globals().get("discover_hltv_match")):
            try:
                url, discovery = discover_hltv_match(team, opponent, player)
            except Exception as exc:
                discovery = {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}
        if _v587_provider_url(url):
            existing = url
            status = dict(discovery or {})

    if _v587_provider_url(existing) and not ctx:
        try:
            ctx, fetched = fetch_match_context(existing)
            ctx = dict(ctx or {})
            status = {**dict(status or {}), **dict(fetched or {})}
        except Exception as exc:
            ctx = {}
            status = {**dict(status or {}), "ok": False, "warning": f"{type(exc).__name__}: {exc}"}

    if not (_v587_provider_url(existing) and ctx):
        return out

    V587_PROVIDER_CONTEXT[key] = {"url": existing, "context": dict(ctx), "status": dict(status)}
    team_rec = _v587_team_record(ctx, team)
    opp_rec = _v587_team_record(ctx, opponent)
    ids = dict(out.get("identity_ids") or {})
    provider_mid = str(ctx.get("provider_match_id") or status.get("match_id") or "").strip()
    if not provider_mid and callable(globals().get("_match_id_from_url")):
        try:
            provider_mid = str(_match_id_from_url(existing) or "").strip()
        except Exception:
            provider_mid = ""
    # Keep source match ID separately; canonical match ID may become the actual
    # stats-provider match ID once confidently recovered.
    if provider_mid:
        out["source_match_id"] = str((out.get("source_identity_ids") or {}).get("match_id") or out.get("underdog_match_id") or "")
        ids["match_id"] = provider_mid
    if team_rec.get("team_id"):
        ids["team_id"] = str(team_rec.get("team_id"))
    if opp_rec.get("team_id"):
        ids["opponent_id"] = str(opp_rec.get("team_id"))

    out.update({
        "provider_match_url": existing,
        "match_url": existing,
        "provider_match_id": provider_mid or out.get("provider_match_id") or "",
        "identity_ids": ids,
        "match_format": ctx.get("format") or out.get("match_format") or "BO3",
        "event": ctx.get("event") or out.get("event"),
        "provider_context_verified": True,
        "v587_provider_context": True,
    })
    groups = [dict(x) for x in list(ctx.get("lineup_groups") or []) if isinstance(x, dict)]
    if groups:
        out["confirmed_lineup_groups"] = groups
    if ctx.get("lineup_names"):
        out["confirmed_lineup_names"] = list(ctx.get("lineup_names") or [])
    if ctx.get("lineup_source"):
        out["lineup_source"] = ctx.get("lineup_source")

    fresh = dict(out.get("source_freshness") or {})
    if status.get("age_seconds") is not None:
        fresh["match_age_seconds"] = status.get("age_seconds")
    else:
        fresh["match_age_seconds"] = 0.0
    out["source_freshness"] = fresh
    return out


# Put provider context on the prop BEFORE the protected projection engine runs.
if "_v55_resolve_prop" in globals():
    _v587_resolve_base = _v55_resolve_prop
    def _v55_resolve_prop(prop):
        prepared = _v587_discover_provider(_v587_ensure_market_format(prop))
        out = _v587_resolve_base(prepared)
        out = _v587_discover_provider(out)
        return out


if "_v55_ready" in globals():
    _v587_ready_base = _v55_ready
    def _v55_ready(row):
        normalized = _v587_ensure_market_format(row)
        row.clear(); row.update(normalized)
        return _v587_ready_base(row)


# Preserve provider URL/context through wrappers that rebuild rows after modeling.
if "build_full_board" in globals():
    _v587_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        prepared = [_v587_ensure_market_format(x) for x in list(props or []) if isinstance(x, dict)]
        board, status = _v587_board_base(prepared, deep_enabled)
        board = [dict(x) for x in list(board or []) if isinstance(x, dict)]
        status = dict(status or {})
        provider_rows = deep_rows = profile_rows = core_rows = projection_ready = official_ready = 0
        missing = Counter()
        for i, row in enumerate(board):
            row = _v587_ensure_market_format(row)
            if _v587_supported(row):
                row = _v587_discover_provider(row)
                provider_rows += int(_v587_provider_url(row.get("match_url")))
                profile_rows += int((safe_int(row.get("profile_maps"), 0) or 0) >= MIN_PROFILE_MAPS and safe_float(row.get("base_kpr"), None) is not None)
                core_rows += int(bool(row.get("core_kpr_verified")))
                deep_rows += int((safe_int(row.get("team_recent_maps"), 0) or 0) > 0 and (safe_int(row.get("opponent_mapstats_samples"), 0) or 0) > 0)
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
            board[i] = row

        completion = {
            "version": "5.8.7",
            "updated_at": now_iso(),
            "board_rows": len(board),
            "supported_rows": sum(_v587_supported(x) for x in board),
            "verified_profile_rows": profile_rows,
            "core_kpr_rows": core_rows,
            "real_provider_match_rows": provider_rows,
            "deep_team_map_rows": deep_rows,
            "projection_ready_rows": projection_ready,
            "official_ready_rows": official_ready,
            "missing_projection_requirements": dict(missing),
            "projection_math_changed": False,
        }
        status["v587_completion"] = completion
        try:
            health = load_json(V57_CONTEXT_HEALTH_FILE, {}) or {}
            if isinstance(health, dict):
                health.update({
                    "version": "5.8.7", "runtime_layer": "5.8.7", "updated_at": now_iso(),
                    "real_provider_match_rows": provider_rows,
                    "projection_ready_rows": projection_ready,
                    "official_ready_rows": official_ready,
                    "verified_profile_rows": profile_rows,
                    "core_kpr_rows": core_rows,
                    "deep_team_map_rows": deep_rows,
                    "projection_math_changed": False,
                })
                save_json(V57_CONTEXT_HEALTH_FILE, health, force=True)
                status["v57_context_health"] = health
            readiness = load_json(V55_READINESS_FILE, {}) or {}
            if isinstance(readiness, dict):
                readiness.update({
                    "version": "5.8.7", "updated_at": now_iso(), "board_rows": len(board),
                    "projection_ready_rows": projection_ready, "official_ready_rows": official_ready,
                    "verified_profile_rows": profile_rows, "core_kpr_rows": core_rows,
                    "real_provider_match_rows": provider_rows, "deep_team_map_rows": deep_rows,
                    "missing_projection_requirements": dict(missing),
                })
                save_json(V55_READINESS_FILE, readiness, force=True)
                status["v55_data_readiness"] = readiness
        except Exception as exc:
            status["v587_status_warning"] = f"{type(exc).__name__}: {exc}"
        return board, status


try:
    APP_VERSION = "CS2 v5.8.7 — COMPLETE VERIFIED DATA RECOVERY"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.7 COMPLETE VERIFIED DATA RECOVERY ===
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
        tmp = p.with_suffix(p.suffix + ".v587.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8.7 patch {'applied' if changed else 'already present'}: {p}")

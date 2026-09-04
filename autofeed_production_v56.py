from __future__ import annotations
import os, sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ CS2 PRODUCTION DATA PIPELINE V5.6 ==="

OVERLAY = r'''
# === ONEWAYPICKZ CS2 PRODUCTION DATA PIPELINE V5.6 ===
# Restores real BO3 match discovery after later wrappers, hardens provider
# failures, persists current identity context, and exposes operational health.
# Projection math is intentionally unchanged.
PRODUCTION_DATA_VERSION = "5.6"
V56_OPERATIONAL_FILE = os.path.join(STORAGE_DIR, "cs2_operational_status.json")


def _v56_extract_profile_team(page, matchup_teams=None):
    if not page:
        return ""
    matchup_teams = [str(x or "").strip() for x in (matchup_teams or []) if str(x or "").strip()]
    # Current-team links normally appear before Transfers History on BO3 player pages.
    head = page
    stop = re.search(r"Transfers History", head, re.I)
    if stop:
        head = head[:stop.start()]
    candidates = []
    for slug, anchor in re.findall(r'href=["\'](?:https?://bo3\.gg)?/teams/([^"\'/?#]+)[^"\']*["\'][^>]*>(.*?)</a>', head, re.I | re.S):
        name = strip_tags(anchor).replace("\n", " ").strip()
        if not name:
            name = slug.replace("-", " ").strip()
        if name and normalize_team(name) not in {normalize_team(x) for x in candidates}:
            candidates.append(name)
    if matchup_teams:
        scored = []
        for cand in candidates:
            score = max([name_similarity(cand, t) for t in matchup_teams] or [0])
            if score >= .82:
                scored.append((score, cand))
        if scored:
            return max(scored, key=lambda x: x[0])[1]
    return candidates[0] if candidates else ""


# BO3 HTML contains a reliable team link more often than the page title does.
if "_bo3_parse_player_html" in globals():
    _v56_parse_player_base = _bo3_parse_player_html
    def _bo3_parse_player_html(page, player, slug, url):
        profile, meta = _v56_parse_player_base(page, player, slug, url)
        if profile is not None and not str(profile.team or "").strip():
            team = _v56_extract_profile_team(page)
            if team:
                profile.team = team
                meta = dict(meta or {})
                meta["profile_team_source"] = "BO3 current-team link"
        return profile, meta


# The base API fallback contains provider-version-specific payload paths. Never
# allow one malformed fallback response to crash a full collector cycle.
if "_bo3_profile_from_api" in globals():
    _v56_profile_api_base = _bo3_profile_from_api
    def _bo3_profile_from_api(player, alias=None):
        try:
            return _v56_profile_api_base(player, alias)
        except Exception as exc:
            return None, {"ok": False, "provider": "BO3", "warning": f"BO3 API fallback isolated: {type(exc).__name__}: {exc}"}


# v4.7 already has real BO3 fixture discovery. A later bridge wrapper could hide
# it, leaving only synthetic bridge:// matches. Restore BO3 as the first choice.
if "discover_hltv_match" in globals():
    _v56_discover_base = discover_hltv_match
    def discover_hltv_match(team, opponent, player=""):
        if callable(globals().get("discover_bo3_match")):
            try:
                url, meta = discover_bo3_match(team, opponent, player)
                if url:
                    return url, {**dict(meta or {}), "v56_real_match": True}
            except Exception:
                pass
        # Optional PandaScore only when already configured/available.
        if callable(globals().get("fetch_pandascore_upcoming")):
            try:
                rows, st = fetch_pandascore_upcoming()
                best = (0.0, None)
                for raw in rows or []:
                    names = [str(((x.get("opponent") or {}).get("name")) or "") for x in (raw.get("opponents") or [])]
                    score = max([name_similarity(team, n) for n in names] or [0]) * .58 + max([name_similarity(opponent, n) for n in names] or [0]) * .38
                    if score > best[0]:
                        best = (score, raw)
                if best[1] is not None and best[0] >= .70:
                    raw = best[1]
                    mid = str(raw.get("id") or "")
                    slug = str(raw.get("slug") or mid)
                    return f"pandascore://{mid}/{slug}", {**dict(st or {}), "ok": True, "method": "PandaScore fixture fallback", "v56_real_match": True}
            except Exception:
                pass
        return _v56_discover_base(team, opponent, player)


# Make the identity resolver carry real match/roster/format context forward so
# the projection engine and readiness checks see the same verified matchup.
if "_v55_resolve_prop" in globals():
    _v56_resolve_base = _v55_resolve_prop
    def _v55_resolve_prop(prop):
        out = _v56_resolve_base(prop)
        player = str(out.get("player") or "")
        a, b = _v55_matchup(out) if callable(globals().get("_v55_matchup")) else ("", "")
        if player and a and b and not bool(out.get("v55_preprojection_identity_verified")):
            try:
                url, meta = discover_hltv_match(a, b, player)
                ctx, cst = fetch_match_context(url) if url else ({}, {})
                ctx = dict(ctx or {})
                groups = list(ctx.get("lineup_groups") or [])
                team = _v55_team_from_groups(player, groups) if callable(globals().get("_v55_team_from_groups")) else ""
                if team and (_team_name_matches(team, a) or _team_name_matches(team, b)):
                    t, o = (a, b) if _team_name_matches(team, a) else (b, a)
                    out.update({
                        "team": t,
                        "opponent": o,
                        "match_url": url,
                        "match_format": ctx.get("format") or out.get("match_format") or "BO3",
                        "event": ctx.get("event") or out.get("event"),
                        "provider_team_verified": True,
                        "v55_preprojection_identity_verified": True,
                        "identity_reconciled": True,
                        "identity_reconcile_source": "v5.6 real BO3/PandaScore roster",
                        "confirmed_lineup_groups": groups,
                        "confirmed_lineup_names": list(ctx.get("lineup_names") or []),
                    })
                    if callable(globals().get("_v55_save_team")):
                        _v55_save_team(player, t, o, "v5.6 real provider roster")
            except Exception as exc:
                flags = list(out.get("flags") or [])
                flags.append(f"V5.6 IDENTITY RECOVERY PENDING: {type(exc).__name__}")
                out["flags"] = list(dict.fromkeys(flags))
        return out


if "_v55_ready" in globals():
    _v56_ready_base = _v55_ready
    def _v55_ready(row):
        # Preserve the strict gate, but accept the canonical format field if a
        # provider populated it under `format` rather than `match_format`.
        if not row.get("match_format") and row.get("format"):
            row["match_format"] = row.get("format")
        result = _v56_ready_base(row)
        result["production_version"] = "5.6"
        return result


def _v56_write_operational_status(board, status=None):
    rows = list(board or [])
    profiles = sum((safe_int(r.get("profile_maps"), 0) or 0) >= MIN_PROFILE_MAPS for r in rows)
    identities = sum(bool(r.get("provider_team_verified") and r.get("team") and r.get("opponent")) for r in rows)
    real_matches = sum(bool(str(r.get("match_url") or "").startswith(("bo3://", "pandascore://", "https://www.hltv.org/"))) for r in rows)
    projection_ready = sum(bool(r.get("projection_data_ready")) for r in rows)
    official_ready = sum(bool(r.get("official_data_ready")) for r in rows)
    db = database_status() if callable(globals().get("database_status")) else {}
    payload = {
        "version": "5.6",
        "updated_at": now_iso(),
        "board_rows": len(rows),
        "verified_profile_rows": profiles,
        "verified_identity_rows": identities,
        "real_match_rows": real_matches,
        "projection_ready_rows": projection_ready,
        "official_ready_rows": official_ready,
        "database_status": db,
        "pipeline_ready": bool(len(rows) > 0 and profiles > 0 and identities > 0 and real_matches > 0),
        "calibration_status": "LIVE LEARNING" if official_ready else "BUILDING FROM VERIFIED FROZEN ROWS",
    }
    try:
        save_json(V56_OPERATIONAL_FILE, payload, force=True)
    except Exception:
        pass
    return payload


if "build_full_board" in globals():
    _v56_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        board, status = _v56_board_base(props, deep_enabled)
        op = _v56_write_operational_status(board, status)
        status = dict(status or {})
        status["v56_operational_status"] = op
        return board, status

try:
    APP_VERSION = "CS2 v5.6 — PRODUCTION VERIFIED DATA PIPELINE"
except Exception:
    pass
# === END ONEWAYPICKZ CS2 PRODUCTION DATA PIPELINE V5.6 ===
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
        tmp = p.with_suffix(p.suffix + ".v56.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    try:
        changed = patch_app(p)
        compile(p.read_text(encoding="utf-8"), str(p), "exec")
        print(f"v5.6 patch {'applied' if changed else 'already present'}: {p}")
    except Exception as exc:
        print(f"v5.6 patch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

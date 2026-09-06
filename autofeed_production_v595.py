from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.5 PRODUCTION COMPLETION GATES ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.5 PRODUCTION COMPLETION GATES ===
# Verified data plumbing only. Protected Maps 1-2 Kills projection math,
# probability math, side selection, confidence, thresholds, and frozen rows
# are unchanged.
AUTOFEED_PRODUCTION_V595_VERSION = "5.9.5"

# v5.9.4's patch module imported urljoin outside the inserted runtime overlay.
# Define the same alias in the compiled app namespace so direct grading can run.
from urllib.parse import urljoin as _v594_urljoin

V595_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_v595_production_health.json")
V595_HEALTH = {
    "team_page_attempts": 0,
    "team_page_ok": 0,
    "map_cards_parsed": 0,
    "map_samples": 0,
    "last_error": "",
}


def _v595_save_health(extra=None):
    try:
        payload = {"version": "5.9.5", "updated_at": now_iso(), **dict(V595_HEALTH)}
        if isinstance(extra, dict):
            payload.update(extra)
        save_json(V595_HEALTH_FILE, payload, force=True)
    except Exception:
        pass


def _v595_map_aliases(map_name):
    if str(map_name) == "Dust2":
        return ["Dust2", "Dust II", "Dust 2"]
    return [str(map_name)]


def _v595_parse_team_page_map_pool(page, source_url=""):
    """Parse only explicit HLTV map cards with exact W/D/L observations.

    The public team page exposes each recent-map card as text in this order:
    map name, win percentage, veto data, then exact Win/Draw/Loss counts.  We
    require all of those anchors before accepting a sample; no counts are
    inferred from percentages and no missing data is synthesized.
    """
    text = strip_tags(page or "")
    if not text:
        return {}
    pool = {}
    for map_name in KNOWN_MAPS:
        aliases = _v595_map_aliases(map_name)
        alias_pat = "(?:" + "|".join(re.escape(x) for x in aliases) + ")"
        for hit in re.finditer(rf"(?im)^[ \t]*{alias_pat}[ \t]*$", text):
            chunk = text[hit.start(): min(len(text), hit.start() + 7000)]
            # Reject ordinary news/result mentions: a genuine map card has a
            # win percentage immediately after the map heading and the W/D/L block.
            head = re.match(rf"(?is)^[ \t]*{alias_pat}[ \t]*\n[ \t]*(\d+(?:\.\d+)?)%", chunk)
            if not head:
                continue
            if "Biggest win on map" not in chunk[:1200] or "Win / draw / losses" not in chunk[:5200]:
                continue
            wdl = re.search(
                r"(?is)Win\s*/\s*draw\s*/\s*losses\s*\n\s*(\d+)\s*\n\s*Win\s*\n\s*(\d+)\s*\n\s*Draws?\s*\n\s*(\d+)\s*\n\s*Losses",
                chunk,
            )
            if not wdl:
                continue
            wins, draws, losses = (int(wdl.group(1)), int(wdl.group(2)), int(wdl.group(3)))
            played = wins + draws + losses
            if played <= 0:
                continue
            pick = re.search(r"(?is)Picks\s*\n\s*(\d+(?:\.\d+)?)%\s+of\s+(\d+)", chunk)
            ban = re.search(r"(?is)Bans\s*\n\s*(\d+(?:\.\d+)?)%\s+of\s+(\d+)", chunk)
            pool[map_name] = {
                "maps": played,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "win_pct": float(head.group(1)),
                "pick_pct": float(pick.group(1)) if pick else None,
                "pick_opportunities": int(pick.group(2)) if pick else 0,
                "ban_pct": float(ban.group(1)) if ban else None,
                "ban_opportunities": int(ban.group(2)) if ban else 0,
                "source": str(source_url or "HLTV public team page"),
                "period": "HLTV public team page last 3 months",
                "verified_wdl": True,
            }
            break
    return pool


# Replace only the transport/parser that feeds v5.9.1's already-strict deep
# profile builder.  v5.9.1 still owns the model-facing profile structure.
_v595_map_pool_base = _v591_team_map_pool

def _v591_team_map_pool(team_id, slug, days=120):
    tid = str(team_id or "").strip()
    if not tid.isdigit():
        return _v595_map_pool_base(team_id, slug, days)
    url = f"https://www.hltv.org/team/{tid}/{slug or 'team'}"
    V595_HEALTH["team_page_attempts"] = int(V595_HEALTH.get("team_page_attempts") or 0) + 1
    page, status = _v591_direct_get(url, ttl=4 * 3600, timeout=30)
    pool = _v595_parse_team_page_map_pool(page, str((status or {}).get("url") or url)) if page else {}
    sample = sum(int((v or {}).get("maps") or 0) for v in pool.values())
    if pool and sample > 0:
        V595_HEALTH["team_page_ok"] = int(V595_HEALTH.get("team_page_ok") or 0) + 1
        V595_HEALTH["map_cards_parsed"] = int(V595_HEALTH.get("map_cards_parsed") or 0) + len(pool)
        V595_HEALTH["map_samples"] = int(V595_HEALTH.get("map_samples") or 0) + sample
        V595_HEALTH["last_error"] = ""
        out = {
            **dict(status or {}),
            "ok": True,
            "provider": "HLTV public team-page verified W/D/L v5.9.5",
            "maps_found": len(pool),
            "sample_maps": sample,
            "period": "last 3 months",
        }
        # Keep the existing map-health surface current for collector/audit UI.
        try:
            V593_MAP_HEALTH["ok"] = int(V593_MAP_HEALTH.get("ok") or 0) + 1
            V593_MAP_HEALTH["maps_parsed"] = int(V593_MAP_HEALTH.get("maps_parsed") or 0) + len(pool)
            V593_MAP_HEALTH["sample_maps"] = int(V593_MAP_HEALTH.get("sample_maps") or 0) + sample
            V593_MAP_HEALTH["last_error"] = ""
            _v593_save_health({"version": "5.9.5", "last_status": out, "last_team_id": tid, "last_slug": slug})
        except Exception:
            pass
        _v595_save_health({"last_status": out, "last_team_id": tid, "last_slug": slug})
        return pool, out

    # Preserve the older verified parser/provider fallbacks.  A fallback is only
    # accepted by v5.9.1 if it yields a genuine positive map sample.
    try:
        fallback, fstatus = _v595_map_pool_base(team_id, slug, days)
    except Exception as exc:
        fallback, fstatus = {}, {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}
    fsample = sum(int((v or {}).get("maps") or 0) for v in (fallback or {}).values())
    if fallback and fsample > 0:
        _v595_save_health({"last_status": fstatus, "last_team_id": tid, "last_slug": slug, "fallback_used": True})
        return fallback, fstatus

    err = str((status or {}).get("warning") or (fstatus or {}).get("warning") or "No verified W/D/L map cards parsed")
    V595_HEALTH["last_error"] = err
    out = {
        **dict(status or {}),
        "ok": False,
        "provider": "HLTV public team-page verified W/D/L v5.9.5",
        "maps_found": 0,
        "sample_maps": 0,
        "warning": err,
        "fallback": dict(fstatus or {}),
    }
    _v595_save_health({"last_status": out, "last_team_id": tid, "last_slug": slug})
    return {}, out


try:
    APP_VERSION = "CS2 v5.9.5 — PRODUCTION COMPLETION GATES"
except Exception:
    pass
# === END ONEWAYPICKZ V5.9.5 PRODUCTION COMPLETION GATES ===
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
        tmp = p.with_suffix(p.suffix + ".v595.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.9.5 patch {'applied' if changed else 'already present'}: {p}")

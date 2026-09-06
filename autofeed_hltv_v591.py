from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.1 DIRECT HLTV CONTEXT + TEAM MAP HYDRATION ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.1 DIRECT HLTV CONTEXT + TEAM MAP HYDRATION ===
# Data acquisition/context only. Protected Maps 1-2 Kills projection math,
# probabilities, thresholds, side selection, and confidence are unchanged.
AUTOFEED_HLTV_V591_VERSION = "5.9.1"

import time as _v591_time
import requests as _v591_requests

V591_HTTP_CACHE = {}
V591_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_hltv_direct_health.json")
V591_HEALTH = {
    "match_fetch_attempts": 0, "match_fetch_ok": 0,
    "team_fetch_attempts": 0, "team_fetch_ok": 0,
    "deep_profiles_ok": 0, "last_error": "", "last_match_url": "",
}
V591_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _v591_save_health(extra=None):
    try:
        payload = {"version": "5.9.1", "updated_at": now_iso(), **dict(V591_HEALTH)}
        if isinstance(extra, dict):
            payload.update(extra)
        save_json(V591_HEALTH_FILE, payload, force=True)
    except Exception:
        pass


def _v591_direct_get(url, params=None, ttl=180.0, timeout=22):
    key = (str(url), tuple(sorted((params or {}).items())))
    now = _v591_time.time()
    cached = V591_HTTP_CACHE.get(key)
    if isinstance(cached, dict) and now - float(cached.get("at") or 0) < float(ttl):
        return str(cached.get("text") or ""), dict(cached.get("status") or {})
    try:
        r = _v591_requests.get(str(url), params=params or None, headers=V591_HEADERS, timeout=timeout, allow_redirects=True)
        text = r.text or ""
        status = {
            "ok": bool(r.status_code == 200 and len(text) > 1000),
            "status": int(r.status_code),
            "bytes": len(text.encode("utf-8", errors="ignore")),
            "url": str(r.url or url),
            "provider": "HLTV direct public HTML v5.9.1",
            "age_seconds": 0.0,
            "fetched_at": now_iso(),
        }
        if not status["ok"]:
            status["warning"] = "Direct HLTV response was unavailable or too small"
        V591_HTTP_CACHE[key] = {"at": now, "text": text if status["ok"] else "", "status": status}
        return (text if status["ok"] else ""), status
    except Exception as exc:
        status = {"ok": False, "url": str(url), "provider": "HLTV direct public HTML v5.9.1", "warning": f"{type(exc).__name__}: {exc}", "fetched_at": now_iso()}
        V591_HEALTH["last_error"] = status["warning"]
        return "", status


def _v591_match_id(url):
    m = re.search(r"/matches/(\d+)/", str(url or ""))
    return str(m.group(1)) if m else ""


def _v591_fetch_hltv_match(match_url):
    V591_HEALTH["match_fetch_attempts"] = int(V591_HEALTH.get("match_fetch_attempts") or 0) + 1
    V591_HEALTH["last_match_url"] = str(match_url or "")
    page, status = _v591_direct_get(match_url, ttl=90.0, timeout=24)
    if not page:
        _v591_save_health()
        return {}, status
    try:
        text = strip_tags(page)
        teams = _extract_team_links(page)
        ranks = _extract_world_ranks(page)
        event_url = _extract_event_link(page)
        event = ""
        em = re.search(r'class=["\'][^"\']*(?:event|event-name)[^"\']*["\'][^>]*>(.*?)</', page, flags=re.I | re.S)
        if em:
            event = strip_tags(em.group(1)).replace("\n", " ").strip()
        fmt = str(_extract_format(page) or "").upper().strip()
        ctx = {
            "match_url": str(match_url),
            "provider_match_id": _v591_match_id(match_url),
            "provider": "HLTV direct public match page v5.9.1",
            "format": fmt,
            "confirmed_maps": _extract_confirmed_maps(page),
            "veto_actions": _extract_veto_actions(page),
            "map_results": _extract_map_results(page),
            "world_ranks": ranks,
            "teams": teams,
            "lineup_names": _extract_lineup_names(page),
            "lineup_groups": _extract_lineup_groups(page),
            "lineup_source": "HLTV direct match page",
            "standin_warning": bool(re.search(r"\bstand-?in\b|replacement|substitute|ineligible starting roster", text, flags=re.I)),
            "postponed": bool(re.search(r"postponed|cancelled|canceled|invalidated", text, flags=re.I)),
            "environment": _extract_match_environment(page),
            "stage": _extract_match_stage(page),
            "match_datetime": _extract_match_datetime(page),
            "event": event,
            "event_url": event_url,
            "page_text_sample": text[:2400],
        }
        try:
            ctx["event_tier"], ctx["event_tier_confidence"] = _classify_event_tier(event, ctx.get("stage", ""), ranks)
        except Exception:
            ctx["event_tier"], ctx["event_tier_confidence"] = "LOW/UNKNOWN", 0.0
        ok = bool(len(teams) >= 2 and _v591_match_id(match_url))
        status = {**status, "ok": ok, "match_id": _v591_match_id(match_url), "teams": len(teams), "lineup_names": len(ctx.get("lineup_names") or []), "format": fmt}
        if ok:
            V591_HEALTH["match_fetch_ok"] = int(V591_HEALTH.get("match_fetch_ok") or 0) + 1
        else:
            status["warning"] = "HLTV match page did not expose two team identities"
        _v591_save_health({"last_match_status": status})
        return (ctx if ok else {}), status
    except Exception as exc:
        status = {**status, "ok": False, "warning": f"HLTV match parse {type(exc).__name__}: {exc}"}
        V591_HEALTH["last_error"] = status["warning"]
        _v591_save_health({"last_match_status": status})
        return {}, status


_v591_fetch_match_base = fetch_match_context

def fetch_match_context(match_url):
    raw = str(match_url or "").strip()
    if raw.startswith("https://www.hltv.org/matches/") or raw.startswith("https://hltv.org/matches/"):
        return _v591_fetch_hltv_match(raw.replace("https://hltv.org/", "https://www.hltv.org/"))
    return _v591_fetch_match_base(match_url)


def _v591_team_map_pool(team_id, slug, days=120):
    if not str(team_id or "").isdigit():
        return {}, {"ok": False, "warning": "numeric HLTV team id required"}
    start, end = _period_dates(max(int(days or 120), 1))
    url = f"https://www.hltv.org/stats/teams/maps/{team_id}/{slug or normalize_team('team').replace(' ','-')}"
    page, status = _v591_direct_get(url, params={"startDate": start, "endDate": end, "csVersion": "CS2"}, ttl=4 * 3600, timeout=24)
    pool = {}
    if page:
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
            txt = strip_tags(tr).replace("%", "")
            map_name = next((m for m in KNOWN_MAPS if re.search(rf"\b{re.escape('Dust II' if m == 'Dust2' else m)}\b", txt, re.I)), None)
            if not map_name:
                continue
            pcts = [safe_float(x, None) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", strip_tags(tr))]
            pcts = [float(x) for x in pcts if x is not None and 0 <= float(x) <= 100]
            nums = [safe_float(x, None) for x in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", txt)]
            nums = [float(x) for x in nums if x is not None]
            maps_played = 0
            for n in nums:
                if 0 <= n <= 250 and abs(n - round(n)) < 1e-9:
                    maps_played = int(round(n))
                    break
            if maps_played <= 0:
                continue
            pool[map_name] = {
                "maps": maps_played,
                "win_pct": float(pcts[-1]) if pcts else None,
                "round_win_pct": float(pcts[0]) if len(pcts) >= 2 else None,
                "pistol_win_pct": float(pcts[1]) if len(pcts) >= 3 else None,
                "source": str(status.get("url") or url),
            }
    return pool, {**status, "ok": bool(pool), "maps_found": len(pool), "sample_maps": sum(int(v.get("maps") or 0) for v in pool.values())}


def _v591_team_roster(team_id, slug):
    if not str(team_id or "").isdigit():
        return [], {"ok": False, "warning": "numeric HLTV team id required"}
    url = f"https://www.hltv.org/team/{team_id}/{slug or 'team'}"
    page, status = _v591_direct_get(url, ttl=3600, timeout=24)
    players = []
    if page:
        for raw in re.findall(r'href=["\']/player/\d+/[^"\']+["\'][^>]*>(.*?)</a>', page, flags=re.I | re.S):
            name = strip_tags(raw).replace("\n", " ").strip()
            if name and len(name) <= 40 and normalize_name(name) not in {normalize_name(x) for x in players}:
                players.append(name)
    return players[:7], {**status, "ok": bool(players), "players": len(players[:7]), "url": str(status.get("url") or url)}


_v591_team_deep_base = build_team_deep_profile

def build_team_deep_profile(team_id, slug, team_name):
    tid = str(team_id or "").strip()
    if not tid.isdigit():
        return _v591_team_deep_base(team_id, slug, team_name)
    V591_HEALTH["team_fetch_attempts"] = int(V591_HEALTH.get("team_fetch_attempts") or 0) + 1
    pool, pool_status = _v591_team_map_pool(tid, str(slug or ""), 120)
    roster, roster_status = _v591_team_roster(tid, str(slug or ""))
    total_maps = sum(int((v or {}).get("maps") or 0) for v in pool.values())
    if total_maps <= 0:
        # Preserve any previously verified local/provider deep profile rather than fabricating samples.
        prior, prior_status = _v591_team_deep_base(team_id, slug, team_name)
        if prior:
            return prior, prior_status
        _v591_save_health({"last_team_status": {"pool": pool_status, "roster": roster_status}})
        return {}, {"ok": False, "provider": "HLTV direct team map history v5.9.1", "pool": pool_status, "roster": roster_status, "warning": "No verified team map sample returned"}
    profile = {
        "team_id": tid,
        "slug": str(slug or ""),
        "team": str(team_name or ""),
        "current_roster": list(roster),
        "recent_matches": 0,
        "recent_maps": int(total_maps),
        "cumulative_map_observations": 0,
        "same_core_matches": 0,
        "current_roster_maps": 0,
        "roster_stability": 0.0,
        "pick_counts": {},
        "ban_counts": {},
        "map_profiles": dict(pool),
        "kills_for_per_round": None,
        "deaths_allowed_per_round": None,
        # This is the exact verified sample size from HLTV's team map-stat table.
        "mapstats_samples": int(total_maps),
        "environment_counts": {},
        "latest_match": "",
        "rest_days": None,
        "historical_opponent_rank_avg": None,
        "historical_opponent_rank_samples": 0,
        "updated_at": now_iso(),
        "provider": "HLTV direct team map history v5.9.1",
        "map_sample_source": "HLTV /stats/teams/maps",
    }
    V591_HEALTH["team_fetch_ok"] = int(V591_HEALTH.get("team_fetch_ok") or 0) + 1
    V591_HEALTH["deep_profiles_ok"] = int(V591_HEALTH.get("deep_profiles_ok") or 0) + 1
    try:
        sqlite_store_entity_snapshot("team_deep", tid, profile, "HLTV direct team map history", 0, now_iso())
    except Exception:
        pass
    status = {
        "ok": True,
        "provider": profile["provider"],
        "pool": pool_status,
        "roster": roster_status,
        "roster_fresh": bool(roster_status.get("ok") and len(roster) >= 5),
        "pool_fresh": bool(pool_status.get("ok") and total_maps > 0),
        "mapstats_samples": int(total_maps),
    }
    _v591_save_health({"last_team_status": status})
    return profile, status


# Make HLTV the first provider attempt for a current exact matchup. If it is not
# discoverable, keep every existing BO3/PandaScore/cache fallback intact.
if "_v587_discover_provider" in globals():
    _v591_provider_base = _v587_discover_provider
    def _v587_discover_provider(row):
        prepared = dict(row or {})
        try:
            existing = str(prepared.get("provider_match_url") or "").strip()
            if not _v587_provider_url(existing) and _v587_supported(prepared):
                team = str(prepared.get("team") or "").strip()
                opp = str(prepared.get("opponent") or "").strip()
                if team and opp and callable(globals().get("_v590_find_hltv")):
                    url, meta = _v590_find_hltv(team, opp)
                    if _v587_provider_url(url):
                        prepared["provider_match_url"] = url
                        prepared["match_url"] = url
                        prepared["v591_direct_discovery"] = meta
        except Exception as exc:
            V591_HEALTH["last_error"] = f"provider preflight {type(exc).__name__}: {exc}"
        out = _v591_provider_base(prepared)
        try:
            _v591_save_health({
                "last_provider_url": str(out.get("provider_match_url") or ""),
                "last_provider_context": bool(out.get("v587_provider_context")),
            })
        except Exception:
            pass
        return out


try:
    APP_VERSION = "CS2 v5.9.1 — DIRECT HLTV CONTEXT + TEAM MAP HYDRATION"
except Exception:
    pass
# === END ONEWAYPICKZ V5.9.1 DIRECT HLTV CONTEXT + TEAM MAP HYDRATION ===
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
        tmp = p.with_suffix(p.suffix + ".v591.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.9.1 patch {'applied' if changed else 'already present'}: {p}")
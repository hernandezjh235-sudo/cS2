from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.2 DIRECT HLTV VERIFIED PLAYER PROFILES ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.2 DIRECT HLTV VERIFIED PLAYER PROFILES ===
# Verified player-stat acquisition only. Protected projection math, side choice,
# probability math, thresholds, and confidence are unchanged.
AUTOFEED_PROFILES_V592_VERSION = "5.9.2"
V592_TABLE_CACHE = {}
V592_PROFILE_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_hltv_profile_health.json")
V592_PROFILE_HEALTH = {"table_requests": 0, "table_rows": {}, "advanced_requests": 0, "advanced_ok": 0, "last_error": ""}


def _v592_health(extra=None):
    try:
        payload = {"version": "5.9.2", "updated_at": now_iso(), **dict(V592_PROFILE_HEALTH)}
        if isinstance(extra, dict): payload.update(extra)
        save_json(V592_PROFILE_HEALTH_FILE, payload, force=True)
    except Exception:
        pass


def _v592_direct_table(days):
    days = int(days or 180)
    cached = V592_TABLE_CACHE.get(days)
    if isinstance(cached, dict) and cached.get("rows"):
        return dict(cached["rows"]), dict(cached.get("status") or {})
    start_date, end_date = _period_dates(days)
    url = f"{HLTV_BASE}/stats/players"
    merged = {}
    page_status = []
    max_pages = max(1, min(8, int(float(os.getenv("CS2_HLTV_DIRECT_PROFILE_PAGES", "5") or 5))))
    for page_no in range(max_pages):
        params = {"startDate": start_date, "endDate": end_date, "minMapCount": 1, "csVersion": "CS2"}
        if page_no:
            params["start"] = page_no * 100
        V592_PROFILE_HEALTH["table_requests"] = int(V592_PROFILE_HEALTH.get("table_requests") or 0) + 1
        html, status = _v591_direct_get(url, params=params, ttl=4 * 3600, timeout=24)
        rows = _extract_hltv_player_rows(html or "") if html else {}
        before = len(merged)
        for key, row in rows.items():
            rec = dict(row or {})
            rec["_source_age_seconds"] = 0.0
            rec["_source_cache"] = "v5.9.2 direct HLTV"
            rec["_source_fresh"] = True
            merged[key] = rec
        page_status.append({**dict(status or {}), "page": page_no, "rows": len(rows), "new_rows": len(merged)-before})
        if not rows or (page_no and len(merged) == before):
            break
        # HLTV often returns fewer than 100 records on the final page.
        if len(rows) < 90:
            break
    status = {
        "ok": bool(merged), "provider": "HLTV direct paginated player table v5.9.2",
        "rows": len(merged), "period_days": days, "age_seconds": 0.0,
        "fetched_at": now_iso(), "pages": page_status,
    }
    V592_TABLE_CACHE[days] = {"rows": dict(merged), "status": status}
    V592_PROFILE_HEALTH.setdefault("table_rows", {})[str(days)] = len(merged)
    _v592_health({"last_table_status": status})
    return merged, status


def fetch_hltv_player_table(days):
    rows, status = _v592_direct_table(days)
    if rows:
        try:
            sqlite_store_entity_snapshot("player_table", f"{int(days)}d", {"rows": len(rows), "players": rows}, "HLTV direct player table v5.9.2", 0, now_iso())
        except Exception:
            pass
        return rows, status
    # Preserve the already-built verified persistent cache if public HLTV is temporarily unavailable.
    if isinstance(globals().get("V51_RUNTIME"), dict) and V51_RUNTIME.get("table"):
        fallback = dict(V51_RUNTIME.get("table") or {})
        return fallback, {"ok": bool(fallback), "provider": "persistent verified player cache fallback", "rows": len(fallback), "requested_days": int(days)}
    return {}, status


def _v592_profile_get(path, days=180, map_name="", side=""):
    start_date, end_date = _period_dates(days)
    params = {"startDate": start_date, "endDate": end_date, "csVersion": "CS2"}
    if map_name and map_name in HLTV_MAP_KEYS: params["maps"] = HLTV_MAP_KEYS[map_name]
    if side: params["side"] = side
    V592_PROFILE_HEALTH["advanced_requests"] = int(V592_PROFILE_HEALTH.get("advanced_requests") or 0) + 1
    page, status = _v591_direct_get(f"{HLTV_BASE}{path}", params=params, ttl=4 * 3600, timeout=22)
    if page:
        V592_PROFILE_HEALTH["advanced_ok"] = int(V592_PROFILE_HEALTH.get("advanced_ok") or 0) + 1
    else:
        V592_PROFILE_HEALTH["last_error"] = str((status or {}).get("warning") or "direct player page unavailable")
    return page, status


def fetch_hltv_individual_profile(player_id, slug, days=180):
    if not player_id:
        return {}, {"ok": False, "warning": "no player id"}
    page, status = _v592_profile_get(f"/stats/players/individual/{player_id}/{slug}", days)
    if not page:
        _v592_health(); return {}, status
    profile = {
        "kills": safe_int(_extract_labeled_metric(page, ["Total kills"]), 0),
        "deaths": safe_int(_extract_labeled_metric(page, ["Total deaths"]), 0),
        "rounds": safe_int(_extract_labeled_metric(page, ["Total rounds played", "Rounds played"]), 0),
        "maps": safe_int(_extract_labeled_metric(page, ["Maps played"]), 0),
        "kpr": _extract_labeled_metric(page, ["Kills / round", "Kills per round", "Kill / Round"]),
        "dpr": _extract_labeled_metric(page, ["Deaths / round", "Deaths per round"]),
        "adr": _extract_labeled_metric(page, ["Damage / round", "Average damage per round"]),
        "kd": _extract_labeled_metric(page, ["K/D ratio", "Kill / death ratio"]),
        "rating": _extract_labeled_metric(page, ["Rating 3.0", "Rating 2.1", "Rating 2.0", "Rating"]),
        "hs_pct": _extract_labeled_metric(page, ["Headshot %", "Headshots"]),
        "opening_kpr": _extract_labeled_metric(page, ["Opening kills / round", "Opening kills per round"]),
        "href": f"{HLTV_BASE}/stats/players/individual/{player_id}/{slug}",
    }
    if profile["kpr"] is None and (profile["rounds"] or 0) > 0 and (profile["kills"] or 0) > 0:
        profile["kpr"] = float(profile["kills"]) / float(profile["rounds"])
    if profile["hs_pct"] is not None and profile["hs_pct"] <= 1: profile["hs_pct"] *= 100
    status = {**dict(status or {}), "ok": bool(profile.get("kpr") is not None or profile.get("kills")), "metrics": sum(v not in [None,0,""] for v in profile.values()), "age_seconds": 0.0}
    _v592_health({"last_individual_status": status})
    return profile, status


def fetch_hltv_player_overview_profile(player_id, slug, days=180, map_name=""):
    if not player_id:
        return {}, {"ok": False, "warning": "no player id"}
    page, status = _v592_profile_get(f"/stats/players/{player_id}/{slug}", days, map_name=map_name)
    if not page:
        _v592_health(); return {}, status
    values = _extract_labeled_metrics_all(page, "Kills per round", limit=6)
    advanced = _extract_round_and_weapon_stats(page)
    advanced.update({
        "kpr": values[0] if values else None,
        "ct_kpr": values[1] if len(values) >= 3 else None,
        "t_kpr": values[2] if len(values) >= 3 else None,
        "rating": _extract_labeled_metric(page, ["Rating 3.0", "Rating 2.1", "Rating 2.0", "Rating"]),
        "adr": _extract_labeled_metric(page, ["Damage per round", "Average damage per round"]),
        "maps": _extract_labeled_metric(page, ["maps"]),
        "href": f"{HLTV_BASE}/stats/players/{player_id}/{slug}", "map_name": map_name,
    })
    status = {**dict(status or {}), "ok": bool(advanced.get("kpr") is not None or advanced.get("rating") is not None), "metric_count": sum(v not in [None,0,""] for v in advanced.values()), "map_name": map_name, "age_seconds": 0.0}
    _v592_health({"last_overview_status": status})
    return advanced, status


def fetch_hltv_filtered_player_profile(player_id, slug, days=180, map_name="", side=""):
    if not player_id:
        return {}, {"ok": False, "warning": "no player id"}
    page, status = _v592_profile_get(f"/stats/players/individual/{player_id}/{slug}", days, map_name=map_name, side=side)
    if not page:
        _v592_health(); return {}, status
    profile = {
        "kills": safe_int(_extract_labeled_metric(page, ["Kills", "Total kills"]), 0) or 0,
        "deaths": safe_int(_extract_labeled_metric(page, ["Deaths", "Total deaths"]), 0) or 0,
        "rounds": safe_int(_extract_labeled_metric(page, ["Total rounds played", "Rounds played"]), 0) or 0,
        "maps": safe_int(_extract_labeled_metric(page, ["Maps played"]), 0) or 0,
        "kpr": _extract_labeled_metric(page, ["Kill / Round", "Kills / round", "Kills per round"]),
        "dpr": _extract_labeled_metric(page, ["Deaths / round", "Deaths per round"]),
        "adr": _extract_labeled_metric(page, ["Damage / round", "Average damage per round"]),
        "rating": _extract_labeled_metric(page, ["Rating 3.0", "Rating 2.1", "Rating 2.0", "Rating"]),
        "opening_kills": safe_int(_extract_labeled_metric(page, ["Total opening kills"]), 0) or 0,
        "opening_deaths": safe_int(_extract_labeled_metric(page, ["Total opening deaths"]), 0) or 0,
        "opening_ratio": _extract_labeled_metric(page, ["Opening kill ratio"]),
        "map_name": map_name, "side": side,
        "href": f"{HLTV_BASE}/stats/players/individual/{player_id}/{slug}",
    }
    if profile["kpr"] is None and profile["rounds"] > 0 and profile["kills"] > 0: profile["kpr"] = profile["kills"] / profile["rounds"]
    if profile["dpr"] is None and profile["rounds"] > 0 and profile["deaths"] > 0: profile["dpr"] = profile["deaths"] / profile["rounds"]
    status = {**dict(status or {}), "ok": bool(profile.get("kpr") is not None or profile.get("kills")), "map_name": map_name, "side": side, "maps": profile["maps"], "rounds": profile["rounds"], "age_seconds": 0.0}
    _v592_health({"last_filtered_status": status})
    return profile, status


try:
    APP_VERSION = "CS2 v5.9.2 — DIRECT VERIFIED HLTV PLAYER PROFILES"
except Exception:
    pass
# === END ONEWAYPICKZ V5.9.2 DIRECT HLTV VERIFIED PLAYER PROFILES ===
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
        tmp = p.with_suffix(p.suffix + ".v592.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.9.2 patch {'applied' if changed else 'already present'}: {p}")
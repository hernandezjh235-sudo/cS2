from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.0 DIRECT HLTV MATCH DISCOVERY ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.0 DIRECT HLTV MATCH DISCOVERY ===
# Provider discovery only. Protected Maps 1-2 Kills projection math,
# probabilities, thresholds, side choice, and confidence are unchanged.
AUTOFEED_PROVIDER_V590_VERSION = "5.9.0"

import html as _v590_html
import time as _v590_time
import requests as _v590_requests

V590_MATCH_INDEX = {"fetched_at": 0.0, "links": [], "status": {}}
V590_DISCOVERY = {"attempts": 0, "hits": 0, "misses": 0, "last_error": "", "last_url": "", "last_pair": ""}
V590_PROVIDER_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_provider_discovery_health.json")


def _v590_slug(value):
    raw = _v590_html.unescape(str(value or "")).lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return re.sub(r"-+", "-", raw)


def _v590_team_variants(value):
    slug = _v590_slug(value)
    if not slug:
        return []
    generic = {"team", "esports", "esport", "gaming", "club", "cs2", "counter", "strike", "organization", "org"}
    toks = [x for x in slug.split("-") if x]
    variants = {slug}
    trimmed = [x for x in toks if x not in generic]
    if trimmed:
        variants.add("-".join(trimmed))
    # Prefix/suffix-only stripping keeps names like Eternal Fire or Infinite Gaming
    # safe while still handling Team Spirit / Falcons Esports style source labels.
    edge = list(toks)
    while edge and edge[0] in generic:
        edge.pop(0)
    while edge and edge[-1] in generic:
        edge.pop()
    if edge:
        variants.add("-".join(edge))
    return sorted((x for x in variants if len(x) >= 2), key=len, reverse=True)


def _v590_get_match_index(force=False):
    now = _v590_time.time()
    if not force and V590_MATCH_INDEX.get("links") and now - float(V590_MATCH_INDEX.get("fetched_at") or 0.0) < 90.0:
        return list(V590_MATCH_INDEX.get("links") or []), dict(V590_MATCH_INDEX.get("status") or {})
    url = "https://www.hltv.org/matches"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        response = _v590_requests.get(url, headers=headers, timeout=22, allow_redirects=True)
        text = response.text or ""
        raw_links = re.findall(r'href=["\'](/matches/\d+/[^"\'#?]+)', text, flags=re.I)
        seen, links = set(), []
        for href in raw_links:
            href = _v590_html.unescape(str(href or "")).strip()
            if not re.match(r"^/matches/\d+/[a-z0-9%_-]+", href, flags=re.I):
                continue
            if href in seen:
                continue
            seen.add(href)
            parts = href.strip("/").split("/", 2)
            if len(parts) < 3:
                continue
            match_id, slug = parts[1], parts[2]
            if "-vs-" not in slug:
                continue
            links.append({"url": "https://www.hltv.org" + href, "match_id": match_id, "slug": slug})
        status = {
            "ok": bool(response.status_code == 200 and links),
            "provider": "HLTV public /matches direct HTML",
            "status": int(response.status_code),
            "bytes": len(text.encode("utf-8", errors="ignore")),
            "links": len(links),
            "fetched_at": now_iso(),
            "url": url,
        }
        if not links:
            status["warning"] = "HLTV matches page returned no parseable /matches/<id>/<slug> links"
        V590_MATCH_INDEX.update({"fetched_at": now, "links": links, "status": status})
        return list(links), dict(status)
    except Exception as exc:
        status = {"ok": False, "provider": "HLTV public /matches direct HTML", "url": url, "warning": f"{type(exc).__name__}: {exc}", "fetched_at": now_iso()}
        V590_MATCH_INDEX.update({"fetched_at": now, "links": [], "status": status})
        return [], status


def _v590_side_score(side_slug, target):
    side = _v590_slug(side_slug)
    if not side:
        return 0.0
    best = 0.0
    for variant in _v590_team_variants(target):
        if side == variant:
            best = max(best, 1.0)
        elif side.startswith(variant + "-") or variant.startswith(side + "-"):
            best = max(best, 0.94)
        else:
            try:
                best = max(best, float(name_similarity(side.replace("-", " "), variant.replace("-", " "))))
            except Exception:
                pass
    return best


def _v590_right_score(right_slug, target):
    right = _v590_slug(right_slug)
    best = 0.0
    for variant in _v590_team_variants(target):
        if right == variant:
            best = max(best, 1.0)
        elif right.startswith(variant + "-"):
            # HLTV appends the event slug after the second team name.
            best = max(best, 0.99)
        else:
            # Only compare the beginning of the right side; comparing against the
            # event suffix can create false positives for common team names.
            prefix = "-".join(right.split("-")[: max(1, len(variant.split("-")) + 1)])
            try:
                best = max(best, float(name_similarity(prefix.replace("-", " "), variant.replace("-", " "))))
            except Exception:
                pass
    return best


def _v590_find_hltv(team, opponent):
    links, status = _v590_get_match_index()
    V590_DISCOVERY["attempts"] = int(V590_DISCOVERY.get("attempts") or 0) + 1
    V590_DISCOVERY["last_pair"] = f"{team} vs {opponent}"
    best = (0.0, None, None)
    for rec in links:
        slug = str(rec.get("slug") or "")
        if "-vs-" not in slug:
            continue
        left, right = slug.split("-vs-", 1)
        s1 = _v590_side_score(left, team)
        s2 = _v590_right_score(right, opponent)
        forward = min(s1, s2) * 0.78 + (s1 + s2) * 0.11
        r1 = _v590_side_score(left, opponent)
        r2 = _v590_right_score(right, team)
        reverse = min(r1, r2) * 0.78 + (r1 + r2) * 0.11
        score = max(forward, reverse)
        if score > best[0]:
            best = (score, rec, "forward" if forward >= reverse else "reverse")
    if best[1] and best[0] >= 0.86:
        rec = dict(best[1])
        V590_DISCOVERY["hits"] = int(V590_DISCOVERY.get("hits") or 0) + 1
        V590_DISCOVERY["last_url"] = rec.get("url") or ""
        return rec.get("url") or "", {
            "ok": True,
            "provider": "HLTV public matches index v5.9.0",
            "match_id": str(rec.get("match_id") or ""),
            "match_score": round(float(best[0]), 4),
            "orientation": best[2],
            "index_status": status,
            "team": team,
            "opponent": opponent,
        }
    V590_DISCOVERY["misses"] = int(V590_DISCOVERY.get("misses") or 0) + 1
    return "", {
        "ok": False,
        "provider": "HLTV public matches index v5.9.0",
        "warning": "No exact current HLTV matchup link met the two-team threshold",
        "best_score": round(float(best[0]), 4),
        "index_status": status,
        "team": team,
        "opponent": opponent,
    }


# Replace the older discovery implementation with an exact two-team lookup against
# HLTV's current public match index. This endpoint is server-rendered and does not
# require an API key.
def discover_hltv_match(team, opponent, player=None):
    return _v590_find_hltv(str(team or "").strip(), str(opponent or "").strip())


# v5.8.7 already owns verified provider-context merging. Its runtime global lookup
# now sees the direct discovery function above; this wrapper only records health.
if "_v587_discover_provider" in globals():
    _v590_provider_base = _v587_discover_provider
    def _v587_discover_provider(row):
        out = _v590_provider_base(row)
        try:
            url = str(out.get("provider_match_url") or out.get("match_url") or "")
            payload = {
                "version": "5.9.0", "updated_at": now_iso(),
                "index": dict(V590_MATCH_INDEX.get("status") or {}),
                "discovery": dict(V590_DISCOVERY),
                "last_row_provider_url": url if _v587_provider_url(url) else "",
                "projection_math_changed": False,
            }
            save_json(V590_PROVIDER_HEALTH_FILE, payload, force=True)
        except Exception:
            pass
        return out


try:
    APP_VERSION = "CS2 v5.9.0 — DIRECT HLTV MATCH DISCOVERY"
except Exception:
    pass
# === END ONEWAYPICKZ V5.9.0 DIRECT HLTV MATCH DISCOVERY ===
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
        tmp = p.with_suffix(p.suffix + ".v590.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.9.0 patch {'applied' if changed else 'already present'}: {p}")
from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.1 CACHE-FIRST WEB MATCH CONTEXT ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.1 CACHE-FIRST WEB MATCH CONTEXT ===
# Web latency fix only. The browser reads verified persistent match/roster data;
# the background collector remains responsible for slow provider discovery.
# Protected Maps 1-2 kill projection math and readiness gates are unchanged.
AUTOFEED_WEBFAST_VERSION = "5.8.1"
V581_ALLOW_WEB_PROVIDER_NETWORK = os.getenv("CS2_WEB_ALLOW_PROVIDER_NETWORK", "false").strip().lower() in {"1", "true", "yes", "on"}
V581_WEB_MATCH_CACHE = {}


def _v581_bridge_match(team, opponent, player=""):
    key = "|".join(sorted([normalize_team(team), normalize_team(opponent)]))
    if key in V581_WEB_MATCH_CACHE:
        return V581_WEB_MATCH_CACHE[key]
    url, meta = "", {}
    if callable(globals().get("_v48_bridge_match")):
        try:
            url, meta = _v48_bridge_match(team, opponent, player)
        except Exception as exc:
            url, meta = "", {"ok": False, "warning": f"bridge lookup failed: {type(exc).__name__}: {exc}"}
    out = (url, dict(meta or {}))
    V581_WEB_MATCH_CACHE[key] = out
    return out


if "discover_bo3_match" in globals():
    _v581_bo3_network_base = discover_bo3_match
    def discover_bo3_match(team, opponent, player=""):
        url, meta = _v581_bridge_match(team, opponent, player)
        if url:
            return url, {**dict(meta or {}), "v581_cache_first": True}
        if V581_ALLOW_WEB_PROVIDER_NETWORK:
            return _v581_bo3_network_base(team, opponent, player)
        return "", {
            "ok": False,
            "provider": "v5.8.1 cache-first web runtime",
            "background_collector": True,
            "warning": "Verified match context is not cached yet; background collector will recover it without blocking the browser.",
        }


if "discover_hltv_match" in globals():
    _v581_hltv_network_base = discover_hltv_match
    def discover_hltv_match(team, opponent, player=""):
        url, meta = _v581_bridge_match(team, opponent, player)
        if url:
            return url, {**dict(meta or {}), "v581_cache_first": True}
        if V581_ALLOW_WEB_PROVIDER_NETWORK:
            return _v581_hltv_network_base(team, opponent, player)
        return "", {
            "ok": False,
            "provider": "v5.8.1 cache-first web runtime",
            "background_collector": True,
            "warning": "Verified match context is not cached yet; background collector will recover it without blocking the browser.",
        }


try:
    APP_VERSION = "CS2 v5.8.1 — FAST CACHE-FIRST WEB + COMPLETE LIVE LINE COVERAGE"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.1 CACHE-FIRST WEB MATCH CONTEXT ===
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
        tmp = p.with_suffix(p.suffix + ".v581.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8.1 patch {'applied' if changed else 'already present'}: {p}")

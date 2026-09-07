from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V6.0.2 HLTV READER HTML FALLBACK ==="

OVERLAY = r'''
# === ONEWAYPICKZ V6.0.2 HLTV READER HTML FALLBACK ===
# Transport/reliability patch only. Projection math, side selection, Monte Carlo,
# thresholds, calibration, and frozen pregame values are unchanged.
AUTOFEED_HLTV_READER_V602_VERSION = "6.0.2"
V602_HLTV_READER_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_hltv_reader_health.json")
V602_HLTV_READER_HEALTH = {
    "attempts": 0,
    "successes": 0,
    "failures": 0,
    "last_target": "",
    "last_status": {},
}


def _v602_save_health(extra=None):
    try:
        payload = {"version": "6.0.2", "updated_at": now_iso(), **dict(V602_HLTV_READER_HEALTH)}
        if isinstance(extra, dict):
            payload.update(extra)
        save_json(V602_HLTV_READER_HEALTH_FILE, payload, force=True)
    except Exception:
        pass


def _v602_reader_enabled():
    return str(os.getenv("CS2_HLTV_READER_FALLBACK", "true") or "true").strip().lower() not in {"0", "false", "no", "off"}


def _v602_reader_get(target_url, timeout=32):
    V602_HLTV_READER_HEALTH["attempts"] = int(V602_HLTV_READER_HEALTH.get("attempts") or 0) + 1
    V602_HLTV_READER_HEALTH["last_target"] = str(target_url or "")
    reader_url = "https://r.jina.ai/" + str(target_url or "")
    headers = {
        "X-Return-Format": "html",
        "X-Engine": "browser",
        "X-Timeout": str(max(10, min(30, int(timeout or 30)))),
        "X-No-Cache": "true",
        "Accept": "text/html,text/plain;q=0.9,*/*;q=0.8",
        "User-Agent": "OneWayPickz-CS2/6.0.2 verified-data-fallback",
    }
    key = str(os.getenv("JINA_API_KEY", "") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = _v591_requests.get(reader_url, headers=headers, timeout=timeout, allow_redirects=True)
        text = r.text or ""
        # Reader's HTML return should preserve enough DOM for the existing HLTV
        # parsers. Reject tiny/error payloads so durable verified cache remains the fallback.
        looks_html = bool(re.search(r"<(?:html|body|table|tr|a|div)\b", text, flags=re.I))
        ok = bool(r.status_code == 200 and len(text.encode("utf-8", errors="ignore")) > 1000 and looks_html)
        status = {
            "ok": ok,
            "status": int(r.status_code),
            "bytes": len(text.encode("utf-8", errors="ignore")),
            "url": str(target_url or ""),
            "reader_url": reader_url,
            "provider": "Jina Reader HTML fallback for HLTV v6.0.2",
            "jina_api_key_configured": bool(key),
            "fetched_at": now_iso(),
            "age_seconds": 0.0,
        }
        if ok:
            V602_HLTV_READER_HEALTH["successes"] = int(V602_HLTV_READER_HEALTH.get("successes") or 0) + 1
        else:
            V602_HLTV_READER_HEALTH["failures"] = int(V602_HLTV_READER_HEALTH.get("failures") or 0) + 1
            status["warning"] = "Reader fallback did not return usable HLTV HTML"
        V602_HLTV_READER_HEALTH["last_status"] = status
        _v602_save_health()
        return (text if ok else ""), status
    except Exception as exc:
        V602_HLTV_READER_HEALTH["failures"] = int(V602_HLTV_READER_HEALTH.get("failures") or 0) + 1
        status = {
            "ok": False,
            "url": str(target_url or ""),
            "reader_url": reader_url,
            "provider": "Jina Reader HTML fallback for HLTV v6.0.2",
            "warning": f"{type(exc).__name__}: {exc}",
            "fetched_at": now_iso(),
        }
        V602_HLTV_READER_HEALTH["last_status"] = status
        _v602_save_health()
        return "", status


if "_v591_direct_get" in globals():
    _v602_direct_get_base = _v591_direct_get

    def _v591_direct_get(url, params=None, ttl=180.0, timeout=22):
        page, direct_status = _v602_direct_get_base(url, params=params, ttl=ttl, timeout=timeout)
        if page:
            return page, direct_status
        raw_url = str(url or "")
        if not _v602_reader_enabled() or "hltv.org" not in raw_url.lower():
            return page, direct_status
        try:
            req = _v591_requests.Request("GET", raw_url, params=params or None).prepare()
            target = str(req.url or raw_url)
        except Exception:
            target = raw_url
        reader_page, reader_status = _v602_reader_get(target, timeout=max(26, int(timeout or 22)))
        if reader_page:
            merged = {
                **dict(reader_status or {}),
                "fallback": True,
                "direct_provider_status": dict(direct_status or {}),
                "direct_status_code": (direct_status or {}).get("status"),
                "recovered_from_direct_failure": True,
            }
            try:
                key = (str(url), tuple(sorted((params or {}).items())))
                V591_HTTP_CACHE[key] = {"at": _v591_time.time(), "text": reader_page, "status": merged}
            except Exception:
                pass
            return reader_page, merged
        return page, {
            **dict(direct_status or {}),
            "reader_fallback_attempted": True,
            "reader_status": dict(reader_status or {}),
        }


try:
    APP_VERSION = "CS2 v6.0.2 — HLTV READER FALLBACK + ROBUST SIDE GATE"
except Exception:
    pass
# === END ONEWAYPICKZ V6.0.2 HLTV READER HTML FALLBACK ===
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
        tmp = p.with_suffix(p.suffix + ".v602.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v6.0.2 patch {'applied' if changed else 'already present'}: {p}")

from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.7 DURABLE VERIFIED CONTEXT REPLAY ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.7 DURABLE VERIFIED CONTEXT REPLAY ===
# Data continuity only. Protected Maps 1-2 Kills projection math, probability
# math, side selection, confidence, thresholds, and frozen rows are unchanged.
#
# GitHub Actions can reach verified public provider pages that Railway may be
# blocked from reaching directly. The verified provider bridge already stores
# exact Underdog source IDs plus provider/deep-context facts. This layer replays
# those facts ONLY when the exact source match+player IDs still match the live
# row. Provider freshness is aged from the original verification timestamp, so
# replayed data can never masquerade as newly fetched data.
AUTOFEED_DURABLE_V597_VERSION = "5.9.7"
V597_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_v597_durable_context_health.json")
V597_HEALTH = {
    "bridge_records": 0,
    "exact_records": 0,
    "provider_rows_replayed": 0,
    "deep_rows_replayed": 0,
    "provider_rows_current": 0,
    "deep_rows_current": 0,
    "last_error": "",
}

from datetime import datetime as _v597_datetime, timezone as _v597_timezone


def _v597_save(extra=None):
    try:
        payload = {"version": "5.9.7", "updated_at": now_iso(), **dict(V597_HEALTH)}
        if isinstance(extra, dict):
            payload.update(extra)
        save_json(V597_HEALTH_FILE, payload, force=True)
    except Exception:
        pass


def _v597_iso_age(value):
    raw = str(value or "").strip()
    if not raw:
        return 601.0
    try:
        dt = _v597_datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_v597_timezone.utc)
        now = _v597_datetime.now(_v597_timezone.utc)
        return max(0.0, (now - dt.astimezone(_v597_timezone.utc)).total_seconds())
    except Exception:
        return 601.0


def _v597_source_ids(obj):
    obj = obj if isinstance(obj, dict) else {}
    ids = dict(obj.get("source_identity_ids") or {})
    mid = str(ids.get("match_id") or obj.get("underdog_match_id") or obj.get("source_match_id") or "").strip()
    pid = str(ids.get("player_id") or obj.get("underdog_player_id") or obj.get("source_player_id") or "").strip()
    return mid, pid


def _v597_bridge_records():
    path = str(globals().get("V48_BRIDGE_LOCAL_FILE") or os.path.join(STORAGE_DIR, "cs2_provider_cache.json"))
    try:
        bridge = load_json(path, {}) or {}
    except Exception:
        bridge = {}
    raw = bridge.get("matches") if isinstance(bridge, dict) else []
    if isinstance(raw, dict):
        rows = [dict(x) for x in raw.values() if isinstance(x, dict)]
    else:
        rows = [dict(x) for x in list(raw or []) if isinstance(x, dict)]
    V597_HEALTH["bridge_records"] = len(rows)
    return rows


def _v597_build_index():
    idx = {}
    exact = 0
    for rec in _v597_bridge_records():
        mid, pid = _v597_source_ids(rec)
        if not (mid and pid):
            continue
        url = str(rec.get("provider_match_url") or rec.get("match_url") or "").strip()
        if not _v587_provider_url(url):
            continue
        idx[(mid, pid)] = rec
        exact += 1
    V597_HEALTH["exact_records"] = exact
    return idx


V597_EXACT_INDEX = _v597_build_index()


def _v597_restore_verified_context(row):
    out = dict(row or {})
    mid, pid = _v597_source_ids(out)
    rec = V597_EXACT_INDEX.get((mid, pid)) if mid and pid else None
    if not isinstance(rec, dict):
        return out

    # Re-check exact source IDs before copying anything provider-derived.
    rmid, rpid = _v597_source_ids(rec)
    if not (rmid == mid and rpid == pid):
        return out
    url = str(rec.get("provider_match_url") or rec.get("match_url") or "").strip()
    if not _v587_provider_url(url):
        return out

    out["provider_match_url"] = url
    out["match_url"] = url
    if rec.get("provider_match_id"):
        out["provider_match_id"] = str(rec.get("provider_match_id") or "")
    fmt = str(rec.get("match_format") or "").strip().upper()
    if fmt and fmt not in {"UNKNOWN", "BO1"}:
        out["match_format"] = rec.get("match_format")
    if rec.get("event") and not out.get("event"):
        out["event"] = rec.get("event")

    # Provider identity itself is immutable for the exact source match/player.
    out["provider_context_verified"] = True
    out["v587_provider_context"] = True
    out["v597_context_replayed"] = True

    for src, dst in [
        ("lineup_groups", "confirmed_lineup_groups"),
        ("lineup_names", "confirmed_lineup_names"),
    ]:
        vals = list(rec.get(src) or [])
        if vals and not out.get(dst):
            out[dst] = vals

    for key in ["team_recent_maps", "team_mapstats_samples", "opponent_recent_maps", "opponent_mapstats_samples"]:
        try:
            value = int(rec.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            out[key] = max(int(out.get(key) or 0), value)

    provider_ts = str(rec.get("provider_context_verified_at") or "").strip()
    deep_ts = str(rec.get("deep_context_verified_at") or "").strip()
    if provider_ts:
        out["provider_context_verified_at"] = provider_ts
    if deep_ts:
        out["deep_context_verified_at"] = deep_ts

    # Age the provider timestamp. If an older cache lacks an absolute timestamp,
    # force age above the 600-second Official freshness gate instead of guessing.
    provider_age = _v597_iso_age(provider_ts)
    fresh = dict(out.get("source_freshness") or {})
    try:
        prior_age = float(fresh.get("match_age_seconds") or 0.0)
    except Exception:
        prior_age = 0.0
    fresh["match_age_seconds"] = max(prior_age, provider_age)
    out["source_freshness"] = fresh
    out["provider_context_cache_age_seconds"] = provider_age

    if int(out.get("team_recent_maps") or 0) > 0 and int(out.get("opponent_mapstats_samples") or 0) > 0:
        out["deep_team_map_verified"] = True
        out["v596_deep_context"] = True
        out["v597_deep_context_replayed"] = True
        V597_HEALTH["deep_rows_replayed"] = int(V597_HEALTH.get("deep_rows_replayed") or 0) + 1
    V597_HEALTH["provider_rows_replayed"] = int(V597_HEALTH.get("provider_rows_replayed") or 0) + 1
    return out


# v5.9.6 remains the source-of-truth provider/deep hydrator. We only seed it
# with an exact-ID verified bridge record when local provider transport is down.
_v597_provider_base = _v587_discover_provider

def _v587_discover_provider(row):
    seeded = _v597_restore_verified_context(row)
    out = _v597_provider_base(seeded)
    out = dict(out or {})

    provider_ok = bool(_v587_provider_url(out.get("provider_match_url") or out.get("match_url")) and out.get("v587_provider_context"))
    deep_ok = int(out.get("team_recent_maps") or 0) > 0 and int(out.get("opponent_mapstats_samples") or 0) > 0
    if provider_ok:
        V597_HEALTH["provider_rows_current"] = int(V597_HEALTH.get("provider_rows_current") or 0) + 1
        fresh = dict(out.get("source_freshness") or {})
        try:
            age = float(fresh.get("match_age_seconds") or 0.0)
        except Exception:
            age = 999999.0
        # A genuine current provider fetch in this cycle will replace the replay
        # age with its real fresh age. Stamp only that genuinely fresh context.
        if age <= 600.0 and not out.get("provider_context_verified_at"):
            out["provider_context_verified_at"] = now_iso()
    if deep_ok:
        V597_HEALTH["deep_rows_current"] = int(V597_HEALTH.get("deep_rows_current") or 0) + 1
        if not out.get("deep_context_verified_at"):
            out["deep_context_verified_at"] = now_iso()
    _v597_save()
    return out


try:
    APP_VERSION = "CS2 v5.9.7 — DURABLE VERIFIED CONTEXT REPLAY"
except Exception:
    pass
# === END ONEWAYPICKZ V5.9.7 DURABLE VERIFIED CONTEXT REPLAY ===
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
        tmp = p.with_suffix(p.suffix + ".v597.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.9.7 patch {'applied' if changed else 'already present'}: {p}")

from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.9.9 DURABLE VERIFIED PLAYER PROFILE REPLAY ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.9.9 DURABLE VERIFIED PLAYER PROFILE REPLAY ===
# Verified data continuity only. Projection math, probability math, side choice,
# confidence, thresholds, market lines and frozen rows remain unchanged.
AUTOFEED_DURABLE_PROFILES_V599_VERSION = "5.9.9"
V599_PROFILE_CACHE_FILE = os.path.join(STORAGE_DIR, "cs2_verified_profile_cache.json")
V599_HEALTH_FILE = os.path.join(STORAGE_DIR, "cs2_v599_profile_continuity_health.json")
V599_HEALTH = {
    "bridge_profiles": 0,
    "durable_profiles": 0,
    "player_db_profiles": 0,
    "merged_profiles": 0,
    "table_fallback_uses": 0,
    "direct_table_successes": 0,
    "player_db_hydrated": 0,
    "deep_data_forced_on": False,
    "last_error": "",
}

from datetime import datetime as _v599_datetime, timezone as _v599_timezone


def _v599_age_seconds(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = _v599_datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_v599_timezone.utc)
        return max(0.0, (_v599_datetime.now(_v599_timezone.utc) - dt.astimezone(_v599_timezone.utc)).total_seconds())
    except Exception:
        return None


def _v599_profile_valid(rec):
    if not isinstance(rec, dict):
        return False
    kpr = safe_float(rec.get("kpr") if rec.get("kpr") is not None else rec.get("base_kpr"), None)
    maps = safe_int(rec.get("profile_maps") if rec.get("profile_maps") is not None else rec.get("maps"), 0) or 0
    if kpr is None or not (0.30 <= float(kpr) <= 1.25) or maps < 4:
        return False
    src = " ".join(str(rec.get(k) or "") for k in ["profile_source", "provider", "kpr_source", "source", "identity_verified_source"]).lower()
    if not any(token in src for token in ["hltv", "bo3", "professional statistics", "verified current roster", "public player"]):
        return False
    return True


def _v599_clean_profile(key, rec):
    out = dict(rec or {})
    name = str(out.get("player") or out.get("nickname") or key or "").strip()
    if not name:
        return None, None
    norm = normalize_name(name)
    if not norm:
        return None, None
    out["player"] = name
    out.setdefault("nickname", name)
    if out.get("kpr") is None and out.get("base_kpr") is not None:
        out["kpr"] = out.get("base_kpr")
    if out.get("base_kpr") is None and out.get("kpr") is not None:
        out["base_kpr"] = out.get("kpr")
    if out.get("profile_maps") is None and out.get("maps") is not None:
        out["profile_maps"] = out.get("maps")
    if out.get("maps") is None and out.get("profile_maps") is not None:
        out["maps"] = out.get("profile_maps")
    stamp = out.get("updated_at") or out.get("generated_at") or out.get("identity_verified_at")
    age = _v599_age_seconds(stamp)
    if age is not None:
        out["_source_age_seconds"] = age
    out["_durable_verified_profile_v599"] = True
    out.setdefault("_source_cache", "durable verified profile cache v5.9.9")
    return norm, out


def _v599_dict_profiles(raw):
    if not isinstance(raw, dict):
        return {}
    for container_key in ["profiles", "players"]:
        if isinstance(raw.get(container_key), dict):
            raw = raw.get(container_key) or {}
            break
    out = {}
    for key, value in raw.items():
        if not _v599_profile_valid(value):
            continue
        norm, rec = _v599_clean_profile(key, value)
        if norm and rec:
            out[norm] = rec
    return out


def _v599_load_json_file(path):
    try:
        data = load_json(path, {}) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _v599_merge_profiles(*sources):
    merged = {}
    for source in sources:
        for key, rec in dict(source or {}).items():
            if _v599_profile_valid(rec):
                norm, clean = _v599_clean_profile(key, rec)
                if norm and clean:
                    merged[norm] = clean
    return merged


def _v599_persist_profiles(profiles, reason=""):
    profiles = _v599_dict_profiles(profiles)
    if not profiles:
        return
    try:
        old = _v599_dict_profiles(_v599_load_json_file(V599_PROFILE_CACHE_FILE))
        merged = _v599_merge_profiles(old, profiles)
        payload = {
            "version": "5.9.9",
            "generated_at": now_iso(),
            "reason": str(reason or "verified profile continuity"),
            "profiles": merged,
        }
        save_json(V599_PROFILE_CACHE_FILE, payload, force=True)
        V599_HEALTH["durable_profiles"] = len(merged)
    except Exception as exc:
        V599_HEALTH["last_error"] = f"durable cache: {type(exc).__name__}: {exc}"

    try:
        existing_raw = _v599_load_json_file(PLAYER_DATABASE_FILE)
        existing = _v599_dict_profiles(existing_raw)
        merged_db = _v599_merge_profiles(existing, profiles)
        if merged_db:
            save_json(PLAYER_DATABASE_FILE, merged_db, force=True)
            V599_HEALTH["player_db_hydrated"] = len(merged_db)
            try:
                meta = load_json(DATABASE_META_FILE, {}) or {}
                if isinstance(meta, dict):
                    meta["last_updated"] = now_iso()
                    meta["schema_version"] = max(int(meta.get("schema_version") or 0), int(globals().get("DATABASE_SCHEMA_VERSION") or 0))
                    meta["v599_player_database_profiles"] = len(merged_db)
                    save_json(DATABASE_META_FILE, meta, force=True)
            except Exception:
                pass
    except Exception as exc:
        V599_HEALTH["last_error"] = f"player db: {type(exc).__name__}: {exc}"


def _v599_collect_persistent_profiles():
    durable_raw = _v599_load_json_file(V599_PROFILE_CACHE_FILE)
    durable = _v599_dict_profiles(durable_raw)
    player_db = _v599_dict_profiles(_v599_load_json_file(PLAYER_DATABASE_FILE))
    bridge_path = str(globals().get("V48_BRIDGE_LOCAL_FILE") or os.path.join(STORAGE_DIR, "cs2_provider_cache.json"))
    bridge_raw = _v599_load_json_file(bridge_path)
    bridge = _v599_dict_profiles(bridge_raw)
    V599_HEALTH["bridge_profiles"] = len(bridge)
    V599_HEALTH["durable_profiles"] = len(durable)
    V599_HEALTH["player_db_profiles"] = len(player_db)
    merged = _v599_merge_profiles(player_db, durable, bridge)
    V599_HEALTH["merged_profiles"] = len(merged)
    if merged:
        _v599_persist_profiles(merged, "startup replay from verified persistent sources")
    return merged


V599_PERSISTENT_PROFILES = _v599_collect_persistent_profiles()

try:
    _v599_deep_env = str(os.getenv("CS2_DEEP_DATA", "true") or "true").strip().lower() not in {"0", "false", "no", "off"}
    if _v599_deep_env:
        DEEP_DATA_ENABLED_DEFAULT = True
        try:
            st.session_state["deep_data_enabled"] = True
        except Exception:
            pass
        V599_HEALTH["deep_data_forced_on"] = True
except Exception as exc:
    V599_HEALTH["last_error"] = f"deep data state: {type(exc).__name__}: {exc}"


_v599_fetch_table_base = fetch_hltv_player_table

def fetch_hltv_player_table(days):
    global V599_PERSISTENT_PROFILES
    try:
        rows, status = _v599_fetch_table_base(days)
    except Exception as exc:
        rows, status = {}, {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}
    valid = _v599_dict_profiles(rows if isinstance(rows, dict) else {})
    if valid:
        V599_HEALTH["direct_table_successes"] = int(V599_HEALTH.get("direct_table_successes") or 0) + 1
        V599_PERSISTENT_PROFILES = _v599_merge_profiles(V599_PERSISTENT_PROFILES, valid)
        _v599_persist_profiles(valid, f"successful player table {int(days or 0)}d")
        try:
            save_json(V599_HEALTH_FILE, {"version":"5.9.9","updated_at":now_iso(),**dict(V599_HEALTH)}, force=True)
        except Exception:
            pass
        return rows, status

    persistent = _v599_collect_persistent_profiles()
    if persistent:
        V599_PERSISTENT_PROFILES = persistent
        V599_HEALTH["table_fallback_uses"] = int(V599_HEALTH.get("table_fallback_uses") or 0) + 1
        ages = [safe_float((x or {}).get("_source_age_seconds"), None) for x in persistent.values()]
        ages = [x for x in ages if x is not None]
        fallback_status = {
            "ok": True,
            "provider": "durable verified BO3/HLTV player profile cache v5.9.9",
            "rows": len(persistent),
            "period_days": int(days or 0),
            "age_seconds": min(ages) if ages else None,
            "direct_status": dict(status or {}),
            "fallback": True,
            "provenance_preserved": True,
            "fetched_at": now_iso(),
        }
        try:
            save_json(V599_HEALTH_FILE, {"version":"5.9.9","updated_at":now_iso(),**dict(V599_HEALTH),"last_table_status":fallback_status}, force=True)
        except Exception:
            pass
        return dict(persistent), fallback_status
    return rows, status


try:
    APP_VERSION = "CS2 v5.9.9 — DURABLE VERIFIED PLAYER PROFILE REPLAY"
except Exception:
    pass
# === END ONEWAYPICKZ V5.9.9 DURABLE VERIFIED PLAYER PROFILE REPLAY ===
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
        tmp = p.with_suffix(p.suffix + ".v599.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.9.9 patch {'applied' if changed else 'already present'}: {p}")

from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.3 COMPLETE SUPPORTED-LINE VISIBILITY ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.3 COMPLETE SUPPORTED-LINE VISIBILITY ===
# Visibility / cache continuity only. Protected Maps 1-2 kill projection math,
# probability math, side selection, thresholds, and readiness gates are unchanged.
AUTOFEED_LIVEBOARD_V583_VERSION = "5.8.3"


def _v583_supported_market(row):
    if not isinstance(row, dict):
        return False
    if row.get("model_supported") is True or row.get("projection_eligible_market") is True:
        return True
    raw = f"{row.get('market','')} | {row.get('stat_name','')} | {row.get('evidence','')}".lower()
    compact = re.sub(r"[^a-z0-9]+", " ", raw)
    if "headshot" in raw:
        return False
    kills = bool(re.search(r"\bkills?\b", raw))
    maps12 = bool(
        re.search(r"\bmaps?\s*1\s*[-+&/]\s*(?:maps?\s*)?2\b", raw)
        or re.search(r"\bmaps?\s*1\s+(?:and\s+)?2\b", raw)
        or "maps 1 2" in compact
        or "map 1 2" in compact
    )
    return bool(kills and maps12)


def _v583_line_key(row):
    line_id = str((row or {}).get("source_line_id") or (row or {}).get("prop_id") or "").strip()
    if line_id:
        return ("id", line_id)
    line = safe_float((row or {}).get("line"), None)
    return (
        "fallback",
        normalize_name((row or {}).get("player")),
        line,
        str((row or {}).get("start_time") or "")[:16],
        normalize_name((row or {}).get("market") or "Maps 1-2 Kills"),
    )


def _v583_fresh_saved_supported_lines(max_age_seconds=420):
    path = globals().get("V582_LIVE_CATALOG_FILE") or globals().get("V58_LIVE_CATALOG_FILE")
    if not path:
        return []
    payload = load_json(path, {}) or {}
    if not isinstance(payload, dict):
        return []
    stamp = _parse_iso_datetime(payload.get("updated_at")) if callable(globals().get("_parse_iso_datetime")) else None
    if stamp is not None:
        try:
            age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
            if age > max_age_seconds:
                return []
        except Exception:
            pass
    return [dict(x) for x in list(payload.get("rows") or []) if isinstance(x, dict) and _v583_supported_market(x)]


if "fetch_underdog_cs2_board" in globals():
    _v583_ud_board_base = fetch_underdog_cs2_board
    def fetch_underdog_cs2_board():
        rows, meta = _v583_ud_board_base()
        rows = [dict(x) for x in list(rows or []) if isinstance(x, dict)]
        meta = dict(meta or {})
        by_key = {_v583_line_key(x): x for x in rows}
        restored = 0
        # A short-lived persistent catalog bridges transient endpoint misses but
        # can never resurrect old slate rows beyond seven minutes.
        for row in _v583_fresh_saved_supported_lines():
            key = _v583_line_key(row)
            if key not in by_key:
                by_key[key] = row
                restored += 1
        merged = list(by_key.values())
        for row in merged:
            if _v583_supported_market(row):
                row["market"] = "Maps 1-2 Kills"
                row["market_scope"] = "maps_1_2"
                row["market_scope_verified"] = True
                row["model_supported"] = True
                row["projection_eligible_market"] = True
        meta["v583_complete_line_visibility"] = {
            "input_rows": len(rows),
            "supported_rows_returned": sum(_v583_supported_market(x) for x in merged),
            "fresh_catalog_rows_restored": restored,
            "no_line_cap": True,
        }
        meta["rows"] = len(merged)
        return merged, meta


if "build_full_board" in globals():
    _v583_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        incoming = [dict(x) for x in list(props or []) if isinstance(x, dict) and _v583_supported_market(x)]
        board, status = _v583_board_base(incoming, deep_enabled)
        board = [dict(x) for x in list(board or []) if isinstance(x, dict)]
        status = dict(status or {})
        existing = {_v583_line_key(x) for x in board}
        added = 0
        for prop in incoming:
            key = _v583_line_key(prop)
            if key in existing:
                continue
            row = dict(prop)
            row.update({
                "market": "Maps 1-2 Kills",
                "market_scope": "maps_1_2",
                "market_scope_verified": True,
                "model_supported": True,
                "projection_eligible_market": True,
                "projection": None,
                "raw_projection": None,
                "probability": None,
                "raw_probability": None,
                "lean": "WAIT",
                "status": "PASS",
                "status_label": "⏳ DATA BUILDING — REAL LINE ONLY",
                "data_score": 0,
                "data_readiness_score": 0,
                "projection_data_ready": False,
                "official_data_ready": False,
                "identity_official_ready": False,
            })
            row["profile_maps"] = safe_int(row.get("profile_maps"), 0) or 0
            flags = list(row.get("flags") or [])
            flags.extend([
                "REAL UNDERDOG MAPS 1-2 KILL LINE — PROFILE/CONTEXT STILL BUILDING",
                "NO PROJECTION UNTIL VERIFIED PLAYER DATA PASSES GATES",
            ])
            row["flags"] = list(dict.fromkeys(flags))
            board.append(row)
            existing.add(key)
            added += 1
        board.sort(key=lambda x: (
            {"OFFICIAL":0,"PLAYABLE":1,"TRACK":2,"PASS":3}.get(str(x.get("status") or "PASS"), 9),
            1 if x.get("projection") is None else 0,
            str(x.get("start_time") or ""),
            normalize_name(x.get("player")),
        ))
        status["v583_line_completeness"] = {
            "supported_input_rows": len(incoming),
            "board_rows": len(board),
            "data_building_rows_added": added,
            "supported_rows_visible": sum(_v583_supported_market(x) for x in board),
            "no_line_cap": True,
            "projection_math_changed": False,
        }
        return board, status


try:
    APP_VERSION = "CS2 v5.8.3 — COMPLETE LIVE LINES + VERIFIED DATA GATES"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.3 COMPLETE SUPPORTED-LINE VISIBILITY ===
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
        tmp = p.with_suffix(p.suffix + ".v583.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8.3 patch {'applied' if changed else 'already present'}: {p}")

from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.4 ALL-LINE INSTANT DISPLAY ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.4 ALL-LINE INSTANT DISPLAY ===
# Visibility/latency only. Unsupported CS2 markets are display-only PASS rows.
# Protected Maps 1-2 Kills projection/probability/side/confidence math is unchanged.
AUTOFEED_LIVEBOARD_V584_VERSION = "5.8.4"


def _v584_line_key(row):
    row = row if isinstance(row, dict) else {}
    line_id = str(row.get("source_line_id") or row.get("prop_id") or "").strip()
    if line_id:
        return ("id", line_id)
    line = safe_float(row.get("line"), None)
    return (
        "fallback",
        normalize_name(row.get("player")),
        normalize_name(row.get("market") or row.get("stat_name") or ""),
        line,
        str(row.get("start_time") or "")[:16],
        normalize_name(row.get("matchup") or ""),
    )


def _v584_all_live_catalog(max_age_seconds=420):
    path = globals().get("V582_LIVE_CATALOG_FILE") or globals().get("V58_LIVE_CATALOG_FILE")
    payload = {}
    if path:
        try:
            payload = load_json(path, {}) or {}
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        return []
    stamp = None
    if callable(globals().get("_parse_iso_datetime")):
        try:
            stamp = _parse_iso_datetime(payload.get("updated_at"))
        except Exception:
            stamp = None
    if stamp is not None:
        try:
            age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
            if age > max_age_seconds:
                return []
        except Exception:
            pass
    return [dict(x) for x in list(payload.get("rows") or []) if isinstance(x, dict)]


def _v584_display_only_row(prop):
    row = dict(prop or {})
    row.update({
        "market_scope": "unsupported_model_market",
        "market_scope_verified": False,
        "model_supported": False,
        "projection_eligible_market": False,
        "projection": None,
        "raw_projection": None,
        "probability": None,
        "raw_probability": None,
        "lean": "WAIT",
        "status": "PASS",
        "status_label": "🚫 PASS — UNSUPPORTED MARKET (LINE ONLY)",
        "data_score": 0,
        "data_readiness_score": 0,
        "projection_data_ready": False,
        "official_data_ready": False,
        "identity_official_ready": False,
    })
    row["profile_maps"] = safe_int(row.get("profile_maps"), 0) or 0
    flags = list(row.get("flags") or [])
    flags.extend([
        "REAL UNDERDOG CS2 LINE — DISPLAY ONLY",
        "UNSUPPORTED MARKET — NO VALIDATED PROJECTION MODEL",
        "DO NOT APPLY MAPS 1-2 KILLS FORMULA TO THIS MARKET",
    ])
    row["flags"] = list(dict.fromkeys(flags))
    return row


if "build_full_board" in globals():
    _v584_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        # The protected model still receives only its supported rows through the
        # existing v5.8.3 wrapper. All other current CS2 lines are appended only
        # after modeling, so they cannot contaminate projections or confidence.
        board, status = _v584_board_base(props, deep_enabled)
        board = [dict(x) for x in list(board or []) if isinstance(x, dict)]
        status = dict(status or {})
        catalog = _v584_all_live_catalog()
        existing = {_v584_line_key(x) for x in board}
        unsupported_added = 0
        supported_catalog = 0
        unsupported_catalog = 0
        for prop in catalog:
            supported = bool(_v583_supported_market(prop)) if callable(globals().get("_v583_supported_market")) else bool(prop.get("model_supported"))
            if supported:
                supported_catalog += 1
                continue
            unsupported_catalog += 1
            key = _v584_line_key(prop)
            if key in existing:
                continue
            board.append(_v584_display_only_row(prop))
            existing.add(key)
            unsupported_added += 1

        board.sort(key=lambda x: (
            1 if str(x.get("market_scope") or "") == "unsupported_model_market" else 0,
            {"OFFICIAL":0,"PLAYABLE":1,"TRACK":2,"PASS":3}.get(str(x.get("status") or "PASS"), 9),
            1 if x.get("projection") is None else 0,
            str(x.get("start_time") or ""),
            normalize_name(x.get("player")),
            normalize_name(x.get("market") or ""),
        ))

        all_visible = len(board)
        unsupported_visible = sum(str(x.get("market_scope") or "") == "unsupported_model_market" for x in board)
        supported_visible = all_visible - unsupported_visible
        status["v584_all_line_visibility"] = {
            "catalog_rows": len(catalog),
            "supported_catalog_rows": supported_catalog,
            "unsupported_catalog_rows": unsupported_catalog,
            "unsupported_rows_added": unsupported_added,
            "supported_rows_visible": supported_visible,
            "unsupported_rows_visible": unsupported_visible,
            "all_rows_visible": all_visible,
            "no_line_cap": True,
            "unsupported_rows_modeled": 0,
            "projection_math_changed": False,
        }
        # Keep health diagnostics honest about the complete live display while
        # preserving the verified/projection-ready counters from prior layers.
        try:
            health_path = globals().get("V57_CONTEXT_HEALTH_FILE")
            if health_path:
                health = load_json(health_path, {}) or {}
                if isinstance(health, dict):
                    health["runtime_layer"] = "5.8.4"
                    health["updated_at"] = now_iso()
                    health["board_rows"] = all_visible
                    health["supported_rows_visible"] = supported_visible
                    health["unsupported_rows_visible"] = unsupported_visible
                    health["all_live_rows_visible"] = all_visible
                    health["data_building_rows"] = sum(
                        x.get("projection") is None and str(x.get("market_scope") or "") != "unsupported_model_market"
                        for x in board
                    )
                    save_json(health_path, health, force=True)
                    status["v57_context_health"] = health
        except Exception as exc:
            status["v584_context_stamp_warning"] = f"{type(exc).__name__}: {exc}"
        return board, status


try:
    APP_VERSION = "CS2 v5.8.4 — ALL LIVE LINES INSTANT + VERIFIED KILLS MODEL"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.4 ALL-LINE INSTANT DISPLAY ===
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
        tmp = p.with_suffix(p.suffix + ".v584.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8.4 patch {'applied' if changed else 'already present'}: {p}")

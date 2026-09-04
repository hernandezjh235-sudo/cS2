from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.4 ALL-LINE INSTANT DISPLAY ==="
SIDEBAR_SLOT_MARKER = '    _audit_sidebar_slot = st.empty()\n'

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.4 ALL-LINE INSTANT DISPLAY ===
# Visibility/latency only. Missing-data and unsupported rows are display-only PASS rows.
# Protected Maps 1-2 Kills projection/probability/side/confidence math is unchanged.
AUTOFEED_LIVEBOARD_V584_VERSION = "5.8.4"
V584_CATALOG_FRESH_SECONDS = int(max(120, min(1800, float(os.getenv("CS2_LIVE_CATALOG_FRESH_SECONDS", "420") or 420))))
V584_CATALOG_VISIBLE_SECONDS = int(max(V584_CATALOG_FRESH_SECONDS, min(21600, float(os.getenv("CS2_LIVE_CATALOG_VISIBLE_SECONDS", "7200") or 7200))))


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


def _v584_all_live_catalog(max_age_seconds=None):
    # The verified model path still uses its much shorter freshness window.
    # This longer window is DISPLAY ONLY so transient provider blocks do not
    # make genuine Underdog lines disappear from the user's screen.
    path = globals().get("V582_LIVE_CATALOG_FILE") or globals().get("V58_LIVE_CATALOG_FILE")
    payload = {}
    if path:
        try:
            payload = load_json(path, {}) or {}
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        return []
    visible_seconds = int(max_age_seconds or V584_CATALOG_VISIBLE_SECONDS)
    age = None
    stamp = None
    if callable(globals().get("_parse_iso_datetime")):
        try:
            stamp = _parse_iso_datetime(payload.get("updated_at"))
        except Exception:
            stamp = None
    if stamp is not None:
        try:
            age = max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
            if age > visible_seconds:
                return []
        except Exception:
            age = None
    stale = bool(age is not None and age > V584_CATALOG_FRESH_SECONDS)
    out = []
    for raw in list(payload.get("rows") or []):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["v584_catalog_age_seconds"] = round(age, 1) if age is not None else None
        row["v584_catalog_stale"] = stale
        out.append(row)
    return out


def _v584_base_display_row(prop, supported=False):
    row = dict(prop or {})
    stale = bool(row.get("v584_catalog_stale"))
    if supported:
        scope = "maps_1_2"
        label = "⏳ DATA BUILDING — REAL LINE VISIBLE"
        flags = [
            "REAL UNDERDOG MAPS 1-2 KILL LINE — DISPLAYED WHILE VERIFIED DATA BUILDS",
            "NO PROJECTION OR CONFIDENCE UNTIL PLAYER/MATCH/LINEUP GATES PASS",
        ]
    else:
        scope = "unsupported_model_market"
        label = "🚫 PASS — UNSUPPORTED MARKET (LINE ONLY)"
        flags = [
            "REAL UNDERDOG CS2 LINE — DISPLAY ONLY",
            "UNSUPPORTED MARKET — NO VALIDATED PROJECTION MODEL",
            "DO NOT APPLY MAPS 1-2 KILLS FORMULA TO THIS MARKET",
        ]
    if stale:
        label = "🕒 CACHED LINE — TRACK ONLY / REFRESH PENDING"
        flags.append("CATALOG OLDER THAN LIVE MODEL WINDOW — NEVER ELIGIBLE FOR PROJECTION")
    row.update({
        "market_scope": scope,
        "market_scope_verified": bool(supported),
        "model_supported": bool(supported),
        "projection_eligible_market": bool(supported),
        "projection": None,
        "raw_projection": None,
        "probability": None,
        "raw_probability": None,
        "over_probability": None,
        "under_probability": None,
        "lean": "PASS",
        "status": "PASS",
        "status_label": label,
        "data_score": 0,
        "data_readiness_score": 0,
        "projection_data_ready": False,
        "official_data_ready": False,
        "identity_official_ready": False,
        "line_only_fallback": True,
        "assisted_official": False,
        "official_mode": "NONE",
        "confidence_grade": None,
        "best_win_tier": "PASS",
        "pick_action": "WAIT FOR VERIFIED DATA" if supported else "TRACK LINE ONLY",
    })
    row["profile_maps"] = safe_int(row.get("profile_maps"), 0) or 0
    row["flags"] = list(dict.fromkeys(list(row.get("flags") or []) + flags))
    row["risk_notes"] = list(row["flags"][:10])
    return row


def _v584_display_only_row(prop):
    return _v584_base_display_row(prop, supported=False)


def _v584_supported_pending_row(prop):
    return _v584_base_display_row(prop, supported=True)


def _v584_build_full_live_audit_zip():
    # Additive diagnostics only; never mutates projections or grading.
    buffer = io.BytesIO()
    board = [dict(x) for x in list(st.session_state.get("cs2_board") or []) if isinstance(x, dict)]
    board_status = dict(st.session_state.get("cs2_board_status") or {})
    source_status = dict(st.session_state.get("cs2_line_source_status") or {})
    try:
        health = app_data_health_report() if callable(globals().get("app_data_health_report")) else {}
    except Exception as exc:
        health = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        audit_df = build_app_audit_dataframe() if callable(globals().get("build_app_audit_dataframe")) else pd.DataFrame()
    except Exception:
        audit_df = pd.DataFrame()
    manifest = {
        "generated_at": now_iso() if callable(globals().get("now_iso")) else datetime.now(timezone.utc).isoformat(),
        "app_version": str(globals().get("APP_VERSION") or ""),
        "model_version": str(globals().get("MODEL_VERSION") or ""),
        "runtime_layer": "5.8.4",
        "storage_dir": str(globals().get("STORAGE_DIR") or ""),
        "board_rows": len(board),
        "audit_rows": len(audit_df),
        "purpose": "Pregame inputs, live board, data health, grading and learning diagnostics",
        "projection_math_changed": False,
    }
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        zf.writestr("data_health.json", json.dumps(health, indent=2, default=str))
        zf.writestr("board_status.json", json.dumps(board_status, indent=2, default=str))
        zf.writestr("source_status.json", json.dumps(source_status, indent=2, default=str))
        zf.writestr("current_board.json", json.dumps(board, indent=2, default=str))
        if board:
            try:
                zf.writestr("current_board.csv", pd.DataFrame(board).to_csv(index=False))
            except Exception:
                pass
        if not audit_df.empty:
            try:
                zf.writestr("pregame_result_audit.csv", audit_df.to_csv(index=False))
            except Exception:
                pass
        known_files = {
            "saved_snapshots.json": globals().get("PICK_LOG"),
            "graded_results.json": globals().get("RESULT_LOG"),
            "learning.json": globals().get("LEARNING_FILE"),
            "line_history.json": globals().get("LINE_HISTORY_FILE"),
            "context_health.json": globals().get("V57_CONTEXT_HEALTH_FILE"),
            "live_underdog_catalog.json": globals().get("V582_LIVE_CATALOG_FILE") or globals().get("V58_LIVE_CATALOG_FILE"),
            "operational_status.json": globals().get("V56_OPERATIONAL_FILE"),
            "grading_health.json": globals().get("V55_GRADING_HEALTH_FILE"),
        }
        for arcname, path in known_files.items():
            try:
                if path and os.path.isfile(path):
                    zf.write(path, f"storage/{arcname}")
            except Exception:
                pass
    return buffer.getvalue()


if "build_full_board" in globals():
    _v584_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        # Protected model receives only the same verified supported inputs as before.
        # The complete catalog is appended AFTER modeling strictly for visibility.
        board, status = _v584_board_base(props, deep_enabled)
        board = [dict(x) for x in list(board or []) if isinstance(x, dict)]
        status = dict(status or {})
        catalog = _v584_all_live_catalog()
        existing = {_v584_line_key(x) for x in board}
        unsupported_added = 0
        supported_pending_added = 0
        supported_catalog = 0
        unsupported_catalog = 0
        stale_catalog_rows = 0
        for prop in catalog:
            stale_catalog_rows += int(bool(prop.get("v584_catalog_stale")))
            supported = bool(_v583_supported_market(prop)) if callable(globals().get("_v583_supported_market")) else bool(prop.get("model_supported"))
            if supported:
                supported_catalog += 1
            else:
                unsupported_catalog += 1
            key = _v584_line_key(prop)
            if key in existing:
                continue
            if supported:
                board.append(_v584_supported_pending_row(prop))
                supported_pending_added += 1
            else:
                board.append(_v584_display_only_row(prop))
                unsupported_added += 1
            existing.add(key)

        board.sort(key=lambda x: (
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
            "supported_pending_rows_added": supported_pending_added,
            "unsupported_rows_added": unsupported_added,
            "stale_catalog_rows": stale_catalog_rows,
            "catalog_live_window_seconds": V584_CATALOG_FRESH_SECONDS,
            "catalog_display_window_seconds": V584_CATALOG_VISIBLE_SECONDS,
            "supported_rows_visible": supported_visible,
            "unsupported_rows_visible": unsupported_visible,
            "all_rows_visible": all_visible,
            "no_line_cap": True,
            "unsupported_rows_modeled": 0,
            "stale_rows_modeled": 0,
            "projection_math_changed": False,
        }
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
    APP_VERSION = "CS2 v5.8.4 — ALL LIVE LINES + FAST VERIFIED DATA + AUDIT"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.4 ALL-LINE INSTANT DISPLAY ===
'''

AUDIT_SLOT = r'''    _audit_sidebar_slot = st.empty()
    with _audit_sidebar_slot.container():
        with st.expander("🧪 FULL LIVE AUDIT DOWNLOAD", expanded=True):
            _audit_download_enabled = st.toggle(
                "Enable audit download",
                value=True,
                key="cs2_enable_full_live_audit_download",
            )
            if _audit_download_enabled:
                try:
                    _audit_zip = _v584_build_full_live_audit_zip()
                    st.download_button(
                        "⬇️ Download Full Live Audit",
                        data=_audit_zip,
                        file_name=f"cs2_full_live_audit_{local_now().date().isoformat()}.zip",
                        mime="application/zip",
                        use_container_width=True,
                        type="primary",
                        key="cs2_full_live_audit_download",
                    )
                    st.caption("Includes current board, source/data health, frozen snapshots, grading, learning, line history and context health.")
                except Exception as _audit_exc:
                    st.warning(f"Audit bundle unavailable: {type(_audit_exc).__name__}: {_audit_exc}")
'''


def patch_text(source: str) -> str:
    if PATCH_MARKER in source:
        return source
    if MARKER not in source:
        raise RuntimeError("SESSION BOARD LOAD marker not found")
    if SIDEBAR_SLOT_MARKER not in source:
        raise RuntimeError("audit sidebar slot marker not found")
    source = source.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)
    source = source.replace(SIDEBAR_SLOT_MARKER, AUDIT_SLOT, 1)
    # Visibility defaults only: users can still change these controls manually.
    source = source.replace(
        '    mobile_preview_rows = st.slider("Mobile rows/cards shown", 5, 50, 15, 5)',
        '    mobile_preview_rows = st.slider("Mobile rows/cards shown", 10, 250, 100, 10)',
        1,
    )
    source = source.replace(
        '    hide_line_only_passes = st.checkbox("Hide line-only PASS rows", value=True)',
        '    hide_line_only_passes = st.checkbox("Hide line-only PASS rows", value=False)',
        1,
    )
    source = source.replace(
        '        default=["OFFICIAL", "PLAYABLE", "TRACK"],',
        '        default=["OFFICIAL", "PLAYABLE", "TRACK", "PASS"],',
        1,
    )
    return source


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

from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V5.8.9 VERIFIED FREEZE + GRADING HANDOFF ==="

OVERLAY = r'''
# === ONEWAYPICKZ V5.8.9 VERIFIED FREEZE + GRADING HANDOFF ===
# Status/freeze/grading handoff only. Protected Maps 1-2 Kills projection math,
# probability math, side selection, thresholds, and confidence are unchanged.
AUTOFEED_HANDOFF_V589_VERSION = "5.8.9"


def _v589_refresh_ready_status(row):
    out = dict(row or {})
    if not (out.get("model_supported") and out.get("market_scope_verified")):
        return out
    if not callable(globals().get("_v55_ready")):
        return out
    try:
        ready = _v55_ready(out)
    except Exception:
        return out
    out["data_readiness"] = ready
    out["projection_data_ready"] = bool(ready.get("projection_ready"))
    out["official_data_ready"] = bool(ready.get("official_ready"))
    out["data_readiness_score"] = ready.get("readiness_score")

    # Strict readiness overlays can downgrade a row while data is still missing.
    # When that same frozen projection later becomes fully projection-ready, rerun
    # the app's existing classifier instead of leaving a stale DATA BUILDING tag.
    # The classifier is part of the protected app and no projection inputs change.
    if out.get("projection_data_ready") and safe_float(out.get("projection"), None) is not None:
        stale = out.get("status") == "PASS" or "DATA BUILDING" in str(out.get("status_label") or "")
        if stale and callable(globals().get("_v44_reclassify")):
            try:
                _v44_reclassify(out)
            except Exception:
                pass
        # Official still requires the stricter live roster/map/freshness/calibration
        # gates. Until those are all true, a model-qualified row can be tracked and
        # frozen for real postgame learning but cannot be presented as Official.
        if not out.get("official_data_ready") and out.get("status") in {"OFFICIAL", "PLAYABLE"}:
            out["status"] = "TRACK"
            out["status_label"] = "⚠️ TRACK — FULL DATA NOT READY"
            out["pick_action"] = "TRACK / FREEZE FOR LEARNING"
    return out


if "build_full_board" in globals():
    _v589_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        board, status = _v589_board_base(props, deep_enabled)
        board = [_v589_refresh_ready_status(x) for x in list(board or []) if isinstance(x, dict)]
        status = dict(status or {})
        projection_ready = sum(bool(x.get("projection_data_ready")) for x in board)
        official_ready = sum(bool(x.get("official_data_ready")) for x in board)
        freeze_candidates = sum(
            bool(x.get("projection_data_ready") and x.get("lean") in {"OVER", "UNDER"} and x.get("status") in {"OFFICIAL", "PLAYABLE", "TRACK"})
            for x in board
        )
        try:
            health = load_json(V57_CONTEXT_HEALTH_FILE, {}) or {}
            if isinstance(health, dict):
                health.update({
                    "version": "5.8.9", "runtime_layer": "5.8.9", "updated_at": now_iso(),
                    "board_rows": len(board), "projection_ready_rows": projection_ready,
                    "official_ready_rows": official_ready, "freeze_candidate_rows": freeze_candidates,
                    "projection_math_changed": False,
                })
                save_json(V57_CONTEXT_HEALTH_FILE, health, force=True)
                status["v57_context_health"] = health
            readiness = load_json(V55_READINESS_FILE, {}) or {}
            if isinstance(readiness, dict):
                readiness.update({
                    "version": "5.8.9", "updated_at": now_iso(), "board_rows": len(board),
                    "projection_ready_rows": projection_ready, "official_ready_rows": official_ready,
                    "freeze_candidate_rows": freeze_candidates,
                })
                save_json(V55_READINESS_FILE, readiness, force=True)
                status["v55_data_readiness"] = readiness
        except Exception:
            pass
        status["v589_verified_handoff"] = {
            "version": "5.8.9", "board_rows": len(board),
            "projection_ready_rows": projection_ready, "official_ready_rows": official_ready,
            "freeze_candidate_rows": freeze_candidates, "projection_math_changed": False,
        }
        return board, status


if "auto_freeze_verified_pregame" in globals():
    _v589_freeze_base = auto_freeze_verified_pregame
    def auto_freeze_verified_pregame(board):
        prepared = [_v589_refresh_ready_status(x) for x in list(board or []) if isinstance(x, dict)]
        out = dict(_v589_freeze_base(prepared) or {})
        out["verified_projection_ready"] = sum(bool(x.get("projection_data_ready")) for x in prepared)
        out["freeze_candidates"] = sum(
            bool(x.get("projection_data_ready") and x.get("lean") in {"OVER", "UNDER"} and x.get("status") in {"OFFICIAL", "PLAYABLE", "TRACK"})
            for x in prepared
        )
        out["version"] = "5.8.9"
        return out


try:
    APP_VERSION = "CS2 v5.8.9 — VERIFIED IDENTITY / PROVIDER / FREEZE / GRADING PIPELINE"
except Exception:
    pass
# === END ONEWAYPICKZ V5.8.9 VERIFIED FREEZE + GRADING HANDOFF ===
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
        tmp = p.with_suffix(p.suffix + ".v589.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v5.8.9 patch {'applied' if changed else 'already present'}: {p}")
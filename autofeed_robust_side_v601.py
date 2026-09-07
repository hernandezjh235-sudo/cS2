from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V6.0.1 ROBUST OVER-UNDER SIDE GATE ==="

OVERLAY = r'''
# === ONEWAYPICKZ V6.0.1 ROBUST OVER-UNDER SIDE GATE ===
# Selection-confidence layer only. Raw projection, Monte Carlo samples, line,
# lean, probability and frozen pregame values remain unchanged for grading.
AUTOFEED_ROBUST_SIDE_V601_VERSION = "6.0.1"
V601_ROUND_SWING = float(os.getenv("CS2_SIDE_ROUND_SWING", "3.0") or 3.0)
V601_MIN_TRACK_PROB = float(os.getenv("CS2_SIDE_MIN_TRACK_PROB", "0.575") or 0.575)
V601_MIN_TRACK_EDGE = float(os.getenv("CS2_SIDE_MIN_TRACK_EDGE", "1.20") or 1.20)
V601_MIN_TRACK_DATA = int(float(os.getenv("CS2_SIDE_MIN_TRACK_DATA", "66") or 66))
V601_MIN_PREVETO_MAP_CONF = float(os.getenv("CS2_SIDE_MIN_PREVETO_MAP_CONF", "65") or 65)


def _v601_num(value, default=None):
    try:
        if value is None or value == "": return default
        return float(value)
    except Exception:
        return default


def _v601_side_health(row):
    row = dict(row or {})
    projection = _v601_num(row.get("projection"), None)
    line = _v601_num(row.get("line"), None)
    expected_rounds = _v601_num(row.get("expected_rounds"), None)
    kpr = _v601_num(row.get("adjusted_kpr"), None)
    if kpr is None: kpr = _v601_num(row.get("base_kpr"), None)
    probability = _v601_num(row.get("probability"), None)
    edge = abs(_v601_num(row.get("edge"), 0.0) or 0.0)
    data_score = _v601_num(row.get("data_score"), 0.0) or 0.0
    map_conf = _v601_num(row.get("map_confidence"), None)
    veto_state = str(row.get("veto_state") or "").strip().upper()
    lean = str(row.get("lean") or "").strip().upper()
    status = str(row.get("status") or "").strip().upper()
    supported = bool(projection is not None and line is not None and lean in {"OVER", "UNDER"})
    reasons = []
    short_projection = long_projection = None
    side_stable = True

    if supported and expected_rounds and expected_rounds > V601_ROUND_SWING:
        if kpr is not None and 0.30 <= kpr <= 1.25:
            short_projection = projection - kpr * V601_ROUND_SWING
            long_projection = projection + kpr * V601_ROUND_SWING
        else:
            ratio = V601_ROUND_SWING / max(expected_rounds, 1.0)
            short_projection = projection * (1.0 - ratio)
            long_projection = projection * (1.0 + ratio)
        if lean == "OVER" and short_projection <= line:
            side_stable = False
            reasons.append("OVER flips in shorter-round scenario")
        if lean == "UNDER" and long_projection >= line:
            side_stable = False
            reasons.append("UNDER flips in longer-round scenario")

    if veto_state == "PRE_VETO" and (map_conf is None or map_conf < V601_MIN_PREVETO_MAP_CONF):
        reasons.append("low pre-veto map certainty")

    track_gate = bool(
        probability is not None and probability >= V601_MIN_TRACK_PROB and
        edge >= V601_MIN_TRACK_EDGE and data_score >= V601_MIN_TRACK_DATA
    )
    if status == "TRACK" and not track_gate:
        reasons.append("below strengthened TRACK edge/probability/data gate")

    hard_block = bool(
        supported and (
            not side_stable or
            (veto_state == "PRE_VETO" and (map_conf is None or map_conf < V601_MIN_PREVETO_MAP_CONF) and edge < 2.5) or
            (status == "TRACK" and not track_gate)
        )
    )
    return {
        "supported": supported,
        "side_stable": side_stable,
        "hard_block": hard_block,
        "short_projection": round(short_projection, 2) if short_projection is not None else None,
        "base_projection": projection,
        "long_projection": round(long_projection, 2) if long_projection is not None else None,
        "round_swing": V601_ROUND_SWING,
        "track_gate": track_gate,
        "reasons": reasons,
    }


def _v601_apply_gate(row):
    out = dict(row or {})
    health = _v601_side_health(out)
    out["side_stability"] = health
    out["side_stable"] = bool(health.get("side_stable"))
    out["side_short_projection"] = health.get("short_projection")
    out["side_long_projection"] = health.get("long_projection")
    if health.get("hard_block") and str(out.get("status") or "").upper() in {"TRACK", "PLAYABLE", "OFFICIAL"}:
        out["pre_v601_status"] = out.get("status")
        out["pre_v601_status_label"] = out.get("status_label")
        out["pre_v601_pick_action"] = out.get("pick_action")
        out["status"] = "PASS"
        out["pick_action"] = "PASS — SIDE UNSTABLE"
        out["status_label"] = "⛔ PASS — SIDE UNSTABLE"
        out["risk_notes"] = " | ".join(x for x in [str(out.get("risk_notes") or "").strip(), "; ".join(health.get("reasons") or [])] if x)
        out["flags"] = list(dict.fromkeys(list(out.get("flags") or []) + ["ROBUST SIDE GATE BLOCK"]))
    return out


if "build_full_board" in globals():
    _v601_build_full_board_base = build_full_board
    def build_full_board(props, deep_enabled=True):
        board, status = _v601_build_full_board_base(props, deep_enabled)
        blocked = stable = tested = 0
        for idx, row in enumerate(list(board or [])):
            updated = _v601_apply_gate(row)
            h = updated.get("side_stability") or {}
            if h.get("supported"):
                tested += 1
                stable += int(bool(h.get("side_stable")))
            blocked += int(updated.get("pre_v601_status") is not None)
            board[idx] = updated
        status = dict(status or {})
        status["v601_robust_side_gate"] = {
            "version": "6.0.1", "tested_rows": tested, "stable_rows": stable,
            "blocked_rows": blocked, "round_swing": V601_ROUND_SWING,
            "min_track_probability": V601_MIN_TRACK_PROB,
            "min_track_edge": V601_MIN_TRACK_EDGE,
            "min_track_data_score": V601_MIN_TRACK_DATA,
            "min_preveto_map_confidence": V601_MIN_PREVETO_MAP_CONF,
            "policy": "selection gate only; raw projection and lean preserved",
        }
        return board, status

try:
    APP_VERSION = "CS2 v6.0.1 — ROBUST OVER/UNDER SIDE GATE"
except Exception:
    pass
# === END ONEWAYPICKZ V6.0.1 ROBUST OVER-UNDER SIDE GATE ===
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
        tmp = p.with_suffix(p.suffix + ".v601.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v6.0.1 patch {'applied' if changed else 'already present'}: {p}")

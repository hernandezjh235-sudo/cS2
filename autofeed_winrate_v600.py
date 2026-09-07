from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "# ============================================================\n# SESSION BOARD LOAD"
PATCH_MARKER = "# === ONEWAYPICKZ V6.0 TRACKING + WIN-RATE AUDIT ==="

OVERLAY = r'''
# === ONEWAYPICKZ V6.0 TRACKING + WIN-RATE AUDIT ===
# Selection/audit/UI layer only. Projection math, Monte Carlo samples, side choice,
# probability calibration, official/playable thresholds, and frozen pregame values
# are intentionally unchanged.
AUTOFEED_WINRATE_V600_VERSION = "6.0"
V600_WINRATE_AUDIT_FILE = os.path.join(STORAGE_DIR, "cs2_winrate_loss_audit.json")


def _v600_text(value):
    return str(value or "").strip()


def _v600_display_action(row):
    """Return a truthful display action without mutating model status."""
    row = dict(row or {})
    status = _v600_text(row.get("status")).upper()
    action = _v600_text(row.get("pick_action")).upper()
    label = _v600_text(row.get("status_label"))
    flags = " | ".join(str(x) for x in (row.get("flags") or []))
    text = f"{label} {flags}".upper()

    if "UNSUPPORTED MARKET" in text:
        return "PASS — UNSUPPORTED MARKET", "🚫 PASS — UNSUPPORTED MARKET (LINE ONLY)"
    if status == "PASS":
        if "NEUTRAL" in text:
            return "PASS — NEUTRAL ZONE", "⛔ PASS — NEUTRAL ZONE"
        if any(x in text for x in ["DATA BUILDING", "PROFILE REQUIRED", "WAIT FOR VERIFIED PROFILE", "VERIFIED PROFILE"]):
            return "PASS / WAIT", "⏳ PASS / WAIT — VERIFIED PROFILE REQUIRED"
        return "PASS / WAIT", "⛔ PASS / WAIT"
    if status == "TRACK" or action == "TRACK ONLY":
        return "TRACK ONLY", "⚠️ TRACK ONLY"
    if status == "PLAYABLE":
        return "PLAYABLE", label or "✅ PLAYABLE"
    if status == "OFFICIAL":
        return "OFFICIAL", label or "🔥 OFFICIAL"
    return action or status or "PASS / WAIT", label or "⛔ PASS / WAIT"


def _v600_display_row(row):
    out = dict(row or {})
    action, label = _v600_display_action(out)
    out["pick_action"] = action
    out["status_label"] = label
    out["display_pick_action"] = action
    out["display_status_label"] = label
    return out


if "board_dataframe" in globals():
    _v600_board_dataframe_base = board_dataframe
    def board_dataframe(rows):
        safe_rows = [_v600_display_row(x) for x in list(rows or [])]
        return _v600_board_dataframe_base(safe_rows)


if "render_pick_card" in globals():
    _v600_render_pick_card_base = render_pick_card
    def render_pick_card(row, official_style=False):
        return _v600_render_pick_card_base(_v600_display_row(row), official_style=official_style)


def _v600_actual_rounds(row):
    """Best-effort actual Maps 1-2 rounds from verified grade metadata."""
    for key in ("actual_rounds", "maps12_rounds", "total_rounds"):
        val = safe_float(row.get(key), None)
        if val is not None and 20 <= val <= 90:
            return float(val)
    meta = row.get("grade_meta") if isinstance(row.get("grade_meta"), dict) else {}
    for key in ("actual_rounds", "maps12_rounds", "total_rounds"):
        val = safe_float(meta.get(key), None)
        if val is not None and 20 <= val <= 90:
            return float(val)
    results = meta.get("map_results") if isinstance(meta.get("map_results"), list) else []
    total = 0.0
    count = 0
    for rec in results[:2]:
        if not isinstance(rec, dict):
            continue
        score = rec.get("score") or rec.get("scores")
        if isinstance(score, (list, tuple)) and len(score) >= 2:
            a, b = safe_float(score[0], None), safe_float(score[1], None)
            if a is not None and b is not None:
                total += a + b; count += 1
        else:
            a = safe_float(rec.get("team1_score") or rec.get("score1") or rec.get("left_score"), None)
            b = safe_float(rec.get("team2_score") or rec.get("score2") or rec.get("right_score"), None)
            if a is not None and b is not None:
                total += a + b; count += 1
    return float(total) if count == 2 else None


def _v600_bucket(value, cuts, labels):
    x = safe_float(value, None)
    if x is None:
        return "UNKNOWN"
    for cut, label in zip(cuts, labels):
        if x < cut:
            return label
    return labels[-1]


def _v600_loss_reason(row):
    result = _v600_text(row.get("graded_result")).upper()
    if result != "LOSS":
        return ""
    expected_rounds = safe_float(row.get("expected_rounds"), None)
    actual_rounds = _v600_actual_rounds(row)
    projection = safe_float(row.get("projection_before_learning"), None)
    if projection is None:
        projection = safe_float(row.get("projection"), None)
    actual = safe_float(row.get("actual_kills"), None)
    edge = abs(safe_float(row.get("edge"), 0) or 0)
    data_score = safe_float(row.get("data_score"), 0) or 0
    profile_maps = safe_int(row.get("profile_maps"), 0) or 0
    map_conf = safe_float(row.get("map_confidence"), None)
    veto_state = _v600_text(row.get("veto_state")).upper()
    projected_kpr = safe_float(row.get("adjusted_kpr"), None)

    if actual_rounds is not None and expected_rounds is not None and abs(actual_rounds - expected_rounds) >= 5.0:
        return "ROUND ENVIRONMENT MISS"
    if actual_rounds and actual is not None and projected_kpr is not None:
        observed_kpr = actual / max(actual_rounds, 1.0)
        if abs(observed_kpr - projected_kpr) >= 0.085:
            return "PLAYER KPR / FORM MISS"
    if veto_state == "PRE_VETO" and (map_conf is None or map_conf < 65):
        return "MAP / VETO UNCERTAINTY"
    if profile_maps < MIN_OFFICIAL_PROFILE_MAPS:
        return "THIN PLAYER SAMPLE"
    if data_score < MIN_PLAYABLE_DATA_SCORE:
        return "LOW DATA QUALITY"
    if edge < 1.20:
        return "SMALL EDGE / COIN-FLIP ZONE"
    if projection is not None and actual is not None and abs(actual - projection) >= 5.0:
        return "LARGE PLAYER OUTCOME VARIANCE"
    return "UNEXPLAINED MODEL MISS"


def _v600_win_signature(row):
    if _v600_text(row.get("graded_result")).upper() != "WIN":
        return ""
    edge = abs(safe_float(row.get("edge"), 0) or 0)
    prob = safe_float(row.get("probability"), None)
    data = safe_float(row.get("data_score"), 0) or 0
    maps = safe_int(row.get("profile_maps"), 0) or 0
    pieces = []
    if edge >= 2.0: pieces.append("2+ EDGE")
    elif edge >= 1.2: pieces.append("1.2+ EDGE")
    else: pieces.append("SMALL EDGE")
    if prob is not None and prob >= .62: pieces.append("62%+")
    elif prob is not None and prob >= .575: pieces.append("57.5%+")
    if data >= 80: pieces.append("80+ DATA")
    if maps >= 25: pieces.append("25+ PROFILE MAPS")
    return " · ".join(pieces)


def build_v600_winrate_audit(results=None):
    raw = results
    if raw is None:
        raw = load_json(RESULT_LOG, [])
    raw = raw if isinstance(raw, list) else []
    rows = []
    for source in raw:
        result = _v600_text(source.get("graded_result")).upper()
        if result not in {"WIN", "LOSS", "PUSH"}:
            continue
        row = dict(source)
        actual_rounds = _v600_actual_rounds(row)
        actual = safe_float(row.get("actual_kills"), None)
        projection = safe_float(row.get("projection_before_learning"), None)
        if projection is None:
            projection = safe_float(row.get("projection"), None)
        observed_kpr = actual / actual_rounds if actual is not None and actual_rounds else None
        probability = safe_float(row.get("probability"), None)
        edge = abs(safe_float(row.get("edge"), 0) or 0)
        rows.append({
            "graded_at": row.get("graded_at"), "player": row.get("player"), "team": row.get("team"),
            "opponent": row.get("opponent"), "lean": row.get("lean"), "line": row.get("line"),
            "projection": projection, "actual_kills": actual, "result": result,
            "probability": probability, "edge": edge, "data_score": row.get("data_score"),
            "profile_maps": row.get("profile_maps"), "expected_rounds": row.get("expected_rounds"),
            "actual_rounds": actual_rounds, "round_error": (actual_rounds - safe_float(row.get("expected_rounds"), actual_rounds)) if actual_rounds is not None and safe_float(row.get("expected_rounds"), None) is not None else None,
            "adjusted_kpr": row.get("adjusted_kpr"), "observed_kpr": observed_kpr,
            "kpr_error": (observed_kpr - safe_float(row.get("adjusted_kpr"), observed_kpr)) if observed_kpr is not None and safe_float(row.get("adjusted_kpr"), None) is not None else None,
            "veto_state": row.get("veto_state"), "map_confidence": row.get("map_confidence"),
            "status": row.get("status"), "pick_action": _v600_display_action(row)[0],
            "loss_reason": _v600_loss_reason(row), "win_signature": _v600_win_signature(row),
            "edge_bucket": _v600_bucket(edge, [1.2, 2.0, 3.0, 999], ["<1.2", "1.2-1.99", "2.0-2.99", "3.0+"]),
            "prob_bucket": _v600_bucket(probability, [.575, .62, .67, 999], ["<57.5%", "57.5-61.9%", "62-66.9%", "67%+"]),
            "data_bucket": _v600_bucket(row.get("data_score"), [66, 80, 90, 999], ["<66", "66-79", "80-89", "90+"]),
            "profile_bucket": _v600_bucket(row.get("profile_maps"), [15, 25, 50, 100000], ["<15", "15-24", "25-49", "50+"]),
        })
    wins = sum(x["result"] == "WIN" for x in rows)
    losses = sum(x["result"] == "LOSS" for x in rows)
    pushes = sum(x["result"] == "PUSH" for x in rows)
    loss_reasons = {}
    win_signatures = {}
    for x in rows:
        if x["loss_reason"]:
            loss_reasons[x["loss_reason"]] = loss_reasons.get(x["loss_reason"], 0) + 1
        if x["win_signature"]:
            win_signatures[x["win_signature"]] = win_signatures.get(x["win_signature"], 0) + 1
    summary = {
        "version": "6.0", "updated_at": now_iso(), "wins": wins, "losses": losses, "pushes": pushes,
        "win_rate": wins / max(wins + losses, 1), "loss_reasons": loss_reasons,
        "win_signatures": win_signatures, "graded_rows": len(rows), "rows": rows,
        "policy": "diagnostic only; no projection formula changes and no automatic promotion",
    }
    try:
        save_json(V600_WINRATE_AUDIT_FILE, summary, force=True)
    except Exception:
        pass
    return summary


def v600_bucket_performance(rows, field):
    groups = {}
    for row in rows or []:
        key = str(row.get(field) or "UNKNOWN")
        result = str(row.get("result") or "")
        rec = groups.setdefault(key, {"bucket": key, "wins": 0, "losses": 0, "pushes": 0})
        if result == "WIN": rec["wins"] += 1
        elif result == "LOSS": rec["losses"] += 1
        elif result == "PUSH": rec["pushes"] += 1
    out = []
    for rec in groups.values():
        decisions = rec["wins"] + rec["losses"]
        rec["samples"] = decisions
        rec["win_rate"] = rec["wins"] / max(decisions, 1)
        out.append(rec)
    return sorted(out, key=lambda x: (-x["samples"], x["bucket"]))


try:
    APP_VERSION = "CS2 v6.0 — TRACKING + WIN-RATE LOSS AUDIT"
except Exception:
    pass
# === END ONEWAYPICKZ V6.0 TRACKING + WIN-RATE AUDIT ===
'''

UI_INSERT = r'''

    st.markdown('<div class="section-title-pro">Win-Rate Loss Audit</div>', unsafe_allow_html=True)
    _v600_audit = build_v600_winrate_audit(results_now)
    _v600_rows = _v600_audit.get("rows") or []
    if _v600_rows:
        _v600_df = pd.DataFrame(_v600_rows)
        _va1, _va2, _va3, _va4 = st.columns(4)
        _va1.metric("Audit Wins", _v600_audit.get("wins", 0))
        _va2.metric("Audit Losses", _v600_audit.get("losses", 0))
        _va3.metric("Audit Win Rate", f"{_v600_audit.get('win_rate', 0) * 100:.1f}%")
        _va4.metric("Graded Samples", _v600_audit.get("graded_rows", 0))
        _loss_df = _v600_df[_v600_df["result"] == "LOSS"].copy()
        if not _loss_df.empty:
            st.caption("Losses are diagnosed without changing the frozen pregame projection. Use repeated patterns—not one-off misses—to tighten future selection gates.")
            _loss_cols = [c for c in ["player","lean","line","projection","actual_kills","expected_rounds","actual_rounds","round_error","adjusted_kpr","observed_kpr","kpr_error","edge","probability","data_score","profile_maps","veto_state","loss_reason"] if c in _loss_df.columns]
            st.dataframe(_loss_df[_loss_cols].sort_values(["loss_reason","edge"], ascending=[True, False]).head(_preview_limit), use_container_width=True, hide_index=True)
        _perf = []
        for _field, _label in [("edge_bucket","Edge"),("prob_bucket","Probability"),("data_bucket","Data Score"),("profile_bucket","Profile Maps")]:
            for _rec in v600_bucket_performance(_v600_rows, _field):
                _perf.append({"group": _label, **_rec})
        if _perf:
            _perf_df = pd.DataFrame(_perf)
            _perf_df["win_rate"] = (_perf_df["win_rate"] * 100).round(1)
            st.markdown("**Win-rate buckets — use these to keep winning conditions and suppress repeated losing conditions**")
            st.dataframe(_perf_df[["group","bucket","wins","losses","pushes","samples","win_rate"]], use_container_width=True, hide_index=True)
        st.download_button("Download Win-Rate Loss Audit", _v600_df.to_csv(index=False).encode("utf-8"), "cs2_winrate_loss_audit.csv", "text/csv", use_container_width=True)
    else:
        st.info("Win-rate audit activates after finished WIN/LOSS grades exist. Pending matches do not count and will not be used to change selection behavior.")
'''


def patch_text(source: str) -> str:
    if PATCH_MARKER in source:
        return source
    if MARKER not in source:
        raise RuntimeError("SESSION BOARD LOAD marker not found")
    source = source.replace(MARKER, OVERLAY + "\n\n" + MARKER, 1)
    ui_anchor = "    st.markdown('<div class=\"section-title-pro\">Automatic Maps 1–2 Grading</div>', unsafe_allow_html=True)"
    if ui_anchor in source and "Win-Rate Loss Audit" not in source:
        source = source.replace(ui_anchor, UI_INSERT + "\n" + ui_anchor, 1)
    return source


def patch_app(path="app.py"):
    p = Path(path)
    old = p.read_text(encoding="utf-8")
    new = patch_text(old)
    changed = new != old
    if changed:
        tmp = p.with_suffix(p.suffix + ".v600.tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    return changed


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.py")
    changed = patch_app(p)
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
    print(f"v6.0 patch {'applied' if changed else 'already present'}: {p}")

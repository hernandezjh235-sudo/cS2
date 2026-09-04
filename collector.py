"""Railway cron collector for OneWayPickz CS2 v5.2 autofeed.

Runs every 10 minutes and keeps the shared Railway volume populated without
manual CSV/demo uploads: real lines, market ticks, verified profiles, full-board
projections, persistent entities, pregame freezes, and completed grading.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")
PATCH_PATH = Path(__file__).with_name("autofeed_patch.py")
MARKER = "# ============================================================\n# SESSION BOARD LOAD"


def truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ensure_runtime_patch() -> None:
    if not PATCH_PATH.exists():
        return
    spec = importlib.util.spec_from_file_location("cs2_autofeed_patch", PATCH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.patch_app(APP_PATH)


def load_engine() -> dict:
    ensure_runtime_patch()
    source = APP_PATH.read_text(encoding="utf-8")
    definitions = source.split(MARKER)[0]
    module_name = "onewaypickz_cs2_collector_runtime"
    module = types.ModuleType(module_name)
    module.__file__ = str(APP_PATH)
    sys.modules[module_name] = module
    exec(compile(definitions, str(APP_PATH), "exec"), module.__dict__)
    return module.__dict__


def main() -> int:
    os.environ.setdefault("CS2_DATA_DIR", "/data/cs2_engine")
    os.environ.setdefault("CS2_ASSISTED_OFFICIAL", "false")
    os.environ.setdefault("CS2_AUTO_HARVEST_HISTORY", "true")
    os.environ.setdefault("CS2_COLLECT_PROJECTIONS", "true")
    os.environ.setdefault("CS2_AUTO_GRADE", "true")
    os.environ.setdefault("CS2_DEEP_DATA", "true")
    os.environ.setdefault("CS2_BO3_PROFILES_PER_REFRESH", "180")

    ns = load_engine()
    ud_rows, ud_meta = ns["fetch_underdog_cs2_board"]()

    collect_pp = truthy("CS2_COLLECT_PRIZEPICKS", "false")
    if collect_pp:
        pp_rows, pp_meta = ns["fetch_prizepicks_cs2_board"]()
    else:
        pp_rows, pp_meta = [], {"ok": False, "disabled": True, "message": "PrizePicks collector disabled; Underdog is primary."}

    all_rows = ns["annotate_market_consensus"](list(ud_rows) + list(pp_rows))
    ticks = ns["sqlite_store_market_ticks"](all_rows)
    ns["update_line_history"](all_rows)
    demo_ingest = ns["auto_ingest_demo_dropbox"]()

    board = []
    board_status = {}
    profile_recovery = {}
    freeze_status = {"added": 0, "skipped": 0, "eligible": 0}
    grade_status = {"graded": 0, "pending": 0, "errors": 0}
    maintenance = {}

    props = [x for x in all_rows if str(x.get("source")) == "Underdog"]
    if props:
        players = list(dict.fromkeys(str(x.get("player") or "").strip() for x in props if str(x.get("player") or "").strip()))

        prefetch = ns.get("v48_prefetch_provider_data")
        if callable(prefetch):
            try:
                profile_recovery = prefetch(players, force=False) or {}
            except Exception as exc:
                profile_recovery = {"ok": False, "warning": str(exc), "requested": len(players)}

        # Build every real Underdog row. Source TTLs/caches prevent needless
        # repeated network requests while the databases continue to deepen.
        board, board_status = ns["build_full_board"](props, truthy("CS2_DEEP_DATA", "true"))

        ns["save_asof_projection_history"](
            board,
            {"Underdog": ud_meta, "PrizePicks": pp_meta, "collector": True, "autofeed": "v5.2"},
        )

        auto_freeze = ns.get("auto_freeze_verified_pregame")
        if callable(auto_freeze):
            try:
                freeze_status = auto_freeze(board) or freeze_status
            except Exception as exc:
                freeze_status = {"added": 0, "skipped": 0, "eligible": 0, "warning": str(exc)}

        maint = ns.get("run_v45_collector_maintenance")
        if callable(maint):
            try:
                maintenance = maint(board, board_status) or {}
            except Exception as exc:
                maintenance = {"warning": str(exc)}

        ns["save_model_fit"](
            "collector:deep_history",
            {"sample": len(board), "fit_type": "collector_autofeed_v52", "completed_at": ns["now_iso"]()},
        )

    if truthy("CS2_AUTO_GRADE", "true"):
        grader = ns.get("grade_pending_automatically")
        if callable(grader):
            try:
                grade_status = grader() or grade_status
            except Exception as exc:
                grade_status = {"graded": 0, "pending": 0, "errors": 1, "warning": str(exc)}

    db_status = ns.get("database_status", lambda: {})()
    model_health = ns["model_health_report"](board, {"Underdog": ud_meta, "PrizePicks": pp_meta})
    verified = sum((ns["safe_int"](row.get("profile_maps"), 0) or 0) > 0 for row in board)
    projected = sum(row.get("projection") is not None for row in board)

    summary = {
        "ok": bool(ud_rows),
        "autofeed_version": "5.2",
        "underdog_rows": len(ud_rows),
        "prizepicks_rows": len(pp_rows),
        "market_ticks_added": ticks,
        "line_history_updated": True,
        "unique_players": len({str(x.get('player') or '') for x in props}),
        "verified_profile_rows": verified,
        "projection_rows_saved": projected,
        "full_board_rows": len(board),
        "profile_recovery": profile_recovery,
        "auto_freeze": freeze_status,
        "auto_grade": grade_status,
        "database_status": db_status,
        "maintenance": maintenance,
        "demo_dropbox": demo_ingest,
        "underdog_status": ud_meta,
        "prizepicks_status": pp_meta,
        "board_status": board_status,
        "model_health": model_health,
    }
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0 if summary["ok"] else 2


def run_locked() -> int:
    """Prevent the web-embedded collector and Railway cron collector from overlapping."""
    import fcntl
    import time

    data_dir = Path(os.getenv("CS2_DATA_DIR", "/data/cs2_engine"))
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / ".autofeed_collector.lock"
    heartbeat_path = data_dir / ".autofeed_collector.heartbeat"
    fh = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": True, "skipped": True, "reason": "another autofeed collector is running"}))
            return 0

        try:
            age = time.time() - heartbeat_path.stat().st_mtime
        except Exception:
            age = 10**9
        if age < 480:
            print(json.dumps({"ok": True, "skipped": True, "reason": "recent autofeed cycle already completed", "heartbeat_age_seconds": round(age, 1)}))
            return 0

        heartbeat_path.touch()
        code = main()
        heartbeat_path.touch()
        return code
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


if __name__ == "__main__":
    raise SystemExit(run_locked())

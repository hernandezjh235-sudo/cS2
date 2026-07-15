"""Railway cron collector for OneWayPickz CS2 v4.5.

This short-lived process records real market ticks on every run. Set
CS2_COLLECT_PROJECTIONS=true to also build and save full as-of projections.
It intentionally exits after the collection finishes so Railway cron can run it again.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")
MARKER = "# ============================================================\n# SESSION BOARD LOAD"


def load_engine() -> dict:
    source = APP_PATH.read_text(encoding="utf-8")
    definitions = source.split(MARKER)[0]
    module_name = "onewaypickz_cs2_collector_runtime"
    module = types.ModuleType(module_name)
    module.__file__ = str(APP_PATH)
    sys.modules[module_name] = module
    exec(compile(definitions, str(APP_PATH), "exec"), module.__dict__)
    return module.__dict__


def truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    ns = load_engine()
    ud_rows, ud_meta = ns["fetch_underdog_cs2_board"]()
    pp_rows, pp_meta = ns["fetch_prizepicks_cs2_board"]()
    all_rows = ns["annotate_market_consensus"](list(ud_rows) + list(pp_rows))
    ticks = ns["sqlite_store_market_ticks"](all_rows)
    ns["update_line_history"](all_rows)
    demo_ingest = ns["auto_ingest_demo_dropbox"]()

    board = []
    board_status = {}
    collect_projections = truthy("CS2_COLLECT_PROJECTIONS", "false")
    collect_history = truthy("CS2_COLLECT_TEAM_HISTORY", "true")
    # Market ticks stay on the 10-minute cron. Deep history collection runs at
    # most once per hour so team maps/vetoes/rosters accumulate without
    # repeatedly hammering public pages.
    full_run = collect_projections
    if collect_history and ud_rows and not full_run:
        last = ns["load_model_fit"]("collector:deep_history")
        updated = ns["_parse_iso_datetime"](last.get("updated_at")) if last else None
        full_run = not updated or (ns["datetime"].now(ns["timezone"].utc) - updated).total_seconds() >= 3600
    if full_run and ud_rows:
        props = ns["annotate_market_consensus"](list(ud_rows) + list(pp_rows))
        props = [x for x in props if str(x.get("source")) == "Underdog"]
        board, board_status = ns["build_full_board"](props, truthy("CS2_DEEP_DATA", "true"))
        ns["save_model_fit"]("collector:deep_history", {"sample": len(board), "fit_type": "collector_history", "completed_at": ns["now_iso"]()})
        maintenance = ns.get("run_v45_collector_maintenance", lambda b,s: {})(board, board_status)
        board_status = {**board_status, "v45_collector_maintenance": maintenance}
        if collect_projections:
            ns["save_asof_projection_history"](board, {"Underdog": ud_meta, "PrizePicks": pp_meta, "collector": True})

    summary = {
        "ok": bool(ud_rows or pp_rows),
        "underdog_rows": len(ud_rows),
        "prizepicks_rows": len(pp_rows),
        "market_ticks_added": ticks,
        "projection_rows_saved": len(board),
        "collect_projections": collect_projections,
        "collect_team_history": collect_history,
        "deep_history_run": bool(board),
        "demo_dropbox": demo_ingest,
        "underdog_status": ud_meta,
        "prizepicks_status": pp_meta,
        "board_status": board_status,
        "model_health": ns["model_health_report"](board, {"Underdog": ud_meta, "PrizePicks": pp_meta}),
    }
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

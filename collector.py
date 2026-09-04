"""OneWayPickz CS2 v5.4 automatic data collector.

Runs independently from Streamlit, applies data-only overlays to an isolated app
copy, restores/uses the public GitHub provider cache, deepens the Railway volume,
freezes verified pregame projections, and grades completed rows.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SOURCE_APP_PATH = ROOT / "app.py"
RUNTIME_APP_PATH = Path(os.getenv("CS2_COLLECTOR_RUNTIME_APP", "/tmp/onewaypickz_cs2_collector_app.py"))
PATCH_PATHS = [
    ROOT / "autofeed_patch.py",
    ROOT / "autofeed_recovery_v53.py",
    ROOT / "autofeed_cache_v54.py",
]
MARKER = "# ============================================================\n# SESSION BOARD LOAD"


def truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def build_runtime_app() -> Path:
    RUNTIME_APP_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_APP_PATH, RUNTIME_APP_PATH)
    patch_status = []
    for idx, patch_path in enumerate(PATCH_PATHS):
        if not patch_path.exists():
            patch_status.append({"file": patch_path.name, "ok": False, "warning": "missing"})
            continue
        spec = importlib.util.spec_from_file_location(f"cs2_collector_patch_{idx}", patch_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {patch_path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        changed = bool(module.patch_app(RUNTIME_APP_PATH))
        patch_status.append({"file": patch_path.name, "ok": True, "changed": changed})
    compile(RUNTIME_APP_PATH.read_text(encoding="utf-8"), str(RUNTIME_APP_PATH), "exec")
    data_dir = Path(os.getenv("CS2_DATA_DIR", "/data/cs2_engine"))
    atomic_json(data_dir / ".collector_runtime_patch.json", {"ok": True, "patches": patch_status, "built_at": time.time()})
    return RUNTIME_APP_PATH


def load_engine() -> dict:
    runtime_path = build_runtime_app()
    source = runtime_path.read_text(encoding="utf-8")
    definitions = source.split(MARKER)[0]
    module_name = "onewaypickz_cs2_collector_runtime"
    module = types.ModuleType(module_name)
    module.__file__ = str(runtime_path)
    sys.modules[module_name] = module
    exec(compile(definitions, str(runtime_path), "exec"), module.__dict__)
    return module.__dict__


def _bridge_match_from_row(ns: dict, row: dict) -> dict | None:
    team = str(row.get("team") or "").strip()
    opponent = str(row.get("opponent") or "").strip()
    if not team or not opponent:
        return None
    mid = str(((row.get("identity_ids") or {}).get("match_id")) or row.get("match_id") or "").strip()
    if not mid:
        raw = f"{ns['normalize_team'](team)}|{ns['normalize_team'](opponent)}|{str(row.get('start_time') or '')[:16]}"
        mid = "ud-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]
    maps = [str(x).strip() for x in (row.get("confirmed_maps") or []) if str(x).strip()]
    if not maps:
        likely = [str(x).strip() for x in (row.get("likely_maps") or []) if str(x).strip() and "unconfirmed" not in str(x).lower()]
        if row.get("veto_state") in {"CONFIRMED", "FINAL", "LOCKED"}:
            maps = likely[:3]
    return {
        "match_id": mid,
        "provider_match_id": mid,
        "match_url": row.get("match_url"),
        "start_time": row.get("start_time"),
        "format": row.get("match_format") or "BO3",
        "event": row.get("event") or row.get("matchup") or "Underdog CS2",
        "stage": row.get("stage") or "",
        "event_tier": row.get("event_tier") or "LOW/UNKNOWN",
        "environment": row.get("environment") or "UNKNOWN",
        "confirmed_maps": maps,
        "veto_actions": list(row.get("veto_actions") or []),
        "teams": [{"name": team}, {"name": opponent}],
        "lineup_names": list(row.get("confirmed_lineup_names") or []),
        "lineup_groups": list(row.get("lineup_groups") or []),
        "lineup_source": row.get("lineup_source") or "",
        "provider": "OneWayPickz v5.4 collector",
        "updated_at": ns["now_iso"](),
    }


def export_provider_bridge(ns: dict, board: list[dict], previous: dict | None = None) -> dict:
    runtime = ns.get("V48_RUNTIME") if isinstance(ns.get("V48_RUNTIME"), dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    profiles = dict(previous.get("profiles") or {})
    profiles.update(dict(runtime.get("profiles") or {}))
    teams = dict(previous.get("teams") or {})
    teams.update(dict(runtime.get("teams") or {}))
    if callable(ns.get("_v49_build_team_index")):
        try:
            teams.update(ns["_v49_build_team_index"](profiles) or {})
        except Exception:
            pass

    by_match: dict[str, dict] = {}
    for rec in list(previous.get("matches") or []) + list(runtime.get("matches") or []):
        if not isinstance(rec, dict):
            continue
        key = str(rec.get("match_id") or rec.get("provider_match_id") or rec.get("match_url") or "").strip()
        if key:
            by_match[key] = rec
    for row in board:
        rec = _bridge_match_from_row(ns, row)
        if rec:
            by_match[str(rec["match_id"])] = rec

    bridge = {
        "schema_version": 7,
        "generated_at": ns["now_iso"](),
        "profiles": profiles,
        "teams": teams,
        "matches": list(by_match.values())[-2000:],
        "source_status": {
            "autofeed_version": "5.4",
            "verified_profile_count": len(profiles),
            "team_count": len(teams),
            "match_count": len(by_match),
        },
    }
    path = Path(str(ns.get("V48_BRIDGE_LOCAL_FILE") or (Path(ns["STORAGE_DIR"]) / "cs2_provider_cache.json")))
    ns["save_json"](str(path), bridge, force=True)
    seed = ns.get("_v54_seed_databases_from_bridge")
    if callable(seed):
        try:
            bridge["source_status"]["database_seed"] = seed(bridge)
        except Exception as exc:
            bridge["source_status"]["database_seed_warning"] = str(exc)
    ns["save_json"](str(path), bridge, force=True)
    return bridge


def main() -> int:
    os.environ.setdefault("CS2_DATA_DIR", "/data/cs2_engine")
    os.environ.setdefault("CS2_ASSISTED_OFFICIAL", "false")
    os.environ.setdefault("CS2_AUTO_HARVEST_HISTORY", "true")
    os.environ.setdefault("CS2_COLLECT_PROJECTIONS", "true")
    os.environ.setdefault("CS2_AUTO_GRADE", "true")
    os.environ.setdefault("CS2_DEEP_DATA", "true")
    os.environ.setdefault("CS2_BO3_PROFILES_PER_REFRESH", "180")
    os.environ.setdefault("CS2_AUTOFEED_DIRECT_PROFILE_BATCH", "60")
    os.environ.setdefault("CS2_AUTOFEED_DIRECT_WORKERS", "4")
    os.environ.setdefault("CS2_BRIDGE_REPO", "hernandezjh235-sudo/cS2")
    os.environ.setdefault("CS2_BRIDGE_BRANCH", "data-cache")

    ns = load_engine()

    bridge_before, bridge_status = ({}, {})
    loader = ns.get("load_provider_bridge")
    if callable(loader):
        try:
            bridge_before, bridge_status = loader(force=False)
        except Exception as exc:
            bridge_status = {"ok": False, "warning": str(exc)}

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

    props = [x for x in all_rows if str(x.get("source")) == "Underdog"]
    board: list[dict] = []
    board_status: dict = {}
    profile_recovery: dict = {}
    direct_profile_recovery: dict = {}
    freeze_status: dict = {"added": 0, "skipped": 0, "eligible": 0}
    grade_status: dict = {"graded": 0, "pending": 0, "errors": 0}
    maintenance: dict = {}

    if props:
        players = list(dict.fromkeys(str(x.get("player") or "").strip() for x in props if str(x.get("player") or "").strip()))
        prefetch = ns.get("v48_prefetch_provider_data")
        if callable(prefetch):
            try:
                profile_recovery = prefetch(players, force=False) or {}
            except Exception as exc:
                profile_recovery = {"ok": False, "warning": str(exc), "requested": len(players)}

        direct = ns.get("_autofeed_direct_profile_recovery")
        if callable(direct):
            try:
                direct_profile_recovery = direct(players) or {}
            except Exception as exc:
                direct_profile_recovery = {"ok": False, "warning": str(exc), "requested": len(players)}

        board, board_status = ns["build_full_board"](props, truthy("CS2_DEEP_DATA", "true"))
        board_status = dict(board_status or {})
        board_status["github_bridge_bootstrap"] = bridge_status
        board_status["autofeed_direct_profile_recovery_v53"] = direct_profile_recovery

        persist_entities = ns.get("save_projection_entities")
        for row in board:
            if (ns["safe_int"](row.get("profile_maps"), 0) or 0) > 0 and row.get("projection") is not None:
                if callable(persist_entities):
                    try:
                        persist_entities(row)
                    except Exception:
                        pass
        ns["save_asof_projection_history"](
            board,
            {"Underdog": ud_meta, "PrizePicks": pp_meta, "collector": True, "autofeed": "v5.4-github-cache"},
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

    bridge_after = export_provider_bridge(ns, board, bridge_before)

    if truthy("CS2_AUTO_GRADE", "true"):
        grader = ns.get("grade_pending_automatically")
        if callable(grader):
            try:
                grade_status = grader() or grade_status
            except Exception as exc:
                grade_status = {"graded": 0, "pending": 0, "errors": 1, "warning": str(exc)}
        try:
            ns["build_learning_profiles"]()
        except Exception:
            pass
        try:
            ns["save_calibration_state"]()
        except Exception:
            pass

    db_status = ns.get("database_status", lambda: {})()
    model_health = ns["model_health_report"](board, {"Underdog": ud_meta, "PrizePicks": pp_meta})
    verified = sum((ns["safe_int"](row.get("profile_maps"), 0) or 0) > 0 for row in board)
    projected = sum(row.get("projection") is not None for row in board)
    verified_team_rows = sum(bool(row.get("provider_team_verified")) for row in board)

    summary = {
        "ok": bool(ud_rows),
        "autofeed_version": "5.4-github-cache-full-gas",
        "underdog_rows": len(ud_rows),
        "prizepicks_rows": len(pp_rows),
        "market_ticks_added": ticks,
        "unique_players": len({str(x.get("player") or "") for x in props}),
        "verified_profile_rows": verified,
        "verified_team_rows": verified_team_rows,
        "projection_rows_saved": projected,
        "full_board_rows": len(board),
        "profile_recovery": profile_recovery,
        "direct_profile_recovery": direct_profile_recovery,
        "github_bridge_before": bridge_status,
        "github_bridge_profiles_after": len(bridge_after.get("profiles") or {}),
        "github_bridge_teams_after": len(bridge_after.get("teams") or {}),
        "github_bridge_matches_after": len(bridge_after.get("matches") or []),
        "auto_freeze": freeze_status,
        "auto_grade": grade_status,
        "database_status": db_status,
        "maintenance": maintenance,
        "demo_dropbox": demo_ingest,
        "underdog_status": ud_meta,
        "board_status": board_status,
        "model_health": model_health,
        "completed_at": ns["now_iso"](),
    }
    data_dir = Path(os.environ["CS2_DATA_DIR"])
    atomic_json(data_dir / ".autofeed_collector_status.json", summary)
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0 if summary["ok"] else 2


def run_locked() -> int:
    import fcntl

    data_dir = Path(os.getenv("CS2_DATA_DIR", "/data/cs2_engine"))
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / ".autofeed_collector.lock"
    heartbeat_path = data_dir / ".autofeed_collector.heartbeat"
    status_path = data_dir / ".autofeed_collector_status.json"
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
            print(json.dumps({"ok": True, "skipped": True, "reason": "recent successful autofeed cycle", "heartbeat_age_seconds": round(age, 1)}))
            return 0

        atomic_json(data_dir / ".autofeed_collector_running.json", {"started_at": time.time(), "pid": os.getpid()})
        try:
            code = main()
        except Exception as exc:
            atomic_json(status_path, {"ok": False, "autofeed_version": "5.4", "error": f"{type(exc).__name__}: {exc}", "failed_at": time.time()})
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
            return 2
        if code == 0:
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

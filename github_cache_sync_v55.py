"""OneWayPickz CS2 v5.7 GitHub/Railway cache sync.

Adds source-recovery caches, timestamp-aware entity merging, non-destructive
SQLite merging, and standalone operational/readiness/context files so newer
GitHub and Railway knowledge converge without resets.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import github_cache_sync as base

EXTRA_CACHE_FILES = [
    "cs2_hltv_paginated_v51.json", "cs2_hltv_batch_mirror.json", "cs2_bo3_player_index_v50.json",
    "cs2_profile_recovery_cursor.json", "cs2_data_readiness.json", "cs2_grading_health.json",
    "cs2_operational_status.json", "cs2_context_health.json",
]
for _name in EXTRA_CACHE_FILES:
    if _name not in base.CACHE_FILES:
        base.CACHE_FILES.append(_name)

ENTITY_FILES = {
    "player_database.json", "team_database.json", "match_database.json", "map_database.json",
    "roster_database.json", "veto_database.json", "player_aliases.json",
    "cs2_player_map_profiles.json", "cs2_deep_team_profiles.json", "cs2_role_timeline.json",
}
LATEST_WINS_FILES = {
    "cs2_data_readiness.json", "cs2_grading_health.json", "cs2_operational_status.json",
    "cs2_context_health.json", "database_meta.json",
}
STANDALONE_FILES = {
    "cs2_provider_cache.json", "cs2_data_readiness.json", "cs2_grading_health.json",
    "cs2_operational_status.json", "cs2_context_health.json", "database_meta.json",
}


def _record_time(row: Any):
    if not isinstance(row, dict): return None
    for key in ("updated_at", "saved_at", "generated_at", "identity_verified_at", "timestamp", "observed_at"):
        dt = base._parse_dt(row.get(key))
        if dt: return dt
    return None


def merge_entity_json(remote_path: Path, local_path: Path) -> str:
    remote = base._json_load(remote_path)
    local = base._json_load(local_path) if local_path.exists() else None
    if remote is None: return "remote-invalid"
    if local is None:
        base.atomic_json(local_path, remote); return "restored"
    if not isinstance(remote, dict) or not isinstance(local, dict):
        base.atomic_json(local_path, base.merge_values(remote, local)); return "merged-generic"
    out = dict(local)
    newer_remote = 0
    for key, rval in remote.items():
        if key not in out:
            out[key] = rval; newer_remote += 1; continue
        lval = out[key]
        rdt, ldt = _record_time(rval), _record_time(lval)
        if rdt and (not ldt or rdt > ldt):
            out[key] = rval; newer_remote += 1
        elif not rdt and isinstance(rval, dict) and isinstance(lval, dict):
            merged = dict(rval); merged.update(lval); out[key] = merged
    base.atomic_json(local_path, out)
    return f"entity-merged remote-newer={newer_remote}"


def merge_latest_json(remote_path: Path, local_path: Path) -> str:
    remote = base._json_load(remote_path); local = base._json_load(local_path) if local_path.exists() else None
    if remote is None: return "remote-invalid"
    if local is None:
        base.atomic_json(local_path, remote); return "restored"
    rdt, ldt = _record_time(remote), _record_time(local)
    chosen = remote if rdt and (not ldt or rdt >= ldt) else local
    base.atomic_json(local_path, chosen)
    return "remote-newer" if chosen is remote else "local-newer"


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    try:
        q = table.replace("'", "''")
        return [str(r[1]) for r in conn.execute(f"PRAGMA {schema}.table_info('{q}')").fetchall()]
    except Exception:
        return []


def merge_sqlite(remote_path: Path, local_path: Path) -> dict[str, Any]:
    if not base.sqlite_healthy(remote_path): return {"ok": False, "warning": "remote SQLite failed integrity check"}
    if not base.sqlite_healthy(local_path):
        local_path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(remote_path, local_path)
        return {"ok": base.sqlite_healthy(local_path), "action": "restored-remote"}
    merged = {}; conn = sqlite3.connect(str(local_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000"); conn.execute("ATTACH DATABASE ? AS remote", (str(remote_path),))
        local_tables = {str(r[0]) for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall()}
        tables = [str(r[0]) for r in conn.execute("SELECT name FROM remote.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        for table in tables:
            if table not in local_tables: continue
            cols = [c for c in _table_columns(conn,"remote",table) if c in _table_columns(conn,"main",table)]
            if not cols: continue
            before=conn.total_changes; quoted=",".join('"'+c.replace('"','""')+'"' for c in cols); tq='"'+table.replace('"','""')+'"'
            conn.execute(f"INSERT OR IGNORE INTO main.{tq} ({quoted}) SELECT {quoted} FROM remote.{tq}")
            merged[table]=conn.total_changes-before
        conn.commit(); conn.execute("DETACH DATABASE remote")
    finally: conn.close()
    return {"ok": base.sqlite_healthy(local_path), "action":"merged", "inserted":merged}


def pull_cache(data_dir: Path, repo: str, branch: str) -> dict[str, Any]:
    status = dict(base.pull_cache(data_dir, repo, branch, force_sqlite=False) or {})
    try:
        manifest = json.loads(base.fetch_bytes(base.raw_url(repo,branch,base.MANIFEST_NAME),20).decode("utf-8"))
        archive = base.fetch_bytes(base.raw_url(repo,branch,str(manifest.get("archive") or base.ARCHIVE_NAME)),60)
        with tempfile.TemporaryDirectory(prefix="cs2_v57_merge_") as td:
            root=Path(td)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                for info in zf.infolist():
                    name=Path(info.filename).name
                    if info.is_dir() or name not in base.CACHE_FILES: continue
                    (root/name).write_bytes(zf.read(info))
            entity_actions={}
            for name in ENTITY_FILES:
                rp=root/name
                if rp.exists(): entity_actions[name]=merge_entity_json(rp,data_dir/name)
            for name in LATEST_WINS_FILES:
                rp=root/name
                if rp.exists(): entity_actions[name]=merge_latest_json(rp,data_dir/name)
            status["entity_merge_v57"] = entity_actions
            remote_sqlite=root/"cs2_core_v42.sqlite3"
            if remote_sqlite.exists(): status["sqlite_merge_v57"]=merge_sqlite(remote_sqlite,data_dir/"cs2_core_v42.sqlite3")
        status["cache_version"]="5.7"
    except Exception as exc:
        status["v57_merge_warning"]=f"{type(exc).__name__}: {exc}"
    base.atomic_json(data_dir/base.SYNC_STATUS_NAME,status); print(json.dumps(status,ensure_ascii=False,default=str)); return status


def package_cache(data_dir: Path, output_dir: Path) -> dict[str, Any]:
    manifest=dict(base.package_cache(data_dir,output_dir) or {})
    manifest["repo_cache_version"]="5.7"
    base.atomic_json(output_dir/base.MANIFEST_NAME,manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in STANDALONE_FILES:
        src=data_dir/name
        if src.exists() and src.is_file():
            shutil.copy2(src, output_dir/name)
    return manifest


def main() -> int:
    p=argparse.ArgumentParser(description="OneWayPickz CS2 v5.7 cache sync"); sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("pull"); a.add_argument("--data-dir",default=base.DEFAULT_DATA_DIR); a.add_argument("--repo",default=base.DEFAULT_REPO); a.add_argument("--branch",default=base.DEFAULT_BRANCH)
    b=sub.add_parser("package"); b.add_argument("--data-dir",default=base.DEFAULT_DATA_DIR); b.add_argument("--output-dir",required=True)
    args=p.parse_args()
    if args.command=="pull": return 0 if pull_cache(Path(args.data_dir),args.repo,args.branch).get("ok") else 2
    if args.command=="package": package_cache(Path(args.data_dir),Path(args.output_dir)); return 0
    return 2
if __name__=="__main__": raise SystemExit(main())

"""v5.5 GitHub/Railway CS2 cache sync.

Extends v5.4 with source-recovery caches and non-destructive SQLite merging so
GitHub-collected grading/projection/audit rows reach a healthy Railway database.
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
    "cs2_hltv_paginated_v51.json",
    "cs2_hltv_batch_mirror.json",
    "cs2_bo3_player_index_v50.json",
    "cs2_profile_recovery_cursor.json",
    "cs2_data_readiness.json",
    "cs2_grading_health.json",
]
for _name in EXTRA_CACHE_FILES:
    if _name not in base.CACHE_FILES:
        base.CACHE_FILES.append(_name)


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info('{table.replace(chr(39), chr(39)*2)}')").fetchall()
        return [str(r[1]) for r in rows]
    except Exception:
        return []


def merge_sqlite(remote_path: Path, local_path: Path) -> dict[str, Any]:
    if not base.sqlite_healthy(remote_path):
        return {"ok": False, "warning": "remote SQLite failed integrity check"}
    if not base.sqlite_healthy(local_path):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote_path, local_path)
        return {"ok": base.sqlite_healthy(local_path), "action": "restored-remote"}

    merged: dict[str, int] = {}
    conn = sqlite3.connect(str(local_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("ATTACH DATABASE ? AS remote", (str(remote_path),))
        local_tables = {str(r[0]) for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall()}
        tables = [str(r[0]) for r in conn.execute(
            "SELECT name FROM remote.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        for table in tables:
            if table not in local_tables:
                continue
            rcols = _table_columns(conn, "remote", table)
            lcols = _table_columns(conn, "main", table)
            cols = [c for c in rcols if c in lcols]
            if not cols:
                continue
            before = conn.total_changes
            quoted = ",".join('"' + c.replace('"', '""') + '"' for c in cols)
            tq = '"' + table.replace('"', '""') + '"'
            conn.execute(f"INSERT OR IGNORE INTO main.{tq} ({quoted}) SELECT {quoted} FROM remote.{tq}")
            merged[table] = conn.total_changes - before
        conn.commit()
        conn.execute("DETACH DATABASE remote")
    finally:
        conn.close()
    return {"ok": base.sqlite_healthy(local_path), "action": "merged", "inserted": merged}


def pull_cache(data_dir: Path, repo: str, branch: str) -> dict[str, Any]:
    status = dict(base.pull_cache(data_dir, repo, branch, force_sqlite=False) or {})
    try:
        manifest_bytes = base.fetch_bytes(base.raw_url(repo, branch, base.MANIFEST_NAME), timeout=20)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        archive = base.fetch_bytes(base.raw_url(repo, branch, str(manifest.get("archive") or base.ARCHIVE_NAME)), timeout=60)
        with tempfile.TemporaryDirectory(prefix="cs2_v55_sqlite_") as td:
            root = Path(td)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                member = next((x for x in zf.infolist() if Path(x.filename).name == "cs2_core_v42.sqlite3"), None)
                if member:
                    remote = root / "cs2_core_v42.sqlite3"
                    remote.write_bytes(zf.read(member))
                    status["sqlite_merge_v55"] = merge_sqlite(remote, data_dir / "cs2_core_v42.sqlite3")
        status["cache_version"] = "5.5"
    except Exception as exc:
        status["sqlite_merge_v55"] = {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}
    base.atomic_json(data_dir / base.SYNC_STATUS_NAME, status)
    print(json.dumps(status, ensure_ascii=False, default=str))
    return status


def package_cache(data_dir: Path, output_dir: Path) -> dict[str, Any]:
    manifest = dict(base.package_cache(data_dir, output_dir) or {})
    manifest["repo_cache_version"] = "5.5"
    base.atomic_json(output_dir / base.MANIFEST_NAME, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="OneWayPickz CS2 v5.5 cache sync")
    sub = parser.add_subparsers(dest="command", required=True)
    pull = sub.add_parser("pull")
    pull.add_argument("--data-dir", default=base.DEFAULT_DATA_DIR)
    pull.add_argument("--repo", default=base.DEFAULT_REPO)
    pull.add_argument("--branch", default=base.DEFAULT_BRANCH)
    package = sub.add_parser("package")
    package.add_argument("--data-dir", default=base.DEFAULT_DATA_DIR)
    package.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    if args.command == "pull":
        result = pull_cache(Path(args.data_dir), args.repo, args.branch)
        return 0 if result.get("ok") else 2
    if args.command == "package":
        package_cache(Path(args.data_dir), Path(args.output_dir))
        return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())

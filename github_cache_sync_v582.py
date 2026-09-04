"""OneWayPickz CS2 v5.8.2 cache sync.

Persists the complete live Underdog CS2 line catalog in addition to the v5.7
verified entity/context cache so Railway restarts do not lose current line
visibility while the background collector refills profiles.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import github_cache_sync_v55 as v57

LIVE_FILE = "cs2_live_line_catalog.json"
if LIVE_FILE not in v57.base.CACHE_FILES:
    v57.base.CACHE_FILES.append(LIVE_FILE)


def pull_cache(data_dir: Path, repo: str, branch: str):
    status = dict(v57.pull_cache(data_dir, repo, branch) or {})
    status["cache_version"] = "5.8.2"
    return status


def package_cache(data_dir: Path, output_dir: Path):
    manifest = dict(v57.package_cache(data_dir, output_dir) or {})
    manifest["repo_cache_version"] = "5.8.2"
    v57.base.atomic_json(output_dir / v57.base.MANIFEST_NAME, manifest)
    src = data_dir / LIVE_FILE
    if src.exists() and src.is_file():
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, output_dir / LIVE_FILE)
    return manifest


def main() -> int:
    p = argparse.ArgumentParser(description="OneWayPickz CS2 v5.8.2 cache sync")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("pull")
    a.add_argument("--data-dir", default=v57.base.DEFAULT_DATA_DIR)
    a.add_argument("--repo", default=v57.base.DEFAULT_REPO)
    a.add_argument("--branch", default=v57.base.DEFAULT_BRANCH)
    b = sub.add_parser("package")
    b.add_argument("--data-dir", default=v57.base.DEFAULT_DATA_DIR)
    b.add_argument("--output-dir", required=True)
    args = p.parse_args()
    if args.command == "pull":
        out = pull_cache(Path(args.data_dir), args.repo, args.branch)
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0 if out.get("ok") else 2
    if args.command == "package":
        out = package_cache(Path(args.data_dir), Path(args.output_dir))
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

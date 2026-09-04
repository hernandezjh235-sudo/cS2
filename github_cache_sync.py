from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

DEFAULT_REPO = os.getenv("CS2_BRIDGE_REPO", "hernandezjh235-sudo/cS2").strip() or "hernandezjh235-sudo/cS2"
DEFAULT_BRANCH = os.getenv("CS2_BRIDGE_BRANCH", "data-cache").strip() or "data-cache"
DEFAULT_DATA_DIR = os.getenv("CS2_DATA_DIR", "/data/cs2_engine")
ARCHIVE_NAME = "cs2_data_cache.zip"
MANIFEST_NAME = "cs2_data_manifest.json"
PROVIDER_CACHE_NAME = "cs2_provider_cache.json"
SYNC_STATUS_NAME = ".github_cache_sync.json"

CACHE_FILES = [
    PROVIDER_CACHE_NAME,
    "player_database.json",
    "team_database.json",
    "match_database.json",
    "map_database.json",
    "roster_database.json",
    "veto_database.json",
    "player_aliases.json",
    "database_meta.json",
    "cs2_official_snapshots.json",
    "cs2_graded_results.json",
    "cs2_learning.json",
    "cs2_line_history.json",
    "cs2_manual_odds.json",
    "cs2_role_overrides.json",
    "cs2_player_profile_overrides.json",
    "cs2_source_cache.json",
    "cs2_match_aliases.json",
    "cs2_deep_team_profiles.json",
    "cs2_player_map_profiles.json",
    "cs2_veto_history.json",
    "cs2_roster_history.json",
    "cs2_market_consensus.json",
    "cs2_probability_calibration.json",
    "cs2_patch_map_pool_eras.json",
    "cs2_role_timeline.json",
    "cs2_demo_telemetry.json",
    "cs2_book_odds_history.json",
    "cs2_slip_history.json",
    "cs2_live_watch_history.json",
    "cs2_asof_projection_history.jsonl",
    "cs2_core_v42.sqlite3",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def merge_values(remote: Any, local: Any) -> Any:
    if isinstance(remote, dict) and isinstance(local, dict):
        out = dict(remote)
        for key, lval in local.items():
            if key in out:
                out[key] = merge_values(out[key], lval)
            else:
                out[key] = lval
        return out
    if isinstance(remote, list) and isinstance(local, list):
        seen: set[str] = set()
        out = []
        for item in remote + local:
            try:
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
            except Exception:
                marker = repr(item)
            digest = hashlib.sha1(marker.encode("utf-8", "ignore")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            out.append(item)
        return out[-50000:]
    if _meaningful(local):
        return local
    return remote


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def merge_json_file(remote_path: Path, local_path: Path) -> str:
    remote = _json_load(remote_path)
    if remote is None:
        return "remote-invalid"
    local = _json_load(local_path) if local_path.exists() else None
    if local is None:
        atomic_json(local_path, remote)
        return "restored"

    if local_path.name == PROVIDER_CACHE_NAME and isinstance(remote, dict) and isinstance(local, dict):
        rdt = _parse_dt(remote.get("generated_at") or remote.get("updated_at"))
        ldt = _parse_dt(local.get("generated_at") or local.get("updated_at"))
        chosen = remote if (rdt and (not ldt or rdt >= ldt)) else local
        atomic_json(local_path, chosen)
        return "remote-newer" if chosen is remote else "local-newer"

    merged = merge_values(remote, local)
    atomic_json(local_path, merged)
    return "merged"


def merge_jsonl_file(remote_path: Path, local_path: Path) -> str:
    lines = []
    seen: set[str] = set()
    for path in [remote_path, local_path]:
        if not path.exists():
            continue
        try:
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line:
                    continue
                digest = hashlib.sha1(line.encode("utf-8", "ignore")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                lines.append(line)
        except Exception:
            continue
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = local_path.with_suffix(local_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines[-100000:]) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, local_path)
    return "merged"


def sqlite_healthy(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 65536:
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return bool(row and str(row[0]).lower() == "ok")
    except Exception:
        return False


def raw_url(repo: str, branch: str, filename: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{filename}"


def fetch_bytes(url: str, timeout: int = 35) -> bytes:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "OneWayPickz-CS2-GitHub-Cache-v5.4"})
    response.raise_for_status()
    return response.content


def pull_cache(data_dir: Path, repo: str, branch: str, force_sqlite: bool = False) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "ok": False,
        "action": "pull",
        "repo": repo,
        "branch": branch,
        "pulled_at": now_iso(),
        "files": {},
    }
    try:
        manifest_bytes = fetch_bytes(raw_url(repo, branch, MANIFEST_NAME), timeout=20)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        archive_name = str(manifest.get("archive") or ARCHIVE_NAME)
        archive = fetch_bytes(raw_url(repo, branch, archive_name), timeout=60)
        expected = str(manifest.get("archive_sha256") or "")
        actual = sha256_bytes(archive)
        if expected and actual != expected:
            raise RuntimeError(f"cache SHA256 mismatch: expected {expected}, got {actual}")

        with tempfile.TemporaryDirectory(prefix="cs2_cache_pull_") as td:
            root = Path(td)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                for info in zf.infolist():
                    name = Path(info.filename).name
                    if name not in CACHE_FILES or info.is_dir():
                        continue
                    (root / name).write_bytes(zf.read(info))

            for name in CACHE_FILES:
                remote_path = root / name
                if not remote_path.exists():
                    continue
                local_path = data_dir / name
                try:
                    if name.endswith(".json"):
                        action = merge_json_file(remote_path, local_path)
                    elif name.endswith(".jsonl"):
                        action = merge_jsonl_file(remote_path, local_path)
                    elif name.endswith(".sqlite3"):
                        if force_sqlite or not sqlite_healthy(local_path):
                            local_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(remote_path, local_path)
                            action = "restored" if sqlite_healthy(local_path) else "restored-unverified"
                        else:
                            action = "kept-local-healthy"
                    else:
                        action = "skipped"
                    status["files"][name] = action
                except Exception as exc:
                    status["files"][name] = f"error: {exc}"

        status.update({
            "ok": True,
            "cache_generated_at": manifest.get("generated_at"),
            "archive_sha256": actual,
            "manifest_files": len(manifest.get("files") or []),
        })
    except Exception as exc:
        status["warning"] = f"{type(exc).__name__}: {exc}"

    atomic_json(data_dir / SYNC_STATUS_NAME, status)
    print(json.dumps(status, ensure_ascii=False, default=str))
    return status


def package_cache(data_dir: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / ARCHIVE_NAME
    entries = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in CACHE_FILES:
            path = data_dir / name
            if not path.exists() or not path.is_file():
                continue
            try:
                zf.write(path, arcname=name)
                entries.append({"name": name, "size": path.stat().st_size, "sha256": sha256_file(path)})
            except Exception:
                continue

    provider = data_dir / PROVIDER_CACHE_NAME
    if provider.exists():
        shutil.copy2(provider, output_dir / PROVIDER_CACHE_NAME)

    manifest = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "repo_cache_version": "5.4",
        "archive": ARCHIVE_NAME,
        "archive_sha256": sha256_file(archive_path),
        "archive_size": archive_path.stat().st_size,
        "files": entries,
    }
    atomic_json(output_dir / MANIFEST_NAME, manifest)
    print(json.dumps(manifest, ensure_ascii=False, default=str))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync OneWayPickz CS2 persistent data with the GitHub data-cache branch")
    sub = parser.add_subparsers(dest="command", required=True)

    pull = sub.add_parser("pull")
    pull.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    pull.add_argument("--repo", default=DEFAULT_REPO)
    pull.add_argument("--branch", default=DEFAULT_BRANCH)
    pull.add_argument("--force-sqlite", action="store_true")

    package = sub.add_parser("package")
    package.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    package.add_argument("--output-dir", required=True)

    args = parser.parse_args()
    if args.command == "pull":
        pull_cache(Path(args.data_dir), args.repo, args.branch, bool(args.force_sqlite))
        return 0
    if args.command == "package":
        package_cache(Path(args.data_dir), Path(args.output_dir))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

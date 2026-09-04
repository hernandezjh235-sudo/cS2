from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_APP = ROOT / "app.py"
TARGET_APP = Path(os.getenv("CS2_WEB_RUNTIME_APP", "/tmp/onewaypickz_cs2_web_app.py"))
PATCHES = [
    ROOT / "autofeed_patch.py",
    ROOT / "autofeed_recovery_v53.py",
    ROOT / "autofeed_cache_v54.py",
    ROOT / "autofeed_readiness_v55.py",
    ROOT / "autofeed_identity_v551.py",
    ROOT / "autofeed_production_v56.py",
    ROOT / "autofeed_identity_v562.py",
    ROOT / "autofeed_context_v57.py",
    ROOT / "autofeed_liveboard_v58.py",
    ROOT / "autofeed_webfast_v581.py",
]


def _write_status(payload: dict) -> None:
    data_dir = Path(os.getenv("CS2_DATA_DIR", "/data/cs2_engine"))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = data_dir / ".web_runtime_status.json.tmp"
        out = data_dir / ".web_runtime_status.json"
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, out)
    except Exception:
        pass


def _load_patch(path: Path, idx: int):
    spec = importlib.util.spec_from_file_location(f"cs2_web_patch_{idx}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load patch module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "patch_app"):
        raise RuntimeError(f"patch_app missing: {path.name}")
    return module


def main() -> int:
    status = {"ok": False, "source": str(SOURCE_APP), "target": str(TARGET_APP), "patches": []}
    try:
        if not SOURCE_APP.exists():
            raise FileNotFoundError(SOURCE_APP)
        TARGET_APP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_APP, TARGET_APP)
        for idx, patch_path in enumerate(PATCHES):
            if not patch_path.exists():
                status["patches"].append({"file": patch_path.name, "ok": False, "warning": "missing"})
                continue
            module = _load_patch(patch_path, idx)
            changed = bool(module.patch_app(TARGET_APP))
            status["patches"].append({"file": patch_path.name, "ok": True, "changed": changed})
        compile(TARGET_APP.read_text(encoding="utf-8"), str(TARGET_APP), "exec")
        status["ok"] = True
        status["runtime_app"] = str(TARGET_APP)
        status["runtime_version"] = "5.8"
        status["web_latency_layer"] = "5.8.1"
        _write_status(status)
        print(str(TARGET_APP))
        return 0
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        try:
            compile(SOURCE_APP.read_text(encoding="utf-8"), str(SOURCE_APP), "exec")
            status["fallback"] = str(SOURCE_APP)
            _write_status(status)
            print(str(SOURCE_APP))
            return 0
        except Exception as base_exc:
            status["base_error"] = f"{type(base_exc).__name__}: {base_exc}"
            _write_status(status)
            print(json.dumps(status), file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())

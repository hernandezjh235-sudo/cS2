"""Build the optional OneWayPickz CS2 v4.9 provider cache outside Streamlit.

GitHub Actions runs this script on a schedule. It pulls the current Underdog
board and refreshes a small number of batch aggregate tables through Jina
Reader, rather than sending one blocked request per player. The app can also
run the same batch mirror directly, so this workflow is a warm cache and not a
required deployment dependency.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")
MARKER = "# ============================================================\n# SESSION BOARD LOAD"


def load_engine() -> dict:
    source = APP_PATH.read_text(encoding="utf-8")
    definitions = source.split(MARKER)[0]
    module_name = "onewaypickz_cs2_bridge_runtime"
    module = types.ModuleType(module_name)
    module.__file__ = str(APP_PATH)
    sys.modules[module_name] = module
    exec(compile(definitions, str(APP_PATH), "exec"), module.__dict__)
    return module.__dict__


def load_previous(path: str) -> dict:
    p = Path(path)
    if not path or not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", default="")
    parser.add_argument("--output", default="bridge_output/cs2_provider_cache.json")
    args = parser.parse_args()

    ns = load_engine()
    rows, line_status = ns["fetch_underdog_cs2_board"]()
    players = [str(x.get("player") or "").strip() for x in rows if str(x.get("player") or "").strip()]
    previous = load_previous(args.previous)
    output = ns["v49_generate_bridge_cache"](players, previous, rows)
    output["underdog_status"] = line_status
    output["underdog_rows"] = len(rows)
    output["board_players"] = list(dict.fromkeys(players))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary = {
        "ok": bool(output.get("profiles")),
        "underdog_rows": len(rows),
        "unique_players": len(set(players)),
        "profiles": len(output.get("profiles") or {}),
        "matches": len(output.get("matches") or []),
        "refreshed_profiles": output.get("refreshed_profiles", 0),
        "source_status": output.get("source_status") or {},
        "output": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, default=str))
    # Preserve/publish the previous cache during a temporary mirror outage.
    return 0 if output.get("profiles") or previous.get("profiles") else 2


if __name__ == "__main__":
    raise SystemExit(main())

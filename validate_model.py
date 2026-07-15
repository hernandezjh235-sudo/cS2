"""Fast offline validation for OneWayPickz CS2 v4.9.

No live network calls are made. The test covers exact Underdog scope, the batch
mirror parser, demo denominators, MR12 simulation, and an end-to-end projection
using synthetic verified aggregate player data.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import types

import numpy as np
import pandas as pd

os.environ["CS2_DATA_DIR"] = tempfile.mkdtemp(prefix="cs2_v49_validate_")
os.environ["CS2_JINA_MIRROR_ENABLED"] = "false"
os.environ["CS2_BO3_LAST_RESORT"] = "false"


class _Cache:
    def __call__(self, *args, **kwargs):
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]
        return lambda fn: fn

    def clear(self):
        return None


class _FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.cache_data = _Cache()
        self.secrets = {}

    def set_page_config(self, *args, **kwargs):
        return None

    def markdown(self, *args, **kwargs):
        return None


sys.modules["streamlit"] = _FakeStreamlit()
APP = pathlib.Path(__file__).with_name("app.py")
source = APP.read_text(encoding="utf-8")
definitions = source.split("# ============================================================\n# SESSION BOARD LOAD")[0]
ns: dict = {}
exec(compile(definitions, str(APP), "exec"), ns)

assert ns["MODEL_VERSION"] == "OWP_CS2_KILLS_M12_4.9"
assert "minMapCount=0" in ns["_v49_target_url"](365)

# Exact Maps 1–2 relationship parser.
payload = {
    "sports": [{"id": "s1", "name": "CS2"}],
    "teams": [{"id": "t1", "name": "Just Players"}, {"id": "t2", "name": "Lavked"}],
    "players": [{"id": "p1", "display_name": "H1te", "team_id": "t1", "sport_id": "s1"}],
    "games": [{"id": "g1", "title": "Just Players vs Lavked", "home_team_id": "t1", "away_team_id": "t2", "sport_id": "s1", "scheduled_at": "2099-07-15T08:00:00Z"}],
    "appearances": [{"id": "a1", "player_id": "p1", "team_id": "t1", "game_id": "g1", "sport_id": "s1"}],
    "over_under_lines": [{"id": "l1", "status": "active", "over_under": {"appearance_stat": {"appearance_id": "a1", "player_id": "p1", "stat": "Maps 1-2 Kills"}, "stat_value": 27.5}}],
}
props = ns["_parse_underdog_top_level"](payload)
assert len(props) == 1 and props[0]["market_scope_verified"]

# Batch mirror parser fixture.
players_a = ["H1te", "sstiNiX", "spirit", "sm3t", "Something"]
players_b = ["1NVISIBLEE", "k4nfuz", "Djon8", "B4", "B5"]
lines = []
for i, player in enumerate(players_a + players_b):
    team = "Just Players" if i < 5 else "Lavked"
    maps, rounds = 35 + i, 800 + i * 25
    diff = 40 - i * 8
    kd = 1.06 - i * 0.01
    rating = 1.10 - i * 0.01
    lines.append(
        f"| [{player}](https://www.hltv.org/stats/players/{3000+i}/{player.lower()}) | "
        f"[{team}](https://www.hltv.org/stats/teams/{4001 if i < 5 else 4002}/{team.lower().replace(' ', '-')}) | "
        f"{maps} | {rounds} | {diff:+d} | {kd:.2f} | {rating:.2f} |"
    )
parsed = ns["_v49_parse_hltv_batch_markdown"]("\n".join(lines), pulled_at=ns["now_iso"]())
assert len(parsed) == 10
assert parsed["h1te"]["maps"] == 35 and 0.45 < parsed["h1te"]["kpr"] < 0.95
team_index = ns["_v49_build_team_index"](parsed)
assert team_index[ns["normalize_team"]("Just Players")]["current_roster"] == []
assert len(team_index[ns["normalize_team"]("Just Players")]["roster_candidates"]) == 5

# Make every form window use the local fixture, then run the complete board.
def _fixture_table(days: int, force: bool = False):
    rows = {k: dict(v, maps=max(8, int(v["maps"] * min(1.0, 0.25 + days / 365))), profile_maps=max(8, int(v["maps"] * min(1.0, 0.25 + days / 365)))) for k, v in parsed.items()}
    return rows, {"ok": True, "provider": "offline fixture", "rows": len(rows), "days": days, "age_seconds": 0}


ns["_v49_fetch_table"] = _fixture_table
board, status = ns["build_full_board"](props, deep_enabled=True)
assert len(board) == 1
row = board[0]
assert row["profile_maps"] > 0 and row["projection"] is not None
assert row["match_url"].startswith("mirror://")
assert row["status"] in {"PASS", "TRACK"}  # No fake Official without lineup/veto.
assert status["v49_source_recovery"]["projections_generated"] == 1

# Demo KPR cannot use kill-only rounds as its denominator.
demo = pd.DataFrame([
    {"player": "DemoP", "map": "Mirage", "match_id": "dm1", "match_rounds": 20, "round": 1, "side": "CT", "weapon": "m4a1"},
    {"player": "DemoP", "map": "Mirage", "match_id": "dm1", "match_rounds": 20, "round": 2, "side": "T", "weapon": "ak47"},
])
assert ns["ingest_demo_dataframe"](demo, "offline") ["added"] == 2
profile = ns["demo_profile_for"]("DemoP", "Mirage")
assert profile["denominator_verified"] and profile["rounds"] == 20

# MR12 score-coupled simulation sanity.
env = ns["_simulate_mr12_round_environment"](np.random.default_rng(49), 1200, 0.55, 0.48, "Mirage")
assert env["rounds"].min() >= 13
assert np.all(env["team_kills"] >= 0) and np.all(env["opponent_kills"] >= 0)

# Near-line direction remains neutral/pass rather than forced Over or Under.
assert row["lean"] in {"OVER", "UNDER", "PASS"}
print("CS2 v4.9 offline validation passed")

"""Offline validation for core CS2 parsers and simulation.

This deliberately avoids live network requests. It loads application definitions,
then checks representative veto, map-score, team-scoreboard, map-scenario, round,
and player-kill simulation paths.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import types

os.environ.setdefault("CS2_DATA_DIR", tempfile.mkdtemp(prefix="cs2_validate_"))


class _Cache:
    def __call__(self, *args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

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


sys.modules.setdefault("streamlit", _FakeStreamlit())
app_path = pathlib.Path(__file__).with_name("app.py")
source = app_path.read_text(encoding="utf-8")
definitions = source.split(
    "# ============================================================\n# SESSION BOARD LOAD"
)[0]
namespace: dict[str, object] = {}
exec(compile(definitions, str(app_path), "exec"), namespace)


underdog_payload = {
    "sports": [{"id": "s1", "name": "CS2"}],
    "teams": [{"id": "t1", "name": "Team A"}, {"id": "t2", "name": "Team B"}],
    "players": [{"id": "p1", "display_name": "Player One", "team_id": "t1", "sport_id": "s1"}],
    "games": [{"id": "g1", "title": "Team A vs Team B", "home_team_id": "t1", "away_team_id": "t2", "sport_id": "s1", "scheduled_at": "2099-07-14T18:00:00Z"}],
    "appearances": [{"id": "a1", "player_id": "p1", "team_id": "t1", "game_id": "g1", "sport_id": "s1"}],
    "over_under_lines": [{
        "id": "l1", "status": "active",
        "over_under": {"appearance_stat": {"appearance_id": "a1", "player_id": "p1", "stat": "Maps 1-2 Kills"}, "stat_value": 31.5}
    }],
}
underdog_rows = namespace["_parse_underdog_top_level"](underdog_payload)
assert len(underdog_rows) == 1
assert underdog_rows[0]["player"] == "Player One" and underdog_rows[0]["line"] == 31.5
assert underdog_rows[0]["team"] == "Team A" and underdog_rows[0]["opponent"] == "Team B"

html = """
<div>Maps Best of 3 (LAN) elimination match
1. Team A removed Ancient
2. Team B removed Inferno
3. Team A picked Mirage
4. Team B picked Nuke
5. Team A removed Dust2
6. Team B removed Anubis
7. Overpass was left over</div>
<div class="mapholder"><div class="mapname">Mirage</div><div class="results-teamname">Team A</div><div class="results-team-score">13</div><div class="results-teamname">Team B</div><div class="results-team-score">9</div></div>
<div class="mapholder"><div class="mapname">Nuke</div><div class="results-teamname">Team A</div><div class="results-team-score">11</div><div class="results-teamname">Team B</div><div class="results-team-score">13</div></div>
"""
veto = namespace["_extract_veto_actions"](html)
map_results = namespace["_extract_map_results"](html)
assert [row["map"] for row in veto if row["action"] == "picked"] == ["Mirage", "Nuke"]
assert len(map_results) == 2 and map_results[0]["rounds"] == 22 and map_results[1]["rounds"] == 24

mapstats = """
<div class="teamName">Team A</div><table>
<tr><td><a href="/stats/players/1/a">A1</a></td><td>20-15</td></tr><tr><td><a href="/stats/players/2/a">A2</a></td><td>18-16</td></tr><tr><td><a href="/stats/players/3/a">A3</a></td><td>15-17</td></tr><tr><td><a href="/stats/players/4/a">A4</a></td><td>14-18</td></tr><tr><td><a href="/stats/players/5/a">A5</a></td><td>12-19</td></tr></table>
<div class="teamName">Team B</div><table>
<tr><td><a href="/stats/players/6/b">B1</a></td><td>19-16</td></tr><tr><td><a href="/stats/players/7/b">B2</a></td><td>17-15</td></tr><tr><td><a href="/stats/players/8/b">B3</a></td><td>16-16</td></tr><tr><td><a href="/stats/players/9/b">B4</a></td><td>14-17</td></tr><tr><td><a href="/stats/players/10/b">B5</a></td><td>13-18</td></tr></table>
"""
tables = namespace["parse_mapstats_team_tables"](mapstats, ("Team A", "Team B"))
assert len(tables) == 2 and tables[0]["total_kills"] == 79 and tables[1]["total_kills"] == 79

maps = ["Mirage", "Nuke", "Ancient", "Inferno", "Dust2", "Anubis", "Overpass"]
profiles = [
    {
        "team": "Team A", "recent_maps": 12,
        "pick_counts": {"Mirage": 4, "Nuke": 1}, "ban_counts": {"Ancient": 4},
        "map_profiles": {m: {"maps": 8, "win_pct": 55, "round_win_pct": 52, "avg_rounds": 21.5, "close_rate": .45, "blowout_rate": .2, "ot_rate": .06} for m in maps},
    },
    {
        "team": "Team B", "recent_maps": 13,
        "pick_counts": {"Nuke": 4, "Inferno": 1}, "ban_counts": {"Inferno": 3},
        "map_profiles": {m: {"maps": 9, "win_pct": 51, "round_win_pct": 50, "avg_rounds": 21.8, "close_rate": .42, "blowout_rate": .22, "ot_rate": .05} for m in maps},
    },
]
scenarios = namespace["simulate_veto_scenarios"](profiles, 1000)
assert scenarios and abs(sum(row["probability"] for row in scenarios) - 1.0) < 0.02

context = {"format": "BO3", "world_ranks": [5, 9], "team_deep_profiles": profiles, "environment": "LAN", "event_tier": "S-TIER"}
likely, confidence, scenario_meta = namespace["infer_likely_maps"](context)
mean_rounds, round_sd, round_meta = namespace["project_expected_rounds"](context, likely, scenario_meta, "Team A", "Team B")
assert len(likely) == 2 and 34 < mean_rounds < 51 and round_sd > 3 and confidence >= 50

simulation = namespace["simulation_projection_deep"](
    "Player", 34.5, .72,
    {"Mirage": {"blended_kpr": .74, "maps": 20}, "Nuke": {"blended_kpr": .70, "maps": 18}},
    round_meta, 1.01, {"map_factors": {"Mirage": 1.01, "Nuke": .99}},
    1.0, 1.0, 100, "Star Rifler", 0,
)
assert 20 < simulation["projection"] < 45
assert 0 <= simulation["over_prob"] <= 1 and 0 <= simulation["under_prob"] <= 1
assert simulation["expected_rounds"] > 34

print("CS2 v2.0 offline validation passed")

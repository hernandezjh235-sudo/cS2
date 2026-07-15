# v4.2 data dictionary

## Core SQLite tables

- `projections`: every valid as-of board projection and its later result.
- `market_ticks`: timestamped Underdog/PrizePicks lines used for opening and closing movement.
- `entity_snapshots`: player, team, match and roster states as they were known at collection time.
- `team_map_observations`: unique team/match/map results, scores, opponent rank, environment and roster fingerprint.
- `demo_events`: kill-event telemetry with player, map, round, side, weapon, opening and trade fields.
- `demo_rounds`: full round rows used for CT/T win rates and round-kill environment fitting.
- `roster_events`: team/role/stand-in timeline changes.
- `grading_audit`: exact Map 1 and Map 2 IDs, names, kills, confidence and void reasons.
- `model_parameters`: current learned round, kill-share and blend parameters.
- `model_fit_history`: historical model-fit versions and sample sizes.

## Player form windows

- `15d`: very recent form, heavily shrunk.
- `30d`: primary current-form signal.
- `60d`: intermediate stability.
- `180d`: long-term true-talent baseline.

## Projection integrity fields

- `market_scope_verified`
- `market_identity_confidence`
- `identity_ids.player_id`
- `identity_ids.match_id`
- `identity_ids.team_id`
- `identity_ids.opponent_id`
- `player_in_lineup`
- `veto_state`
- `source_age_seconds`
- `kpr_source`
- `calibration_tier`

## Model-health components

- `integrity`: exact market, IDs, lineup and source freshness.
- `data_depth`: telemetry, market ticks, entity snapshots and accumulated team maps.
- `calibration`: clean chronological graded sample.
- `simulation_training`: round-kill and team-kill-share training depth.

# CS2 v4 Data Dictionary

## Probability

- `raw_probability`: direct Monte Carlo directional probability.
- `probability`: calibrated directional probability used for selection gates.
- `calibration_sample`: comparable prior graded rows used by the live calibrator.
- `calibration_ready`: true at 50+ comparable rows.

## Veto and patch

- `veto_state`: `PRE_VETO`, `PARTIAL`, or `CONFIRMED`.
- `patch_era`: user-managed map-pool/economy era.
- `inactive_likely_maps`: predicted maps that conflict with the configured era.

## Market identity

- `market_scope_verified`: sport/stat/map scope verified independently of line size.
- `market_identity_confidence`: 0–1 identity evidence score.
- `source_line_id`, `appearance_id`, `game_id`, `sport_id`, `stat_name`: source relationship identifiers.

## Matchup

- `strength_of_schedule_factor`: capped schedule/opponent normalization.
- `team_kill_share_model`: simulated team KPR and expected player share.
- `role_history`: persistent role/team change summary.

## Historical data

- `cs2_asof_projection_history.jsonl`: every refreshed projection as it existed before the result.
- `cs2_demo_telemetry.json`: controlled player-map telemetry from uploaded demos.
- `cs2_probability_calibration.json`: current in-sample and walk-forward diagnostics.

# CS2 v4.3 upgrade notes

## Connected demo model
- Demo CT/T KPR now enters each map projection.
- Economy classifications and full round denominators enter the round model.
- Demo data without complete rounds cannot alter KPR.

## Stronger matchup model
- Persistent chronological global/map Elo.
- Historical opponent adjustment layered onto matchup KPR.
- Current-roster sample minimum raised for Official consideration.

## Stronger veto and expected rounds
- Every observed pick/ban is stored in SQLite.
- Veto frequencies are recency-, roster-, and opponent-weighted.
- Map side probabilities blend team CT/T splits with chronological map Elo.

## Stronger simulation
- MR12/MR3 score process.
- Pistol, eco, force and full-buy states.
- Score-coupled team kills.
- CT/T player KPR.
- Trained direct/share blend and model-disagreement flag.

## Stronger calibration
- Local probability sample plus chronological isotonic calibration.
- 90% uncertainty interval.
- Official status requires usable calibration and an acceptable lower bound.

## Reliability
- Global source-health circuit breaker.
- Row-level accuracy health score.
- Cumulative SQLite history remains compatible with v4.2 data.

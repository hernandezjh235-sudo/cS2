# CS2 v4.2 — data depth and reliability upgrade

## Accuracy changes

- Replaced universal 14-day current-data fallback with source-specific freshness limits.
- Added 15/30/60/180-day player-form windows.
- Increased configurable deep samples to 20 recent matches and map-stat pages.
- Persisted player, team, match, roster and market snapshots in SQLite.
- Added current Active Duty map-pool eras and official map-pool refresh checks.
- Added historical strength-of-schedule context.
- Removed unsafe page-order team assignment and tightened fuzzy identity matching.
- Added score-coupled MR12/MR3 round and team-kill simulation.
- Learned direct-KPR versus team-kill-share blending from graded history.
- Added demo dropbox auto-ingestion and model-fit invalidation after new telemetry.
- Added strict chronological map grading and a grading-audit table.
- Reduced early learning shifts and raised minimum learning samples.
- Added a model-health report showing data readiness rather than implying a win rate.
- Added a short-lived Railway cron collector for real market ticks and optional as-of projections.

## Important interpretation

The readiness score is not a betting win-rate estimate. Probability calibration remains unproven until enough clean chronological projections have been graded.

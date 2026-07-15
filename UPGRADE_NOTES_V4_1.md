# CS2 v4.1 reliability and accuracy upgrade

This release connects the data systems that were present but not fully consumed in v4.0.

## Projection changes

- Demo telemetry is blended into map-specific KPR only when full map rounds are verified.
- Uploaded demo round tables can supply team CT/T round-win splits.
- Expected rounds are generated through MR12 regulation and MR3 overtime simulation.
- Quick maps can finish in 13 rounds; the old artificial 33.5 two-map floor is removed.
- Patch-era recency and same-era weighting affect player map samples.
- Historical demo performance can be strength-of-schedule normalized when opponent rankings are supplied.
- Team and opponent profiles are never assigned from page order when names/IDs do not match.

## Official gates

Official status requires:

- exact Underdog Maps 1–2 kill identity and line ID
- verified HLTV player ID and match ID
- exact team and opponent ID matching
- player in the announced lineup
- player in the current team roster
- at least four-player lineup/current-roster overlap
- fresh Underdog line, match page, lineup, and confirmed veto
- core KPR from reported KPR or real kills divided by real rounds
- usable calibration sample

Estimated KPR, missing lineup, stale veto, untrained kill-share priors, or missing identity data caps or blocks the play.

## Learning and storage

- SQLite WAL database stores all valid board projections, not only selected picks.
- Automatic grading can grade the whole historical board chronologically.
- Calibration uses walk-forward context filtering and isotonic calibration with shrinkage.
- Team kill-share parameters learn from observed player share of team kills after grading.
- Roster events are stored as structured timeline records.

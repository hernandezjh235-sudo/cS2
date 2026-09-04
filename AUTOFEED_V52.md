# CS2 v5.2 Autofeed + Verified-Data Accuracy Layer

This update changes data acquisition, identity matching, persistence, eligibility gates and grading capture only. It does not change the protected CS2 projection math.

## Automatic pipeline
- Railway web service and collector share `/data/cs2_engine`.
- Collector runs every 10 minutes.
- Pulls real Underdog CS2 lines and records market ticks / line history.
- Recovers verified player profiles automatically and caches them persistently.
- Builds the entire available Underdog board instead of stopping at 200/250 rows.
- Persists verified player, team, match, map, roster and veto context.
- Reconciles player/team/opponent using matchup + verified profile evidence; ambiguous team identity is left unresolved instead of guessed.
- Zero-profile rows are market-watch PASS rows only: no model projection, probability, confidence or Assisted Official label.
- Automatically freezes verified pregame Official/Playable/Track projections for grading.
- Automatically attempts grading of completed frozen rows.
- Keeps market movement / line history enabled.

## Default safety gates
- Simple all-lines mode: OFF by default.
- Fast refresh: OFF by default.
- Max props per refresh: 500.
- Assisted market-prior Official promotion: disabled.
- `profile_maps <= 0` can never be raised to a 55 data score or actionable tier by the autofeed layer.

## Railway
Web start: `start.sh` applies `autofeed_patch.py` idempotently before Streamlit starts.
Collector start: `railway.collector.toml` applies the same patch before `collector.py` runs.

Recommended shared Railway volume: `/data`, with `CS2_DATA_DIR=/data/cs2_engine` (the code defaults to this automatically).

# v2.0 Upgrade Notes

This build replaces the v1 general-KPR projection with a deeper map-by-map engine.

## Added

- player map-specific 180/60-day KPR profiles
- advanced player opportunity/weapon/teamplay metrics when exposed
- recent team roster continuity and current-core map count
- recent match, map result, pick, and ban history
- confirmed-veto parser and pre-veto Monte Carlo map-pair model
- map score, close/blowout, and overtime distributions
- opponent deaths-allowed-per-round and team kills-per-round context
- LAN/online, event tier, stage, and rest context
- PrizePicks line consensus as an optional validation layer
- deeper data-score components and stricter automatic pass flags
- offline validation script and expanded GitHub Actions checks

## Storage

Existing grades and snapshots remain in `CS2_DATA_DIR`. New profile files are created alongside them. Keep the Railway volume mounted at `/data`.

## First deployment

The first deep-data refresh can perform more public requests than v1. Results are cached. Keep the default match/mapstats limits at 6 initially to reduce blocking and startup load.

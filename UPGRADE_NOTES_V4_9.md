# CS2 v4.9 — Batch Stats Mirror

- Replaced per-player 403/429 request storms with a few global aggregate-table requests.
- Added `minMapCount=0` for low-sample/current players.
- Added 365-, 90-, 30- and 15-day form windows.
- Added local persistent mirror cache with fresh and stale age limits.
- Added exact-name transfer recovery: current team can come from Underdog while historical team association remains a warning.
- Added synthetic match context from verified Underdog teams/opponent/start time.
- Batch profiles can generate projections but remain capped at Track/Pass until lineup/veto/map context is verified.
- GitHub Actions now calls `v49_generate_bridge_cache`; the cache branch is optional rather than required.
- PrizePicks is disabled by default and circuit-broken after 403/429.
- Debug UI now reports Batch Mirror status and avoids obsolete BO3/HLTV instructions.

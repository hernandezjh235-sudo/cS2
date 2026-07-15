# v4.8 Upgrade Notes

- Added GitHub Actions provider bridge to move profile collection away from Railway IPs.
- Added BO3 JSON batch requests with retries, backoff, rate-limit handling, and concurrency limits.
- Added authenticated private-repository bridge reads and large-file raw-media fallback.
- Added provider player details, team identity, team roster, and team map profile collection.
- Added full match lineup groups from provider team records.
- Connected provider/local map KPR directly to `build_player_map_profiles`; HLTV is not required.
- Connected provider team records directly to roster/opponent/map modeling.
- Added conservative overall-profile map fallback when map splits are unavailable.
- Capped direct Railway batch size and retained remaining players for later refreshes.
- Added bounded cache retention and profile pruning.
- Renamed HLTV-specific identity warnings to provider-neutral warnings.
- Removed the unnecessary `cs2api` runtime dependency; the app vendors the documented request path.

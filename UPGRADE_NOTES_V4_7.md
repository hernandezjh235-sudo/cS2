# V4.7 — Railway Source Replacement

- BO3 is now the primary no-key player/team/match provider.
- HLTV is optional and protected by a persistent 403/Cloudflare circuit breaker.
- Repeated HLTV player searches stop after the first detected block.
- Verified local database and full-round demo profiles are checked before live providers.
- Optional PandaScore fixtures can recover schedule/team identity.
- BO3 player pages provide reported KPR and map-level profiles when available.
- BO3 match/team pages provide event, team, roster and map-pool context.
- Parser health distinguishes a working Underdog board from a blocked statistics provider.
- Debug Source Status displays line count, verified profiles, BO3 profiles, matched events and generated projections.
- Added source-circuit reset control.
- Standardized parser-health rows to remove blank `None` rows in the table.
- `Unconfirmed` map display remains fixed.
- No league-default projections were reintroduced.

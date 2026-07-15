# CS2 v3 integrity fixes

- Rejects lines below 12 or above 55 for Maps 1-2 Kills.
- Blocks projections when a player has no verified historical profile.
- Never converts league-average fallback KPR into an actionable pick.
- Requires team, opponent, match, and map context for official status.
- Persists verified player/team/match/map records into the Railway volume.
- Adds database status and rebuild tools to Data Manager.
- Missing data creates PASS, not a fake 100% probability.

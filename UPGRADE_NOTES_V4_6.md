# CS2 v4.6 — Source Recovery and Matching Fix

- Detects Cloudflare, CAPTCHA, access-denied, and JavaScript challenge pages.
- Rejects blocked pages instead of treating them as valid empty HTML.
- Adds direct HLTV player search when the full player table is unavailable.
- Adds exact persistent player-ID/team/opponent/match mappings.
- Adds verified local database and full-round demo fallback for player profiles.
- Never uses league-average KPR to manufacture a missing player projection.
- Enriches Underdog team/opponent/matchup relationships.
- Adds source-collapse diagnostics and disables Official picks when coverage fails.
- Fixes `Unconfirmed` being exported as separated characters.
- Adds manual and CSV mapping tools in Data Manager.

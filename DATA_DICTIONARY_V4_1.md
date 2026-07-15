# v4.1 data dictionary

## SQLite database

`cs2_core_v41.sqlite3`

### projections

Stores every valid board projection, raw and calibrated probability, status, veto state, source payload, and final grade.

### demo_events

Stores deduplicated player kill events with match/map/round identifiers, full map-round denominator, side, weapon, headshot, opening, trade, opponent rank, and event time.

### demo_rounds

Stores round-level CT/T team assignments and winners for team side-strength modeling.

### roster_events

Stores verified-current-roster, unconfirmed-lineup, and possible-stand-in events.

## Key projection fields

- `core_kpr_verified`
- `kpr_source`
- `identity_official_ready`
- `identity_ids`
- `source_freshness`
- `current_roster_verified`
- `roster_overlap`
- `roster_transaction_risk`
- `calibration_tier`
- `market_direction_agrees`
- `market_positive_value`
- `team_kill_share_model`

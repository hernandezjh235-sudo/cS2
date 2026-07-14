# CS2 v2.0 Data Dictionary

## Live board

| Field | Purpose | Primary method | Fallback |
|---|---|---|---|
| player/team/opponent | Prop identity | Underdog relationships | Current-board CSV |
| line | Real Maps 1–2 kill line | Underdog public board | Current-board CSV |
| start time | Match identity and freshness | Underdog | Manual CSV |
| consensus line | Cross-board validation | PrizePicks when matched | Blank |
| opening/current line | Movement | Local observation history | Current line only |
| sportsbook odds | No-vig validation | User-saved odds | Blank |

## Player profile

| Group | Fields |
|---|---|
| Baseline | maps, rounds, kills, deaths, KPR, DPR, ADR, rating, K/D |
| Form | 180-, 60-, and 20-day aggregate profile |
| Map splits | maps, rounds, KPR, DPR, ADR, rating, opening KPR for likely maps |
| Side/role | CT/T KPR when exposed, opening activity, AWP share, inferred role |
| Opportunity | KAST, impact, rounds with a kill, multi-kill rate |
| Weapons | rifle, sniper/AWP, pistol kills |
| Teamplay | assists, flash assists, trade kills, traded deaths, clutches when exposed |

No missing metric is silently populated as a real observation. Some core metrics may use a tightly capped model fallback, and those fallbacks are displayed in warnings.

## Match and roster

| Field | Use |
|---|---|
| format | Reject/flag incompatible BO1 or unknown markets |
| confirmed maps/veto | Use actual Maps 1–2 when posted |
| recent pick/ban counts | Pre-veto map-pair simulation |
| current roster | Verify player and team |
| lineup overlap | Estimate current-core continuity |
| current-roster maps | Minimum sample and confidence gate |
| stand-in/postponed flag | Hard-pass protection |
| world ranks | Match competitiveness prior |
| LAN/online | Mean/variance context |
| event/stage/tier | Confidence and variance context |
| rest days | Small fatigue/uncertainty adjustment |

## Team/map profiles

| Field | Use |
|---|---|
| maps played | Sample weight |
| map win percentage | Veto and strength model |
| round-win percentage | Expected map score |
| average rounds | Expected opportunity |
| round standard deviation | Simulation variance |
| close-map rate | Long-map probability |
| blowout rate | Short-map risk |
| overtime rate | Tail opportunity |
| kills per round | Team pace/offense context |
| deaths allowed per round | Opponent kill-opportunity adjustment |

## Projection output

| Field | Meaning |
|---|---|
| projection | Simulated mean Maps 1–2 kills |
| median | Simulated median |
| expected rounds | Simulated Maps 1–2 round mean |
| adjusted KPR | Effective map/opponent/context KPR |
| over/under probability | Monte Carlo result |
| floor/ceiling | 20th/80th percentiles |
| p10/p90 | Wider distribution range |
| data score | Source, sample, map, roster, matchup, and market quality |
| status | Official, Playable, Track Only, or Pass |
| flags | Explicit projection risks |

## Files stored under CS2_DATA_DIR

The app creates JSON/CSV/cache files for source responses, map profiles, roster history, line observations, official snapshots, grades, learning profiles, saved odds, and manual overrides. These should live on a Railway volume.

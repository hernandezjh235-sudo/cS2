# CS2 v4 Upgrade Notes

## The 12 requested layers

1. Historical as-of database — implemented through append-only JSONL snapshots.
2. Raw demo telemetry — implemented with optional Awpy `.dem` parsing and CSV/JSON import.
3. Exact market-ID/scope parser — implemented; Underdog relationship scope is required.
4. Walk-forward backtesting — implemented chronologically using only prior graded rows.
5. Probability calibration — implemented with beta-bin smoothing, Brier/log loss and reliability buckets.
6. Team kill-share simulation — implemented inside the Monte Carlo engine.
7. Patch/map-pool versioning — implemented with editable era configuration.
8. Pre-veto vs confirmed-veto state — implemented with separate thresholds.
9. Roster/role timelines — implemented with persistent change tracking.
10. Strength-of-schedule normalization — implemented as a capped opponent-rank adjustment.
11. Market/closing-line history — opening/current lines plus multi-book manual history are stored.
12. Slip correlation — implemented with a positive-semidefinite Gaussian joint simulation.

## Additional requested workflow

- 15–30 day player form and CT/T map-side data remain in the player/map engine.
- Stand-ins force Pass.
- Veto weaknesses and map depth affect rounds and confidence.
- Headshots and Over 2.5 Maps are research markets until independently graded.
- Live Watch blends early-round production cautiously with the prematch baseline.
- Bankroll defaults to 1–3% flat risk with a quarter-Kelly cap.
- Odds-shopping observations can be saved by book.

## Limitations

No model can guarantee a win rate. Free public pages can change or block Railway. Demo data and graded results must accumulate before calibration becomes reliable. The app responds by lowering confidence or blocking Official status rather than inventing missing data.

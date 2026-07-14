# OneWayPickz CS2 v2.0 — Railway/GitHub App

White, red, and black Streamlit projection app for **CS2 Maps 1–2 Kills**.

## Automatic workflow

When the app loads or **Refresh Real Board + Projections** is pressed, it:

1. Pulls the current Underdog CS2 board.
2. Keeps active **Maps 1–2 Kills** props only.
3. Optionally pulls PrizePicks as a second-board line-consensus source.
4. Matches each player to public CS2 player statistics.
5. Verifies the scheduled match and listed rosters.
6. Builds player map-specific KPR profiles.
7. Reads confirmed vetoes when posted; otherwise simulates likely map pairs from recent picks, bans, usage, and win rates.
8. Projects map score distributions, regulation rounds, close-map rate, blowout rate, and overtime.
9. Adjusts player opportunity for role, opponent deaths allowed, current-roster stability, LAN/online environment, event tier, stage, and rest.
10. Runs a map-by-map Monte Carlo model and displays projection, edge, over/under probability, expected rounds, floor, ceiling, and risk flags.
11. Grades plays as **Official**, **Playable**, **Track Only**, or **Pass**.
12. Saves line movement, official snapshots, grading results, and learning profiles to persistent storage.

The model never creates a betting line. If a live board is unavailable, it displays a source error and supports a real-board CSV import.

## Deep-data layers included

### Player

- 180-, 60-, and 20-day form
- kills/deaths per round, ADR, rating, K/D
- map-specific long-term and recent KPR
- CT/T KPR when exposed
- opening kills and opening deaths
- KAST, impact, rounds with a kill
- multi-kill frequency
- rifle, sniper/AWP, and pistol kill split
- assists, flash assists, trade kills, traded deaths, and clutches when exposed
- inferred role with a confidence score

### Team, map, and matchup

- current roster and recent lineup overlap
- maps played by the current core
- map usage, map win rate, and round-win rate
- recent map picks and bans
- confirmed or simulated Maps 1–2 veto scenarios
- map score, close-map, blowout, and overtime distributions
- team kills per round and opponent deaths allowed per round from recent map scoreboards
- world-ranking gap and match competitiveness

### Environment and market

- BO1/BO3/BO5 verification
- LAN versus online
- event, stage, and estimated event tier
- recent rest days
- Underdog opening/current line and movement
- second-board line consensus when available
- manually saved sportsbook over/under odds and no-vig probability

## Repository files

- `app.py` — complete application
- `validate_model.py` — offline parser and simulation validation
- `DATA_DICTIONARY.md` — data fields, sources, and fallbacks
- `requirements.txt` — Python packages
- `start.sh` — Railway/Streamlit startup command using Railway's `$PORT`
- `railway.toml` — Railway build/deploy configuration
- `Procfile` — secondary startup fallback
- `.python-version` — Python 3.11
- `.streamlit/config.toml` — theme and server settings
- `.env.example` — variable template; never commit real secrets
- `.github/workflows/validate.yml` — GitHub compile and model validation

## GitHub setup

1. Create a private GitHub repository, such as `onewaypickz-cs2`.
2. Upload **all files and folders from this package**, including `.streamlit` and `.github`.
3. Commit to `main`.
4. Confirm the GitHub Action named **Validate CS2 App** passes.

Do not upload `.env` or `.streamlit/secrets.toml` with real credentials.

## Railway setup

1. In Railway choose **New Project → Deploy from GitHub repo**.
2. Select the repository.
3. Add a persistent volume mounted at `/data`.
4. Add:

```text
CS2_DATA_DIR=/data/cs2_engine
TZ=America/Los_Angeles
CS2_DEEP_DATA=true
CS2_DEEP_MATCH_LIMIT=6
CS2_DEEP_MAPSTATS_LIMIT=6
```

5. Open **Settings → Networking → Generate Domain**.

Railway creates `PORT`; do not set it manually.

### Optional variables

```text
PANDASCORE_TOKEN=
UNDERDOG_URL_OVERRIDE=
GITHUB_TOKEN=
GITHUB_REPO=
GITHUB_BRANCH=main
GITHUB_DATA_PATH=learning_data/cs2
GITHUB_AUTO_BACKUP=false
```

`PANDASCORE_TOKEN` is optional and is used only as a schedule/identity fallback. The app runs without it.

## Persistent grading and learning

With `CS2_DATA_DIR=/data/cs2_engine`, the Railway volume preserves:

- source-page cache
- official pregame snapshots
- graded results
- learning profiles
- map/veto/team profiles
- player map profiles
- roster history
- line movement
- saved sportsbook odds
- manual profile overrides

## Confirm the live line and projection pipeline

1. Open the Railway domain.
2. Leave **Pull Underdog CS2** and **Deep map/veto/roster data** enabled.
3. Press **Refresh Real Board + Projections**.
4. Confirm `Real Props` is greater than zero when Underdog has active CS2 Maps 1–2 props.
5. Open **Debug + Settings → Source Status**.
6. Confirm the Underdog status reports `ok: true`, a parser name, and row count.
7. Open a player card and inspect the player map profile, veto scenarios, map round distribution, opponent data, and data-quality components.

## Empty-board behavior

An empty board can mean:

- Underdog has no current CS2 Maps 1–2 Kill props.
- The public endpoint or market wording changed.
- A cloud IP was blocked.
- The event has not yet posted player props.

The app does not label stale cached lines as current. Use the current-board CSV uploader in **Data Manager** until the source returns.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python validate_model.py
bash start.sh
```

Open `http://localhost:8501`.

## Important model behavior

- Missing real data is not fabricated.
- Low map, roster, opponent, or market confidence lowers the data score.
- Stand-ins, uncertain formats, unmatched players, weak map samples, and extreme mismatches can force a Pass.
- Public pages can change; use **Debug + Settings** to inspect every source response.
- Projections are estimates, not guarantees.

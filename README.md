# OneWayPickz CS2 v4.9

Streamlit/Railway projection engine for **CS2 Maps 1–2 Kills**.

## What v4.9 fixes

Earlier builds pulled Underdog correctly but returned zero projections because Railway and Streamlit Cloud received 403/429 responses from per-player CS2 statistics sources. V4.9 replaces that request pattern with a self-contained **batch statistics mirror**:

1. Underdog supplies current lines, teams, opponents, IDs and start times.
2. Jina Reader retrieves the public HLTV aggregate player table in a few batch requests.
3. Hundreds of player rows are parsed and cached in one operation.
4. Current-board players are matched by nickname and player ID.
5. Local database and full-round demo profiles remain higher-quality fallbacks.
6. GitHub Actions may publish the same data on `data-cache`, but this is optional.

The app does **not** make one request per player and does not create a league-average player projection.

## Upload to GitHub

Upload the complete package, especially:

- `app.py`
- `source_bridge.py`
- `collector.py`
- `requirements.txt`
- `.github/workflows/source_bridge.yml`
- `.github/workflows/validate.yml`
- `.streamlit/config.toml`
- `railway.toml`
- `start.sh`

## Required settings

No paid key is required. The default mirror settings are already in code. Recommended Railway variables:

```text
CS2_DATA_DIR=/data/cs2_engine
TZ=America/Los_Angeles
CS2_JINA_MIRROR_ENABLED=true
CS2_BO3_LAST_RESORT=false
CS2_ENABLE_LEGACY_WEB_SOURCES=false
```

For Streamlit Community Cloud, `CS2_DATA_DIR` may be omitted. The app chooses a writable local folder automatically.

## First refresh

1. Deploy the full v4.9 package.
2. Open the app.
3. Press **Refresh Real Board + Projections** once.
4. The first refresh downloads the batch tables and can take longer than later refreshes.
5. Open **Debug + Settings → Source Status**.

A healthy run should show:

- Underdog Lines above zero
- Verified Profiles above zero
- Matched Teams above zero
- Matched Events above zero
- Projections above zero
- Batch Mirror `READY`

Batch profiles are intentionally capped at Track/Pass until lineup, veto and map-specific data become stronger. The purpose of v4.9 is to restore real projections safely, not to promote incomplete data to Official.

## Optional GitHub warm cache

The workflow **Refresh CS2 Provider Cache** runs on GitHub and writes `cs2_provider_cache.json` to the `data-cache` branch. This can make startup faster but is no longer required.

For a private repository, add a read-only token to the app only when you want it to read the private `data-cache` branch:

```text
CS2_BRIDGE_REPO=owner/repository
CS2_BRIDGE_TOKEN=github_pat_...
```

## Demo Center

Do not commit large professional `.dem` files to GitHub. Upload them in the app or, on Railway, place them under:

```text
/data/cs2_engine/incoming_demos
```

Full-round demos can improve KPR, CT/T, role and economy modeling. Kill-only files never alter KPR without a verified round denominator.

## Safety behavior

When Underdog works but all profile sources fail, the app returns Pass with no projection and shows the exact mirror/cache error. It does not reuse a league-average KPR as though it were a real player profile.

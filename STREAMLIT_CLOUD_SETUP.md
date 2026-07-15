# Streamlit Community Cloud setup — CS2 v4.9

The screenshots show the app is running on `streamlit.app`, not Railway.

1. Replace the existing GitHub repository files with the complete v4.9 package.
2. Confirm these files exist at repository root:
   - `app.py`
   - `source_bridge.py`
   - `requirements.txt`
   - `.python-version`
   - `.streamlit/config.toml`
   - `.github/workflows/source_bridge.yml`
3. In Streamlit Community Cloud, reboot the app after GitHub finishes updating.
4. Open the app and press **Refresh Real Board + Projections** once.
5. The first batch-mirror refresh may take longer because it creates the local cache.
6. Open **Debug + Settings** and confirm Batch Mirror is `READY` and Verified Profiles is above zero.

No paid API key is required. `CS2_DATA_DIR` can be omitted on Streamlit Cloud.

Streamlit Community Cloud local storage is not guaranteed to persist through every restart. The optional GitHub Action writes the player cache to the `data-cache` branch and helps warm future starts.

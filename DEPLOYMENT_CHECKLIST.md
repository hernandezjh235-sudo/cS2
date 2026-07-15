# CS2 v4.9 deployment checklist

- Upload the complete package to the existing GitHub repository.
- Confirm `app.py`, `source_bridge.py`, `requirements.txt`, `.python-version`, `.streamlit/config.toml`, and both GitHub workflows are present.
- Reboot the Streamlit Community Cloud app or redeploy Railway.
- No paid API key is required.
- Recommended: `CS2_JINA_MIRROR_ENABLED=true`.
- Keep `CS2_BO3_LAST_RESORT=false` and `CS2_ENABLE_LEGACY_WEB_SOURCES=false`.
- Press Refresh Real Board + Projections once.
- In Debug + Settings, verify: Underdog Lines > 0, Verified Profiles > 0, Projections > 0, Batch Mirror READY.
- On Railway, keep the volume mounted at `/data` and use `CS2_DATA_DIR=/data/cs2_engine`.
- On Streamlit Cloud, `CS2_DATA_DIR` may be omitted.

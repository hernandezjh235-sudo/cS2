# Deployment Checklist

- [ ] Upload every file, including `.github` and `.streamlit`, to a private GitHub repository.
- [ ] Confirm GitHub Actions passes compile and `validate_model.py`.
- [ ] Connect the repository to Railway.
- [ ] Mount a Railway volume at `/data`.
- [ ] Set `CS2_DATA_DIR=/data/cs2_engine`.
- [ ] Set `TZ=America/Los_Angeles`.
- [ ] Set `CS2_DEEP_DATA=true`.
- [ ] Set `CS2_DEEP_MATCH_LIMIT=6` and `CS2_DEEP_MAPSTATS_LIMIT=6`.
- [ ] Do not manually create `PORT`.
- [ ] Generate a Railway domain.
- [ ] Open the app and press **Refresh Real Board + Projections**.
- [ ] Confirm Underdog source status is `ok: true` when a board is posted.
- [ ] Confirm player cards show likely maps, expected rounds, map KPR, opponent DPR allowed, and data score.
- [ ] Save an official snapshot before matches.
- [ ] Grade completed matches and verify history persists after a redeploy.
- [ ] Keep all tokens in Railway variables; never commit secrets.

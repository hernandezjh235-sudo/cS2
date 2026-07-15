# Railway collector setup

The collector is optional but strongly recommended because it builds line history and data depth while the web app is closed.

1. Deploy the normal web service using `railway.toml`.
2. Create a second service from the same GitHub repository.
3. Configure that service with `railway.collector.toml`.
4. Mount the same Railway volume at `/data`.
5. Copy the same environment variables.
6. Keep `CS2_COLLECT_TEAM_HISTORY=true` so a deep roster/map/veto refresh runs at most once per hour. Leave `CS2_COLLECT_PROJECTIONS=false` to avoid saving every collector projection, or set it to `true` for full pregame as-of snapshots.

The included cron schedule is every 10 minutes. The process exits after every collection cycle.

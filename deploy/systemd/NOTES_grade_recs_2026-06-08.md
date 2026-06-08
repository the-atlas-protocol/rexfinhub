# Grade-recommendations unit files (2026-06-08)

Static systemd unit files for the weekly recommendation-history grader
(`scripts/grade_recommendations.py`). Fires Sunday 23:00 ET so outcome
columns are fresh before the Monday morning stock_recs send.

This pairs with the `append_weekly_recommendations()` wire-up in both
`weekly_v2_report.main()` and `trex_combined_v9.build()` (Wave E1
2026-05-11 plumbing, finally wired 2026-06-08).

## Deploy on the VPS

```bash
# From a checkout of fix/rec-history-wireup-impl on the VPS:
sudo cp deploy/systemd/rexfinhub-grade-recommendations.service /etc/systemd/system/
sudo cp deploy/systemd/rexfinhub-grade-recommendations.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rexfinhub-grade-recommendations.timer
systemctl status rexfinhub-grade-recommendations.timer --no-pager | head -12
systemctl list-timers rexfinhub-grade-recommendations.timer --no-pager
```

## Verify

After deploy:

```bash
# Should show next firing rounded to Sun 23:00 America/New_York.
systemctl list-timers rexfinhub-grade-recommendations.timer

# Live tail of grader runs:
sudo journalctl -u rexfinhub-grade-recommendations.service -f

# Manual one-shot (test):
cd /home/jarvis/rexfinhub
sudo -u jarvis /home/jarvis/venv/bin/python -m scripts.grade_recommendations --dry-run
```

## Sanity check — recommendation_history is populating

After the first Monday send post-deploy:

```bash
cd /home/jarvis/rexfinhub
sqlite3 data/etp_tracker.db \
  "SELECT week_of, COUNT(*) AS recs, COUNT(DISTINCT confidence_tier) AS tiers
   FROM recommendation_history GROUP BY week_of ORDER BY week_of DESC LIMIT 4;"
```

Expect ~12-32 rows for the most recent Monday (12 whitespace + up to 20
pipeline from v9; v2 path writes 12+12 launch+whitespace).

# REX FinHub — systemd Units

All units run as `User=jarvis` on the VPS. The VPS timezone is `America/New_York`; `OnCalendar` expressions that reference a timezone offset use this directly.

---

## File Inventory

| File | Purpose |
|------|---------|
| `rexfinhub-api.service` | FastAPI web process (gunicorn) |
| `rexfinhub-daily.service` | Daily pipeline: git pull → run_daily.py → email reports |
| `rexfinhub-daily.timer` | Fires rexfinhub-daily.service on weekdays |
| `rexfinhub-bloomberg.service` | Bloomberg pull + sync (ad-hoc / legacy one-shot) |
| `rexfinhub-bloomberg.timer` | Scheduled Bloomberg pull at 17:15 + 21:00 ET weekdays |
| `rexfinhub-bloomberg-chain.service` | **Bloomberg pull + sync + apply overrides (scheduled use)** |
| `rexfinhub-parquet-rebuild.service` | L&I Engine parquet rebuild chain (6-step) |
| `rexfinhub-parquet-rebuild.timer` | Fires parquet rebuild Mon + Fri at 06:00 ET |
| `rexfinhub-preflight.service` | Pre-send audit (audit_bloomberg, classification, null data, etc.) |
| `rexfinhub-preflight.timer` | Fires preflight ~1h before the daily send window |
| `rexfinhub-cboe.service` | CBOE symbol reservation nightly sync |
| `rexfinhub-cboe.timer` | Fires CBOE sync at 03:00 ET nightly |
| `rexfinhub-bulk-sync.service` | Bulk DB reconciliation |
| `rexfinhub-bulk-sync.timer` | Weekly bulk sync |
| `rexfinhub-sec-scrape.service` | SEC EDGAR scraper |
| `rexfinhub-classification-sweep.service` | Classification backfill sweep |
| `rexfinhub-classification-sweep.timer` | Fires classification sweep |
| `rexfinhub-reconciler.service` | Data reconciler |
| `rexfinhub-reconciler.timer` | Fires reconciler |
| `rexfinhub-gate-open.service` | Opens the email send gate |
| `rexfinhub-gate-open.timer` | Fires gate-open at the scheduled window |
| `rexfinhub-gate-close.service` | Closes the email send gate |
| `rexfinhub-gate-close.timer` | Fires gate-close after the send window |
| `rexfinhub-atom-watcher.service` | ATOM feed watcher (new SEC filings) |
| `rexfinhub-single-filing-worker.service` | Single-filing extraction worker |

---

## Key Units — Detail

### rexfinhub-bloomberg-chain.service

Replaces the bare `rexfinhub-bloomberg.service` for **scheduled runs**. Runs the same Bloomberg SharePoint pull + `sync_market_data`, then chains three apply scripts so manual overrides survive the nightly resync:

1. `scripts/apply_fund_master.py` — master fund attributes
2. `scripts/apply_underlier_overrides.py` — underlier corrections
3. `scripts/apply_issuer_brands.py` — issuer display names

The original `rexfinhub-bloomberg.service` is retained for ad-hoc one-shot use (e.g. `systemctl start rexfinhub-bloomberg`).

**To switch the timer to the chain service**, edit `rexfinhub-bloomberg.timer` and change `Unit=` to `rexfinhub-bloomberg-chain.service`, then reload.

### rexfinhub-parquet-rebuild.service / .timer

Rebuilds all five L&I Engine parquet files in dependency order:

```
universe_loader → bbg_timeseries → filed_underliers → competitor_counts → launch_candidates → whitespace_v4
```

Each step is a `python -m screener.li_engine.analysis.<module>` invocation. If any step fails, the chain stops (bash `&&` chaining). Timeout: 900s.

Timer fires **Monday and Friday at 06:00 ET** so fresh data is ready before the weekly report build.

---

## Installation

**Atlas (the coordinator) handles installation with sudo.** You do not need to install units yourself.

For reference, the standard install sequence is:

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo cp deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start the parquet rebuild timer
sudo systemctl enable --now rexfinhub-parquet-rebuild.timer

# Enable bloomberg-chain (disable the bare bloomberg timer first if switching)
sudo systemctl enable rexfinhub-bloomberg-chain.service
```

### Verify timers

```bash
systemctl list-timers --all | grep rexfinhub
journalctl -u rexfinhub-parquet-rebuild.service -n 50
journalctl -u rexfinhub-bloomberg-chain.service -n 50
```

### Manual one-shot run

```bash
systemctl start rexfinhub-parquet-rebuild.service
systemctl start rexfinhub-bloomberg-chain.service
```

---

## Notes

- All services use `EnvironmentFile=/home/jarvis/rexfinhub/config/.env` where secrets are needed.
- `Persistent=true` on timers means a missed run (e.g. VPS was down) fires immediately on next boot.
- `StandardOutput=journal` / `StandardError=journal` — tail logs with `journalctl -u <unit> -f`.

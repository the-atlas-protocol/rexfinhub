# Stage 1 Audit — Schedulers
Generated: 2026-05-11T18:50:00-04:00
Agent: schedulers
VPS: jarvis@46.224.126.196 — Ubuntu, TZ America/New_York (EDT, -0400)
HEAD on VPS: `d45252c` (Mon May 11 14:51:54 2026 -0400)

## Summary

**13 critical/high findings.** The scheduler tier looks superficially healthy
(timers fire on schedule, services exit) but is silently broken in three
load-bearing places: (1) the SEC scrape's 2-hour TimeoutStartSec is no longer
sufficient for the DB upload step — tonight's 16:00 run was killed by systemd
TERM at 18:00 mid-`Compressing...`; (2) the daily email pipeline has not
performed a successful **send** since **2026-04-27** (14 days) because
preflight always fails one or more audits, exits 1, never produces
`.preflight_decision.json`, and `send_all --use-decision` correctly
stands down — but nobody is monitoring the absence of sent emails;
(3) the CBOE scanner has been authentication-failing **every night since
at least 2026-05-03** (9 consecutive failed runs in `cboe_scan_runs`)
because the `CBOE_SESSION_COOKIE` is expired and never gets rotated by
the scheduler. Layered on top: db-backup is broken (sqlite3 binary not
installed → exit 127, no backups since 2026-05-06), 13F + parquet-rebuild
+ bloomberg-chain unit files exist in repo but **are not deployed on VPS**,
disk is at **87 % full** (5 GB free on a 38 GB root, with 11 GB cache),
Bloomberg sync silently logs "skipped" + "CSV export error" on every run.

## Timer inventory

All times are America/New_York (DST handled). "Last run" = most recent
trigger time. "Result" = state of the last invocation of the underlying
service. "Notes" = scheduling/health observations.

| Timer | OnCalendar | Last fired | Last result | Next fire | Notes |
|---|---|---|---|---|---|
| `rexfinhub-sec-scrape.timer` | Mon..Fri 08:00, 12:00, 16:00, 20:00 | Mon 16:00:14 EDT | **FAILED (timeout)** | Mon 20:00 | TimeoutStartSec=7200 hit during DB upload `Compressing...` step. Killed by SIGTERM at 18:00. |
| `rexfinhub-daily.timer` | Mon..Fri 19:30 | Fri 19:30:09 EDT | success exit 0, but `send_all` stood down ("no decision or token mismatch") | Mon 19:30 | Pipeline runs end-to-end (16.4 m) but no email goes out. Last actual send via timer: **never** since `--use-decision` flag was added. |
| `rexfinhub-preflight.timer` | Mon..Fri 18:30 | Mon 18:30:05 EDT | **FAILED (exit 1)** | Tue 18:30 | Audit produces `fail` status → script exits 1 → systemd marks failed → `.preflight_decision.json` is never written (only the dashboard click writes it). Has failed every weekday since at least 5/6. |
| `rexfinhub-classification-sweep.timer` | Mon..Fri 09:00 | Mon 09:00:00 EDT | **FAILED (exit 1)** | Tue 09:00 | Same pattern — audit failing → exit 1. "259 unclassified new launches, 8 CC funds missing CC attributes". |
| `rexfinhub-bloomberg.timer` | Mon..Fri 17:15, 21:00 | Mon 17:15:00 EDT | success exit 0, with silent warnings | Mon 21:00 | "Global ETP sync skipped: Sheets dir not found", "CSV export skipped: can only concatenate str (not 'float') to str" — every single run. |
| `rexfinhub-cboe.timer` | Mon..Sun 03:00 | Mon 03:00:04 EDT | **FAILED (exit 3)** | Tue 03:00 | Auth failure: `CBOE redirected to https://account.cboe.com/account/login/`. **9 consecutive failures** in `cboe_scan_runs` (5/3 → 5/11). Last successful scan: 5/8 13:37 (manually triggered, not by timer). |
| `rexfinhub-bulk-sync.timer` | Sun 07:00 | Sun 07:00:02 EDT | success exit 0 | Sun 07:00 | Healthy. 0 new CIKs (universe is current). |
| `rexfinhub-reconciler.timer` | Daily 08:00 | Mon 08:00:19 EDT | success exit 0 (with one logged 403 Forbidden for today's index) | Tue 08:00 | Functional but warns: 403 fetching today's daily-index/form.20260511.idx (SEC may not have published yet at 08:00 UTC). |
| `rexfinhub-gate-open.timer` | Mon..Fri 19:00 | Fri 19:00:01 EDT | success | Mon 19:00 | Gate currently `false` (closed by Fri 20:00 close timer; will reopen at Mon 19:00). |
| `rexfinhub-gate-close.timer` | Mon..Fri 20:00 | Fri 20:00:15 EDT | success | Mon 20:00 | Healthy. |
| `rexfinhub-db-backup.timer` | Daily 23:00 | Sun 23:00:18 EDT | **FAILED (exit 127)** | Tue 23:00 | `/bin/bash: line 1: sqlite3: command not found`. **No DB backups since 2026-05-06**. data/backups/ is empty. |
| `rexfinhub-13f-quarterly.timer` | 02-19, 05-20, 08-19, 11-19 06:00 | — | — | — | **NOT INSTALLED ON VPS** (unit file exists in `deploy/systemd/` repo but not under `/etc/systemd/system/`). Next quarterly window: 2026-05-20. |
| `rexfinhub-parquet-rebuild.timer` | Mon,Fri 06:00 | — | — | — | **NOT INSTALLED ON VPS**. Parquets are still being written by `run_daily.py` step 9.5, so this is duplicative-but-missing. |

## Service inventory (services without timers — long-running daemons)

| Service | Trigger | State | Notes |
|---|---|---|---|
| `rexfinhub-api.service` | enabled, `Restart=on-failure` | active (running) since 2026-04-21 — 2w 5d uptime | Memory peak 1.4 GB, healthy. |
| `rexfinhub-atom-watcher.service` | enabled, `Restart=always` | active (running) since 2026-04-14 — 3w 5d uptime | Cycle 38539 currently. queried=4 fetched=4 parsed=141 new=0 — no new filings observed in latest cycle. Memory peak 1.1 GB. |
| `rexfinhub-single-filing-worker.service` | enabled, `Restart=always` | active (running) since 2026-04-14 — 3w 5d uptime | cycle 77058: processed=0 ok=0 — queue is empty (atom watcher finds nothing). Memory peak 1.3 GB. |
| `rexfinhub-bloomberg-chain.service` | (would-be `[Install] WantedBy=multi-user.target`) | **NOT INSTALLED ON VPS** | The bloomberg.timer drives `rexfinhub-bloomberg.service` (the older non-chain version). The chain version's `apply_fund_master.py`, `apply_underlier_overrides.py`, `apply_issuer_brands.py` post-steps are never running. |
| `rexfinhub-single-filing-worker.service` | (no timer; started by atom-watcher) | (covered above) | |

## Findings

### F1 — SEC scrape DB upload exceeds 2-hour service timeout
- **Severity**: critical
- **Unit affected**: `rexfinhub-sec-scrape.service`
- **Symptom**: Today's 16:00 run was killed by SIGTERM at 18:00:14 mid-`Compressing...`. The service's `TimeoutStartSec=7200` (2h) was hit during step `[10/12] Uploading DB to Render` — specifically the `Preparing lean Render upload... 646 MB` step printed at 16:20:05 then `Compressing...` at 18:00:14 (1h40m of silence). This means the lean upload prep + compression alone now exceeds the entire service budget.
- **Evidence** (journal):
  ```
  May 11 16:19:52 [10/12] Uploading DB to Render...
  May 11 16:20:05   Preparing lean Render upload... 646 MB (was 646 MB)
  May 11 18:00:14   Compressing...
  May 11 18:00:14 systemd: rexfinhub-sec-scrape.service: Killed (timeout, signal=TERM)
  ```
- **Blast radius**: Today's 16:00 SEC scrape produced no Render upload — the production site is running with stale data from the previous successful run (Friday 5/8 at 19:46, 86 MB). The 12:00 + 08:00 SEC scrapes also did not upload (run_all_pipelines.py with `--skip-email --skip-market` only uploads at the end, and they timed out / were skipped). pipeline_runs ID 152 (16:00) shows `0 filings_found` and 18s duration, so the scrape itself was a no-op — the long part was the upload chain that never finished.
- **Hypothesis**: DB grew to 646 MB. The "lean upload" prep is iterating tables and stripping non-essential rows, which now takes ~1h40m. Combined with sqlite gzip compression of a 600 MB file (single-threaded gzip on one core), the total exceeds the 7200s budget. Possible amplifiers: (a) the DB just crossed a size threshold; (b) the Render upload endpoint accepts the upload synchronously and the timer is also waiting on the network; (c) `etp_tracker.db` has bloated tables that lean-upload now scans.
- **Fix size**: medium. Options: bump `TimeoutStartSec` to 14400 (band-aid); split scrape and upload into separate units (better); precompress the lean DB on disk and upload as a separate atomic step (best). Decide after Stage 2.

### F2 — Daily pipeline has not actually emailed since 2026-04-27 (14 days dark)
- **Severity**: critical
- **Unit affected**: `rexfinhub-daily.service` + `rexfinhub-preflight.service` interaction
- **Symptom**: `data/.send_log.json` last entry is `"2026-04-27": {"daily_filing": "21:26", ...}`. Friday 5/8's run shows `send_all: standing down (no decision or token mismatch)` and exits 0 anyway. **Both Ryu and the pipeline believe everything's fine.**
- **Evidence**:
  - Last entry of `.send_log.json`: 2026-04-27.
  - Friday journal: `[12/12] Sending email reports... Sending via send_all.py --use-decision: daily only... ABORT: --use-decision but no data/.preflight_decision.json. Dashboard click required... send_all: standing down (no decision or token mismatch). === Done in 984s (16.4m) === EXIT: 0 (success)`
  - `data/.preflight_decision.json` does not exist (only `.preflight_result.json` and `.preflight_token` are written by the script). The decision file is written by a manual dashboard click — and that click hasn't happened in 14 days (presumably because Ryu didn't see the preflight summary email, or saw it and didn't click GO because audits were failing).
- **Blast radius**: Subscribers have received zero daily filing emails for 14 days via the timer chain. Any sends since 4/27 were manual. Critical recipients lose visibility; the GO/HOLD safeguard is doing its job, but the loop is broken in the "Ryu clicks GO" step.
- **Hypothesis**: When `--use-decision` was added, the assumption was Ryu would see the 18:30 preflight email each weekday and click GO before 19:30. In practice the preflight emails always show fails (classification gaps, ticker dupes, attribution completeness 100% NULL primary_strategy) so Ryu has stopped clicking. The system has no fallback — there's no "auto-GO if only attribution fails" or "auto-GO if same fails as yesterday and Ryu clicked GO yesterday".
- **Fix size**: medium. (1) Send a digest-of-misses ("you have not sent in N days") to relasmar; (2) introduce an auto-GO escape hatch for "known persistent" failures; (3) downgrade `attribution_completeness` from `fail` to `warn` if it's just a missing column population issue; (4) make the preflight summary email louder / clickable from the email itself.

### F3 — Preflight audit_attribution_completeness reports 100 % NULL primary_strategy
- **Severity**: high
- **Unit affected**: `rexfinhub-preflight.service` audit logic + DB schema
- **Symptom**: Every preflight run since at least 5/6 reports `NULL primary_strategy 100.0% (threshold 5%); NULL issuer_display 64.5%`. 100 % NULL is suspicious — either the column was added but never populated, or the audit is reading the wrong column.
- **Evidence**: `.preflight_result.json` contents (today): `"attribution_completeness": {"status": "fail", "detail": "NULL primary_strategy 100.0% (threshold 5%); NULL issuer_display 64.5% (threshold 15%)"}`. Same value on 5/8 (64.4 %) and earlier — the percentage is stable, so no progress is being made on backfill.
- **Blast radius**: This single audit is one of the reasons preflight exits 1 and `.preflight_decision.json` is never auto-written → blocks F2.
- **Hypothesis**: `primary_strategy` column was migrated in but the populator never ran (or runs but every row is still NULL). `issuer_display` 64.5 % NULL is consistent with the morning classification sweep's "0 NULL issuer_display" (different table?) — the two audits may be measuring different universes (one is full DB, other is "ACTV ETPs only").
- **Fix size**: small (Stage 2: identify the populator script for primary_strategy, run it once; reconcile the issuer_display mismatch).

### F4 — CBOE scanner failed 9 consecutive nights — cookie expired since ~5/3
- **Severity**: high
- **Unit affected**: `rexfinhub-cboe.service`
- **Symptom**: Every nightly 03:00 run since 2026-05-03 has failed within 26 seconds with `Auth failure: CBOE redirected to 'https://account.cboe.com/account/login/' (status 302); refresh CBOE_SESSION_COOKIE`. The known-active refresh succeeds (because that hits NASDAQ + SEC EDGAR, not CBOE), but the actual scan exits 3.
- **Evidence**: `cboe_scan_runs` table — IDs 11–20 all `status='failed'` with the same error message except ID 17 (2026-05-08 13:37 — manual trigger, completed with 715 state changes). Journal confirms `2026-05-11 03:01:43,010 ERROR Auth failure: CBOE redirected to login...`.
- **Blast radius**: `/filings/symbols` page on rexfinhub.com is showing data 9 days stale. The "CBOE session expired" red banner is presumably visible to anyone hitting the page. The 03:00 sweep that's supposed to refresh the 13,119 reserved-by-competitor universe has not run.
- **Hypothesis**: Cookie typically lasts ~7 days; rotation is manual (per the cboe-cookie skill). Nobody pasted a fresh cookie since 5/2 or so. There is no automated alerting on this — `cboe_scan_runs.status='failed'` has been recording it but no email goes out.
- **Fix size**: trivial (rotate cookie via `/cboe-cookie` skill — Ryu does this manually). To prevent recurrence: add an alert when 2 consecutive runs fail with the auth-failure message.

### F5 — db-backup service has been failing every night since deployment
- **Severity**: high
- **Unit affected**: `rexfinhub-db-backup.service`
- **Symptom**: `/bin/bash: line 1: sqlite3: command not found` → exit 127. Has fired every night at 23:00 and failed every time. `data/backups/` is empty (no files at all).
- **Evidence**:
  ```
  May 10 23:00:18 jarvis bash[1809540]: /bin/bash: line 1: sqlite3: command not found
  ls -la data/backups/  →  total 8 (only . and ..)
  ```
- **Blast radius**: **No DB backups exist on the VPS.** If `etp_tracker.db` (624 MB) gets corrupted, last clean copy is the Render upload (86 MB compressed, 2026-05-08). Recovery would lose the SEC scrape catchup work + the 5/9–5/11 atom-watcher writes + manual CBOE/manual-classification edits.
- **Hypothesis**: The unit is `disabled; preset: enabled` in the unit-files list (other units are `enabled`), AND the script uses the `sqlite3` CLI which isn't installed on the VPS (the project uses Python's `sqlite3` module so the binary was never required for runtime). The `Persistent=true` on the timer kept it firing despite the disabled state — the timer is enabled, the service is disabled, but the timer fires the service anyway via `Unit=` directive.
- **Fix size**: trivial. (1) `apt install sqlite3` on VPS, OR (2) rewrite `ExecStart` to use `/home/jarvis/venv/bin/python -c "import sqlite3; ..."` for portability. Also enable the .service unit explicitly.

### F6 — 13F + parquet-rebuild + bloomberg-chain unit files in repo but not installed on VPS
- **Severity**: high
- **Units affected**: `rexfinhub-13f-quarterly.{service,timer}`, `rexfinhub-parquet-rebuild.{service,timer}`, `rexfinhub-bloomberg-chain.service`
- **Symptom**: `systemctl list-unit-files 'rexfinhub-*'` returns 25 units, all named — but the three above are absent. `systemctl status rexfinhub-13f-quarterly.timer` returns `Unit could not be found.`
- **Evidence**: Unit-files list has no `rexfinhub-13f-quarterly.*`, no `rexfinhub-parquet-rebuild.*`, no `rexfinhub-bloomberg-chain.*`. All three exist in `C:/Projects/rexfinhub/deploy/systemd/`.
- **Blast radius**:
  - 13F quarterly ingestion: **next scheduled fire was 2026-05-20** — that won't happen automatically. Ryu would have to remember to manually run `python scripts/fetch_13f.py --backfill`.
  - Parquet rebuild: documented as twice-weekly (Mon+Fri 06:00). The same parquets ARE built inside `run_daily.py` step 9.5 daily — so the data is fresh, but the standalone rebuild path doesn't exist as a fallback.
  - Bloomberg chain: the post-steps `apply_fund_master.py`, `apply_underlier_overrides.py`, `apply_issuer_brands.py` never run after the bloomberg sync. Manual overrides may be getting reverted by nightly Bloomberg sync (the exact problem the chain unit was created to solve, per its own header comment: "applies manual overrides so that hand-corrected values are not reverted by nightly Bloomberg resync").
- **Hypothesis**: These units were committed to repo but the deploy step (presumably manual `scp + systemctl daemon-reload + enable`) was never performed.
- **Fix size**: small. Copy the three unit files to `/etc/systemd/system/`, daemon-reload, enable + start the timers. Verify bloomberg.timer should drive bloomberg-chain.service instead of bloomberg.service (this is a swap, not an add).

### F7 — Bloomberg sync silently logs two errors on every run
- **Severity**: medium
- **Unit affected**: `rexfinhub-bloomberg.service`
- **Symptom**: Every Bloomberg sync run prints `Global ETP sync skipped: Sheets dir not found: data/DASHBOARD/sheets` and `CSV export skipped: can only concatenate str (not "float") to str`. Service exits 0, so systemd reports success, so nobody notices.
- **Evidence**: Same two lines in journal for May 4, 5, 6, 7, 8, 11 — every run, both timers (17:15 and 21:00).
- **Blast radius**: Unknown. The "Global ETP sync" was probably a feature for international ETP ingestion (the rexfinhub-asia / global-AUM stuff?). The "CSV export" is downstream consumers. Both have been silently broken for at least 7 days. If "Sheets dir not found" means a specific feature is dark, that feature has been dark.
- **Hypothesis**: (a) `data/DASHBOARD/sheets` directory was deleted/renamed and the code wasn't updated; (b) the CSV export has a type-coercion bug where a numeric column is being concatenated to a string column header — `float` value flowing into a `str` join.
- **Fix size**: small (Stage 2: trace the two log lines to source, either create the missing dir or guard the code path properly).

### F8 — Disk at 87 % full (5 GB free of 38 GB)
- **Severity**: medium
- **Unit affected**: VPS `/` filesystem (`/dev/sda1`)
- **Symptom**: `df -h /home` shows `38G total, 31G used, 5.0G avail, 87% used`. Cache dir is 11 GB total (5.2 GB sec, 5.0 GB web, 497 MB submissions). DB is 624 MB. `data/` is 2.7 GB.
- **Blast radius**: The next DB compaction needs ~2× DB size temporarily (1.2 GB free needed for a 624 MB DB). The Render upload `Compressing...` step also needs scratch. With only 5 GB free, one bad day fills the disk. SEC scrape cache will keep growing.
- **Hypothesis**: SEC + web caches are not pruned. The README mentions the http_cache pattern (~13 GB on local C:); the VPS appears to have inherited a similar pattern with no eviction.
- **Fix size**: small. Add a cache-prune timer (LRU > N days), or move cache to a larger volume.

### F9 — Daily reconciler logs a 403 Forbidden for today's SEC index
- **Severity**: medium
- **Unit affected**: `rexfinhub-reconciler.service`
- **Symptom**: 08:00 reconciler today fetched 5/8 successfully but got `403 Client Error: Forbidden` for `2026-05-11/form.20260511.idx`. Same warning likely on every weekday.
- **Evidence**: `2026-05-11 08:00:24,907 WARNING __main__: reconcile_day: fetch failed for 2026-05-11: 403 Client Error: Forbidden`. Service still exits 0.
- **Blast radius**: Today's filings index is not reconciled at 08:00 (08:00 ET = 12:00 UTC; SEC publishes the daily index later in the morning). On weekdays this means there's a window where the reconciler's "today" check is always failing. Ryu may believe the reconciler caught everything when it hasn't.
- **Hypothesis**: SEC daily-index is not published at 12:00 UTC. The reconciler should fetch yesterday's, not today's, OR the timer should fire later (e.g. 14:00 ET).
- **Fix size**: trivial (shift timer or change `lookback` semantics in `reconciler.py`).

### F10 — `ExecStartPre=-git pull --ff-only` swallows pull failures — pipeline runs with stale code
- **Severity**: low
- **Unit affected**: `rexfinhub-daily.service`, `rexfinhub-preflight.service`, `rexfinhub-classification-sweep.service`, `rexfinhub-13f-quarterly.service`
- **Symptom**: The leading `-` on `ExecStartPre=-/usr/bin/git -C ... pull --ff-only origin main` means systemd ignores the exit code. If local has divergence, `--ff-only` will fail, the pull will not happen, and the service runs with the previous code.
- **Evidence**: Comment in `rexfinhub-daily.service` lines 12–17 acknowledges this trade-off ("we'd rather run with current code than skip the day"). Today's preflight pull succeeded (large fast-forward visible in journal). No evidence this has bitten in the last 7 days, but it's a latent risk.
- **Blast radius**: If a developer pushes a fix and a contemporaneous local commit on the VPS creates divergence (e.g. someone edits a file via SSH), the pull is silently skipped and the fix is never deployed. Detection requires reading the journal for "Already up to date" vs "fatal: ...".
- **Hypothesis**: This is intentional — but it lacks any alerting for "pull failed N days in a row".
- **Fix size**: trivial (log a `logger -t rexfinhub` line on pull failure, hook into existing alert plumbing).

### F11 — Atom watcher + single-filing worker uptime 3w 5d, both find zero new filings — possibly silently dead
- **Severity**: medium
- **Units affected**: `rexfinhub-atom-watcher.service`, `rexfinhub-single-filing-worker.service`
- **Symptom**: Both services have been running since 2026-04-14 (3 weeks 5 days). Latest cycles: atom watcher cycle 38539 → `queried=4 fetched=4 parsed=141 new=0`; single-filing worker cycle 77058 → `processed=0 ok=0 failed=0`. The numbers are stable and `new=0` is consistent across all visible cycles.
- **Evidence**: Live journals (above). The atom watcher polls every 60s and finds nothing new in any cycle observed. Single-filing-worker has no work because there's nothing in its queue.
- **Blast radius**: If the atom feeds shifted URLs or the parser broke after a SEC change, we'd never know — the symptom is "always 0 new" which is the same as "everything is current". Given pipeline_runs shows the SEC pipeline regularly catching new filings via the polling scrape (e.g. 26 new today, 27 on 5/8), the atom watcher's "near-realtime" tier seems to be missing them.
- **Hypothesis**: The atom watcher's `queried=4` suggests only 4 atom feeds are configured, possibly too narrow a CIK list. The `parsed=141` is constant across cycles, suggesting it's parsing the same backlog and seeing no change.
- **Fix size**: small (instrument the watcher's CIK list, compare against what the polling scrape catches).

### F12 — Classification sweep AND preflight both treat audit-fail as service-fail
- **Severity**: medium
- **Units affected**: `rexfinhub-classification-sweep.service`, `rexfinhub-preflight.service`
- **Symptom**: Both scripts return exit 1 when audits fail. Systemd marks the unit `failed`, journalctl decorates with `failed (Result: exit-code)`. This is correct semantically but creates noise — every weekday morning the dashboard shows two failed services as "operational alerts" when the situation is actually steady-state-known-issue.
- **Evidence**: classification-sweep at 09:00 today: `Status: fail; Detail: 259 unclassified new launches, 8 CC funds missing CC attributes` → exit 1. Same on 5/8 with similar gap counts.
- **Blast radius**: Alert fatigue. If a real failure happens (e.g. script syntax error, DB locked), it looks the same as "we have classification work queued".
- **Hypothesis**: These should distinguish between "audits ran and found work" (exit 0, but alert via email) vs "audit infrastructure broken" (exit 1).
- **Fix size**: small (return 0 from the script body when audits ran successfully even if findings exist; reserve exit 1 for genuinely broken script state).

### F13 — `force_scrape_recent.py` and other scripts in `scripts/` have no scheduler
- **Severity**: low (informational)
- **Unit affected**: none
- **Symptom**: `scripts/` contains 50+ Python files including audit_*, apply_*, derive_*, generate_*, force_scrape_recent, prebake_reports, fetch_13f, classify_remaining_funds, backfill_unclassified_funds, etc. Most are run-once tools but several look like ongoing maintenance:
  - `audit_attribute_completeness.py` — duplicate of preflight audit?
  - `audit_cross_ref_integrity.py` — should probably run regularly
  - `apply_classification_sweep.py` — recently committed (5/11), likely needs to run after sweeps
  - `prebake_reports.py` — referenced as "[8.5/12] Pre-baking reports" inside run_daily.py but also exists as a script
- **Blast radius**: Unknown without per-script analysis. Some of these may be supposed to be daemonized but aren't, leading to silent gaps.
- **Hypothesis**: organic growth — scripts get written, run manually a few times, never get a timer.
- **Fix size**: medium (Stage 2: classify each script as `cli-tool / one-shot / should-be-scheduled`).

## Disk + persistence state

```
/dev/sda1        38G   31G  5.0G  87% /

/home/jarvis/rexfinhub/cache       11G   (sec=5.2G, web=5.0G, submissions=497M)
/home/jarvis/rexfinhub/data       2.7G   (etp_tracker.db = 624M; live_feed.db = 20K)
/home/jarvis/rexfinhub/logs        29M
/home/jarvis/rexfinhub/outputs    370M
/home/jarvis/rexfinhub/reports    5.0M

/home/jarvis/rexfinhub/data/backups/                       EMPTY (db-backup broken)
/home/jarvis/rexfinhub/data/.preflight_result.json    1234B  May 11 18:30  status=fail
/home/jarvis/rexfinhub/data/.preflight_token           122B  May 11 18:30  valid 4h
/home/jarvis/rexfinhub/data/.preflight_decision.json     —   does not exist
/home/jarvis/rexfinhub/data/.send_log.json             619B  Apr 27 21:58 (last successful timer-driven send)
/home/jarvis/rexfinhub/data/.send_audit.json         16300B  May  5 23:28
/home/jarvis/rexfinhub/config/.send_enabled              6B  May  8 20:00 = "false"
```

`pipeline_runs` table — last 30 entries summary:
- 153 entries total, IDs 124–153.
- Earliest: 2026-04-30 16:21 (1078 filings). Latest: 2026-05-11 20:04 (26 filings).
- Pattern: most weekday days have 4 runs (00:03, 12:04, 16:00, 20:03). Run #152 (today 16:00) shows 0 filings + 18s duration — that's the run that timed out before producing useful work, but the DB row was written at 16:00:34 (before the timeout) so it shows `status='completed'` despite the surrounding service dying at 18:00.
- **Discrepancy**: `pipeline_runs.triggered_by = 'manual'` for **every single one of the 30 most recent runs**, including the timer-driven ones. The "triggered_by" field is not being populated correctly from systemd context — every run is logged as manual. This will mislead any forensics that filters on `triggered_by`.
- All 153 entries `status='completed'`, `error_message=NULL` — **no failure entries are being recorded**. The pipeline_runs table is recording successful pipeline-step completions, not service-level failures. The SEC scrape that died tonight is not represented as a failed row; the scrape part finished (16:00:34), the upload part died but doesn't write to this table.

## Hunting question answers

1. **Mon..Fri vs Daily vs Weekly**: 9 of 13 timers are weekdays-only (sec-scrape, daily, preflight, classification-sweep, bloomberg, gate-open, gate-close). 2 are daily (db-backup, reconciler). 1 is weekly Sunday (bulk-sync). 1 is daily including weekends (cboe). 1 is quarterly (13f, not installed). Weekend gap is intentional and correct (no SEC filings post on weekends).
2. **TimeoutStartSec adequacy**: sec-scrape=7200 (**too short — see F1**); daily=3600 (Fri took 984s = 16.4m, today's manual run took ~3m, so adequate but no headroom); preflight=900 (adequate); classification-sweep=300 (adequate); bloomberg=600 (adequate, 5min observed); cboe=21600/6h (adequate when working); 13f=14400/4h (untested on VPS); db-backup=300 (n/a — broken); reconciler=600 (adequate); bulk-sync=1800 (adequate).
3. **Silent successes**: classification-sweep on 5/11 — did real work but exits 1 because audit "found gaps" (legitimate but noisy). Bloomberg sync — exits 0 with "Global ETP sync skipped" + "CSV export skipped" warnings on EVERY run (F7).
4. **Chain dependencies**: bloomberg-chain isn't installed (F6). preflight has `After=rexfinhub-bloomberg.service` but the bloomberg unit is the non-chain version. daily.service does not depend on preflight — it just fires at 19:30 regardless of preflight result. send_all uses `--use-decision` to enforce the dependency at the data layer (read `.preflight_decision.json`). This works correctly to fail-closed but it has no fail-open path → F2.
5. **Services dead >7 days**: 13f, parquet-rebuild, bloomberg-chain (F6 — not installed). bulk-sync ran 1.5 days ago (Sunday weekly, healthy). cboe has been functionally dead 9 days (F4) although the unit fires.
6. **Services without timers that should be timers**: bloomberg-chain (F6); the prebake_reports.py script may be a candidate (Stage 2).
7. **Daily 3600s adequacy**: yes for normal runs. Today's "manual" run #153 (re-run after timeout?) only took 173s. But: the daily timer with `--skip-sec` does *not* compress + upload a 624 MB DB (that's the SEC pipeline's job). So daily.service stays well under budget. The risk is in sec-scrape, not daily.
8. **Cron**: `/etc/cron.d/` has only OS jobs (`e2scrub_all`, `sysstat`). `crontab -l` for jarvis: `no crontab`. **No rexfinhub cron jobs**. All scheduling is systemd timers.
9. **Unscheduled scripts**: see F13.
10. **`-git pull --ff-only`**: see F10. Today's preflight pull succeeded with a fast-forward across 31 files — works correctly. The `-` prefix is a latent risk only.

## Surfaces inspected

- All 16 unit files in `C:/Projects/rexfinhub/deploy/systemd/` (read).
- All 25 installed unit files on VPS (`systemctl list-unit-files 'rexfinhub-*'`).
- Service status + last-7-days journal for: sec-scrape, daily, preflight, classification-sweep, bloomberg, cboe, reconciler, bulk-sync, db-backup, gate-open, gate-close, atom-watcher, single-filing-worker, api.
- State files: `.preflight_result.json`, `.preflight_token`, `.preflight_decision.json` (absent), `.send_log.json`, `.send_audit.json`, `.send_enabled`.
- DB tables: `pipeline_runs` (last 30 rows), `cboe_scan_runs` (last 10 rows), schema enumeration.
- Disk usage: root, cache subdirs, data subdirs, db file sizes.
- Cron: `/etc/cron.d/`, jarvis crontab.
- Timezone: confirmed America/New_York EDT.
- VPS HEAD: `d45252c` (today's commits pulled successfully).
- `scripts/` directory listing (50+ files).
- `preflight_check.py` source (head + tail) — confirmed exit-1-on-fail logic.
- Backups dir (empty).

## Surfaces NOT inspected (left for later stages)

- `/opt/render` — couldn't `df -h` (no such mount on VPS; Render is a different host entirely — confused by the original prompt; the upload target is rex-etp-tracker.onrender.com via API, not a mounted disk).
- `webapp/services/market_sync.py` and `webapp/services/graph_files.py` — sources of the bloomberg.service silent errors (F7) — needs source audit.
- The actual contents of pipeline_runs schema vs. what scripts log (could there be an `automation_runs` table or similar that's the real audit log?).
- Render-side state: have the recent uploads actually landed? Last upload mtime per the `/api/v1/db/upload` endpoint? Couldn't check from VPS.
- Alert plumbing: how `send_critical_alert` works, whether F4 (cboe failures) and F5 (no backups) would surface there.
- `prebake_reports.py` — does it duplicate `run_daily.py` step 8.5?
- Whether the dashboard "click GO" button is even reachable / functional given the past 14 days of zero clicks.
- `scripts/run_all_pipelines.py` — the actual code path that's hanging at `Compressing...` for 1h40m before TERM (F1 root cause needs source dive).
- The 13F unit's expected behavior on first install — would `Persistent=true` cause it to immediately backfill 5/20 when installed after that date?

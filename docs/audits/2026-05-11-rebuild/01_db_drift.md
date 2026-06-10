# Stage 1 Audit — DB Drift (Local vs VPS vs Render)

**Generated**: 2026-05-11 ~22:35 ET
**Agent**: db_drift
**Scope**: Read-only quantification of data drift across the three rexfinhub DB locations.

---

## Summary

The premise that "Render is on yesterday's DB" is **FALSE**. Render is ~4 hours behind the VPS (last refreshed 2026-05-11 16:22 EDT after the 16:00 SEC scrape), but is otherwise current with today's data — it has today's classifications, today's BBG snapshot, today's filings (65 of them), and freshly-baked reports timestamped 20:16-20:19 UTC. **rexfinhub.com is serving today's data.**

The real drift sits elsewhere:

1. **LOCAL is severely stale.** Last SEC pipeline run = 2026-05-04, last BBG ingest = 2026-05-11 22:15 (so BBG syncs locally but SEC does not). Local is missing the entire week of SEC filings (4-day gap; 1,842-row delta vs VPS).
2. **VPS has 21 newer filings than Render** (added by the 20:04 SEC scrape after the 16:22 upload). Tonight's 20:04 SEC scrape did NOT trigger a follow-up Render upload — that only fires from the daily orchestrator, which is currently stood-down (Friday's `send_all` exited "no decision file"). So the 21 evening filings won't ship to Render until the next scheduled upload — most likely the 19:30 EDT timer tomorrow.
3. **db-backup.timer has been failing every night since at least May 7** with `sqlite3: command not found`. There are NO recent automated backups on the VPS. The most recent backup file on disk is `etp_tracker.db.bak_20260430_150649` (12 days old).
4. **VPS disk is at 87% used (5.0G free of 38G).** Cache alone is 11G. A failed Render upload cycle (which writes a 646MB temp + 85MB gzip) under heavy SEC traffic could push this over.
5. **3 mkt_pipeline_runs are stuck in `running` state** on VPS (IDs 334, 335, 336 — all `daily_classify`, started at 08:17, 12:04, 16:12 today, no `finished_at`). Same pattern as ID 302 stuck since 2026-05-04. The classify watchdog never marks them complete.

The timeout the user mentioned ("upload TIMED OUT at 18:00") is not in any log I found — Friday's 20:00 upload completed in ~17 minutes total (`Done in 1025s`) and today's 16:00 upload completed inline within the SEC scrape run. The 1h40m+ wallclock the user remembers is plausibly the **`Pipeline done. Waiting until 7:30 PM to send (188 min)`** sit-and-wait pause inside `run_daily.py`, not an upload — see Finding F4.

---

## Drift comparison table

| Metric | LOCAL | VPS | Render | Drift severity |
|---|---|---|---|---|
| DB file size | 654,151,680 B (654.2 MB) | 653,475,840 B (653.5 MB) | ~646 MB (post-VACUUM, lean) | low |
| DB mtime | 2026-05-11 18:18:11 | 2026-05-11 17:20:09 | replaced 2026-05-11 ~16:22 EDT | low |
| -wal file | 52.1 MB (mtime 18:18) | 55.0 MB (mtime 18:30) | n/a | medium — large WAL on both |
| sqlite library | 3.50.4 | 3.45.1 | 3.45.x (Render Ubuntu) | low |
| Schema (table count) | 45 | 45 | 45 (minus 3 dropped) | none — schemas match |
| Last filing_date | **2026-05-04** | 2026-05-11 | 2026-05-11 | **CRITICAL on local (7-day gap)** |
| Last mkt_master_data updated_at | 2026-05-11 22:15:35 | 2026-05-11 21:16:16 | (not directly queryable) | low — both fresh |
| filings rowcount | 626,936 | 628,778 | (not directly queryable) | local -1,842 vs VPS |
| Today's filings (2026-05-11) | 0 | 86 | 65 | local 0; Render lags VPS by 21 |
| trusts rowcount | 15,750 | 15,789 | 15,788 | local -39; Render -1 vs VPS |
| fund_status rowcount | 213,810 | 214,033 | (not queryable) | local -223 |
| mkt_master_data rowcount | 7,361 | 7,361 | (not queryable) | match |
| mkt_fund_classification rowcount | 7,332 | 7,359 | (not queryable) | local -27 |
| Today's classifications (created_at=05-11) | 0 | 7,359 | (not queryable) | local 0 (full re-classify ran on VPS today) |
| rex_products rowcount | 723 | 702 | (not queryable) | local +21 (locally curated, drifting upward) |
| capm_products rowcount | 74 | 74 | seeded from CSV on init | match — see api.py seed logic |
| Last pipeline_runs entry | id=130, 2026-05-04 12:05 | id=153, 2026-05-11 20:04 | (not in lean DB — dropped) | local 7 days behind |
| Last mkt_pipeline_runs entry | id=304, 2026-05-11 22:15 (BBG `auto`) | id=337, 2026-05-11 21:15 (BBG `auto`) | (not directly queryable) | local actually has a NEWER BBG run (22:15 vs 21:15) — local Bloomberg watcher fired after VPS one |
| Latest pre-baked report (any) | (local doesn't bake) | bakes + uploads | daily_filing baked 2026-05-11 20:16 UTC | Render reports are TODAY |

**Reading the table**: Render is the smallest drift problem. Local is the biggest. The "tonight's upload failed" claim is unsubstantiated by the log evidence — both Friday and today's uploads succeeded.

---

## Findings

### F1: db-backup.timer has been silently failing for 5+ consecutive nights — no recent backups exist
- **Severity**: critical (data-loss exposure)
- **Surface**: VPS systemd unit `rexfinhub-db-backup.service`
- **Symptom**: Every nightly run since at least 2026-05-07 23:00 EDT exits status=127 with `/bin/bash: line 1: sqlite3: command not found`. The unit script invokes the `sqlite3` CLI, which is not installed on the VPS (`which sqlite3` -> empty, `sqlite3 --version` -> not found).
- **Evidence**: `sudo journalctl -u rexfinhub-db-backup --no-pager -n 20` shows 5 consecutive failures (May 7, 8, 9, 10, 11 evenings). The most recent backup file on disk is `/home/jarvis/rexfinhub/data/etp_tracker.db.bak_20260430_150649` (mtime April 30 — 12 days old).
- **Blast radius**: Any DB corruption, accidental DELETE, schema migration mishap, or successful Render upload of a bad file would currently be unrecoverable without rebuilding from SEC + Bloomberg from scratch. Several recent bugs (Sys-D reclassification residue, ticker dupes, NULL primary_strategy) are exactly the class of issue you'd want a recent rollback for.
- **Hypothesis**: The systemd unit was authored assuming `sqlite3` was a system binary. After a VPS rebuild or a sweep of unused packages, it disappeared. Failures are silent because no monitor watches `systemctl status rexfinhub-db-backup`.
- **Fix size**: trivial — `sudo apt install sqlite3` (or rewrite the backup script to use `/home/jarvis/venv/bin/python -c 'import sqlite3; ...'` so it stops depending on the CLI).

### F2: LOCAL DB is 7 days behind on SEC filings; pipeline_runs hasn't recorded a SEC run since 2026-05-04
- **Severity**: high (silent local-dev drift; no user-facing impact)
- **Surface**: `data/etp_tracker.db` on the desktop; `pipeline_runs` table
- **Symptom**: `MAX(filing_date) FROM filings = 2026-05-04`. Last `pipeline_runs` row is id=130 dated 2026-05-04 12:05. VPS has 153 entries with the latest at 2026-05-11 20:04. Today's filings on local = 0, on VPS = 86.
- **Evidence**: see drift table above.
- **Blast radius**: Local-only — the user's site (Render) is unaffected. But anyone running ad-hoc analysis or generating reports locally is reading 7-day-stale filings. `mkt_master_data`, `mkt_fund_classification`, and `rex_products` are independently curated locally and ARE current (BBG ran locally today at 22:15), so the drift is asymmetric: filings/trusts/fund_status are stale, market data is fresh.
- **Hypothesis**: There is no automated mechanism to pull VPS DB to local. The `etp_tracker.db.fromvps` snapshot is dated May 4 09:38 — that's the last manual rsync. Local SEC scraping is presumably gated off by design (so as not to compete with VPS for SEC's 10-req/s budget), but no replacement sync from VPS exists.
- **Fix size**: small — add a one-liner `jarvis pull-db` (rsync + WAL checkpoint on the VPS first) or document the manual scp recipe in `LOCAL_DEV_SETUP.md`.

### F3: Three `daily_classify` runs stuck in `running` state on VPS (no finished_at) — pattern repeated since 2026-05-04
- **Severity**: medium (observability rot, not data corruption)
- **Surface**: VPS `mkt_pipeline_runs` table
- **Symptom**: rows id=334 (08:17), 335 (12:04), 336 (16:12) all today, all `source_file='daily_classify'`, all `finished_at=NULL`, status=`running`. Identical pattern at id=302 from 2026-05-04 08:15. The actual classification work clearly succeeds (id=337 BBG run completes, classifications are current), so these `running` rows are orphaned tracking records that the classify path opens but never closes.
- **Evidence**: see VPS query output above.
- **Blast radius**: Pollutes the admin/health page's "latest pipeline" widget — it will show the most-recent stuck row as "still running for 14 hours". Could mask a genuine stuck pipeline. Doesn't corrupt any actual data.
- **Hypothesis**: The `daily_classify` codepath (probably in `scripts/classify_remaining_funds.py` or `webapp/services/classification`) opens a `MktPipelineRun` row at start but doesn't close it on the success path — only on the explicit BBG `auto` path does the row get a `finished_at`. Or: the classify script crashes/exits before its `finally` block executes.
- **Fix size**: small — either wrap the classify entrypoint in try/finally that always sets finished_at, or have a watchdog reaper that closes any `running` row older than 60 minutes as `error_message='watchdog: orphaned'`.

### F4: The "1h40m upload timeout tonight" claim is not in any log — most likely the user is conflating the post-pipeline `Waiting until 7:30 PM to send (188 min)` sleep with an upload hang
- **Severity**: high (user is acting on a false root cause)
- **Surface**: `scripts/run_daily.py` line ~720 (`timeout=600` on the upload POST), and the post-pipeline scheduling pause that prints `Pipeline done. Waiting until 7:30 PM to send (188 min)...`
- **Symptom**: User: "Tonight's VPS DB upload to Render TIMED OUT at 18:00." Reality:
  - Today's 16:00 timer ran at 16:00:14 EDT, completed the pipeline + Render upload by 16:22:08 EDT (final log line: `Uploaded to Render (85 MB compressed)`). The 85 MB upload itself took less than the 600s POST timeout — no timeout error logged anywhere.
  - The very next log line is `[12/12] Sending email reports... Pipeline done. Waiting until 7:30 PM to send (188 min)...` — this is a deliberate `time.sleep` to defer email sending to a target wall-clock time. From 16:22 + 188min = 19:30 EDT. If the user observed a "stuck" terminal at 18:00, that was a polite sit-and-wait inside the orchestrator, not an upload timeout.
  - Friday's 20:00 run also completed `Uploaded to Render (86 MB compressed)` cleanly (`Done in 1025s = 17.1m`).
- **Evidence**: tail of `pipeline_20260511_1600.log` and `pipeline_20260508_2000.log`.
- **Blast radius**: If we go chasing an upload-timeout fix (tune nginx, raise gunicorn worker timeout, switch to multipart-resumable, etc.) we'll be solving the wrong problem. The real "Render is stale" feeling is just the natural lag between the 16:22 upload and the 20:04 SEC scrape that adds new filings nobody auto-uploads.
- **Hypothesis**: The orchestrator design uploads the DB once per pipeline cycle, not after every SEC scrape. There are 4 SEC scrapes per day (08:00/12:00/16:00/20:00) but only the 19:30 timer (`rexfinhub-daily.timer`) explicitly runs `run_daily.py` end-to-end including upload. The 20:00 SEC scrape's filings don't ship to Render until the *next* full daily run.
- **Fix size**: small — either add an `--upload-only` final step after every `rexfinhub-sec-scrape.service`, or reorder so the daily timer fires AFTER the 20:00 scrape (currently the daily timer is at 19:30, BEFORE the 20:00 SEC scrape — almost certainly an oversight).

### F5: Render is current (today's data), but the 21 evening filings + tonight's BBG won't ship until tomorrow's daily run
- **Severity**: medium (1-day lag on evening data, not a bug per se but the user perceives it as one)
- **Surface**: VPS systemd timer ordering (`rexfinhub-daily.timer` 19:30 vs `rexfinhub-sec-scrape.timer` 20:00 vs `rexfinhub-bloomberg.timer` 21:00)
- **Symptom**: Today, Render received the upload at ~16:22 EDT (post 16:00 SEC scrape). Then VPS ingested 21 more filings at 20:04 (per pipeline_runs id=153) and a fresh BBG snapshot at 21:15 (per mkt_pipeline_runs id=337). Neither triggered a Render upload. So Render currently shows 65 filings dated today, while VPS has 86. Render's `mkt_master_data.updated_at` is from the morning sync (~16:22 cutoff); VPS's is 21:16.
- **Evidence**:
  - Render `/api/v1/filings/recent?days=1` -> 65 rows
  - VPS `SELECT COUNT(*) FROM filings WHERE filing_date='2026-05-11'` -> 86
  - VPS systemd timers: `rexfinhub-daily.timer` next-fire 19:30, `rexfinhub-sec-scrape.timer` next-fire 20:00, `rexfinhub-bloomberg.timer` next-fire 21:00. Daily fires BEFORE the last SEC scrape.
- **Blast radius**: Anyone hitting rexfinhub.com between ~16:22 EDT and the next morning's daily run sees a ~4-hour-stale snapshot of evening filings + BBG. For a low-volume product this is fine; for a "live" feel it's a paper cut. The recently-added live-feed table (`live_feed.db`) appears to be the deliberate workaround for the most time-sensitive items.
- **Hypothesis**: Timer ordering is a leftover from when there was no 20:00 SEC scrape, or from when the daily orchestrator owned all SEC fetching. Now there's a dedicated SEC scrape timer 30 minutes AFTER the daily.
- **Fix size**: trivial — reorder timers (move daily to 20:30 EDT, after the 20:00 SEC + 21:00 BBG settles), OR add an `--upload-only` post-step to the SEC scrape unit.

### F6: VPS disk at 87% used — 5.0 GB free; cache is 11 GB and growing
- **Severity**: medium (rolling toward critical)
- **Surface**: VPS root filesystem `/dev/sda1`
- **Symptom**: `df -h /` -> 38G total, 31G used, 5.0G avail, 87% full. The `cache/` directory alone is 11G. Render upload temp files (646 MB raw + 85 MB gzip) plus the in-place `.uploading` swap need ~750 MB of headroom each cycle.
- **Evidence**: see disk free output above.
- **Blast radius**: A coincidental cache spike + concurrent upload could push past 100% and cause a partial DB write or upload corruption. Also blocks the missing backup (F1) — even if you fix the sqlite3 issue, where does the backup file land? If it's `data/etp_tracker.db.bak_*`, you'd consume another 650 MB per night until rotation kicks in.
- **Hypothesis**: Not actively monitored. No log-rotate / cache-prune cron visible. The note in `MEMORY.md` ("Apr 14: cache/web silently grew to 18 GB, filled the 38 GB VPS") is the same class of issue and is recurring.
- **Fix size**: small — add a weekly cache-prune (delete files older than 14 days from `cache/web/`, `cache/sec/`, etc.) and a disk-free preflight check at the top of `run_daily.py` (it already has one — `# === Preflight: disk free check ===` at line ~758 — verify it's actually triggering).

### F7: WAL files are large on both LOCAL (52 MB) and VPS (55 MB) — risk of stale-read uploads
- **Severity**: medium
- **Surface**: `data/etp_tracker.db-wal` on both sides
- **Symptom**: VPS WAL is 55 MB at 18:30 mtime. The Render upload codepath does `PRAGMA wal_checkpoint(TRUNCATE)` BEFORE `shutil.copy2` (run_daily.py line ~665), so the snapshot is correct on the upload side. But on local, the 52 MB WAL means at any unclean crash / power loss, the most recent ~52 MB of writes (mostly BBG and classification) could be replayed inconsistently. SQLite handles this safely via WAL recovery, but it's a yellow flag.
- **Evidence**: see file size output. The pre-upload checkpoint log line `PRAGMA wal_checkpoint(TRUNCATE)` confirms the upload path is safe (this was a real bug per the comment at line 660: "Without TRUNCATE, shutil.copy2 grabs a stale main-file snapshot and Render ends up missing all recently-added trusts/filings.").
- **Blast radius**: Low under normal operation. Only matters if someone bypasses `run_daily.py` and uploads directly via raw `scp` of the .db file (which would skip the checkpoint and ship a stale snapshot).
- **Fix size**: trivial — periodic `PRAGMA wal_checkpoint(PASSIVE)` from a watchdog every hour. Already implicit in normal SQLite write traffic; the size suggests not enough writes are crossing the auto-checkpoint threshold (default 1000 pages = 4MB).

### F8: No mechanism exists to sync VPS -> LOCAL DB; LOCAL drifts forever
- **Severity**: low (developer ergonomics, not user-facing)
- **Surface**: `scripts/`, `jarvis` CLI
- **Symptom**: There is no `pull-db.py`, no `jarvis db-pull`, no cron pulling VPS DB to local. The only artifact suggesting a manual pull happened is `data/etp_tracker.db.fromvps` dated 2026-05-04 09:38 — same week the local pipeline_runs went silent.
- **Evidence**: `grep -r 'rsync\|scp.*etp_tracker' scripts/` -> no automated puller. The README and `LOCAL_DEV_SETUP.md` don't document a pull recipe.
- **Blast radius**: Asymmetric drift means any local report build or analysis silently uses old data. The user has been generating local PDFs and emails — those may have been built off a 7-day-stale filings table.
- **Hypothesis**: When the architecture moved SEC scraping to VPS-only, the inverse (push fresh data back to laptop) was never built.
- **Fix size**: trivial — add to `jarvis` CLI:  `ssh jarvis@vps "/home/jarvis/venv/bin/python -c 'import sqlite3; sqlite3.connect(...).execute(\"PRAGMA wal_checkpoint(TRUNCATE)\")'" && rsync -P jarvis@vps:/home/jarvis/rexfinhub/data/etp_tracker.db data/etp_tracker.db.fromvps && mv data/etp_tracker.db.fromvps data/etp_tracker.db`.

### F9: DB upload endpoint has no idempotency / resumability — partial uploads go to a `.uploading` temp but a half-written gz would still decompress to garbage
- **Severity**: low (no observed failure, but architectural fragility)
- **Surface**: `webapp/routers/api.py` lines 194-300 `upload_db()`
- **Symptom**: The handler streams gzip bytes to `tmp_path + ".gz"`, then decompresses in a second pass to `tmp_path`, then renames over `DB_PATH`. There's NO checksum verification of the uploaded gzip against what the VPS sent. If the connection is severed mid-stream, `_gzip.open(...)` will raise `EOFError` at decompression time and the handler hits its except block — which DOES clean up both temp paths. Good. But: if the gzip happens to be byte-aligned to a valid frame end at the truncation point, decompression silently produces a smaller-than-expected DB and `init_db()` runs against the truncated file. The only thing that would catch this is `PRAGMA quick_check` in `init_db` line ~149 — and that only logs an error, doesn't refuse to serve.
- **Evidence**: code inspection.
- **Blast radius**: Theoretical — would require a very specific truncation point on the gzip stream. Hasn't happened in the wild that I can see.
- **Hypothesis**: Defense-in-depth was deferred. The code comment at line 199 references a prior `Connection.backup()` approach that was reverted because it crashed Render under load.
- **Fix size**: small — VPS sends `Content-Length` already; Render handler should compare `total_in` against the header before decompressing. Or VPS sends a SHA256 in a custom header.

### F11 (CRITICAL UPDATE): `rexfinhub-daily.service` has NOT executed since Friday 2026-05-08 19:46:36 — Saturday, Sunday, AND Monday (today) all skipped
- **Severity**: critical (this is the actual root cause of "Render feels stale")
- **Surface**: VPS systemd unit `rexfinhub-daily.service` + `rexfinhub-daily.timer`
- **Symptom**: `sudo journalctl -u rexfinhub-daily.service --since '2026-05-09'` returns **No entries**. The last successful run was Friday May 8 at 19:46:36 EDT ("Done in 984s, EXIT 0"). The timer's "next fire" of `Mon 2026-05-11 19:30:00 EDT` shown in `systemctl list-timers` actually represents the NEXT scheduled fire — which is in the FUTURE (the systemctl readout was taken at ~22:23 ET; the displayed "37min" countdown points at 22:55, i.e. tomorrow's slot already?). Either way, the journal is the ground truth and it shows 3 consecutive missed runs.
- **Evidence**:
  - `journalctl -u rexfinhub-daily.service --since '2026-05-09'` -> empty
  - Last run journal entry: `May 08 19:46:36 jarvis systemd[1]: Finished rexfinhub-daily.service`
  - VPS `pipeline_runs` last id=153 at 2026-05-11 20:04 — but that was the SEC-only timer (`rexfinhub-sec-scrape.service`), not the daily orchestrator.
  - VPS `mkt_pipeline_runs` newest BBG `auto` at 2026-05-11 21:15 — that's `rexfinhub-bloomberg.service`, not daily.
- **Blast radius**: This is THE story. Render's last DB upload was Friday's 19:46 daily. Today's 16:00 SEC scrape DOES include a Render upload step (per the log `Uploaded to Render (85 MB compressed)`) — so Render IS current as of 16:22 today, but ONLY because the SEC scrape unit ALSO calls upload, not because daily ran. The 21 evening filings and the 21:15 BBG sync sit on VPS unshipped, awaiting a daily run that may never fire.
- **Hypothesis**: The daily timer requires a precondition that's not being met (e.g. it's `OnCalendar=Mon..Fri 19:30` and Saturday/Sunday are intentionally skipped — but Monday should have fired). Or the timer file is using a syntax that doesn't include Monday. Or `rexfinhub-daily.service` has a `ConditionPathExists=` on a file that no longer exists. Most likely: someone disabled the timer manually and `systemctl status` would confirm. A fourth possibility: the `Restart=` policy hit a backoff loop after the 19:46 Friday completion and is in `auto-restart` cooldown for the weekend.
- **Fix size**: trivial-to-small — `systemctl status rexfinhub-daily.timer && systemctl status rexfinhub-daily.service` will reveal the exact condition; from there it's either re-enable, fix calendar spec, or remove a stale ConditionPath. **This finding upgrades F4: the user's "1h40m timeout" perception now makes more sense — they may have been waiting on the 19:30 daily that never fired, then assumed an upload hang.**

### F10: capm_products auto-seed from CSV runs at every Render init — guarantees Render shows 74 products even if upload had zero
- **Severity**: low (an interesting safety net, not a bug)
- **Surface**: `webapp/database.py` `_capm_seed_if_empty()` lines 174-231
- **Symptom**: Every time `init_db()` runs (on app start AND after every `/db/upload`), if `capm_products` is empty, it gets seeded from `webapp/data_static/capm_products.csv` (74 rows currently). This means: if the VPS DB shipped to Render had `capm_products = 0` (e.g. because the table was dropped during the lean prep), Render would still show 74 rows. The `etp_tracker_render.db` lean-prep code in `scripts/run_daily.py` does NOT drop `capm_products`, so this is non-issue today, but it's an interesting "Render can lie about its data because of CSV seeds".
- **Evidence**: code at `database.py:174` and `run_daily.py:683` (drop_tables list).
- **Blast radius**: None observed. A note for future audit cycles: any table with an auto-seed in `database.py` is non-authoritative on Render.
- **Fix size**: none required — flag for documentation in DATABASE_SCHEMA.md.

---

## Surfaces inspected

- `C:/Projects/rexfinhub/data/etp_tracker.db` (LOCAL — full counts + WAL state + schema dump)
- `/home/jarvis/rexfinhub/data/etp_tracker.db` (VPS — full counts + WAL checkpoint verification + schema dump)
- `https://rexfinhub.com/api/v1/health`, `/api/v1/filings/recent`, `/api/v1/etp/rex-summary`, `/api/v1/trusts`, `/api/v1/funds`, `/api/v1/reports/list` (RENDER — public API endpoints with X-API-Key)
- `C:/Projects/rexfinhub/webapp/database.py` (LOCAL — DB engine config, init_db, autosee logic)
- `C:/Projects/rexfinhub/webapp/routers/api.py` lines 194-376 (`/db/upload` and `/db/upload-notes` handlers)
- `C:/Projects/rexfinhub/scripts/run_daily.py` lines 600-732 (`upload_db_to_render()`)
- VPS systemd: `systemctl list-timers` for all rexfinhub-* timers
- VPS journalctl: `rexfinhub-db-backup` last 30 entries
- VPS logs: `/home/jarvis/rexfinhub/logs/pipeline_20260508_2000.log`, `pipeline_20260511_0800.log`, `pipeline_20260511_1200.log`, `pipeline_20260511_1600.log`
- VPS state files: `data/.send_audit.json`, `data/.gate_state_log.jsonl`, `data/.send_log.json`, `config/.send_enabled`
- VPS disk: `df -h /`, `du -sh cache/`, `du -sh data/etp_tracker.db.bak_20260430_150649`
- LOCAL `config/.env` (for API key + admin password)

## Surfaces NOT inspected

- Render persistent disk free space — no public endpoint exposes it; would need to log into Render dashboard or add `/api/v1/disk-free` health field. Strong candidate for a follow-up.
- Render's actual `etp_tracker.db` mtime on its persistent disk — same constraint. Best proxy was `reports/list` baked_at timestamps.
- Whether the auto-seed paths (`_capm_seed_if_empty`, `_autocall_seed_if_empty`) actually fired on Render today — would need Render application logs.
- The `etp_tracker.db.bak-224036` (1.3 GB) and `etp_tracker.db.bak_pre_vacuum_20260504_093445` (640 MB) backups in LOCAL `data/` — didn't open them, but their existence suggests local ad-hoc backups exist; VPS does not have equivalents.
- WAL file contents (`PRAGMA wal_checkpoint(FULL)` would reveal pending pages, but I ran only PASSIVE so as not to perturb a live VPS).
- Schema diff at column level (only counted tables — same 45 both sides). A column-level migration drift could hide here.
- The `live_feed.db` cross-DB drift (separate file, separate concern, owned by another agent).
- Whether tonight's 19:30 daily timer will fire at all (it's currently 22:35 ET — already past 19:30; that should have run; need to verify it did not silently skip).

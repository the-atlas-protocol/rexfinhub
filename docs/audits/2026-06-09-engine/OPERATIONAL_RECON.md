# Engine Session — Operational Recon (inline findings)

> Captured 2026-06-09 14:10–14:30 EDT, live VPS + D: + Render probing. Complements the
> 9-subsystem workflow audit (`rexfinhub-engine-audit`, run wf_a72c2fab-8bd) whose findings
> land in MASTER_AUDIT.md. Status: raw evidence, verified live.

## A. Scheduler ground truth (VPS)

12 systemd timers + 7 crontab entries. ExecStart map:

| Unit | Schedule | Runs | State |
|---|---|---|---|
| rexfinhub-fresh-poller | 15min | scripts/poll_fresh_filings.py | OK |
| rexfinhub-sec-scrape | 4-hourly | scripts/run_all_pipelines.py --skip-email --skip-market, Post: sync_rex_products --apply | **FAILED** |
| rexfinhub-bloomberg(-chain) | 17:15 | SharePoint pull + market_sync (inline `python -c`) | OK |
| rexfinhub-gate-close | 20:00 | set_flag send_enabled=False | OK |
| rexfinhub-db-backup | 23:00 | sqlite .backup + 7-day retention | OK |
| rexfinhub-cboe | 03:00 | refresh_cboe_known_active + run_cboe_scan --tier full | **FAILED** (known: Cloudflare WAF) |
| rexfinhub-morning-triage | 08:00 | run_assertions.py, Post: morning_triage_email.py | **FAILED** (see C) |
| rexfinhub-reconciler | 08:00 | `python -m etp_tracker.reconciler` (NOT status_reconciler!) | OK |
| rexfinhub-classification-sweep | 09:00 | Pre: git pull --ff-only; classification_sweep.py | **FAILED** (see D) |
| rexfinhub-parquet-rebuild | Fri 06:00 | bash chain (rebuilds analysis parquets) | OK |
| rexfinhub-grade-recommendations | Sun 23:00 | scripts.grade_recommendations --stats | OK but **double-scheduled** |
| rexfinhub-13f-quarterly | quarterly | Pre: git pull; run_13f.py backfill | OK |

Crontab: pipeline_summary 20:15 wd (**DEAD — see B**), audit_duplicate_tickers 02:35,
li_engine run_v1 22:30 wd, grade_recommendations Sun 23:00 (**duplicate of timer**),
weekly_file_launch Mon 07:00, weekly_system_report Mon 07:05, disk-hygiene crons (15-min
`*_pre_*.db` delete in data/backups only; 04:00 keep-last-10).

Disabled-but-present units: gate-open, preflight, intraday-refresh, daily, bulk-sync (static, untimered).

## B. Dead cron — pipeline_summary (failing nightly since 2026-05-26)

`scripts/pipeline_summary.py` renamed to `.PAUSED-2026-05-26` on the VPS; crontab entry
left active → `[Errno 2] No such file or directory` every weekday 20:15 for 2 weeks.
Local main repo has the deletion staged uncommitted (`D scripts/pipeline_summary.py`).
**Fix:** comment out the crontab line (blocked by permission classifier — needs Ryu or rule).

## C. Morning triage — watchdog goes silent on failure (design flaw)

- run_assertions.py exits non-zero when any assertion FAILs → systemd marks unit failed →
  `ExecStartPost=morning_triage_email.py` (no `-` prefix) **does not run** → no email exactly
  when there is something to report.
- Today's 08:00 FAIL: `status_cached_matches_history` 13 rows. **Self-healed at 12:18 EDT**
  when the pre-compaction session's reconciler --apply landed (242 status_history rows,
  set_by=reconciler, created 16:18 UTC). Verified with the assertion's exact SQL at 14:25: **0 drift**.
  Tomorrow's run passes. No data fix needed.
- **Fix (slice):** `ExecStartPost=-...` or send the email from inside run_assertions
  regardless of pass/fail.

## D. classification-sweep — warn≠fail conflation + forgotten maintenance window

- Sweep RAN fine and SENT its summary; exits non-zero on `warn` → systemd "failed". Same
  status/exit-code conflation as C.
- Content: **MAINTENANCE WINDOW ACTIVE since 2026-05-11 21:04** (`data/.preflight_maintenance`,
  0-byte flag, created during the rebuild and forgotten). Strict gating suppressed for a month.
  Backlog behind it: **37 unclassified new launches, 20 NULL issuer_display, 7 CC missing attrs = 64 gaps**.
- **Fix order matters:** classify the backlog FIRST, then remove the flag — removing it now
  would brick preflight on send days.

## E. Render — screener cache stale ~2 days (503), root cause identified

- `POST /api/v1/uploads/screener-cache` → 503 on every 4-hourly run since 6/8 16:14.
- Only one 503 in that route: `verify_render_upload_token` raises 503 when
  `RENDER_UPLOAD_TOKEN` resolves empty (webapp/routers/api.py:758-759). Token loads from env
  var OR gitignored `config/.env` — a Render deploy resets the disk copy. origin/main moved
  6/8 20:02 (auto-deploy). Parquet uploads (different auth) succeed → only this token is missing.
- **Fix (Ryu, Render dashboard):** set env var `RENDER_UPLOAD_TOKEN` = value from VPS
  `config/.env`. Permanent: move all M2M tokens to Render env-group, never disk.
- Also: 12:00 run's 791MB DB upload appears to have died mid-compress (process gone, no
  completion log). 16:00 run will retry; watch it.

## F. Version-control split-brain (CRITICAL for engine)

- VPS HEAD == origin/main (ab9271b 6/8 20:02) but **60 uncommitted changes**: 29 modified
  tracked files (all scp-deployed session work) + ~30 untracked.
- **Untracked PRODUCTION code (in cron, not in git):** `screener/li_engine/run_v1.py`
  (nightly engine), `weekly_file_launch.py`, `weekly_system_report.py` (Monday crons),
  `weekly_action_theme.py`, `system_report_v2.py`. VPS loss = code loss.
- trex_combined_v2..v8 untracked version sprawl; `.bak` files in webapp/services;
  15 daily `classification_conflicts_*.csv` accumulating in docs/ with no consumer.
- **Interaction landmine:** classification-sweep + 13f timers do `git pull --ff-only` as
  ExecStartPre (failure-tolerated). Next push to main (e.g. the pending branch merge) makes
  pull fail silently on the dirty tree → VPS code freezes while git history moves on.
- **Fix (slice):** commit untracked production code to a branch from the VPS, reconcile with
  the local branch (`worktree-fix-rex-family-2026-06-08`, 2 commits ahead), THEN merge to main.

## G. Storage / disk

- VPS data/: ~4GB loose `etp_tracker.pre_*` + `etp_tracker_render.*` staging DBs in `data/`
  (cleanup cron only sweeps `data/backups/`); 2 orphaned Render staging DBs + .gz (the
  May disk-full pattern recurring). Disk currently 25% — not urgent, but the leak is structural.
  **No-deletions rule: list compiled for Ryu approval, nothing deleted.**
- VPS repo top-level clutter: `temp_cookies.txt` (cookie in repo root!), stray `rexfinhub.db`,
  `temp/`, `archive/`, `outputs/`, `rexfinhub.db-wal` 760MB.
- D:\sec-data top level: archives/ backups/ cache/ databases/ rexfinhub/ vps-archive-2026-06-02/
  + loose extraction_progress*.json, submissions.zip, rexfinhub_http_cache.7z.
- Local C:\Foundry\Rexfinhub worktree DBs: 5 copies across worktrees (~2.5GB), incl. the
  phase1_work copy in fix-rex-family worktree.

## H. Long-running daemons (VPS, since Jun 04)

atom_watcher, single_filing_worker, pipeline_api (deploy/), `python -m server.app`
(814MB RSS — identify in audit), atlas uvicorn (state_api :8765). Restart/ownership policy
undocumented.

## I. Misc verified-good

- Send gate: closed (gate-close timer healthy; gate-open/preflight disabled as intended).
- db-backup timer healthy (13h-old backup at 08:00 check).
- rexfinhub.com responds (302→200); screener page serves (stale) cache.
- 13F quarterly timer next-fires 2026-08-19 (correct).

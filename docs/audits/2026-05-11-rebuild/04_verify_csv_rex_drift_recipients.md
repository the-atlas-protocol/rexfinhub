# Re-Audit Verification — CSV Rules / REX Tables / DB Drift / Recipients

**Generated:** 2026-05-12 (after Stage 1 + R6/R7/R8/R9/T1/T2/H1 fixes deployed)
**Mode:** READ-ONLY spot check
**Scope:** Verdicts on the four named surfaces post-deploy.

---

## 1. CSV Rules Pipeline

### Stage 1 highest-severity findings + current state

| Finding | Stage 1 severity | Current state |
|---|---|---|
| F1 — Split-brain write paths (`config/rules/` vs `data/rules/`) | CRITICAL | **FIXED.** `tools/rules_editor/classify_engine.py:15` now imports `RULES_DIR as _CONFIG_RULES_DIR` from `market.config`, and line 21 reassigns `RULES_DIR = _CONFIG_RULES_DIR`. All 9 references in classify_engine (lines 44, 50, 66, 77, 479, 511, 543, 576) now write to `config/rules/`. The `data/rules/` directory has been retired both locally and on the VPS — only a `README.md` (956 B, 2026-05-11 21:04) remains, documenting the deprecation and pointing to `fix_R6.md`. |
| F2 — Cross-category attribute leakage (interlock violation) | CRITICAL | **FIXED.** `market/transform.py:194` now joins `cat_attrs` on `["ticker", "etp_category"]` rather than ticker-only. Lines 138-194 (`step6_apply_category_attributes`) reload the per-category attribute CSVs and tag each with its source category before joining, so a ticker in `attributes_Thematic.csv` cannot bleed `map_*` values into a record classified as Crypto. The dim-merge step at line 234 also uses the `(ticker, etp_category)` key. |
| F3 — Missing attribute rows for funds in `fund_mapping` | HIGH | **PARTIALLY ADDRESSED via T1.** Cleanup commit `eb7d5ac` ("classify 32 launches + 16 brand overrides + 35 attribute rows + drop bad nan issuer_mapping") backfilled 35 attribute rows. Local `config/rules/fund_mapping.csv` is now 2366 rows (was 2300); attribute CSVs grew from 2331-row total to 2381-row total (LI 898, CC 358, Crypto 151, Defined 548, Thematic 426). Spot-coverage of every classified fund is not re-verified here — that needs a Stage 2 cross-check after the next pipeline run rewrites `mkt_*` from the new files. |

### New findings from deploy

- **`mkt_*` tables on local AND VPS still reflect the OLD `config/rules/` snapshot.** `mkt_fund_mapping=2300`, `mkt_issuer_mapping=341`, `mkt_category_attributes=2294` on both local and VPS. The new CSV row counts (2366/348/2381) have not yet been pushed through `sync_rules_to_db()`. The R6 fix repaired the writer, but **no full pipeline run has been triggered post-fix** — the next `rexfinhub-bloomberg-chain` (Tue 17:15 EDT) or manual `python scripts/run_market_pipeline.py` will close this gap. Until then, the live website reads stale (pre-R6) rule snapshots from the DB even though the source-of-truth CSVs are correct.
- **Local config/rules vs VPS config/rules row counts diverge by 1 row** (local `fund_mapping=2366`, VPS=2367; local `issuer_mapping=348`, VPS=349; local `attributes_CC=358`, VPS=359; local `attributes_Defined=548`, VPS=549; local `attributes_LI=898`, VPS=899; local `attributes_Crypto=151`, VPS=152; local `attributes_Thematic=426`, VPS=427). VPS is +1 row across every CSV — consistent with one extra classification approval committed VPS-side after the local sync. Not a re-occurrence of split-brain (both trees write to `config/rules/` now), just the normal "VPS is current; local is one cycle behind" pattern.

### Verdict: **PASS (with watch-item)**

R6 is in place and the split-brain is closed. The `(ticker, etp_category)` join blocks F2 leakage at the transform layer. **Watch:** the next market-pipeline run must complete cleanly so `mkt_fund_mapping` etc. catch up to the new CSV row counts. Until that happens, the DB lies about the new T1 classifications.

---

## 2. REX-Specific Tables

### Stage 1 highest-severity findings + current state

| Finding | Stage 1 severity | Current state |
|---|---|---|
| F8 — Audit-log tables missing on VPS → wiped on every Render swap (`capm_audit_log`, `reserved_symbols`, `classification_audit_log`) | HIGH | **FIXED ON VPS.** All three tables now exist on VPS (`SELECT name FROM sqlite_master WHERE type='table' AND name='capm_audit_log'` returns the row, same for the other two). Audit history will now survive a Render upload, since `init_db()` on Render no longer faces "missing-table → create-empty" on these. **Caveat:** the VPS `capm_audit_log` row count was not re-queried (it should be ≥1 after the prior local audit-test write was migrated; if it is 0, that means the upload swap re-created the table empty without porting local rows). Unverified. |
| F3/F4 — Status/form drift + 509-row past-effective backlog | HIGH | **NOT FIXED (data-shape problem; no remediation script ran).** VPS still shows 268 rows of `status='Awaiting Effective' AND latest_form='485BPOS'` and 509 rows past `estimated_effective_date` in pre-effective statuses. These are classified in Stage 2 plan as data-quality work, not code fixes. |
| F5 — Placeholder ticker `'REX'` on 35 rows | HIGH | **NOT FIXED.** VPS still has 35 rows with `ticker='REX'`. Stage 2 should scope a column rename (`ticker_placeholder` flag or NULL-ticker convention) before the unified-view `?include_all=1` keys all 35 to a single CapM row. |

### New findings from deploy

- **VPS rex_products row count is now 702 — Stage 1 was also 702.** Local has 723 (the 21 T-REX 2X manual inserts from `insert_trex_2x_2026_05_09.py`). The VPS DB snapshot in Stage 1 was `etp_tracker.db.fromvps`, which was the May 4 rsync. The **live VPS DB at `/home/jarvis/rexfinhub/data/etp_tracker.db`** also shows 702 rex_products, meaning the 21 manual inserts that were done locally were never pushed to VPS. This is a separate drift channel: rex_products is mutated locally by ad-hoc scripts and is **not part of the pipeline → upload flow**. Stage 2 needs an explicit "push rex_products to VPS" step or a CSV-seed pattern equivalent to capm_products.
- **F7 (capm_audit_log silent on bulk seed) — STILL OPEN.** No code change to `_capm_seed_if_empty` or `_audit_log` was committed; capm_audit_log remains 1 row on local. The new VPS-side audit table is empty (or near-empty) by virtue of being freshly created.

### Verdict: **WATCH**

The most operationally dangerous finding (F8 — audit history wiped on every swap) is fixed at the VPS schema layer, which is the load-bearing repair. The rest of the wart list is data-quality work scoped for Stage 2. **Watch:** confirm `capm_audit_log` row count on VPS post-next-Render-swap; verify the table survives.

---

## 3. DB Drift (Local / VPS / Render)

### Stage 1 highest-severity findings + current state

| Finding | Stage 1 severity | Current state |
|---|---|---|
| F1 — `db-backup.timer` failing 5 nights running with `sqlite3: command not found` | CRITICAL | **FIXED.** `which sqlite3` on VPS returns `/usr/bin/sqlite3` (3.45.1, 2024-01-30 build). `journalctl -u rexfinhub-db-backup` shows the 2026-05-11 20:48:36 run "Finished" cleanly (Deactivated successfully, exit 0, 2.283s CPU). A fresh backup exists at `/home/jarvis/rexfinhub/data/backups/etp_tracker_20260511.db` (651 MB, mtime May 11 20:48). The unit's `ExecStart` (`mkdir -p data/backups && sqlite3 ... .backup ... && find ... -mtime +7 -delete`) now runs end-to-end. |
| F11 — `rexfinhub-daily.service` not executing Sat/Sun/Mon | CRITICAL | **FIXED.** `systemctl list-timers` shows `rexfinhub-daily.timer` LAST=`Mon 2026-05-11 19:30:11 EDT, 2h 14min ago`. The 19:30 daily run did fire today. `logs/pipeline_20260511_2000.log` contains the full flow (SEC scrape + DB compaction + screener cache upload + parquet upload + DB upload to Render + structured-notes step). EXIT 0, 17.2 minutes wall-clock. The "stuck since Friday" gap in Stage 1 has been closed by whatever timer/service repair was bundled with the H1 hotfix or R8 deploy. |
| F2 — Local DB 7 days behind on SEC filings | HIGH | **STILL OPEN.** Local `MAX(filing_date)` is unchanged from Stage 1 — local does not auto-pull from VPS; no `jarvis pull-db` or equivalent has been added. Stage 2 should still ship that helper. |

### New findings from deploy

- **3 stuck `daily_classify` runs persist (F3 in Stage 1).** VPS still shows 5 rows in `mkt_pipeline_runs` with `finished_at IS NULL`: ids 339 (20:08), 338 (19:37), 336 (16:12), 335 (12:04), 334 (08:17). Pattern matches Stage 1 verbatim. The classify watchdog still doesn't close these out. Latest legitimate `auto` run: id 341 at 2026-05-12 01:05:38 EDT, finished 4 minutes after start — works fine. The orphaned `daily_classify` rows are observability rot, not data corruption.
- **VPS disk now at 90% (was 87% in Stage 1).** `df -h /` → 38G total, 32G used, 3.8G avail, 90% Use%. Disk pressure is rising — Stage 2 priority.
- **VPS DB on disk is 660 MB (vs 653 MB at Stage 1) and -wal is 53 MB (vs 55 MB).** WAL did NOT grow but main file did, consistent with normal write traffic + the Render upload's pre-checkpoint truncate. No corruption signal.
- **Render is current.** `https://rexfinhub.com/api/v1/health` → `{"status":"ok","version":"2.0.0"}`. `/api/v1/filings/recent?days=1` returns 50 filings for today. `/api/v1/reports/list` shows fresh bakes at `2026-05-12T00:11:49Z` through `01:39:02Z` for all 9 report types. Last DB upload completed in tonight's 20:00 SEC scrape pipeline. Render is no longer stale.

### Verdict: **PASS**

Both critical Stage 1 findings (F1 db-backup, F11 daily not running) are fixed and verified. The 90% disk + ongoing stuck `daily_classify` rows are real but not new — neither is service-affecting. **Watch:** disk free is on a 1-week trajectory toward 95%+; cache-prune cron is the right Stage 2 fix.

---

## 4. Recipients + Deliverability

### Stage 1 highest-severity findings + current state

| Finding | Stage 1 severity | Current state |
|---|---|---|
| F1 — 5 of 7 recipient lists are a single shared inbox (`etfupdates@rexfin.com`) | HIGH | **NOT FIXED (no R-prefix fix shipped for recipients).** `email_recipients` table on VPS still shows the same 18 active rows / 13 unique addresses / 3 unique domains pattern. Stage 2 should add `relasmar@rexfin.com` (or another canary) to the daily/weekly/li/income/flow lists for ground-truth verification. |
| F2 — Bcc/private mirror is dead code; Graph `send_email()` does not accept Bcc | HIGH | **NOT FIXED.** No edits to `webapp/services/graph_email.py` since the audit. `_load_private_recipients()` still returns `[]` (zero rows in `private` list_type) and the result is never threaded into the Graph payload. |
| F5 — VPS missing `.txt` fallback files | MEDIUM | **PARTIALLY FIXED via R9.** VPS `config/email_recipients.txt`, `config/email_recipients_private.txt`, and `config/autocall_recipients.txt` are all present (no longer just `.bak` snapshots). **However: `config/digest_subscribers.txt` is still missing on VPS.** R9's restore covered the three primary fallback files but not the digest-subscribers queue file. Low impact (the DB is still canonical) but the F5 finding is not 100% closed. |

### New findings from deploy

- **No structural recipient changes since Stage 1.** Domain breakdown identical (rexfin.com=14, rbccm.com=2, caisgroup.com=2). Local DB and VPS DB still byte-identical for `email_recipients`. `expected_recipients.json` still matches both sides — preflight `recipient_diff` continues to pass.
- **DNS posture unchanged.** SPF, DKIM (`selector1`), DMARC all still aligned for `relasmar@rexfin.com` via Graph API. `selector2` still NXDOMAIN (F9 unfixed).
- **`send_all` continued standing down all weekend.** Logs from Friday (`pipeline_20260508_2000.log`) and tonight (`pipeline_20260511_2000.log`) both show `send_all: standing down (no decision or token mismatch)`. Recipient pipeline is structurally OK; the gate is closed by design.

### Verdict: **WATCH**

R9 closed the most urgent operational finding (.txt fallback files restored on VPS, minus the digest_subscribers.txt edge case). The high-severity content findings (F1 single-alias funnel, F2 dead Bcc code, F3 missing List-Unsubscribe header) are all unaddressed and remain Stage 2 candidates. No deliverability degradation observed; nothing actively broken.

---

## Cross-cutting observations

1. **The "next pipeline run" is now load-bearing for two surfaces.** CSV rules need a `sync_rules_to_db` cycle to push the new T1 + R6 row counts into `mkt_*`; and the rex_products 21-row local→VPS gap has no automated push. Both are scheduled to be flushed by the Tuesday 17:15 EDT bloomberg chain — assuming it includes both `sync_rules_to_db` and a rex_products import path. If it doesn't, Stage 2 needs an explicit step.
2. **Audit tables on VPS are now created but empty.** F8 fix re-creates `capm_audit_log`, `reserved_symbols`, `classification_audit_log` schemas on VPS so they survive Render swaps, but it doesn't backfill the 18,983 `classification_audit_log` rows that exist locally. If Ryu wants the historical audit trail to be visible on rexfinhub.com, the local→VPS migration of those rows is a separate Stage 2 task.
3. **Backup retention works but unmonitored.** New backups land at `data/backups/etp_tracker_YYYYMMDD.db`. The `find ... -mtime +7 -delete` clause will keep the last 7 days. No alert fires if the backup fails again — Stage 2 should add a journal-watcher or healthcheck endpoint.
4. **Disk pressure is the silent rolling problem.** F6 in db-drift is now 90% (was 87%). Backup retention adds ~650 MB/day, of which 6 days persist; that's ~3.9 GB of permanent backup overhead on top of the 11 GB cache. Stage 2 cache-prune is overdue.

---

## Source files referenced

- `C:/Projects/rexfinhub/docs/audit_2026-05-11/01_csv_rules.md`
- `C:/Projects/rexfinhub/docs/audit_2026-05-11/01_rex_tables.md`
- `C:/Projects/rexfinhub/docs/audit_2026-05-11/01_db_drift.md`
- `C:/Projects/rexfinhub/docs/audit_2026-05-11/01_recipients.md`
- `C:/Projects/rexfinhub/docs/audit_2026-05-11/fix_R6.md`, `fix_R9.md`, `cleanup_T1.md` (referenced for fix scope)
- `C:/Projects/rexfinhub/market/config.py:21-23`
- `C:/Projects/rexfinhub/market/transform.py:88-234`
- `C:/Projects/rexfinhub/tools/rules_editor/classify_engine.py:15-21`
- `C:/Projects/rexfinhub/data/rules/README.md` (deprecation notice)
- `/home/jarvis/rexfinhub/data/etp_tracker.db` (VPS SQLite — live)
- `/home/jarvis/rexfinhub/data/backups/etp_tracker_20260511.db` (fresh backup, 651 MB)
- `/home/jarvis/rexfinhub/logs/pipeline_20260511_2000.log` (most-recent successful daily run)
- `https://rexfinhub.com/api/v1/health`, `/api/v1/filings/recent?days=1`, `/api/v1/reports/list`

# Stage 1 Audit — Bloomberg Ingestion
Generated: 2026-05-11T19:05:00-04:00
Agent: bloomberg_ingestion

## Summary

The Bloomberg ingestion path is **structurally fragile in three independent ways** that each can produce the symptoms reported (NULL spikes, occasional repeat data, the 17-fund residue tonight). Most importantly, the cf3d02a brand-derivation auto-apply hook **only lives in `scripts/run_daily.py::run_market_sync()`** — the production VPS Bloomberg timer fires `rexfinhub-bloomberg-chain.service`, which does include `apply_issuer_brands.py` as `ExecStartPost`, but the bare `rexfinhub-bloomberg.service` (kept "for ad-hoc one-shot use") and the local `scripts/watch_bloomberg.py` watcher both call `sync_market_data()` directly with **no brand-application step**. Whenever the bare service or local watcher fires, every `issuer_display` value is wiped to NULL until the next chain or run_daily run. Compounding this, `data_engine.build_master_data()` (xlsm path, line 417) only sets `issuer_display = issuer_nickname` with **no fallback to raw `issuer`** — diverging silently from `build_all_from_csvs()` (line 830) which does `.fillna(issuer)`. Result: the 341-row `issuer_mapping.csv` covers only ~35% of active funds; the remaining ~65% always come out NULL on the xlsm code path even before the brand-derivation hook gets a chance to run. The 17-fund preflight number is misleading — the **true count** of NULL `issuer_display` on ACTV funds right now is **3,375 of 5,235 (64.5%)**, exactly matching the preflight's `null_pct` reading. The 17 are the rump that have non-NULL `etp_category` and still NULL `issuer_display`; the missing 3,358 are funds with NULL `etp_category` (not classified at all), which the Tier-2 query intentionally excludes.

A separate stale-data risk: `data/DASHBOARD/sheets/` CSVs are dated **2026-03-03** (62 days stale) but `_export_bloomberg_sheets()` writes its CSVs to `temp/bloomberg_sheets/` — and `temp/bloomberg_sheets/` doesn't exist on disk after run 304, indicating the perf-fix CSV-first path silently failed and the sync fell back to the xlsm code path (which has the issuer_display NULL bug above).

## Findings

### F1: cf3d02a hook is missing from two of three production sync entry points
- **Severity**: critical
- **Surface**:
  - `deploy/systemd/rexfinhub-bloomberg.service:11` — calls `sync_market_data(db)` with no apply step
  - `scripts/watch_bloomberg.py:65-67` — calls `sync_market_data(db)` with no apply step
  - `scripts/run_daily.py:299-318` — has the apply step
  - `deploy/systemd/rexfinhub-bloomberg-chain.service:23-25` — has `ExecStartPost` for apply (only this one is correct)
- **Symptom**: After any run of `rexfinhub-bloomberg.service` or `watch_bloomberg.py`, all `issuer_display` values are NULL. The Tier-2 preflight count drops by ~3,500 vs. the true site state.
- **Evidence**:
  - `deploy/systemd/rexfinhub-bloomberg.service:11` ExecStart inlines: `download_bloomberg_from_sharepoint(); ... sync_market_data(db); db.close(); print('Bloomberg pull + sync complete')` — no apply step
  - `deploy/systemd/rexfinhub-bloomberg.service` header: "kept for ad-hoc one-shot use" (so it's still callable)
  - `scripts/watch_bloomberg.py:60-67` runs sync_market_data directly, then jumps to classification + screener cache
  - The cf3d02a commit message itself says: "Permanent fix: hook the two brand-derivation scripts into `scripts/run_daily.py` inside `run_market_sync()`" — no mention of the watcher or the bare service
  - In live DB (run 304, source_file='auto', completed 2026-05-11 22:18 UTC): 2,647 of 2,647 tickers in `config/rules/issuer_brand_overrides.csv` have NULL `issuer_display` in `mkt_master_data`. That means the apply step did NOT run after run 304.
- **Blast radius**: `/issuers/`, `/market/issuer`, `/issuers/<id>` pages, fund-detail issuer attribution, weekly L&I report issuer rollup, every CSV export that uses `issuer_display`, `flow_report` / `cc_report` cache (recomputed from master with NULL issuers).
- **Hypothesis**: Whichever cron/watcher actually fired tonight at 22:15 UTC was either the bare `rexfinhub-bloomberg.service` or a manual invocation that bypassed the chain. Need to confirm via VPS journalctl, but the DB state is unambiguous: brand application did not run.
- **Fix size**: small (move apply hook into `sync_market_data` itself, or wrap it in a post-sync function imported by all three entry points)

### F2: `data_engine.build_master_data()` xlsm path has no `issuer` fallback (silent NULL fan-out)
- **Severity**: critical
- **Surface**: `webapp/services/data_engine.py:417-418`
- **Symptom**: Funds whose `(etp_category, issuer)` pair isn't in the 341-row `issuer_mapping.csv` get `issuer_display = NULL` even when raw `issuer` from Bloomberg is populated. The CSV-first path at line 830 does `df["issuer_display"] = df["issuer_nickname"].fillna(df.get("issuer", ""))` — semantically different.
- **Evidence**:
  - `data_engine.py:417` reads literally: `if "issuer_display" not in df.columns and "issuer_nickname" in df.columns: df["issuer_display"] = df["issuer_nickname"]`
  - `data_engine.py:830-831`: `df["issuer_display"] = df["issuer_nickname"].fillna(df.get("issuer", ""))`
  - `config/rules/issuer_mapping.csv` has only 341 mappings vs `mkt_master_data` 7,361 rows
  - DB sample (`MUB US`, `IAGG US`, `PRF US`, `SPY US`, `VOO US`, `QQQ US`): all have raw `issuer` populated (`"iShares Trust"`, `"Invesco Exchange-Traded Fund T"`, etc.) but NULL `issuer_display`
  - Top NULL-`issuer_display` raw issuers (ACTV/PEND): `iShares Trust` (330), `Invesco Exchange-Traded Fund T` (136), `First Trust Exchange-Traded Fu` (111), `EA Series Trust` (84), `WisdomTree ETFs/USA` (83), `SPDR Series Trust` (73)
- **Blast radius**: 3,375 ACTV funds with NULL `issuer_display`; brand-derivation pipeline is forced to compensate for what should be the default behavior; CSV path has different output than xlsm path → non-deterministic data depending on which sync entry was used.
- **Hypothesis**: When the original code was ported to the CSV-first path, the `.fillna()` was added there but never back-ported to the xlsm path. The `_export_bloomberg_sheets()` perf fix (Sys-I) was supposed to make this moot because everything would use the CSV path — but that path silently fails (see F3), so the xlsm path keeps running.
- **Fix size**: trivial (one-line change at `data_engine.py:417-418` to mirror line 830)

### F3: CSV-staging perf-fix silently fell back to xlsm; staging dir doesn't even exist
- **Severity**: high
- **Surface**:
  - `scripts/run_daily.py:240-279` (`_export_bloomberg_sheets`)
  - `scripts/run_daily.py:289-294` (`run_market_sync` calls and passes csv_dir)
  - `webapp/services/market_sync.py:196-199` (chooses build_all_from_csvs vs build_all)
- **Symptom**: After today's run 304, `temp/bloomberg_sheets/` does not exist. Either `_export_bloomberg_sheets` was never called (because the trigger was the watcher, not run_daily) or it ran and was cleaned up. Either way, `data/DASHBOARD/sheets/` CSVs are 62 days stale (2026-03-03), so no consumer that reads from the legacy sheets dir will get fresh data.
- **Evidence**:
  - `ls -la C:/Projects/rexfinhub/temp/bloomberg_sheets/` → "No such file or directory"
  - `ls -la C:/Projects/rexfinhub/data/DASHBOARD/sheets/` → all 8 CSVs dated `Mar 3 00:05`
  - `data/DASHBOARD/.last_market_run.json` shows `data_file: ...bloomberg_daily_file.xlsm` (xlsm path, not csv)
  - `market_sync.py:278` for global ETP supplement defaults to `data/DASHBOARD/sheets` — so it's reading 62-day-old assets/cost/performance/flows CSVs as the global supplement (or skipping if `assets.csv` not in expected dir).
- **Blast radius**: Anyone consuming `mkt_global_etp` (UCITS / Asia view) is on March 3 data. The xlsm fallback (which IS being used) has the F2 issuer_display bug.
- **Hypothesis**: The watcher path doesn't call `_export_bloomberg_sheets` at all. So whenever the watcher fires (3 of 5 weekdays based on systemd timer schedules), the perf-fix dies and we silently take the buggy xlsm path. The legacy `data/DASHBOARD/sheets/` was probably never wired up to a regenerator.
- **Fix size**: medium (decide: regenerate sheets/ on every sync, OR remove the dependency on legacy sheets dir, OR confirm xlsm path semantically matches CSV path then stop maintaining two paths)

### F4: Per-cycle full DELETE before INSERT — UPSERT semantics with no rollback
- **Severity**: high
- **Surface**: `webapp/services/market_sync.py:241-245` (3-row delete sweep)
- **Symptom**: `_sync_market_data_locked` does `delete(MktReportCache); delete(MktTimeSeries); delete(MktMasterData)` and then bulk-inserts. If any post-delete step crashes (Python exception, I/O, lock timeout), the DB is left empty until the next successful run. The 5,000-row min validation (`market_sync.py:210-214`) protects against bad input but provides zero safety against mid-write crash.
- **Evidence**:
  - `mkt_pipeline_runs` shows two `running` rows with no `finished_at`: ID 302 (2026-05-04) and ID 298, 299 (2026-05-01) — confirming syncs DO crash mid-run
  - When that happens, the DB ends up with the partial state from whichever step crashed. There is no transaction wrapper covering the full sync (only `db.flush()` between steps)
  - The 5-min `_SYNC_LOCK` (`market_sync.py:159-162`) prevents concurrent corruption but doesn't undo a partial run
- **Blast radius**: a mid-sync crash can leave 0 rows in master, or a partial set, or a cleared cache with no fresh cache. A subsequent read-only request to `/market/...` gets blank data.
- **Hypothesis**: The chain service's `apply_issuer_brands.py` (executed via `ExecStartPost`) only runs if the main `ExecStart` exits 0 — partial-write states won't trigger apply, perpetuating NULL state.
- **Fix size**: medium (wrap the whole sync in `db.begin()` + savepoints; or write to a shadow table and rename atomically)

### F5: `auto_scan_classifications` swallows all errors at line 304-306, hiding cascade failures
- **Severity**: high
- **Surface**: `webapp/services/market_sync.py:303-306`
- **Symptom**: `_auto_scan_classifications` is wrapped in `try/except Exception` with only a log line. If the scanner crashes (which it has — see F4 evidence of stuck `running` rows), the sync still reports success and the run summary lies.
- **Evidence**:
  - Pattern repeats: `_compute_and_cache_screener` (`market_sync.py:271-273`), global-ETP sync (`market_sync.py:281-282`), classification scan (`market_sync.py:303-306`), CSV export (`market_sync.py:288-289`).
  - All five subsystems are non-fatal — but failure mode is "silent NULL/stale data" not "obvious empty page".
- **Blast radius**: Failed screener cache, failed global ETP, failed classification proposals — all invisible to the operator until the dashboard shows wrong numbers.
- **Hypothesis**: This pattern was added defensively to keep the sync alive, but it now masks systemic failures that should be loud.
- **Fix size**: small (re-raise critical paths, log + alert on others)

### F6: `download_bloomberg_from_sharepoint` atomic-rename fallback is undefined when Windows file lock fires
- **Severity**: medium
- **Surface**: `webapp/services/graph_files.py:194-211`
- **Symptom**: The download writes to `<dest>.tmp`, then `tmp_path.replace(dest)`. On Windows, if Excel/openpyxl/another process has the destination open with a write lock, `.replace()` raises `PermissionError` and the function returns `None`. The `.tmp` file is left on disk in this case and is NOT cleaned up by the failure path (only by the size-check failures at lines 203 and 208). On the next run the size-check passes and `.tmp.replace(dest)` is attempted again — repeatedly. No exponential backoff, no retry, no notification.
- **Evidence**:
  - `graph_files.py:195-211`: `tmp_path.unlink(missing_ok=True)` only fires on size-validation failures
  - `bbg_file.py:78-89`: catches generic `Exception` from download and converts to `BloombergGraphError` — does not distinguish lock vs. network
  - User reported this happened tonight per your task description ("We saw this happen tonight")
- **Blast radius**: A locked file leaves the system on yesterday's data — and `bbg_file.py:60-69` will then either pass freshness check (false negative) or raise `BloombergGraphError`. The local cache is never updated; downstream sees stale.
- **Hypothesis**: openpyxl can hold an exclusive read-handle when a previous parse hung; or Defender / OneDrive / Syncthing scans the file and creates a transient lock during the rename window.
- **Fix size**: small (try N times with backoff; clean up `.tmp` in finally; raise distinct lock vs. network exceptions)

### F7: Excel rules sheets that data_engine expects have been REMOVED from the xlsm
- **Severity**: medium
- **Surface**: `webapp/services/data_engine.py:325` (`fund_mapping`), `:353` (`issuer_mapping`), `:377` (`category_attributes` via `_build_category_attributes`), `:432` (`rex_funds`)
- **Symptom**: All four `_read_sheet(xl, ...)` calls raise `ValueError: Worksheet named 'X' not found`. Each is wrapped in `try/except` that silently falls back to `config/rules/*.csv`. So the xlsm sheets are dead but the code still tries them every run, swallowing 4 exceptions per sync.
- **Evidence**:
  - `pd.ExcelFile(...).sheet_names` for current xlsm: `['bbg_pull','microsector','data_ms','data_aum','data_flow','data_notional','data_price','data_nav','data_pull','w1','w2','w3','w4','w5','s1','aum','OC','wcheck','ca_export_32852397_142125','rules','Sheet1','13f_price','mkt_status','FI','INAV_TICKERS','T-REX','Claude Log']` — none of `fund_mapping`, `issuer_mapping`, `category_attributes`, `rex_funds`
  - Test: `xl.parse('fund_mapping')` raises `ValueError: Worksheet named 'fund_mapping' not found`
  - `rules` sheet exists but has just 7 rows of `Reference ID / Value` pairs (Start Date, End Date, aum, flow, turnover, price, nav) — totally different schema from what `_load_ts_include_rules` (`:580-592`) expects (which wants `t_timeseries_include` column with `Unnamed: 11`).
- **Blast radius**: `_load_ts_include_rules` returns empty → time-series `issuer_group` defaults to "Other" (the fallback at `data_engine.py:657`) → flow report rollup buckets everything as "Other" instead of by real issuer.
- **Hypothesis**: The Excel-side rules tab was depricated some time ago when the team moved to CSV rules, but the data_engine code still walks the dead path on every sync, then silently falls back. `_get_known_issuers` (`:594-607`) similarly returns empty.
- **Fix size**: small (either delete the dead xlsm-rules code, or rebuild the `rules` sheet to the schema `_load_ts_include_rules` expects)

### F8: 5,076 of 7,361 master rows are orphans — no entry in `mkt_fund_mapping`
- **Severity**: high
- **Surface**: `webapp/services/data_engine.py:329-331` (left-join semantics retain unmapped rows)
- **Symptom**: 69% of the master universe has `etp_category = NULL` because they're not in the 2,300-row `fund_mapping.csv`. Combined with F2, this is the dominant cause of NULL `issuer_display` (3,358 of 3,375 NULL-display ACTV rows have NULL `etp_category`).
- **Evidence**:
  - `SELECT COUNT(*) FROM mkt_master_data m LEFT JOIN mkt_fund_mapping fm ON m.ticker=fm.ticker WHERE fm.ticker IS NULL` → **5,076** of 7,361
  - `fund_mapping.csv` only covers REX-relevant categories (LI/CC/Crypto/Defined/Thematic). All "plain beta" / general equity ETFs in the Bloomberg universe have no mapping.
  - That's by design — but combined with F2's NULL fan-out, every plain-beta ETF lands in the NULL bucket
- **Blast radius**: The 17-fund Tier-2 "preflight" alert undercounts the real problem by 200x because it gates on `etp_category IS NOT NULL`. The dashboard shows blank issuer for 3,358 plain-beta funds. Brand-derivation `derive_issuer_brands.py:191-198` operates on `WHERE market_status IN ('ACTV','PEND') AND issuer_display IS NULL` — it WILL pick these up — but if F1 prevents apply from firing, they stay NULL.
- **Hypothesis**: The taxonomy was scoped to REX product categories but the dashboard rendering assumes universal coverage. F2's missing fillna turned what should be "plain beta unclassified, but issuer still shows" into "everything blank".
- **Fix size**: small (fix F2; restore raw issuer fallback so unclassified funds still display their issuer)

### F9: `_apply_etn_overrides` swallows exceptions silently
- **Severity**: low
- **Surface**: `webapp/services/market_sync.py:248-253`
- **Symptom**: ETN overrides (MicroSectors / FNGU et al) are wrapped in try/except with only a log warning. If they don't run, the FNGU $274M underreport scenario referenced in the systemd timer comment recurs without anyone noticing.
- **Evidence**:
  - `market_sync.py:251`: `from webapp.services.market_data import _apply_etn_overrides` — private import
  - The systemd timer comment explicitly says "Reading at 17:00 was racing the file saver and picking up NaN values for tickers like FNGU, causing a silent fallback to yesterday's AUM (seen 2026-04-14, $274M under-report)"
- **Blast radius**: Re-occurrence of the FNGU under-report. AUM rankings on `/screener/3x` and the ETN micro-sector view get wrong numbers.
- **Hypothesis**: The timer fix (17:15 + 21:00 schedules) addresses the race but the silent-failure mode of the override is still there.
- **Fix size**: trivial (log at error level + count failures into the run-summary)

### F10: Schema-drift detection on Bloomberg sheets is absent
- **Severity**: medium
- **Surface**: `webapp/services/data_engine.py:118-148` (`_build_from_split_sheets`)
- **Symptom**: `W1_COL_MAP` etc. rename Bloomberg's column headers (e.g., "Ticker" → "ticker", "AUM" → "aum"). If Bloomberg renames a column (e.g., AUM → "Total Assets"), the rename map silently misses it; the column is left under the original Bloomberg header and is dropped from downstream merges. Validation only checks row count (`market_sync.py:210`) and NaN% on `ticker` and `fund_name` (`market_sync.py:218-225`) — not whether expected analytics columns made it through.
- **Evidence**:
  - `data_engine.py:128` `w1.drop_duplicates(subset=["ticker"], keep="first")` assumes rename succeeded
  - `market_sync.py:218`: `_critical_cols = ["ticker", "fund_name"]` only
  - W4 AUM column detection (`data_engine.py:762-768`) is heuristic ("first non-flow, non-ticker column ... or position 10") — fragile to schema drift
- **Blast radius**: A Bloomberg-side column rename produces a sync that passes validation, writes 7,000+ rows, and silently has NULL AUM / NULL returns / NULL fees because the unrenamed columns were never selected.
- **Hypothesis**: Bloomberg's xlsm is owned by `Product Development/MasterFiles/MASTER Data/` — outside our control. A rename is unlikely but plausible.
- **Fix size**: small (assert post-rename that ≥N expected canonical columns are present; add to `market_sync.py` validation)

### F11: NULL fan-out is inconsistent between the two code paths
- **Severity**: medium
- **Surface**:
  - `webapp/services/data_engine.py:413-421` (xlsm derive)
  - `webapp/services/data_engine.py:823-833` (CSV derive)
- **Symptom**: NULL handling diverges in three places between the xlsm `build_master_data` and the CSV `build_all_from_csvs`. F2 covers `issuer_display`. There are also semantic differences in (a) deduplication strategy, (b) ATTR file load order, (c) category_display derivation. So whichever path runs determines the output.
- **Evidence**:
  - F2 evidence covers issuer_display
  - `build_master_data` uses `_build_from_split_sheets` (xlsm-native) which uses `_read_sheet` and openpyxl
  - `build_all_from_csvs` does pandas `pd.read_csv` with `engine="python"` and a different column-detection scheme
- **Blast radius**: Same xlsm input → two different outputs depending on whether csv-staging worked.
- **Hypothesis**: The CSV path was added for perf without auditing semantic equivalence.
- **Fix size**: medium (collapse to one path, or add a snapshot test that asserts xlsm output ≡ csv output)

### F12: Same-day late-evening Bloomberg refresh can race the daily send window
- **Severity**: medium
- **Surface**: `deploy/systemd/rexfinhub-bloomberg.timer:11-19`, `webapp/services/bbg_file.py:74-76`
- **Symptom**: Two scheduled pulls (17:15 and 21:00 ET). If a third refresh happens via `scripts/watch_bloomberg.py` while a daily send is in-flight, the cache underneath is replaced and downstream queries see partially-newer data than the report payload.
- **Evidence**:
  - `_SYNC_LOCK` covers `sync_market_data` but NOT downstream consumers (admin send, preview build)
  - `bbg_file.py:60-92` will download a newer file mid-render if SharePoint advanced
- **Blast radius**: An email shipping at 17:30 could mix 17:15 numbers (already queried) with 21:00 numbers (refetched mid-render). Not catastrophic but produces "occasional repeat data" / inconsistencies in the report payload that match what you reported.
- **Hypothesis**: The 21:00 second pull was added defensively; without coordination with the send window it can poison in-flight renders.
- **Fix size**: medium (introduce a "data version" reference held by the report build for its lifetime)

### F13: Time-zone confusion between Bloomberg `Inception Dt` and DB `inception_date`
- **Severity**: low
- **Surface**: `webapp/services/market_sync.py:404` (`inception_date=_safe_str(row.get("inception_date"))`)
- **Symptom**: `inception_date` is stored as a string ("2007-12-28 00:00:00") with no TZ. Bloomberg native is presumably America/New_York; downstream `date(inception_date) >= date('now','-14 days')` (`preflight_check.py:154`) compares against SQLite's `date('now')` which is UTC-by-default. Around midnight UTC, this can shift the lookback window by one day.
- **Evidence**:
  - `mkt_master_data.inception_date` sample: `'2007-12-28 00:00:00'` (no TZ)
  - `preflight_check.py:154`: `AND date(inception_date) >= date('now','-{NEW_FUND_LOOKBACK_DAYS} days')` — both sides treated as plain dates, server-relative
- **Blast radius**: Edge-case off-by-one in "new launches" lookback — minor, manifests as a fund showing up one day late in alerts.
- **Fix size**: trivial (use `date('now','-14 days','localtime')` or canonicalize on insert)

### F14: `assets.csv not found in data/DASHBOARD/sheets` is silently skipped
- **Severity**: low
- **Surface**: `webapp/services/market_sync.py:276-282`
- **Symptom**: `_sync_global_etp` raises `FileNotFoundError` if `assets.csv` is missing; caller catches and logs `"Global ETP sync skipped: %s"` and proceeds. The user mentioned this happened tonight in the global ETP sync.
- **Evidence**:
  - `market_sync.py:761-763`: `raise FileNotFoundError(f"assets.csv not found in {sheets_dir}")`
  - `market_sync.py:281-282`: catches and logs
  - The expected sheets are exported by `scripts/export_sheets.py` to `temp/bloomberg_sheets/` — but `_sync_global_etp` defaults to `data/DASHBOARD/sheets` if `csv_dir` isn't passed (which it isn't from the watcher / bare service path)
  - `data/DASHBOARD/sheets/` does NOT contain `assets.csv` (it has only w1-w4, s1, data_aum, data_flow, data_notional)
  - `temp/bloomberg_sheets/` does not exist at all
- **Blast radius**: `mkt_global_etp` table is never populated → UCITS / global views are blank
- **Hypothesis**: The expected location for global supplement CSVs was never wired up to anything that writes them. Either xlsm doesn't have these sheets and they need a Bloomberg side change, or `export_sheets.py` should be writing them but isn't running (F3).
- **Fix size**: small (alert when expected supplements missing; or generate them deterministically from the xlsm sheets that do exist)

### F15: `is_rex` cast may flip on non-bool inputs
- **Severity**: low
- **Surface**: `webapp/services/market_sync.py:467`
- **Symptom**: `is_rex=bool(row.get("is_rex", False)) if pd.notna(row.get("is_rex")) else False` — `bool("False")` is `True` (non-empty string truthy), `bool(0)` is `False`, `bool(0.0)` is `False`. If Bloomberg xlsm cell contains the string "FALSE" or `"0"`, this evaluates to True.
- **Evidence**: `market_sync.py:467` literal code.
- **Blast radius**: Spurious is_rex=True on funds that should not be flagged → REX-only filters surface non-REX funds.
- **Fix size**: trivial (use a strict truthy check: `str(val).lower() in {"true","1","yes"}`)

### F16: Pipeline-run `running` rows are never reaped or marked failed
- **Severity**: low
- **Surface**: `webapp/services/market_sync.py:230-238`, `:292-296`
- **Symptom**: `mkt_pipeline_runs` shows two stuck `running` rows from May 1 and one from May 4 with no `finished_at`. Nothing in the codebase cleans these up, so they pollute monitoring queries and obscure real-failure rates.
- **Evidence**: `SELECT id, started_at, finished_at, status FROM mkt_pipeline_runs ORDER BY id DESC LIMIT 8` returns: 302 'running' (5/4), 299 'running' (5/1), 298 'running' (5/1).
- **Blast radius**: low (just operational hygiene), but masks F4 evidence by burying it in noise.
- **Fix size**: trivial (mark as 'failed' if started >24h ago and still 'running'; use a try/finally in `_sync_market_data_locked` to always set a terminal status)

## DB queries run

```sql
-- Total + null counts
SELECT COUNT(*) FROM mkt_master_data;
-- → 7361
SELECT COUNT(*) FROM mkt_master_data WHERE issuer_display IS NULL OR issuer_display='';
-- → 5093

-- By status
SELECT market_status, COUNT(*),
       SUM(CASE WHEN issuer_display IS NULL OR issuer_display='' THEN 1 ELSE 0 END)
FROM mkt_master_data GROUP BY market_status;
-- ACTV 5235, NULL 3375 (64.5%); LIQU 1820, NULL 1487; PEND 126, NULL 80; etc.

-- ACTV-level counts
SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV'
  AND etp_category IS NOT NULL AND (issuer_display IS NULL OR issuer_display='');
-- → 17 (this is what preflight reports)
SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category IS NULL;
-- → 3358 (the hidden NULL bucket)

-- ACTV broken out by etp_category x null_display
SELECT etp_category, issuer_display IS NULL OR issuer_display='', COUNT(*)
FROM mkt_master_data WHERE market_status='ACTV'
GROUP BY etp_category, issuer_display IS NULL OR issuer_display='';
-- (None,1,3358) (CC,0,316) (CC,1,6) (Crypto,0,101) (Crypto,1,4)
-- (Defined,0,501) (Defined,1,2) (LI,0,597) (LI,1,1) (Thematic,0,345) (Thematic,1,4)

-- Top NULL-display raw issuers (ACTV/PEND)
SELECT issuer, COUNT(*) FROM mkt_master_data
WHERE (issuer_display IS NULL OR issuer_display='')
  AND market_status IN ('ACTV','PEND')
GROUP BY issuer ORDER BY 2 DESC LIMIT 25;
-- iShares Trust 330, Invesco Exchange-Traded Fund T 136, First Trust ... 111,
-- EA Series Trust 84, WisdomTree ETFs/USA 83, SPDR Series Trust 73, Global X 70,
-- JP Morgan 64, VanEck 62, Franklin Templeton 59, ...

-- Sample 20 funds: raw issuer present but display NULL
SELECT ticker, fund_name, issuer FROM mkt_master_data
WHERE (issuer_display IS NULL OR issuer_display='')
  AND issuer IS NOT NULL AND issuer != ''
  AND market_status IN ('ACTV','PEND') LIMIT 20;
-- 2578189D US (Goldman), AAA US (Investment Managers Series Tru),
-- AAAA US (EA Series Trust), AAXJ US (iShares Trust → ISHARES MSCI ALL COUNTRY ASIA EX JAPAN ETF)
-- — all have raw issuer populated.

-- Pipeline runs
SELECT id, started_at, finished_at, status, master_rows_written, ts_rows_written, source_file
FROM mkt_pipeline_runs ORDER BY id DESC LIMIT 8;
-- 304 ✓ 22:15→22:18 source_file='auto' 7361 rows, 272357 TS
-- 303 ✓ 5/7  source_file='C:\...\bloomberg_daily_file.xlsm'
-- 302 running 5/4 source_file='daily_classify' (STUCK)
-- 301 ✓ 5/4
-- 300 ✓ 5/2
-- 299 running 5/1 source_file='daily_classify' (STUCK)
-- 298 running 5/1 source_file='daily_classify' (STUCK)
-- 297 ✓ 5/1

-- Run-id distribution (no historical accumulation — DELETE+INSERT replace)
SELECT pipeline_run_id, COUNT(*) FROM mkt_master_data GROUP BY pipeline_run_id;
-- (304, 7361) — only one

-- Duplicate tickers (none)
SELECT ticker, COUNT(*) FROM mkt_master_data GROUP BY ticker HAVING COUNT(*)>1;
-- 0 rows

-- Orphans (no fund_mapping parent)
SELECT COUNT(*) FROM mkt_master_data m
LEFT JOIN mkt_fund_mapping fm ON m.ticker=fm.ticker
WHERE fm.ticker IS NULL;
-- → 5076 (69%)

-- CSV override coverage vs DB state
-- Read config/rules/issuer_brand_overrides.csv (2647 rows, 2557 unique tickers)
-- For each CSV ticker, check current DB value:
-- 2647 of 2647 found in DB; 2647 NULL issuer_display in DB; 0 already correct.
-- ⇒ apply_issuer_brands.py was NOT run after run 304.

-- Foreign tickers (exchange suffixes)
SELECT SUBSTR(ticker, INSTR(ticker, ' ')+1) AS suffix, COUNT(*)
FROM mkt_master_data WHERE ticker LIKE '% %'
GROUP BY suffix ORDER BY 2 DESC;
-- US 7342, LN 11, EU 3, SW 1, NA 1, KS 1, GR 1, B4 1
```

## File-level checks

- `data/DASHBOARD/bloomberg_daily_file.xlsm` — present, 28,589,935 bytes, mtime `2026-05-11 18:13 ET`, age ~1.3h at audit time. Sheets: `bbg_pull, microsector, data_ms, data_aum, data_flow, data_notional, data_price, data_nav, data_pull, w1, w2, w3, w4, w5, s1, aum, OC, wcheck, ca_export_32852397_142125, rules, Sheet1, 13f_price, mkt_status, FI, INAV_TICKERS, T-REX, Claude Log`. **`fund_mapping`, `issuer_mapping`, `category_attributes`, `rex_funds` sheets are NOT present** — data_engine's xlsm-side rules-load fails 100% (silently caught, falls back to CSVs). The `rules` sheet exists but only as `Reference ID/Value` pairs (Start Date, End Date, aum, flow, etc.).
- `data/DASHBOARD/sheets/` — **62 days stale** (all 8 CSVs `Mar 3 00:05`). `assets.csv` is not present (causes F14 silent skip).
- `temp/bloomberg_sheets/` — **does not exist** after run 304. The CSV-first perf path was never used or its dir was cleaned up.
- `data/DASHBOARD/.last_market_run.json` — points to xlsm path (not csv_dir), confirming xlsm fallback was taken.
- `config/rules/issuer_brand_overrides.csv` — present, 2,647 rows, 2,557 unique tickers, mtime `2026-05-11 03:01 ET`. **6 tickers (DDFY, DDTY, MAUG, MFEB, MMAY, MNVR) appear 16 times each** — `derive_issuer_brands.py:204-216` does not dedupe before writing the CSV when one ticker matches multiple `fund_name` rows in the DB. Apply script (`apply_issuer_brands.py:226-256`) writes them serially; final value wins but causes 96 wasted UPDATEs and noise in spot-check sample.
- `data/.market_sync.lock` — present, 0 bytes, mtime `Apr 9` — file-lock hygiene is fine (the lock file is never written, only flock'd).
- `data/etp_tracker.db` — mtime `2026-05-11 18:18 ET` matches run 304's finish (22:18 UTC). DB is on the local disk (matches Syncthing notes — could conflict between desktop and laptop, not investigated).

## Surfaces inspected

- `webapp/services/bbg_file.py` (full)
- `webapp/services/graph_files.py` (full)
- `webapp/services/graph_email.py` (full)
- `webapp/services/market_sync.py` (full)
- `webapp/services/data_engine.py` (key functions: `build_master_data`, `build_all_from_csvs`, `_build_from_split_sheets`, `_load_ts_include_rules`, `_get_known_issuers`)
- `scripts/run_daily.py` (run_market_sync + bbg-export wrapper, lines 240-345)
- `scripts/derive_issuer_brands.py` (full)
- `scripts/apply_issuer_brands.py` (full)
- `scripts/export_sheets.py` (full)
- `scripts/watch_bloomberg.py` (full)
- `scripts/preflight_check.py` (Bloomberg, classification, NULL audits)
- `archive/scripts/sync_dashboard_data.py` (legacy OneDrive sync — confirmed retired)
- `deploy/systemd/rexfinhub-bloomberg.service` + `.timer` + `bloomberg-chain.service`
- `config/rules/issuer_brand_overrides.csv` (audit only — content + duplicates)
- `config/rules/issuer_mapping.csv` (341 rows — confirmed too small to cover universe)
- `config/rules/fund_mapping.csv` (2300 rows — confirmed REX-scoped, not universal)
- xlsm sheet structure (openpyxl read-only)
- Live DB queries via `sqlite3 data/etp_tracker.db`

## Surfaces NOT inspected

- `webapp/services/market_data.py` (101KB — read for `_apply_etn_overrides` import only; full audit is Stage 2 territory)
- `webapp/services/screener_3x_cache.py` (downstream consumer of master; cache-invalidation logic not traced)
- `market/auto_classify.py` and `market/db_writer.py` (classification path called from watcher)
- `market/derive.py`, `market/transform.py`, `market/ingest.py` (referenced but not opened)
- `webapp/services/report_data.py` (92KB — downstream consumer)
- The actual `_apply_etn_overrides` implementation
- The `_compute_and_cache_screener` implementation (only confirmed it's wrapped in try/except)
- Whether the local DB diverges from the VPS DB (would need VPS access)
- Whether VPS journalctl shows which service actually fired tonight (need VPS shell)
- Render-side DB upload path and whether brand-application fires there
- `scripts/apply_fund_master.py` and `scripts/apply_underlier_overrides.py` (other ExecStartPost in chain)
- The 5 `auto_scan_classifications` -> `tools.rules_editor.classify_engine.scan_unmapped` path

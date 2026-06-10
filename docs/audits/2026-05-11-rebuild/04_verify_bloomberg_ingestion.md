# Verify Audit — Bloomberg Ingestion
Generated: 2026-05-11T21:42:00-04:00
Compares to: 01_bloomberg_ingestion.md
Verifier: bloomberg_ingestion_verify (re-audit pass)

## Headline

The three landed fixes (**R1** chain-service swap, **T1** ticker overrides + UTF-8 re-encode, **R7** TZ env on services) **work in production on the VPS**. The 21:00 ET chain run tonight (`run_id=341`) executed all 4 ExecStartPost steps and dropped ACTV NULL `issuer_display` from **3,375 → 826** (a 75% improvement). However:

1. **T1 only landed VPS-side.** The local desktop copy of `config/rules/issuer_brand_overrides.csv` still contains 15 raw 0x97 (cp1252 em-dash) bytes and `apply_issuer_brands.py` opens with strict UTF-8. **Any local run of `scripts/apply_issuer_brands.py` or `scripts/run_daily.py` will crash on a `UnicodeDecodeError` at line 192.**
2. **F1 is only PARTIAL.** The chain service is now wired correctly, but `scripts/watch_bloomberg.py` and the bare `rexfinhub-bloomberg.service` *still* call `sync_market_data()` with no apply step. Either path firing on the VPS or locally would re-NULL the issuer_display column wholesale.
3. **F4/F16 stuck-rows hygiene got WORSE since Stage 1.** Stuck `running` rows on VPS: 92 total, 87 of them >24h old (was 3 in Stage 1's local DB). The `daily_classify` job is the offender — none of those `running` rows ever transitioned to a terminal state.
4. **F2 (xlsm path missing `.fillna()`) is unchanged.** Brand-application masks the symptom but the root cause is still at `data_engine.py:417-418`.

The system is *meaningfully* healthier — the nightly chain works end-to-end — but the underlying fragility (silent fallbacks, exception-swallowing, no apply-step in two of three sync entries) remains, and a new local-environment regression has been introduced by the partial T1 push.

## Re-verification scope

- VPS systemd units: `systemctl cat rexfinhub-bloomberg-chain.service`, `rexfinhub-bloomberg.service`, `rexfinhub-bloomberg.timer`
- VPS journalctl: `rexfinhub-bloomberg-chain.service` for tonight's 21:00 ET run
- VPS `data/etp_tracker.db` queried via `/home/jarvis/venv/bin/python` (sqlite3)
- Local `data/etp_tracker.db` (same queries)
- Both copies of `config/rules/issuer_brand_overrides.csv` (binary inspect for non-ASCII bytes)
- Source files: `webapp/services/data_engine.py`, `webapp/services/market_sync.py`, `webapp/services/graph_files.py`, `scripts/watch_bloomberg.py`, `scripts/apply_issuer_brands.py`, `scripts/apply_classification_sweep.py`

## Stage 1 finding status

### F1 — chain service hook missing: **PARTIAL**
- **R1 verification (production VPS)**: ✓ `rexfinhub-bloomberg.timer` calls `Unit=rexfinhub-bloomberg-chain.service`. Tonight's 21:00 ET run logged all four `ExecStartPost` lines completing successfully — `apply_fund_master`, `apply_underlier_overrides`, `apply_issuer_brands` (2,557 rows updated, 90 no-ops), and `apply_classification_sweep --apply --apply-medium`.
- **Numeric proof — VPS issuer_display NULLs (ACTV) before vs. now**: Stage 1: **3,375 / 5,235 (64.5%)** → Re-audit: **826 / 5,235 (15.8%)**. ~2,549 rows now correctly displayed. The 826 residual is the 812 with NULL `etp_category` plus 14 with `etp_category` set but unmapped (the new "F2 residue").
- **Issuer-brand override coverage (VPS)**: 2,573 unique CSV tickers, **0** of them NULL in DB, **2,573** populated. Apply-step verifiably fired post-sync.
- **NEW: VPS issuer_brand_overrides apply log**: 2,557 rows updated + 90 idempotent no-ops + 0 not-found. Clean.
- **What's still PARTIAL**:
  - `scripts/watch_bloomberg.py:60-67` still imports and calls `sync_market_data(db)` with no apply step. If anyone fires the watcher on the VPS or locally between scheduled chain runs, every CSV-override ticker reverts to NULL.
  - Bare `rexfinhub-bloomberg.service` still exists on VPS (kept "for ad-hoc one-shot use" per its own header) and its `ExecStart` is still the bare sync — running `systemctl start rexfinhub-bloomberg.service` directly would also re-NULL.
  - The cf3d02a `run_daily.py::run_market_sync()` apply hook is still the only one of these three entries that calls apply.
- **Recommendation**: Either move the apply step inside `sync_market_data()` itself (one-shot fix covering all entries) or `systemctl mask rexfinhub-bloomberg.service` and rewrite `watch_bloomberg.py` to call the chain instead.

### F2 — `data_engine.build_master_data()` xlsm path missing `.fillna()`: **PERSISTING**
- **Code state**: Unchanged. `webapp/services/data_engine.py:417-418` still reads `if "issuer_display" not in df.columns and "issuer_nickname" in df.columns: df["issuer_display"] = df["issuer_nickname"]` — no fallback to raw `issuer`.
- **CSV path** at line 830-831 still has the correct `df["issuer_nickname"].fillna(df.get("issuer", ""))`.
- **Active impact (VPS)**: The xlsm path IS being used — `data/DASHBOARD/.last_market_run.json` shows `"data_file":".../bloomberg_daily_file.xlsm"`. After tonight's sync but **before** apply_issuer_brands ran, every fund whose `(etp_category, issuer)` pair wasn't in `issuer_mapping.csv` came out as NULL `issuer_display`.
- **Why it's not a "site failure" today**: `apply_issuer_brands.py` masks the symptom by overwriting all 2,557 CSV-mapped tickers post-sync. The 826 residual ACTV rows with NULL `issuer_display` are the funds NOT in the override CSV — for these, the raw `issuer` column is still populated (sample: `497780 KS / Samsung Asset Management Co Lt`, `AAA US / Investment Managers Series Tru`, `ABCS US / EA Series Trust`) but `issuer_display` is left empty. Top NULL-display issuers: `EA Series Trust` (62), `Tidal Trust I` (39), `Corgi ETF Trust I` (38), `ETF Series Solutions` (27).
- **Conclusion**: F2 is unfixed; it's just being papered over by F1's brand-override CSV. Add the missing `.fillna(df.get("issuer", ""))` and the residual 826 drops to ~14 immediately, no apply needed.

### F3 — CSV-staging perf-fix silently fell back to xlsm: **PARTIALLY MOOT (VPS) / PERSISTING (local)**
- **VPS**: `temp/bloomberg_sheets/` exists with all 8 expected CSVs (`data_aum.csv` 6.3MB, `w1.csv` 3.5MB, etc.) timestamped May 11 08:08-08:09 UTC. Legacy `data/DASHBOARD/sheets/` does not exist on VPS (no stale dir to confuse anyone).
- **Local**: `temp/bloomberg_sheets/` still does not exist. `data/DASHBOARD/sheets/` still has 8 CSVs all dated `Mar 3 00:05` (now 70 days stale, was 62 in Stage 1). Anyone running `scripts/run_daily.py` locally with the bare watcher path falls back to xlsm.
- **VPS xlsm fallback**: Despite the CSVs existing on VPS, `.last_market_run.json` shows xlsm path was used tonight. Either `run_daily.py::_export_bloomberg_sheets()` doesn't run before chain ExecStart (likely — chain doesn't call run_daily, it calls sync_market_data inline) or `csv_dir` isn't being passed. So the perf-fix CSV path is still dormant in production; the xlsm path runs every night.
- **Conclusion**: VPS no longer has the misleading stale-sheets dir, but the underlying issue (CSV path never used; xlsm path always used; both diverge per F11) is unchanged.

### F4 — Per-cycle full DELETE before INSERT, no transaction wrapper: **PERSISTING**
- **Code state**: Unchanged. `webapp/services/market_sync.py:240-296` still does `delete(MktReportCache); delete(MktTimeSeries); delete(MktMasterData); db.flush()` before the `_insert_master_data` call, with no `try/finally` to mark the run as `failed` if a downstream step crashes.
- **Evidence of the symptom (VPS, worsened)**: `mkt_pipeline_runs` shows **92 stuck `running` rows total**, **87 of them >24h old**. Stage 1 saw 3 stuck rows in local DB. The pattern: every `daily_classify` invocation (08:15 + 12:04 + 19:37 + 20:08 etc. on May 11 alone) starts a `running` row and never finishes. ID 339 (2026-05-11 20:08) is a stuck `daily_classify` row from this evening.
- The 17:15 and 21:00 chain runs themselves are fine — IDs 337, 340, 341 all completed cleanly. The stuck rows are exclusively `source_file='daily_classify'`.
- **Conclusion**: F4 root cause unfixed and now a more visible operational hazard. Wrap `_sync_market_data_locked` in a `try/finally` that always sets `run.status='failed'` if not already terminal; separately, debug what's killing `daily_classify` mid-run.

### F5 — `auto_scan_classifications` swallows all errors: **PERSISTING**
- **Code state**: Unchanged. `market_sync.py:303-306` still wraps `_auto_scan_classifications` in `try/except Exception` with only `log.error(..., exc_info=True)` — the run still completes successfully even if the scanner crashes. Same pattern still applies to `_compute_and_cache_screener` (271-273), `_sync_global_etp` (281-282), `_export_csvs` (288-289).
- **Active impact (VPS)**: `mkt_global_etp` table is **empty (0 rows)** on VPS. `_sync_global_etp` is silently failing every run. Stage 1 noted this for local; verified now true on VPS as well — UCITS / Asia view has no global supplement data.
- **Conclusion**: F5 root cause unfixed. The sliding scope of "non-fatal" exception swallows is hiding at least one persistent failure (mkt_global_etp empty).

### F6 — `download_bloomberg_from_sharepoint` Windows lock not handled: **PERSISTING**
- **Code state**: Unchanged. `webapp/services/graph_files.py:194-211` still does single-shot `tmp_path.replace(dest)` with no retry/backoff and no `finally:` cleanup of the `.tmp` file on `PermissionError`. `bbg_file.py` still wraps everything in generic `Exception`.
- **No new evidence** the lock recurred on VPS (Linux file locks rarely fire). On Windows local, there's no recent run to validate.
- **Conclusion**: Latent. Will re-bite first time openpyxl/Excel/Defender holds the file open during a desktop run.

### F7 — Excel rules sheets removed from xlsm but data_engine still tries them: **PERSISTING**
- **Code state**: Unchanged. `data_engine.py` still attempts `_read_sheet(xl, "fund_mapping")` (325), `"issuer_mapping"` (353), `"category_attributes"` (377), `"rex_funds"` (426), and twice for `"rules"` (581, 600). Each falls into a `try/except Exception → CSV fallback`, swallowing the exception.
- **Cost per sync**: 6+ exceptions raised and discarded silently. Cosmetic — but noisy if anyone enables exception logging at debug.
- **Conclusion**: Dead code path. Trivial cleanup remains.

### F8 — 5,076 of 7,361 master rows are orphans (no fund_mapping entry): **PERSISTING**
- **VPS verified**: Same 5,076 orphan count today (`SELECT COUNT(*) FROM mkt_master_data m LEFT JOIN mkt_fund_mapping fm ON m.ticker=fm.ticker WHERE fm.ticker IS NULL` → 5,076).
- ACTV breakdown by category (post-fix):
  ```
  category   total  null_disp
  None       3294   812        ← the orphan bucket; F2 would handle this
  CC         334    3
  Crypto     106    0
  Defined    531    9
  LI         621    2
  Thematic   349    0
  ```
  3,294 of 5,235 ACTV (63%) still have no `etp_category`. Of those, 812 have NULL `issuer_display`. After T1 brought the override CSV up to date, brand-application now covers 2,482 of these orphan rows by ticker — but 812 remain because they're not in any override list AND F2's missing fillna means raw `issuer` doesn't propagate.
- **Conclusion**: F8 unchanged. F2 fix would eliminate the 812 NULLs in the orphan bucket (their raw `issuer` is populated — would just need to fall through).

### F9 — `_apply_etn_overrides` swallows exceptions silently: **PERSISTING**
- **Code state**: Unchanged. `market_sync.py:248-253` still wraps in generic `try/except Exception` with only `log.warning("ETN overrides not applied (non-fatal): %s", e)`.
- **Conclusion**: Latent. No FNGU under-report observed tonight, but the silent-failure surface is still there.

### F10 — Schema-drift detection on Bloomberg sheets is absent: **PERSISTING**
- **Code state**: Unchanged. `market_sync.py:218` still validates only `["ticker", "fund_name"]`; W1-W5 rename maps still fragile.
- **Conclusion**: Latent.

### F11 — NULL fan-out divergent between xlsm and CSV paths: **PERSISTING**
- **Code state**: Unchanged. `build_master_data` (xlsm) and `build_all_from_csvs` (CSV) still have semantically different issuer_display logic plus three other diverged behaviors per Stage 1.
- **Active impact**: VPS uses xlsm path exclusively (verified via `.last_market_run.json`). The CSV path is dead code in production, but tested by no one. Whichever path runs determines output.
- **Conclusion**: Two-path divergence still live.

### F12 — Same-day late-evening refresh races daily-send window: **PERSISTING**
- **Code state**: Unchanged. No "data version" reference held by report build; `_SYNC_LOCK` only covers sync, not downstream consumers.
- **Conclusion**: Latent.

### F13 — Time-zone confusion `inception_date` vs SQLite `date('now')`: **PERSISTING**
- **Code state**: Unchanged at `market_sync.py:403`. No `localtime` modifier added to preflight comparisons.
- R7 (TZ=America/New_York env on systemd services) helps the *process* timezone but doesn't change SQLite's `date('now')` semantics — SQLite still defaults to UTC unless the query uses `'localtime'`.
- **Conclusion**: R7 fix is necessary but insufficient for F13. Still latent.

### F14 — `assets.csv not found` silently skipped: **PERSISTING (and now confirmed in production)**
- **Code state**: Unchanged. `market_sync.py:281-282` still catches and logs at warning level.
- **VPS evidence**: `mkt_global_etp` table contains **0 rows**. The global ETP sync is silently failing every cycle on VPS, and no operator has been alerted because it's wrapped in `try/except`.
- **Conclusion**: F14 confirmed actively broken on VPS. Was theoretical in Stage 1; now verified.

### F15 — `is_rex` cast may flip on non-bool inputs: **PERSISTING**
- **Code state**: Unchanged. `market_sync.py:467` still uses `bool(row.get("is_rex", False))` which makes `bool("False")` → `True`.
- **Conclusion**: Latent.

### F16 — `running` rows never reaped: **PERSISTING (worsened)**
- **VPS evidence**: 92 stuck `running` rows total, 87 >24h old. Stage 1 noted 3 stuck rows in local DB. The leak rate has been ~3 per day on VPS for the `daily_classify` source.
- **Conclusion**: F16 unchanged code-wise; operational impact growing. Trivial fix (mark stale `running` as `failed` in a periodic sweep) increasingly urgent.

## NEW findings post-fix

### N1: T1 fix did NOT propagate to local copy of `issuer_brand_overrides.csv`
- **Severity**: high
- **Surface**: `C:/Projects/rexfinhub/config/rules/issuer_brand_overrides.csv`
- **Evidence**: Local file size 238,631 bytes; VPS file size 235,997 bytes. Local file contains **15 raw 0x97 bytes** (cp1252 em-dash, U+2014). VPS file contains **0 non-ASCII bytes**. Sample: `BNDY US,Horizon,audit-T1,Horizon Funds \x97 CC backfill\r\n`.
- **Impact**: `scripts/apply_issuer_brands.py:192` opens with strict `encoding="utf-8"`. Any local execution (`python scripts/apply_issuer_brands.py`, `python scripts/run_daily.py`) will crash on `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position 34: invalid start byte`.
- **Hypothesis**: The T1 fix was applied directly on VPS (likely via SSH edit) rather than committed and synced through Syncthing/git, so the desktop never received it. The mtime alignment supports this (VPS: May 11 21:28; local: May 11 21:26 — close but local is older AND larger AND has the bad bytes).
- **Fix**: Either (a) re-save local CSV with UTF-8 encoding (replace `\x97` with `-` or `--`), (b) push the VPS-cleaned copy back over Syncthing, or (c) make `apply_issuer_brands.py` resilient by trying `utf-8` then falling back to `cp1252`.

### N2: `mkt_global_etp` table is empty on VPS — `_sync_global_etp` silently failing every cycle
- **Severity**: high
- **Surface**: `webapp/services/market_sync.py:276-282`, `_sync_global_etp` reads from `data/DASHBOARD/sheets` by default
- **Evidence**: `SELECT COUNT(*) FROM mkt_global_etp` on VPS → 0. The chain service has not produced a single global ETP row.
- **Impact**: Any UCITS / Asia / global-supplement view fed by this table is rendering blank (or raises). The exception is being swallowed in `market_sync.py:281-282` with only a warning log.
- **Hypothesis**: F14 manifesting on VPS — `data/DASHBOARD/sheets/` doesn't exist on VPS (only `temp/bloomberg_sheets/`), so the default read path raises `FileNotFoundError`. The `csv_dir` argument is not being passed when the chain service invokes `sync_market_data(db)`.
- **Fix**: Either (a) wire `csv_dir=Path("temp/bloomberg_sheets")` into the chain ExecStart, or (b) make `_sync_global_etp` look in both locations and raise loudly if neither has the expected files.

### N3: `daily_classify` jobs leave 87+ orphan `running` rows on VPS
- **Severity**: medium
- **Surface**: `mkt_pipeline_runs` writes from whatever `daily_classify` invocation lives in cron/systemd
- **Evidence**: 87 of 92 stuck `running` rows have `source_file='daily_classify'`. Last successful `daily_classify` row predates Stage 1 audit. Recent stuck examples: ID 339 (2026-05-11 20:08), ID 338 (19:37), ID 336 (16:12), ID 335 (12:04), ID 334 (08:15) — all today.
- **Impact**: Run-table noise drowns out real failures; F16 worsens with each cycle. Whatever job creates these `running` rows never executes its terminal write.
- **Hypothesis**: A `daily_classify` script either crashes immediately after creating the run row, or it's a different code path that doesn't share `_sync_market_data_locked`'s flow. Need to trace which scheduler invokes `daily_classify`.
- **Fix**: Find the `daily_classify` invocation source, debug why it never commits a terminal status, add `try/finally` cleanup.

### N4: Local DB is 12 hours behind VPS DB
- **Severity**: low (informational)
- **Evidence**: Local DB last run = ID 304 (2026-05-11 22:18 UTC, source `auto`). VPS DB last run = ID 341 (2026-05-12 01:05 UTC, source `auto`). The desktop's local DB hasn't received any sync since Stage 1's audit time.
- **Impact**: Anyone diagnosing locally without first pulling VPS DB will see Stage 1's NULL state, not the post-fix state.
- **Hypothesis**: Local DB and VPS DB are independent (no replication/Syncthing of `data/etp_tracker.db`). This is by design but worth documenting.
- **Fix**: None needed — but verification work should always specify which DB is being queried.

### N5: 4,111 classification conflicts logged tonight — large cohort of pre-existing curated values disagree with classifier
- **Severity**: medium (data-quality signal, not a bug)
- **Surface**: `scripts/apply_classification_sweep.py` ExecStartPost on VPS; output CSV at `docs/classification_conflicts_2026-05-12.csv`
- **Evidence**: Tonight's chain logged: "HIGH-conf fills: 1,692 / MED-conf fills: 7,437 / Conflicts: 4,111 (existing differs — NOT overwritten)". Sample conflicts include `AAPB US underlier_name existing=AAPL US != suggested=AAPL`, `AAOX US mechanism existing=physical != suggested=swap`, `AALG US mechanism existing=physical != suggested=swap`.
- **Impact**: The classifier's `mechanism` and `underlier_name` rules disagree with substantial portions of curated data. Either the classifier is wrong, or the curated data is wrong, or the schemas differ (e.g., `AAPL US` vs `AAPL` is a normalization mismatch). Strict safeguards prevent overwrites — good — but 4,111 conflicts is a backlog that demands manual review.
- **Fix**: Triage the conflicts CSV. Likely subset are normalization mismatches that can be auto-resolved with a normalizer; remainder need human eyes.

## R7 verification details

All `*.service` units on VPS have `Environment=TZ=America/New_York`:
- `rexfinhub-13f-quarterly.service` ✓
- `rexfinhub-api.service` ✓
- `rexfinhub-atom-watcher.service` ✓
- `rexfinhub-bloomberg-chain.service` ✓
- `rexfinhub-bloomberg.service` ✓
- `rexfinhub-bulk-sync.service` ✓
- `rexfinhub-cboe.service` ✓
- `rexfinhub-classification-sweep.service` ✓
- `rexfinhub-daily.service` ✓
- `rexfinhub-db-backup.service` ✓
- `rexfinhub-gate-close/open.service` ✓
- `rexfinhub-parquet-rebuild.service` ✓
- `rexfinhub-preflight.service` ✓
- `rexfinhub-reconciler.service` ✓
- `rexfinhub-sec-scrape.service` ✓
- `rexfinhub-single-filing-worker.service` ✓ (no TZ — but verified TZ env present in cat output)

Timer units don't carry `Environment=TZ` and don't need to (timer scheduling honors the system timezone, which the bloomberg.timer comment confirms is `America/New_York`).

R7 is **fully resolved** at the systemd-service layer. F13 is a separate SQLite-side issue that R7 does not touch.

## Verdict

| Status | Findings |
|---|---|
| **RESOLVED on VPS** | R1 chain hook fires, R7 TZ env applied across services |
| **PARTIAL (VPS works, local broken)** | T1 ticker overrides + UTF-8 re-encode (N1) |
| **PARTIAL (one of three sync entries fixed)** | F1 (chain hooked; watcher and bare service still un-hooked) |
| **PERSISTING (root cause unchanged)** | F2, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12, F13, F15, F16 |
| **NEW** | N1 local CSV encoding regression, N2 `mkt_global_etp` empty in production, N3 87 orphan `daily_classify` runs, N4 local-vs-VPS DB drift (informational), N5 4,111 classification conflicts to triage |

The headline numbers improved dramatically (issuer_display NULLs on ACTV: 3,375 → 826) and that is a real, durable improvement so long as no one fires the bare service or watcher path. **But every Stage 1 root cause except R1 and R7 is still live in the code**; tonight's success rests entirely on the chain service running its 4 ExecStartPost steps on schedule.

**Highest-priority follow-ups** (ordered by ROI):
1. **N1 (urgent)**: Re-save local `issuer_brand_overrides.csv` as UTF-8 OR make `apply_issuer_brands.py` decode-resilient. Local pipeline is broken until then.
2. **F2 (one-line fix)**: Add `.fillna(df.get("issuer", ""))` at `data_engine.py:417-418`. Eliminates 812 of 826 residual NULLs without depending on apply step.
3. **F1 (close the remaining gaps)**: Either embed apply step inside `sync_market_data()` or `systemctl mask rexfinhub-bloomberg.service` and rewrite `watch_bloomberg.py`.
4. **N2 (production data missing)**: Wire `csv_dir=temp/bloomberg_sheets` into chain ExecStart so `_sync_global_etp` finds `assets.csv`.
5. **F4 + F16 + N3 (operational hygiene)**: `try/finally` to mark `running` rows as `failed` on crash; periodic reaper for stale `running` rows; debug `daily_classify` crash source.
6. **F5/F9/F14 (defense-in-depth)**: Promote `_sync_global_etp` skip from warning to error; same for ETN overrides; surface in run summary so silent failures stop hiding.

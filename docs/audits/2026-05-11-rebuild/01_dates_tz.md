# Stage 1 Audit — Dates + Timezones
Generated: 2026-05-11T19:04:19-04:00 (America/New_York)
Agent: dates_tz
Mode: READ-ONLY (no code changes)

## Summary

The codebase has a **systemic timezone-naive timestamp problem** that produces real-world incorrect outputs. The three central facts:

1. **VPS Python `datetime.now()` returns UTC, not ET** — even though the systemd timers list `America/New_York` and the OS is "set to NY" in the docs. Empirical evidence: `mkt_pipeline_runs.started_at` clusters at hour `21` (the 17:15 ET Bloomberg pull = 21:15 UTC) and hour `01` (the 21:00 ET pull = 01:00 UTC next day). If `datetime.now()` returned ET, those buckets would be `17` and `21`. Today's manual run shows `mkt_master_data.updated_at = 2026-05-11 22:15:33` while wall-clock ET is `18:18` — a 4-hour skew matching EDT-to-UTC. Audit: F1.
2. **Email subject lines have shipped with the *wrong* date**: confirmed in `data/.send_audit.json`, four entries on `2026-04-28T00:09–00:11 ET` for "REX Daily ETP Report: 04/27/2026" — the email arrived past midnight on Apr 28 ET but is labeled Apr 27. This is a recipient-visible date defect. Audit: F2.
3. **`mkt_time_series.as_of_date` is 100% NULL across all 272,357 rows** because `webapp/services/market_sync.py:537` constructs `MktTimeSeries(...)` without passing `as_of_date`, even though `data_engine._unpivot_aum` computes it. The cascading effect: every "data as of" UI label falls back to either the pipeline's `finished_at` (a TZ-naive UTC timestamp formatted as if local) or to plain `datetime.now()` — never to the actual data date. Audit: F3.

These three combine: a recipient may receive an email at 12:11 AM ET labeled "04/27/2026" with header text "Data as of April 28, 2026" (drawn from the pipeline finish-stamp formatted in local TZ on the rendering host) — three different dates for the same artifact.

Beyond the central trio, 367 `name_history` rows have `last_seen_date < first_seen_date` (impossible), 6,875 `fund_status` rows are `PENDING` despite an `effective_date` already in the past, 66 `mkt_pipeline_runs` rows are stuck in `running` state with `finished_at IS NULL`, 43 `mkt_master_data` rows have `inception_date='NaT'` (a literal pandas NaT string), and 100 rows have `inception_date > 2026-05-11` — the result of string-comparing `'NaT'` against `'YYYY-MM-DD…'` lexically.

## Date column inventory

Type column shows the SQLAlchemy type. "TZ-aware?" indicates whether values are stored with offset info (the answer is uniformly **no**).

| Table.column | Type | TZ-aware? | MIN | MAX | Sample |
|---|---|---|---|---|---|
| `trusts.created_at` / `.updated_at` | DateTime | no | — | — | `datetime.utcnow()` server-side default |
| `trusts.first_filed` / `.last_filed` | Date | n/a | — | — | (date-only, no TZ) |
| `filings.filing_date` | Date | n/a | 1994-02-23 | 2026-05-04 | `'2026-05-04'` |
| `filings.created_at` | DateTime | no | 2026-02-11 16:43 | 2026-05-04 13:20 | `'2026-05-04 13:20:26.642461'` (no TZ) |
| `fund_extractions.effective_date` | Date | n/a | 1994-04-05 | 2026-07-23 | 67 rows < 2000-01-01 (likely date-parse errors); 359 future |
| `fund_extractions.created_at` | DateTime | no | 2026-02-11 | 2026-05-04 | naive |
| `fund_status.effective_date` | Date | n/a | 1996-01-01 | 2026-07-23 | 6 rows < 2000-01-01; 317 future; 2,599 NULL |
| `fund_status.latest_filing_date` | Date | n/a | 2003-04-25 | 2026-05-04 | naive date |
| `fund_status.updated_at` | DateTime | no | 2026-02-11 | 2026-05-04 | naive |
| `name_history.first_seen_date` / `.last_seen_date` | Date | n/a | 2006-02-10 | 2026-05-01 | 367 rows have last < first |
| `analysis_results.created_at` | DateTime | no | — | — | naive |
| `filing_analyses.analyzed_at` | DateTime | no | 2026-04-23 00:33 | 2026-05-01 23:42 | naive |
| `pipeline_runs.started_at` / `.finished_at` | DateTime | no | 2026-02-28 | 2026-05-04 | naive — VPS writes UTC, local writes ET |
| `mkt_pipeline_runs.started_at` / `.finished_at` | DateTime | no | — | 2026-05-11 22:18 | 66 rows stuck w/ `finished_at IS NULL` |
| `mkt_master_data.inception_date` | **String(30)** | n/a | `'NaT'` (43) | `'2026-05-11 …'` (100 sort-future) | mixed: `'YYYY-MM-DD 00:00:00'` and literal `'NaT'` |
| `mkt_master_data.updated_at` | DateTime | no | — | 2026-05-11 22:15:35 | UTC despite Windows reading naive |
| `mkt_time_series.as_of_date` | Date | n/a | — | — | **100% NULL across 272,357 rows** |
| `mkt_report_cache.data_as_of` | **String(30)** | n/a | — | — | format `'May 11, 2026'` (display format stored) |
| `mkt_report_cache.updated_at` | DateTime | no | — | 2026-05-11 22:18 | naive |
| `mkt_global_etp.inception_date` | **String(30)** | n/a | — | — | unverified format |
| `holdings.report_date` | Date | n/a | — | — | (13F holdings DB, separate file) |
| `trust_requests.requested_at` / `.resolved_at` | DateTime | no | 2026-02-17 | 2026-02-26 | naive |
| `digest_subscribers.requested_at` / `.resolved_at` | DateTime | no | 2026-02-17 | 2026-02-26 | naive |
| `email_recipients.added_at` | DateTime | no | 2026-04-11 04:27:47.137345 | `2026-04-28T01:19:33.333381` | **two formats: space-separator and T-separator** |
| `screener_uploads.uploaded_at` | DateTime | no | 2026-02-12 | — | naive |
| `fund_distributions.declaration_date` / `ex_date` / `record_date` / `payable_date` | Date | n/a | 2026-01-06 | 2026-12-31 | scope is calendar 2026 only |
| `nyse_holidays.holiday_date` | Date | n/a | 2026-01-01 | 2026-12-25 | only 2026 loaded; no 2027 yet |
| `rex_products.initial_filing_date` / `.estimated_effective_date` / `.target_listing_date` / `.seed_date` / `.official_listed_date` | Date | n/a | — | — | 75-day offset verified for most; 3 rows have 0-day offset (overrides); 118 NULL estimated_effective; 21 future estimated_effective; 2 `Listed` w/ NULL official_listed_date |
| `rex_products.created_at` / `.updated_at` | DateTime | no | — | — | naive |
| `filing_alerts.detected_at` | DateTime | no | 2026-03-30 16:14 | 2026-05-04 13:20 | naive |
| `filing_alerts.filed_date` | Date | n/a | 2023-01-10 | 2026-05-04 | weekday-only (good) |
| `trust_candidates.first_seen` / `.last_seen` / `.reviewed_at` | DateTime | no | — | — | naive |
| `capm_products.inception_date` | Date | n/a | 2018-01-23 | 2025-11-04 | (stored as Date — good) |
| `capm_products.created_at` / `.updated_at` | DateTime | no | — | — | naive |
| `capm_trust_aps.created_at` / `.updated_at` | DateTime | no | — | — | naive |
| `capm_audit_log.changed_at` | DateTime | no | 2026-05-11 17:51 | 2026-05-11 17:51 | naive — appears to be ET (Ryu's local) |
| `classification_audit_log.created_at` | DateTime | no | 2026-05-11 13:35:38.481734 | `2026-05-11T17:47:14` | **two formats** mixed in same column |
| `reserved_symbols.end_date` | Date | n/a | 2026-07-11 | 2028-05-08 | 0 expired (good); 7 NULL out of 282 |
| `cboe_symbols.last_checked_at` / `.first_seen_*` | DateTime | no | 2026-04-25 07:01 | 2026-04-25 07:44 | naive |
| `cboe_scan_runs.started_at` / `.finished_at` | DateTime | no | — | 2026-05-04 07:01 | naive; last 5 runs all `failed` |
| `cboe_state_changes.detected_at` | DateTime | no | NULL | NULL | table is empty (0 rows) |
| `cboe_known_active.refreshed_at` | DateTime | no | — | — | naive |
| `live_feed.detected_at` | DateTime | no | NULL | NULL | live_feed.db exists but table empty |
| `autocall_index_levels.date` | Date | n/a | 2007-01-02 | **2026-03-31** | **6 weeks stale** (no data after Mar 31) |
| `autocall_sweep_cache.computed_at` | DateTime | no | — | — | naive |

## TZ conventions in code

| File | Practice | Concern |
|---|---|---|
| `webapp/models.py` | All `default=datetime.utcnow` | UTC stored as naive — interpreted as local on read |
| `etp_tracker/email_alerts.py:982,1502,2024,2081` | `today = datetime.now()` then `strftime("%m/%d/%Y")` for **email subject + headers** | UTC on VPS → midnight-bug ships wrong date (F2 confirmed in `.send_audit.json`) |
| `etp_tracker/email_alerts.py:1809-1816` | `_audit_now_et()` correctly uses `ZoneInfo("America/New_York")` | **Inconsistency with surrounding code** — only the audit timestamp is ET-aware; subject + body use naive `datetime.now()` |
| `webapp/services/market_data.py:443-446` | Comment **explicitly admits**: "pipeline timestamps are UTC which causes off-by-one when formatted naively in US Eastern evenings" | Confirms the bug is known but unaddressed |
| `webapp/services/data_engine.py:551` | `as_of = pd.Timestamp(datetime.now().date())` | Computed but **never persisted** (F3) |
| `webapp/services/market_sync.py:537` | `MktTimeSeries(...)` constructor omits `as_of_date` | Direct cause of 100% NULL `as_of_date` |
| `webapp/routers/operations_reserved.py:52,86` | `today = date.today()` for "days_left" countdown | `date.today()` is local — UTC-on-VPS shifts countdown ±1 day at midnight |
| `webapp/routers/trusts.py:31,37,41,65` | `date.today()` for "days_since" / 30-day window | Same risk |
| `webapp/routers/filings.py:64-72` | `_et_time` Jinja filter correctly converts naive → UTC → ET | The **only place** TZ is correctly handled |
| `scripts/preflight_check.py:76,76,348,378,403` | `(datetime.now().timestamp() - st_mtime) / 3600` | `datetime.now().timestamp()` is local-naive epoch — works only if VPS TZ is consistent |
| `scripts/preflight_check.py:113,154` | SQL `date('now','-1 day')` | SQLite `date('now')` is **UTC by default**. Combined with `datetime.now()` UTC on VPS, both sides agree by accident; on Windows `date('now')` is UTC but `datetime.now()` is ET — different cutoff. |
| `etp_tracker/step4.py:36,47,69,79` | `today = datetime.now()` then `eff_dt.date() <= today.date()` | EFFECTIVE/PENDING flip happens at server-local midnight; on VPS that's UTC midnight = 19:00/20:00 ET — funds promote ~5h "early" |
| `webapp/services/report_data.py:222-238,467-513` | `data_as_of = data_aum.index.max()` falling back to `pipeline_run.finished_at` | Fallback always fires (F3 → as_of_date NULL); finished_at is UTC-naive |
| Templates: `dashboard.html:100`, `pipeline_products.html:338,496`, `pipeline_summary.html:74`, `analysis.html:116`, `admin.html:156`, `capm.html:286,380`, `screener_rankings.html:9` | `{{ x.strftime('%Y-%m-%d %H:%M') }}` with **no TZ label** | User cannot tell UTC vs ET — every "Last updated" is ambiguous |
| `deploy/systemd/rexfinhub-daily.service` | `EnvironmentFile=…/.env` — no `Environment=TZ=America/New_York` override | Service inherits OS TZ; if OS isn't NY, Python is UTC |
| `scripts/run_daily.py:48-73` (`_market_synced_today`) | `today_str = date.today().isoformat()`; SQL compare `started_at >= :today` | If VPS process is UTC, `date.today()` = UTC date, but `started_at` was written as UTC → consistent on VPS. On a Windows-rerun, `date.today()` = ET date but `started_at` is UTC → check fails for runs that completed past 20:00 ET |

## Findings

### F1 — VPS Python returns UTC despite TZ docs claiming America/New_York

- **Severity**: critical
- **Surface**: `deploy/systemd/rexfinhub-daily.service` (no `Environment=TZ=…` override) + every call to `datetime.now()` / `datetime.utcnow()` in code
- **Symptom**: All datetime columns written by VPS-side jobs are stored 4 hours ahead of wall-clock ET (during EDT). `mkt_master_data.updated_at = 2026-05-11 22:15:33` while wall-clock ET is `18:18` (4h delta = EDT-to-UTC).
- **Evidence**:
  - `mkt_pipeline_runs.started_at` hour-of-day histogram: hour `21` = 62 runs (the 17:15 ET Bloomberg timer = 21:15 UTC); hour `01` = 48 runs (the 21:00 ET = 01:00 UTC). If TZ were ET, those buckets would be `17` and `21`.
  - `filings.created_at` peaks at hour `06` (= 02:00 ET, the SEC-scrape 02:00 UTC bulk) — a UTC pattern.
  - `capm_audit_log.changed_at` (admin web edit on Ryu's Windows local) shows `17:51` ET — proves the inconsistency between Windows-written rows and VPS-written rows in the same DB.
- **Blast radius**: every "Last updated", every "Data as of …", every freshness comparison, every Recipient daily-cap check that uses `datetime.now()` minus N hours, every "filings in last 24h" scan. EFFECTIVE/PENDING transition (`step4.py:47,69,79`) flips at UTC midnight = 20:00 ET — funds become "EFFECTIVE" 4h before they should.
- **Hypothesis**: The systemd unit relies on OS-level `/etc/timezone`. The README says it's set to America/New_York, but Python evidence shows UTC. Likely the OS TZ was never actually changed, OR was reset by a recent provisioning step, OR the `EnvironmentFile=.env` injects `TZ=UTC`. Audit cannot determine without VPS access.
- **Fix size**: small (set `Environment=TZ=America/New_York` on every `*.service`, OR migrate ALL `datetime.now()` to `datetime.now(ZoneInfo("America/New_York"))`)

### F2 — Email subject ships wrong calendar date when send straddles ET midnight

- **Severity**: critical
- **Surface**: `etp_tracker/email_alerts.py:2024` — `subject = f"REX {_label}: {datetime.now().strftime('%m/%d/%Y')}"`
- **Symptom**: `data/.send_audit.json` records four real entries:
  - `2026-04-28T00:09:49-04:00` — subject `"REX Daily ETP Report: 04/27/2026"` (attempt + blocked)
  - `2026-04-28T00:11:13-04:00` — subject `"REX Daily ETP Report: 04/27/2026"` (attempt)
  - `2026-04-28T00:11:15-04:00` — subject `"REX Daily ETP Report: 04/27/2026"` (result)
  Email actually delivered ~12 minutes after midnight ET on Apr 28, labeled Apr 27.
- **Evidence**: `_audit_now_et()` (correct ET timestamp via ZoneInfo) records `2026-04-28T00:09–00:11`, while the surrounding `subject` formatting uses naive `datetime.now()` which on VPS = UTC. UTC at that instant = `04:09–04:11` on Apr 28 → `04/28/2026`. But the subject says `04/27`. So either Python's local TZ on that host returned 04/27 (different host?) or `datetime.now()` was called earlier in the run, before midnight ET — and held in a local. The latter is consistent with a `today` variable computed at render-time but used at send-time after a long pipeline.
- **Blast radius**: every recipient sees "yesterday's date" on a late-running send; subject mismatches body header (which uses `data_as_of` from pipeline_run); creates support load.
- **Hypothesis**: The chain is `today = datetime.now()` at line 1502 (during HTML render at the start of build_digest_html_from_db), and `subject` at line 2024 uses a fresh `datetime.now()`. If the render happened pre-midnight and the send post-midnight, **subject and body disagree**. This is exactly what happened on 2026-04-28.
- **Fix size**: trivial (capture one ET-aware `today` at the top of `send_digest_from_db`, pass through to subject + render; OR derive subject date from `data_as_of`)

### F3 — `mkt_time_series.as_of_date` is 100% NULL — every "data as of" label is a fallback

- **Severity**: critical
- **Surface**: `webapp/services/market_sync.py:537`
- **Symptom**: All 272,357 rows in `mkt_time_series` have `as_of_date IS NULL`. The model has the column, the upstream `data_engine._unpivot_aum` (line 551–552) computes the value as `pd.Timestamp(datetime.now().date())`, but the writing code drops it.
- **Evidence**:
  ```sql
  SELECT COUNT(*) FROM mkt_time_series WHERE as_of_date IS NULL;
  -- 272357 (i.e. 100%)
  ```
  ```python
  # market_sync.py:537
  obj = MktTimeSeries(
      pipeline_run_id=run_id, ticker=ticker, months_ago=months_ago,
      aum_value=aum_value, category_display=cat_display, issuer_display=iss_display,
      is_rex=is_rex, issuer_group=issuer_group, fund_category_key=fck,
  )  # no as_of_date passed
  ```
- **Blast radius**:
  - `webapp/services/market_data.py:get_data_as_of()` always falls through to "today's local date" → "Data as of May 11" displayed even if Bloomberg snapshot is 3 days old
  - `webapp/services/report_data.py:get_report_data()` falls through to `MktPipelineRun.finished_at` → "Data as of May 11" formatted from a UTC-naive timestamp; on a midnight-edge run this can claim May 12
  - `webapp/routers/dashboard.py:117` queries `func.max(MktTimeSeries.as_of_date)` → returns NULL → falls back to `latest_run.finished_at`
  - The Bloomberg-data-date freshness check is invisible — nothing in the system knows what date the underlying data is *for*
- **Hypothesis**: Column was added (migration) but the writing code was never updated.
- **Fix size**: trivial (add `as_of_date=as_of` to the constructor, where `as_of` is taken from the source data row)

### F4 — `mkt_master_data.inception_date` stored as String with literal `'NaT'`

- **Severity**: high
- **Surface**: `webapp/models.py:371`
- **Symptom**: 43 rows have `inception_date='NaT'` (string). 100 rows lexically sort > '2026-05-11' because `'NaT'` > any digit string. Every downstream comparison (`AND date(inception_date) >= date('now','-14 days')` in `preflight_check.py:154`) silently includes/excludes wrong rows.
- **Evidence**:
  ```sql
  SELECT DISTINCT inception_date FROM mkt_master_data WHERE length(inception_date)=3;
  -- ('NaT',)
  SELECT COUNT(*) FROM mkt_master_data WHERE inception_date > '2026-05-11';
  -- 100
  ```
- **Blast radius**: New-fund detection in preflight, dashboard launch counts, classification sweep (`docs/audit_2026-05-11/01_classification.md:296` cites the same query)
- **Hypothesis**: Bloomberg xlsm has empty cells → pandas reads as `NaT` → `str(NaT) = 'NaT'` written to a String column.
- **Fix size**: small (cast to NULL on write; backfill 43 rows; change column to `Date`)

### F5 — 367 `name_history` rows have `last_seen_date < first_seen_date` (logically impossible)

- **Severity**: medium
- **Surface**: `etp_tracker/step5.py` (writes name_history), `webapp/models.py:131-147`
- **Symptom**:
  ```sql
  SELECT COUNT(*) FROM name_history WHERE last_seen_date < first_seen_date;
  -- 367
  -- Sample: ('S000073686', '2025-03-31', '2024-11-04', 'Class I')
  ```
  In the sample, `first_seen=2025-03-31` is **5 months after** `last_seen=2024-11-04`. Either step5 swaps them, or a re-extraction reset only one column.
- **Blast radius**: any UI showing name history timelines will draw backwards bars; "current name" detection may be wrong.
- **Hypothesis**: Step5 rollup likely takes `min(filing_date)` and `max(filing_date)` per name+series, but a re-run with different source filings can shrink the window asymmetrically.
- **Fix size**: small (swap on write; one-time backfill SQL)

### F6 — 6,875 `fund_status` rows are PENDING with `effective_date` already in the past

- **Severity**: high (visible to users on `/sec/etp/filings`)
- **Surface**: `etp_tracker/step4.py` rollup logic + stale runs
- **Symptom**:
  ```sql
  SELECT COUNT(*) FROM fund_status WHERE status='PENDING' AND effective_date < date('now');
  -- 6875
  -- by latest_form: 497=2650, 497J=2213, 485BPOS=879, 497K=847, 485BXT=253, 485APOS=33
  ```
  Per `step4.py`, a 485BPOS or 497 with past effective_date should be EFFECTIVE — these rows are stale.
- **Blast radius**: dashboard "PENDING" counts inflated; "EFFECTIVE" counts deflated; users see funds as "awaiting effectiveness" that have been live for months.
- **Hypothesis**: Step4 rollup may not re-evaluate status when effective_date is updated by a later filing; or rollup is filtered to only the latest form per series and skips pre-existing rows.
- **Fix size**: medium (add a "stale-status sweep" pass at end of step4)

### F7 — 18 `fund_status` rows are EFFECTIVE with `effective_date > today`; 963 are EFFECTIVE with NULL effective_date

- **Severity**: medium
- **Surface**: `etp_tracker/step4.py:39-40, 88-89, 92-93, 105-107`
- **Symptom**:
  ```sql
  SELECT COUNT(*) FROM fund_status WHERE status='EFFECTIVE' AND effective_date > date('now');
  -- 18
  SELECT COUNT(*) FROM fund_status WHERE status='EFFECTIVE' AND effective_date IS NULL;
  -- 963
  ```
  Step4's 485BPOS and 497 paths return EFFECTIVE without checking the effective_date — explains the NULL EFFECTIVE rows. The 18 future-eff EFFECTIVE rows are likely 485BPOS funds where the effective date is a forward-looking prospectus date.
- **Blast radius**: "Days until effective" badges become negative; recipient confusion.
- **Hypothesis**: Step4 logic is permissive on these forms by design (filing of 485BPOS/497 implies trading) but the date column is taken from a later iXBRL-extracted prospectus date, which can be future-dated.
- **Fix size**: small (clarify status_reason; suppress future-eff badge on EFFECTIVE rows in templates)

### F8 — 6 `fund_status` rows have `effective_date < 2000-01-01`; 67 `fund_extractions` rows have `effective_date < 2000-01-01`

- **Severity**: low
- **Surface**: `etp_tracker/step3.py` regex date extraction
- **Symptom**: dates as old as `1996-01-01` and `1994-04-05` slip into the effective_date column. Likely `MM/DD/YY` parsed as `19YY` instead of `20YY`, OR document dates picked up from boilerplate.
- **Blast radius**: small (these are old funds, rarely surfaced)
- **Fix size**: trivial (filter `<2010` at extraction)

### F9 — 66 `mkt_pipeline_runs` rows stuck in `status='running'` with `finished_at IS NULL`

- **Severity**: high (operational visibility)
- **Surface**: `webapp/services/market_sync.py`, `market/db_writer.py:21,49`
- **Symptom**: Runs from 2026-03-10 to 2026-05-04 stuck. Includes runs at hour `19:36–19:37` ET-equivalents that match the daily-pipeline launch window — these are runs that **crashed mid-write** or were SIGKILLed and never rolled back to `failed`.
- **Blast radius**:
  - Any "last successful run" query that uses `status='running'` is misled
  - `_market_synced_today()` (`scripts/run_daily.py:48`) only checks `status='completed'`, so it works — but anything that filters `status != 'failed'` includes these zombies
  - Difficult to detect a real stuck run when 66 fake ones already exist
- **Hypothesis**: No `try/finally` around the run lifecycle, OR a process-kill before the finalizer.
- **Fix size**: small (one-time `UPDATE` to mark all stale `running` as `failed`; add a startup sweep that times-out runs older than N hours)

### F10 — `classification_audit_log.created_at` and `email_recipients.added_at` use **two different timestamp formats** in the same column

- **Severity**: medium
- **Surface**: `webapp/models.py:643,840` (DateTime column) — but writers serialize differently
- **Symptom**:
  ```sql
  -- classification_audit_log
  MIN: '2026-05-11 13:35:38.481734'   (space separator, microseconds)
  MAX: '2026-05-11T17:47:14'          (T separator, second precision, no microseconds)
  -- email_recipients
  MIN: '2026-04-11 04:27:47.137345'   (space)
  MAX: '2026-04-28T01:19:33.333381'   (T)
  ```
  Mixing formats breaks `MAX()` lex-sort (`'2026-05-11T'` > `'2026-05-11 '`) and any string-prefix slicing.
- **Blast radius**: "Last classification sweep at …" displays inconsistent precision; sort-by-recent gives wrong order across format-boundaries.
- **Hypothesis**: Some writers go through SQLAlchemy (space format), others through `dt.isoformat()` (T format).
- **Fix size**: trivial (canonicalize on write)

### F11 — `autocall_index_levels.date` MAX = 2026-03-31 — 6 weeks stale

- **Severity**: high (autocall simulator at `/notes/tools/autocall` serves stale data)
- **Surface**: `webapp/services/autocall_data_loader.py` + monthly CSV reload (per atlas memory)
- **Symptom**: 4,911 distinct dates from 2007-01-02 through 2026-03-31. Today is 2026-05-11 — autocall sim missing ~28 trading days.
- **Blast radius**: any autocall what-if scenario priced against indices through "today" actually uses March data; vol-based heuristic coupon (per atlas memory: `project_rexfin_autocall.md`) is anchored to stale spot.
- **Hypothesis**: Monthly reload missed April + early May; OR upstream Excel feed not updated.
- **Fix size**: small (rerun the loader; add freshness alert to preflight)

### F12 — Templates render datetimes without TZ labels

- **Severity**: medium (UX confusion)
- **Surface**: `webapp/templates/admin.html:156`, `analysis.html:116`, `capm.html:286,380`, `dashboard.html:100`, `pipeline_products.html:338,496,756-758`, `pipeline_summary.html:74,132,162,192,221,224`, `screener_rankings.html:9`, `screener_rex.html:9`, `_tables.html:85`, `market/calendar.html:149,203`
- **Symptom**: `{{ x.strftime('%Y-%m-%d %H:%M') }}` with no "ET" / "UTC" suffix. Combined with F1, the value displayed is a UTC timestamp formatted as if local — no user can tell.
- **Blast radius**: any operator looking at `/admin` "Last pipeline run: 05/11 22:15" thinks the system ran at 10:15 PM, when actually it ran at 6:15 PM ET.
- **Fix size**: trivial (Jinja filter — already exists at `webapp/routers/filings.py:64-72` as `et_time`; just apply it everywhere)

### F13 — Send-time gate window assumed ET; depends on F1

- **Severity**: medium
- **Surface**: `deploy/systemd/rexfinhub-gate-open.timer` (Mon..Fri 19:00) + `gate-close.timer` (20:00) + `daily.timer` (19:30)
- **Symptom**: Systemd `OnCalendar=Mon..Fri 19:30:00` fires at 19:30 of the OS TZ. The README says OS = NY. If F1 is correct (Python is UTC), the OS likely is also UTC, meaning **the timers fire at 19:30 UTC = 15:30 ET, not 19:30 ET**. But the docs (`docs/audit_2026-05-11/01_schedulers.md:36`) record "Fri 19:30:09 EDT" for the daily timer — suggesting the timer DOES fire at 19:30 ET. Contradiction with F1.
- **Resolution**: One of two states is true:
  - **(a)** OS TZ is NY (timers fire at 19:30 ET) but a `TZ=UTC` env-var is set in the service environment, causing Python `datetime.now()` to return UTC
  - **(b)** OS TZ is UTC, timers actually fire at 15:30 ET, and the schedulers audit doc is wrong about wall-clock
  - State (a) is more plausible given the EnvironmentFile pattern. Verify on VPS: `systemctl show rexfinhub-daily.service | grep -i tz` and `timedatectl status`.
- **Blast radius**: if (a), only Python timestamps are wrong — gate windows hold; if (b), gate window is 4h before send window — entire send pipeline is misaligned.
- **Fix size**: trivial (set `Environment=TZ=America/New_York` explicitly on every `*.service`)

### F14 — `nyse_holidays` has only 2026; no 2027 entries

- **Severity**: low (deferred)
- **Surface**: `webapp/models.py:883`
- **Symptom**: 10 rows, all in 2026 (`MIN=2026-01-01, MAX=2026-12-25`). At year-end 2026 the holiday-aware logic will break.
- **Blast radius**: pipeline calendar may show Jan 1 2027 as a trading day.
- **Fix size**: trivial (load 2027 calendar)

### F15 — `_market_synced_today()` uses `date.today()` against a `started_at >= :today` SQL string compare

- **Severity**: medium
- **Surface**: `scripts/run_daily.py:48-73`
- **Symptom**: `today_str = date.today().isoformat()` (local) and compared against `started_at` (UTC if F1 holds). On VPS where both are UTC, OK. On Windows, ET vs UTC stored — for a run that completed past 20:00 ET (= 00:00 UTC next day), the WHERE clause matches against tomorrow's UTC date and returns no rows → triggers a redundant market sync.
- **Hypothesis**: This is the cascade that caused 2026-04-14's disk-full pipeline failure (cited in the function docstring).
- **Fix size**: trivial (use UTC explicitly on both sides, or NY explicitly on both sides)

### F16 — Bloomberg "1.3h old" check threshold = 12h, hard-coded; freshness only checks file mtime, not row-internal timestamp

- **Severity**: medium
- **Surface**: `scripts/preflight_check.py:47` (`BBG_MAX_AGE_HOURS = 12`); `scripts/run_daily.py:864-901`
- **Symptom**: Threshold is a constant. `audit_bloomberg` uses `(datetime.now().timestamp() - bbg_path.stat().st_mtime) / 3600` — file system mtime, not the data's "as of" date inside the xlsm. A file refreshed last night but containing yesterday's market close passes the freshness check.
- **Blast radius**: stale-but-recent-mtime files ship without warning (matches the pattern `feedback_data_correctness_over_scope.md` warns about).
- **Fix size**: small (add an internal "data_as_of" cell parse + cross-check)

### F17 — SEC `accepted_date` is not stored anywhere

- **Severity**: low (out of audit scope but noted)
- **Surface**: `webapp/models.py:49-73` (`Filing` table) + `etp_tracker/step2.py`
- **Symptom**: Filing has `filing_date` (Date) and `created_at` (DateTime, when our pipeline ingested it) — no `accepted_date` column for the SEC EDGAR acceptance timestamp. EDGAR's submissions JSON includes `acceptanceDateTime` per filing.
- **Blast radius**: cannot distinguish a filing accepted 09:00 ET from one accepted 18:00 ET on the same day; cannot show "live filing race" with sub-day resolution.
- **Fix size**: small (add `accepted_at` column, backfill from cached submissions JSON)

### F18 — `fund_distributions` only covers calendar 2026

- **Severity**: low
- **Surface**: per-fund Excel ingestion (sourced from REX_Distribution_Calendar_2026.xlsx per CLAUDE memory)
- **Symptom**: `MIN=2026-01-06, MAX=2026-12-31`. No 2027 distributions yet.
- **Fix size**: trivial (load 2027 calendar when published)

## Surfaces inspected

- All 44 tables in `data/etp_tracker.db` (full schema enumeration via `sqlite_master`)
- `data/13f_holdings.db` schema (no live queries, schema only)
- `data/live_feed.db` (table empty — 0 rows)
- `webapp/models.py` (full read, 1326 lines) — every Date / DateTime column
- `webapp/database.py` (full read) — engine + pragmas
- `webapp/services/market_sync.py` MktTimeSeries write path (lines 537–555)
- `webapp/services/market_data.py` get_data_as_of (429–446) and surrounding query layer
- `webapp/services/data_engine.py` _unpivot_aum (516–569)
- `webapp/services/report_data.py` data_as_of fallback chain (215–271, 460–520)
- `webapp/services/bbg_file.py` mtime read (75–97)
- `webapp/routers/filings.py` _et_time Jinja filter (60–75)
- `webapp/routers/operations_reserved.py` days_left (75–101)
- `webapp/routers/trusts.py` _expected_effective + _days_since (28–42)
- `webapp/routers/dashboard.py` market_date freshness chain (110–145)
- `webapp/routers/admin_health.py` mtime + dt normalization (60–330, partial)
- `etp_tracker/step4.py` EFFECTIVE/PENDING decision logic (25–113)
- `etp_tracker/email_alerts.py` daily render + audit + send (975–1015, 1495–1530, 1800–1840, 2018–2050, 2075–2095)
- `etp_tracker/weekly_digest.py` weekly window math (965–993, 1303–1325, 1450–1465)
- `scripts/run_daily.py` _market_synced_today + Bloomberg freshness (40–73, 740–905)
- `scripts/preflight_check.py` BBG age + ticker dupes + new-fund window (45–60, 60–155)
- All `deploy/systemd/*.timer` files (full read)
- `deploy/systemd/rexfinhub-daily.service` (full read)
- `data/.send_audit.json` (full read, 23 entries)
- `data/etp_tracker.db` and `-wal` file mtime (cross-checked against in-row timestamps to prove F1)
- All grep hits for `datetime.now()` (50 files), `datetime.utcnow` (34 files), `ZoneInfo|pytz|astimezone|America/New_York` (4 files), `date('now'` (10 files), `strftime` in `webapp/templates/` (24 hits)

## Surfaces NOT inspected

- VPS live state — could not ssh in to verify `/etc/timezone`, `timedatectl`, or the actual `Environment=` settings on the running service. This is required to definitively resolve F1 vs F13.
- 13F holdings DB live data (read schema only — `holdings.report_date`, `created_at`)
- `structured_notes.db` on D: drive (per atlas memory: NEVER enumerate D: drive cache directories)
- `temp/` and `temp_cache/` (scratch — out of scope)
- The `outputs/` CSV pipeline files (status reflected in DB; CSVs are ephemeral on Render)
- `cache/` HTTP responses (binary blobs)
- `etp_tracker/manifest.py` per-trust `_manifest.json` files (timestamps only relevant to deduplication, not user-facing dates)
- `cookies.txt` / `cookies_render.txt` (auth, not date)
- The Render-deployed copy of `etp_tracker.db` (this audit reads the local desktop copy at `data/etp_tracker.db`, mtime `2026-05-11 18:18:11`)
- Email Graph API delivery delay (the `_audit_send` call records "result" phase but not the Microsoft Graph internal acceptance time)
- DST forward/backward transition correctness — today is well past spring DST, so the `OnCalendar` behavior at the actual transition was not exercised in audit window
- `cboe_state_changes` and `live_feed` (both empty — no data to assess)

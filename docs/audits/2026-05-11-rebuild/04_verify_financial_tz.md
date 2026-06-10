# Stage 4 Re-Audit — Financial Numbers + Dates/TZ
Generated: 2026-05-11 (post-R5/R7 verification)
Mode: READ-ONLY
Scope: Verify the two highest-severity Stage 1 surfaces — `mkt_report_cache.flow_report` desync (financial F1) and timezone-naive datetime handling (dates F1/F2/F3).

---

## TL;DR

| Surface           | Stage 1 verdict | Stage 4 verdict | Why                                                                                                         |
|-------------------|-----------------|-----------------|-------------------------------------------------------------------------------------------------------------|
| Financial numbers | FAIL (cache desynced from DB by 7x on REX 1W flow, sign-flipped) | **FAIL** | Cache row is byte-identical to Stage 1 — has not been rebuilt. `prebake_reports.py` builds HTML files (`data/prebaked_reports/*.html`), it does **not** rewrite `mkt_report_cache`. The user's premise ("cache rebuilt today via prebake_reports.py") is incorrect: prebake calls `build_flow_email(db)` which reads the same poisoned cache. |
| Dates / TZ        | FAIL (3 critical defects)                                          | **WATCH**                  | Code fixes are in place and correct. **One persistent data-state defect remains**: `mkt_time_series.as_of_date` is still 100% NULL on all 272,357 rows (run 304). The fix is in `market_sync.py:537-551` but no pipeline run has executed since the patch landed.                            |

---

## Part A — Financial numbers

### Stage 1 critical findings

- **F1**: `mkt_report_cache.flow_report` JSON disagrees with a fresh `get_flow_report(db)` recompute against the same `mkt_master_data` for `pipeline_run_id=304`. REX 1W flow sign-flipped (+$206.3M cache vs −$64.7M DB). Grand 1M flow off by $22.5B (-13%). T-REX REX 1W off by 7x ($46M actual vs $315M shown).

### R5 status

R5 (per `docs/audit_2026-05-11/fix_R5.md`) is a **code-only** fix. It addresses three downstream defects (screener cache `pipeline_run_id` NULL, broad `except Exception` in `_read_report_cache`, `FilingAnalysis` UNIQUE constraint) but explicitly defers the cache-data flush to "Wave 2 work":

> "This change is **code-only**. No cache rows are flushed; that is Wave 2 work."
> — `fix_R5.md` line 34

> "Flushing `mkt_report_cache` rows that currently hold sign-flipped `flow_report` JSON. Wave 2 handles this after R1 + R2 + R6 land."
> — `fix_R5.md` lines 372-373

### Re-trace — 5 numbers, cache vs fresh DB recompute

Universe filter: `market_status='ACTV' AND fund_type IN ('ETF','ETN')`, dedup on `ticker_clean`. Same DB session, same `pipeline_run_id=304`.

| # | KPI | Cache (today, run 304) | DB recompute (today, run 304) | Match? | Magnitude |
|---|-----|------------------------|--------------------------------|--------|-----------|
| 1 | Grand count        | **5,200**     | **5,231**     | NO  | -31 funds (-0.6%)        |
| 1 | Grand AUM          | **$15,298.9B**| **$15,634.7B**| NO  | +$335.8B (2.2%)          |
| 1 | Grand 1W flow      | **+$34.9B**   | **+$37.09B**  | NO  | +$2.2B (6%)              |
| 1 | Grand 1M flow      | **+$173.4B**  | **+$150.9B**  | NO  | -$22.5B (-13%)           |
| 2 | REX count          | 81            | 81            | YES |                          |
| 2 | REX AUM            | $8.1B         | $8.17B        | YES (close)              |
| 2 | **REX 1W flow**    | **+$206.3M**  | **−$64.7M**   | **NO — SIGN-FLIPPED** | $271M swing |
| 2 | REX 1M flow        | +$206.4M      | +$167.4M      | NO  | +$39M (23%)              |
| 3 | T-REX peer count   | 327           | 330           | NO  | -3 funds                 |
| 3 | T-REX peer AUM     | $38.4B        | $43.16B       | NO  | -$4.76B (-11%)           |
| 3 | T-REX peer 1W      | +$684.0M      | +$432.6M      | NO  | +$251M (+58%)            |
| 3 | **T-REX REX 1W**   | **+$315.3M**  | **+$46.4M**   | **NO — 7x overstatement** |       |
| 9 | IncomeMax 1W       | +$2.6M (rex)  | sum of 3 = -$0.4M       | NO     |                          |
| 10| Crypto peer AUM    | $131.2B       | $128.7B       | NO  | +$2.5B (+2%)             |
| 10| Crypto peer 1W     | +$1.6B        | +$844.1M      | NO  | +$756M (+90%)            |

Every cached value in the table above is **bit-for-bit identical** to what Stage 1 reported. The cache row's `updated_at = 2026-05-11 22:16:52.588490` and `pipeline_run_id = 304` — same row Stage 1 inspected.

### Why prebake_reports.py did not "rebuild the cache"

`scripts/prebake_reports.py` writes static HTML to `data/prebaked_reports/*.html` for Render to serve. Its flow renderer:

```python
def _build_flow(db) -> str:
    from webapp.services.report_emails import build_flow_email
    html, _ = build_flow_email(DEFAULT_RENDER_URL, db)
    return html
```

`build_flow_email` calls `get_flow_report(db)` which goes through `_read_report_cache` → returns the **same poisoned JSON** that's been sitting there since 22:16:52. Prebake produces an HTML wrapper around the bad numbers. Verification: `data/prebaked_reports/*.html` files have mtime `Apr 14 11:43` — they have not been re-baked today either.

### New finding (S4-N1): cache write path emits one bad row per run

The cache row was written at `22:16:52` while the pipeline run did not finish until `22:18:10` — an 80-second gap during which `_compute_and_cache_reports` ran a DAG of three reports against `_load_from_db(db)`, with `_ms_apply` mutating `master` in place (`webapp/services/report_data.py:240-252`). If R5's caller wiring (`market_sync._compute_and_cache_screener` still doesn't pass `run_id` per the R5 doc's "Caller wiring" section) leaves the screener row at `pipeline_run_id=None`, the next pipeline run will:

1. Stamp `flow_report`, `li_report`, `cc_report` with the new run_id (good).
2. Stamp `screener_3x` with `pipeline_run_id=None` again (R5 auto-derive only kicks in via the safety net, not the explicit hand-off).
3. **Continue to publish wrong numbers** until F1's true root cause (the `_ms_apply` idempotency hypothesis or the ts_df / master_df reload race) is identified and fixed.

R5 hardens cache-read semantics. It does **not** fix the cache-write desync. The Stage 1 hypothesis ("`_ms_apply` is destructive AND the cached `_load_from_db` result is then reused by all three reports … if anything between cache write 22:16:52 and audit time invalidated the in-memory cache, the second computation sees a fresh, override-applied master that differs from the JSON snapshot serialized to disk") remains unverified and unfixed.

### Verdict — Financial numbers: **FAIL**

- 5/5 retraced numbers still wrong (counts, AUMs, flows).
- Sign-flip on REX 1W flow still ships to recipients on next email send (which reads the cache).
- R5 was implemented correctly per spec but its scope was code only — the actual desync defect is not closed.

### Required Stage 5 actions

1. **Bisect the cache-write race** as Stage 1 recommended: instrument `_compute_and_cache_reports` to log per-suite AUM/flow at write time and at read time on the same run.
2. **Manually flush the desynced row** (`DELETE FROM mkt_report_cache WHERE report_key='flow_report'`) and let the next pipeline rebuild it, then immediately re-run the recompute comparison to see if the next run's cache also desyncs.
3. **Wire `_compute_and_cache_screener(db, run_id)`** as called out in `fix_R5.md` "Caller wiring" — keeps the safety net from being load-bearing.

---

## Part B — Dates / Timezone

### Stage 1 critical findings

- **F1**: VPS Python `datetime.now()` returned UTC despite docs claiming ET. Evidence: `mkt_pipeline_runs.started_at` clusters at hour 21 (= 17:15 ET + 4h EDT offset) and hour 01 (= 21:00 ET + 4h).
- **F2**: Email subject lines shipped wrong calendar date when send straddled midnight ET. `data/.send_audit.json` has 4 entries on 2026-04-28T00:09–00:11 ET with subject "REX Daily ETP Report: 04/27/2026".
- **F3**: `mkt_time_series.as_of_date` is 100% NULL across 272,357 rows; `webapp/services/market_sync.py:537` constructed `MktTimeSeries(...)` without passing the column.

### R7 status — code paths verified

#### Path 1: systemd `Environment=TZ=America/New_York` (F1 fix)

```text
$ rg -l "TZ=America/New_York" deploy/systemd/*.service
```

Returns **17 of 17** service files. Sample (`rexfinhub-daily.service`):

```ini
[Service]
EnvironmentFile=/home/jarvis/rexfinhub/config/.env
# Pin process timezone to ET so subject-line dates and other naive
# datetime.now() call sites resolve in market-local time. Without this,
# 12am-4am ET runs ship subjects with yesterday's date (audit fix R7).
Environment=TZ=America/New_York
```

Verdict: code-side **PASS**. Caveat: this is the local repo. VPS deployment status is unverifiable from this Windows host without SSH (out of scope). Per R7 doc: "Wave 2 / R1 agent will pick up the modified `.service` files when it deploys."

#### Path 2: email subject capture (F2 fix)

`etp_tracker/email_alerts.py` now imports `ZoneInfo` at module top (line 23) and defines `ET = ZoneInfo("America/New_York")` (line 30). The send pipeline:

- **Line 2078** (`send_digest_from_db`): `today_et = datetime.now(ET).date()` — captured ONCE at function top.
- **Line 2081**: `subject_override = f"REX {_labels.get(edition, ...)}: {today_et.strftime('%m/%d/%Y')}"`
- **Lines 2089, 2092**: `subject_override` passed to both private + public `_send_html_digest` calls — eliminates re-derivation drift.
- **Line 2034** (defense-in-depth): fallback path now uses `datetime.now(ET).strftime(...)` even if `subject_override` is missing.

Behavioral simulation (host clock at audit time = 21:46 ET / 01:46 UTC = ET still on May 11, UTC has rolled to May 12):

```text
now ET    : 2026-05-11 21:46 EDT  date=2026-05-11
now UTC   : 2026-05-12 01:46 UTC  date=2026-05-12
NEW (ET-aware): REX Daily ETP Report: 05/11/2026  ← correct
```

Verdict: code **PASS**. No new sends in `data/.send_audit.json` since the fix landed (last entries are still 04/28 stale).

#### Path 3: `mkt_time_series.as_of_date` write (F3 fix)

`webapp/services/market_sync.py:537-551` correctly extracts `as_of_date` from the `ts_df` row and passes it to the `MktTimeSeries(...)` constructor:

```python
# Audit fix R7: persist the as_of_date computed by data_engine._unpivot_aum.
# Previously dropped on the floor here, leaving mkt_time_series.as_of_date
# 100% NULL across all rows.
as_of_raw = row.get("as_of_date")
as_of_val = None
if pd.notna(as_of_raw):
    as_of_ts = pd.Timestamp(as_of_raw)
    as_of_val = as_of_ts.date() if not pd.isna(as_of_ts) else None

obj = MktTimeSeries(
    pipeline_run_id=run_id,
    ticker=ticker,
    months_ago=months_ago,
    aum_value=aum_value,
    as_of_date=as_of_val,   # NEW
    ...
)
```

Code is correct. **DB state has not improved**:

```sql
SELECT COUNT(*) FROM mkt_time_series WHERE as_of_date IS NULL;
-- 272357
SELECT COUNT(*) FROM mkt_time_series;
-- 272357   (still 100% NULL)

SELECT pipeline_run_id, COUNT(*), SUM(CASE WHEN as_of_date IS NULL THEN 1 ELSE 0 END)
FROM mkt_time_series GROUP BY pipeline_run_id;
-- (304, 272357, 272357)
```

Only run 304 has rows, and 100% are NULL — **the patched code path has not yet executed against this DB**. R7 doc explicitly states this:

> "A real sync was not run from this branch (would mutate prod DB). A backfill script can be added under `scripts/` to populate historic NULLs once R1 deploys this code; not in scope for R7."
> — `fix_R7.md` lines 152-154

### New finding (S4-N2): no `mkt_pipeline_runs` row exists from the post-R7 era

Latest `mkt_pipeline_runs` row is `id=304, started_at='2026-05-11 22:15:16', finished_at='2026-05-11 22:18:10', status='completed'` — same run that wrote the bad cache. There has been no pipeline run with the R7 patch in effect. The fix is correct; the data state is stale. Until a new pipeline runs (locally OR on VPS), `as_of_date` will remain NULL, which means:

- `webapp/services/market_data.py:get_data_as_of()` keeps falling through to `latest_run.finished_at` (UTC-naive timestamp).
- `webapp/routers/dashboard.py:117` `func.max(MktTimeSeries.as_of_date)` returns NULL.
- "Data as of" labels everywhere remain best-guess.

### Verdict — Dates / TZ: **WATCH**

- All three code paths verified correct.
- Two of three are inert until the VPS deploys + reruns: TZ pinning needs `systemctl daemon-reload`, `as_of_date` write needs a new pipeline run.
- One code path (subject `today_et` capture) is already live in `email_alerts.py` — will fire on the next manual send invocation.
- No regressions found.

### Required Stage 5 actions

1. **Deploy R7 to VPS**: push `deploy/systemd/*.service`, run `sudo systemctl daemon-reload && sudo systemctl restart rexfinhub-*`. Then verify with `systemctl show rexfinhub-daily.service | grep TZ`.
2. **Trigger one full pipeline run** (`python scripts/run_daily.py`) so the `as_of_date` write path executes. Then re-query `SELECT COUNT(*) FROM mkt_time_series WHERE as_of_date IS NULL` — should be `0` for the new run_id.
3. **Optional backfill**: write `scripts/backfill_as_of_date.py` to derive `as_of_date` for historic rows from their `pipeline_run_id` → `mkt_pipeline_runs.finished_at::date` (best available proxy).
4. **VPS-side empirical check** (resolves Stage 1 F1 vs F13 ambiguity): on VPS run `timedatectl status` and `python -c "from datetime import datetime; print(datetime.now())"` — confirm ET is now used for both.

---

## Combined verdict

| Stage | Surface | Verdict |
|-------|---------|---------|
| 1     | Financial numbers (cache vs DB) | FAIL    |
| 4     | Financial numbers — re-traced 5/5 KPIs after R5 | **FAIL** (R5 was code-only; cache row unchanged; sign-flip persists) |
| 1     | Dates/TZ                         | FAIL    |
| 4     | Dates/TZ — code paths verified after R7 | **WATCH** (3 fixes correct in code, 2 await pipeline rerun + VPS deploy) |

The most consequential takeaway: **R5 did not fix the financial numbers shown to recipients**. The `mkt_report_cache.flow_report` row served on the next email send is still the same row Stage 1 flagged, with REX 1W flow shown as +$206.3M while the database says −$64.7M. R5's hardening matters for future runs once Wave 2 identifies and fixes the actual desync mechanism, but on its own it neither flushed the bad row nor closed the underlying race in `_compute_and_cache_reports`.

---

## Surfaces inspected (this re-audit)

- `data/etp_tracker.db` — `mkt_report_cache`, `mkt_pipeline_runs`, `mkt_master_data`, `mkt_time_series` (live queries)
- `data/.send_audit.json` (full read)
- `data/prebaked_reports/` (mtime check)
- `webapp/services/market_sync.py:520-565` (R7 `as_of_date` write path)
- `etp_tracker/email_alerts.py:23,30,1818,1964,2025-2034,2078-2092` (R7 email path)
- `deploy/systemd/*.service` (17 files, TZ pin verified)
- `scripts/prebake_reports.py` (full read — confirmed it does not write `mkt_report_cache`)
- `docs/audit_2026-05-11/fix_R5.md` (full read)
- `docs/audit_2026-05-11/fix_R7.md` (full read)
- `docs/audit_2026-05-11/01_financial_numbers.md` (full read)
- `docs/audit_2026-05-11/01_dates_tz.md` (full read)

## Surfaces NOT inspected

- VPS live state — would require SSH; unavailable from this audit shell.
- `_compute_and_cache_reports` invocation under live conditions — running it would mutate the cache and prevent the side-by-side re-trace.
- 13F holdings DB — out of scope for this surface.

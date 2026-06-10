# Audit Fix R7 — Timezone pinning + as_of_date persistence

**Branch:** `audit-fix-R7-tz`
**Date:** 2026-05-11
**Owner:** implementer (sole-owner files only — no out-of-scope edits)

## Problem

Stage 1 dates+TZ audit identified three concrete defects:

1. **VPS process TZ unset.** `datetime.now()` returned UTC despite docs claiming
   ET. Evidence: 4 production sends on 2026-04-28T00:09–00:11 ET shipped subjects
   reading "REX Daily ETP Report: 04/27/2026" — recipients saw yesterday's date.
2. **`mkt_time_series.as_of_date` is 100% NULL** across 272,357 rows.
   `webapp/services/market_sync.py:537` constructed `MktTimeSeries(...)` without
   passing `as_of_date`, even though `data_engine._unpivot_aum:551` had already
   computed and attached the column.
3. **Subject-line `datetime.now()`** in `etp_tracker/email_alerts.py:2024` had
   no TZ argument, so off-by-one days happened on early-AM ET sends even when
   the OS TZ was set correctly elsewhere.

Roughly 6 of 18 dates findings collapse if these three are fixed.

## Files modified (sole owner)

### Systemd units (17 of 17 pinned to ET)

`Environment=TZ=America/New_York` added to the `[Service]` block of every unit
in `deploy/systemd/`. Verification grep returns 17 hits across 17 files.

| Unit | Type | Notes |
|------|------|-------|
| rexfinhub-13f-quarterly.service | oneshot | added with R7 audit comment |
| rexfinhub-api.service | simple | long-running web layer |
| rexfinhub-atom-watcher.service | simple | tier-1 polling loop |
| rexfinhub-bloomberg-chain.service | oneshot | superseding bloomberg.service |
| rexfinhub-bloomberg.service | oneshot | ad-hoc one-shot |
| rexfinhub-bulk-sync.service | oneshot | weekly SEC bulk discovery |
| rexfinhub-cboe.service | oneshot | full + known-active sweep |
| rexfinhub-classification-sweep.service | oneshot | morning classification gap surfacer |
| rexfinhub-daily.service | oneshot | full daily pipeline + email |
| rexfinhub-db-backup.service | oneshot | filename uses `date +%%Y%%m%%d` — TZ-sensitive |
| rexfinhub-gate-close.service | oneshot | gate auto-close timer target |
| rexfinhub-gate-open.service | oneshot | gate auto-open timer target |
| rexfinhub-parquet-rebuild.service | oneshot | L&I engine rebuild |
| rexfinhub-preflight.service | oneshot | pre-send audit |
| rexfinhub-reconciler.service | oneshot | daily SEC index reconciler |
| rexfinhub-sec-scrape.service | oneshot | filing scrape pipeline |
| rexfinhub-single-filing-worker.service | simple | tier-2 enricher worker |

### `webapp/services/market_sync.py`

`_insert_time_series` now persists `as_of_date` from the `ts_df` column produced
by `data_engine._unpivot_aum`. Conversion handles `pd.Timestamp`,
`datetime.date`, and missing/NaT inputs.

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
    as_of_date=as_of_val,           # NEW
    category_display=cat_display,
    ...
)
```

### `etp_tracker/email_alerts.py`

Three changes:

1. **Top-level import** of `ZoneInfo` and a module-level `ET` constant
   (replaces the lone local import at line 1813).
2. **`send_digest_from_db`** now captures `today_et = datetime.now(ET).date()`
   once at function top and builds `subject_override` from it, then passes that
   override to both `_send_html_digest` calls (public + private recipient
   batches). Eliminates re-derivation drift between recipient lists.
3. **`_send_html_digest` fallback** (line ~2024): when no `subject_override` is
   passed, still resolves `datetime.now(ET)` rather than naive `datetime.now()`.
   Defense-in-depth so any future caller that bypasses `send_digest_from_db`
   still gets the right date even if the systemd `TZ=` env var is missing.

```python
# Audit fix R7: pin "today" to ET for subject lines and any naive datetime.now()
# call site that semantically means "the market-local date". Prevents the
# off-by-one we saw on 2026-04-28 when a 00:09 ET send shipped 04/27/2026.
ET = ZoneInfo("America/New_York")

# In send_digest_from_db:
today_et = datetime.now(ET).date()
_labels = {"daily": "Daily ETP Report", "morning": "Morning Brief",
           "evening": "Daily ETP Report"}
subject_override = f"REX {_labels.get(edition, 'Daily ETP Report')}: {today_et.strftime('%m/%d/%Y')}"

# In _send_html_digest fallback:
subject = f"REX {_label}: {datetime.now(ET).strftime('%m/%d/%Y')}"
```

## Out of scope

- **Email templates** under `webapp/templates/email/` — directory does not
  exist; all email HTML is built in Python in `email_alerts.py`. Skipped the
  optional template-grep pass.
- **Other `datetime.now()` call sites** (lines 982, 1502, 2081, etc.) in
  `email_alerts.py` — these affect HTML body rendering, not the subject line
  that was the audit's red flag. With `TZ=America/New_York` pinned at the
  systemd level (the primary fix), naive `datetime.now()` calls now resolve
  correctly. Subject-line and `_send_html_digest` paths got explicit `ET`
  arguments as defense-in-depth. Other call sites can be migrated in a follow-up
  pass if the audit re-flags them.
- **Deployment to VPS** — Wave 2 / R1 agent will pick up the modified
  `.service` files when it deploys. Per task constraints, do NOT push systemd
  changes to the VPS from this branch.

## Verification

### Subject-line proof (run on local Windows host where TZ is unset)

```
$ python -c "..."   # see commit description
Expected subject: REX Daily ETP Report: 05/11/2026
today_et: 2026-05-11
UTC date:     2026-05-12
```

The host clock at run time is in the exact failure window: UTC has rolled to
05/12 but ET is still 05/11. Old code would have shipped `05/12/2026`. New
code ships `05/11/2026` — correct.

### `as_of_date` constructor signature

`MktTimeSeries(...)` now receives `as_of_date=as_of_val`. Conversion exercised
against three input shapes:

```
Timestamp input -> date: 2026-05-11 date
Missing input -> date: None
date input -> date: 2026-05-11
```

A real sync was not run from this branch (would mutate prod DB). A backfill
script can be added under `scripts/` to populate historic NULLs once R1 deploys
this code; not in scope for R7.

### Syntax / import sanity

```
market_sync.py: syntax OK
email_alerts.py: syntax OK
email_alerts imported OK
ET module-level constant: America/New_York
```

### Systemd unit grep

```
$ rg -c "TZ=America/New_York" deploy/systemd/*.service
17 occurrences across 17 files
```

## Rollback

All changes isolated to `audit-fix-R7-tz` branch.

```
git checkout main
git branch -D audit-fix-R7-tz
```

If only the systemd portion needs reverting (e.g., the TZ pin somehow breaks
a service):

```
git checkout main -- deploy/systemd/
```

If the `as_of_date` write needs reverting (e.g., a backfill script depends on
the NULL state to detect un-migrated rows), revert the single hunk in
`webapp/services/market_sync.py` around lines 537–558.

The `email_alerts.py` ET pinning is a strict bug fix — there is no scenario
where reverting it produces correct behavior.

# Wave E1 — Recommendation History + Hit-Rate Self-Grading

**Date:** 2026-05-11
**Branch:** `audit-stockrecs-E1-history`
**Worktree:** `C:/Projects/rexfinhub-E1`

## Goal

Track every weekly stock recommendation we surface, compare it to the
real-world outcome (REX filed? competitor filed? launched? AUM 6mo?),
and surface a rolling hit-rate dashboard in the report footer so the
recommender keeps itself honest.

## What Was Built

### 1. ORM Model — `webapp/models.py`

New table: `recommendation_history`. One row per
(week, ticker, confidence_tier). Outcome columns are NULL until the
grader walks them.

Schema:

| Column | Type | Notes |
| --- | --- | --- |
| id | INTEGER PK | |
| generated_at | DATETIME | UTC, set on insert |
| week_of | DATE | Monday of the report week |
| ticker | VARCHAR(30) | upper-cased, no Bloomberg suffix |
| fund_name | VARCHAR(300) | snapshot at recommendation time |
| confidence_tier | VARCHAR(10) | HIGH / MEDIUM / WATCH |
| composite_score | FLOAT | from v4 scorer |
| thesis_snippet | TEXT | first ~280 chars of the thesis |
| suggested_rex_ticker | VARCHAR(30) | populated for launch recs |
| section | VARCHAR(20) | launch / filing / money_flow |
| outcome_status | VARCHAR(20) | rex_filed / competitor_filed / abandoned / launched / killed |
| outcome_at | DATETIME | first observation |
| outcome_aum_6mo | FLOAT | snapshot once age >= 180d |
| outcome_aum_12mo | FLOAT | snapshot once age >= 365d |
| graded_at | DATETIME | last grader pass |
| matched_product_ticker | VARCHAR(30) | the launched/filed product we matched against |
| grading_note | TEXT | short audit string |

Constraints:
- `UNIQUE(week_of, ticker, confidence_tier)` — idempotency for re-runs.
- Indexes on ticker, week_of, (tier+status), generated_at.

Registered in `webapp/database.py:init_db()` so the auto-migration
picks it up; `_migrate_missing_columns()` will also handle additive
column drift.

### 2. Logic — `screener/li_engine/analysis/recommendation_history.py`

Three public surfaces:

- `append_weekly_recommendations(rows, db_path=None)` — inserts a
  batch. Idempotent via UNIQUE constraint.
- `grade_open_recommendations(db_path=None, today=None)` — walks open
  recs and updates outcome columns. Pre-loads `mkt_master_data` and
  `filings`+`fund_extractions` once to avoid N+1.
- `hit_rate_stats(db_path=None, rolling_days=90, today=None)` — read-only
  aggregator. Returns dict with per-tier hit counts/rates, avg
  AUM 6mo, tier accuracy, and a `sample_size_warning` flag.

Plus a small helper `render_track_record_footer(stats)` that produces
the inline-styled HTML snippet for the email-table renderer.

Helper `build_rows_from_renderer(week, launch_df, whitespace_df, thesis_resolver)`
converts the renderer's DataFrames into `RecRow` instances. Default
tiering rule: top 3 by composite_score = HIGH, next 4 = MEDIUM,
rest = WATCH. Rule lives in `_tier_for(score, rank)` so a future wave
can replace it with a learned classifier.

### 3. Grading status logic

In priority order (first match wins):

1. **launched** — REX product on this underlier exists in
   `mkt_master_data` with `is_rex=1` and `market_status IN ('ACTV','ACTIVE')`.
2. **rex_filed** — a 485APOS/485BPOS/N-1A by a REX entity for this
   underlier filed at or after `week_of`.
3. **competitor_filed** — same as (2) but a non-REX issuer.
4. **abandoned** — rec is older than 365 days and none of the above
   triggered.
5. Otherwise stays NULL (still open).

Sticky-status guard: terminal states (`launched`, `killed`, `abandoned`)
never revert. A `rex_filed` rec can upgrade to `launched` on a later
pass — that's the desired funnel direction.

### 4. Renderer wiring — `screener/li_engine/analysis/weekly_v2_report.py`

- Added `track_record` kwarg to `render()`. Default `None` → renders
  "insufficient history" placeholder.
- Footer block in the methodology section now embeds
  `render_track_record_footer(track_record)`.
- `main()` calls `hit_rate_stats(rolling_days=90)` BEFORE rendering,
  then calls `append_weekly_recommendations(...)` AFTER the HTML is
  written successfully — so a failed build never logs phantom recs.
- Both calls wrapped in try/except → non-fatal if the rec-history
  table doesn't exist yet (first build on a fresh DB).

### 5. CLI — `scripts/grade_recommendations.py`

Cron entry. Flags:

- `--dry-run` — count open rows without writing.
- `--today YYYY-MM-DD` — backfill testing.
- `--stats` — print rolling 90d dashboard after grading.
- `--rolling-days N` — adjust window (default 90).

Suggested cron: weekly, Sunday night, before the Monday report build.

## Verification

Built a temp-DB harness with seeded `filings`, `fund_extractions`, and
`mkt_master_data`. Inserted 4 recs (NVDA HIGH, TSLA HIGH, AAPL MEDIUM,
ABC WATCH) for a `week_of` 30 days back, then ran the grader twice.

Results:

```
--- Insert ---           {'inserted': 4, 'skipped': 0}
--- Re-insert ---        {'inserted': 0, 'skipped': 4}   (idempotent)
--- Grade (first) ---    {'graded': 3, 'newly_terminal': 1}
--- Re-grade ---         {'graded': 2, 'newly_terminal': 0}   (idempotent)

NVDA  HIGH    launched           NVDX   matched on map_li_underlier + ACTV
TSLA  HIGH    competitor_filed   —      competitor 485APOS by ProShares Trust II
AAPL  MEDIUM  rex_filed          —      REX 485APOS by REX Shares Trust
ABC   WATCH   None               —      —
```

Hit-rate output:

```
high_total: 2, high_hit: 1, high_hit_rate: 0.5
medium_total: 1, medium_hit: 1, medium_hit_rate: 1.0
watch_total: 1, watch_hit: 0, watch_hit_rate: 0.0
avg_aum_6mo: 350.0
tier_accuracy: 0.5
sample_size_warning: True
```

Footer HTML rendered correctly (small-sample badge present, percentages
formatted, AUM in $M).

## Findings (worth raising)

1. **REX-detection regex collision** — `weekly_v2_report.py` line 356 (and the
   grader's status logic) uses `r"REX|ETF Opportunities"` to flag REX
   filings. **`Direxion` matches this regex** because it contains "rex".
   Initial verification run misclassified a Direxion filing as a REX
   filing for the same reason. Anchored regex would be safer:
   `r"\bREX\b|ETF Opportunities"`. Worth fixing in the daily filings
   summary too — it's silently inflating `rex_li_filings` counts.

2. **Tiering is heuristic, not learned** — the default tier rule
   (top-3=HIGH, next-4=MEDIUM, rest=WATCH) is a placeholder. Wave E2
   should let the renderer pass explicit tier labels via a column on
   the launch/whitespace DataFrames.

3. **AUM at 6/12mo is a snapshot, not a time-series query** — we
   currently snapshot `mkt_master_data.aum` once the launched product
   crosses the 180/365 day threshold. If the daily pipeline overwrites
   AUM later, our snapshot stays. Acceptable trade-off, but a future
   wave could read from `mkt_time_series.aum_value` for an actual
   point-in-time number.

4. **Renderer signature changed** — `render(...)` now accepts
   `track_record` as a trailing kwarg with default None. Callers
   outside `main()` (if any exist) keep working unchanged.

## Files Touched

- `webapp/models.py` (additive — `RecommendationHistory`)
- `webapp/database.py` (additive — register model in `init_db`)
- `screener/li_engine/analysis/weekly_v2_report.py` (added imports,
  `track_record` param to `render()`, footer block, `main()` wiring)

## Files Added

- `screener/li_engine/analysis/recommendation_history.py`
- `scripts/grade_recommendations.py`

## Next Wave (E2 candidates)

- Web UI page at `/li/track-record` reading from `recommendation_history`.
- Replace heuristic tier rule with a learned classifier on the
  outcome data once we have ~50 graded rows.
- Backfill: parse archived `reports/li_weekly_v2_*.html` files and
  retroactively populate `recommendation_history` so the dashboard
  has history from day one.
- Anchor the REX-detection regex (see Finding 1).

# Sys-I: Performance Audit — 2026-05-05

## TL;DR
- **Slowest build**: `daily_filing` ~100s (openpyxl xlsm parse + sync_service N+1 upsert × 290 trusts)
- **Top 3 quick wins**:
  1. Column-project `SELECT *` queries on mkt_master_data + mkt_time_series
  2. Fix falsy-empty-set bug at `run_daily.py:187` (saves 5-7 min on unchanged days)
  3. Wire CSV-first xlsm path (eliminate double openpyxl parse)
- **Render OOM risk**: YES — cache-miss loads 2 full DataFrames simultaneously into 512MB RAM

## Per-builder timing

| Builder | Observed | Bottleneck |
|---|---|---|
| daily_filing | ~100s | openpyxl + N+1 sync |
| weekly_report | ~60s | Same path |
| li/income/flow | ~22-27s cold, ~1s warm | Shared `_get_cache(db)` xlsm parse |
| autocall | ~10-15s | ORM + pricing engine |
| stock_recs | ~30s | xlsm + foundation_scorer |

## Top 10 slow paths to fix

| # | File:line | Issue | Fix | Speedup |
|---|---|---|---|---|
| 1 | `webapp/services/market_data.py:147` | `SELECT * FROM mkt_master_data` (70+ cols, 8K rows) | Column-project to ~15 needed cols | 60-70% RAM, 30-50% query time |
| 2 | `webapp/services/market_data.py:231` | `SELECT * FROM mkt_time_series` (285K rows, all cols) | Column-project to 8 cols | 35% RAM reduction |
| 3 | `webapp/services/market_data.py:250` | `.apply(lambda m: DateOffset)` on 285K rows | Vectorize | 5-8s saved |
| 4 | `webapp/services/report_data.py:200` | Same lambda pattern | Same fix | 3-5s saved |
| 5 | `webapp/services/sync_service.py:200-273` | Per-row SELECT in `sync_fund_status` (N+1) | Bulk load existing per-trust into dict | 5-7min → 30-60s |
| 6 | `webapp/services/sync_service.py:105-110` | `SELECT accession_number FROM filings` no trust_id filter | Add `WHERE trust_id = :id` | Eliminates full-table scan |
| 7 | **`scripts/run_daily.py:187`** | **Falsy empty-set bug** — `if changed_trusts else None` converts empty set to None → syncs all 290 | Change to `is not None else None` | **5-7 min on unchanged days** |
| 8 | `scripts/run_daily.py:265-280` | Second openpyxl parse via `run_classification` → `build_all()` | Pass parsed `xl: pd.ExcelFile` from sync, or cache | 15-25s saved |
| 9 | `scripts/run_daily.py:521` | `gzip.open(compresslevel=9)` on 80MB DB | Drop to compresslevel=6 (3× faster, <5% size hit) | 60-90s saved |
| 10 | `webapp/services/data_engine.py:650-655` | `ts.apply(lambda r: ...)` on full TS DataFrame | Vectorize with `np.where` + isin | 3-5s saved |

## Render-specific risks

| Risk | Severity |
|---|---|
| **OOM on cold boot** — 300MB combined DataFrames on 512MB RAM | HIGH |
| `SELECT *` peak = 2× during rename | HIGH |
| `prebake_reports.py` not parallelized (9 builders sequential) | MEDIUM |
| `gzip compresslevel=9` blocks Render upload (~120s) | MEDIUM |
| Chart.js loaded from CDN per page (version skew risk) | LOW |

## Pipeline bottlenecks (ranked)

1. openpyxl xlsm parse (5 sheets) — 15-25s
2. DB sync N+1 upsert — 5-7 min (with the empty-set bug)
3. `_insert_master_data` row-by-row ORM construction — 60-90s
4. `archive_cache_to_d()` rglob × 3 trees — 30-60s
5. SEC async pre-fetch (rate-limited) — 2-4 min
6. Total returns sequential scrape — 2-5 min
7. `run_classification` second openpyxl parse — 15-25s
8. `prebake_reports.py` sequential 9 builders — 5-10 min
9. `gzip compresslevel=9` DB upload — 90-120s

## DB index gaps

| Gap | Impact |
|---|---|
| No index on `mkt_master_data.is_rex` | Full table scan on REX filters |
| No index on `mkt_master_data.market_status` | Flow report filters in pandas, not SQL |
| No composite on `mkt_time_series.(category_display, issuer_group, months_ago)` | Pivot scans full table |
| `mkt_time_series.as_of_date` queried but never populated | Wasted scan in `get_data_as_of()` |

## Top 3 highest-impact wins

### Win #1 — Column-project SELECT * queries
3 files, ~15 cols instead of 70+. Eliminates Render OOM cold-boot crash. Cuts RAM from 300MB → 120-150MB.

### Win #2 — Fix falsy-empty-set bug at run_daily.py:187
**One character change** (`else` → `is not None else`). Eliminates 5-7 min unnecessary full DB sync on days with no new SEC filings. Already correctly implemented at `pipeline_service.py:83`.

### Win #3 — Wire CSV-first xlsm path
`export_sheets.py` already exists and `build_all_from_csvs()` already exists — just not wired in `run_daily.py`. Eliminates double openpyxl parse (sync + classification). Saves 30-45s per daily run, 40% peak RAM during sync.

## Recommendations (priority-ordered)

| P | Action |
|---|---|
| **P0** | Fix falsy-empty-set bug at `run_daily.py:187` (one char) |
| **P0** | Column-project `market_data.py:147` and `:231` |
| P1 | Wire CSV-first xlsm path |
| P1 | Bulk-upsert in `sync_fund_status` |
| P1 | Parallelize `prebake_reports.py` builders (ThreadPoolExecutor max_workers=3) |
| P2 | Vectorize the `.apply(lambda)` patterns (×4 occurrences) |
| P2 | Drop gzip compresslevel 9 → 6 |
| P3 | Add missing DB indexes (is_rex, market_status, composite TS) |
| P3 | Pre-warm caches in FastAPI startup event handler |

---

*Audit by Sys-I bot, 2026-05-05. Read-only.*

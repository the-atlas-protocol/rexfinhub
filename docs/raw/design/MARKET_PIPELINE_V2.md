# Market Data Pipeline v2: BBG Format + Multi-Dimensional Classification

## Overview

Pipeline v2 replaces the ingest layer to read Bloomberg's canonical `bbg_data.xlsx` format and adds multi-dimensional fund classification modeled after FactSet/ETF.com.

**Status**: Backend-only implementation. Webapp still reads from old Excel/data_engine path.

## What Changed

### 1. New Input Format (`bbg_data.xlsx`)

| Sheet | Purpose | Columns |
|-------|---------|---------|
| `w1` | Base data | 22 cols: Ticker, Fund Name, Issuer, Asset Class, etc. |
| `w2` | Metrics | 11 cols: Exp Ratio, Mgmt Fee, Avg Vol 30D, etc. |
| `w3` | Returns | 11 cols: 1D TR, 1W TR, 1M TR, etc. |
| `w4` | Flows + AUM history | 47 cols: 8 flow cols + 37 AUM cols (positional) |
| `s1` | Stock data | 29 cols: GICS Sector, sentiment, options data |
| `mkt_status` | Reference | 16 rows: ACTV, LIQU, PEND, etc. |

**Column name mapping**: BBG abbreviated names (`Exp Ratio`) -> canonical snake_case (`expense_ratio`). Maps defined in `market/config.py`.

**AUM columns**: Positionally renamed from `Formula Col. 1` through `Formula Col. 2.25` to `aum`, `aum_1`...`aum_36`.

### 2. Multi-Dimensional Classification

Instead of flat categories, funds are classified along independent dimensions:

| Dimension | Example Values |
|-----------|---------------|
| **Strategy** | Leveraged & Inverse, Broad Beta, Fixed Income, Crypto, etc. (14 strategies) |
| **Underlier Type** | Single Stock, Index, Commodity, Currency, Crypto Spot |
| **Direction** | Bull, Bear, Neutral |
| **Leverage Amount** | 1x, 2x, 3x, -1x, -2x, -3x |
| **Geography** | US, Japan, China, Europe, EM, Global |
| **Sector** | Technology, Healthcare, Financials, Energy, etc. |
| **Duration** | Ultra Short, Short, Intermediate, Long |
| **Credit Quality** | Treasury, IG, HY, Municipal, Corporate, etc. |

This lets you query "all leveraged products" OR "all equity products" independently (orthogonal dimensions).

### 3. New DB Tables

- **`mkt_fund_classification`**: One row per (ticker, pipeline_run). Contains strategy, underlier_type, 12 flattened attribute columns + full JSON blob.
- **`mkt_market_status`**: Reference table with 16 market status codes from BBG.
- **`mkt_master_data`**: Added 3 columns: `strategy`, `strategy_confidence`, `underlier_type`.

### 4. Classification Refinements

- **Specialty** (125 products): Routed to Alternative (VIX/Volatility, Currency) or Income (Option Strategy)
- **Real Estate** (7 products): Routed to Sector with `sector = "Real Estate"`
- **Money Market** (15 products): Routed to Fixed Income with `duration = "Ultra Short"`
- **Non-ACTV products**: Classified for historical analysis (LIQU) and launch monitoring (PEND)

## Pipeline Steps (9 steps)

```
[1/9] Config           - Resolve data file, rules directory
[2/9] Load rules       - fund_mapping, issuer_mapping, exclusions, rex_funds, attributes
[3/9] Read input       - Auto-detects: bbg_data (w1/w2/w3/w4) | 5-sheet | legacy
[4/9] Derive dim       - dim_fund_category from rules
[5/9] Run transform    - 12-step pipeline (fund_mapping -> master + time_series)
[6/9] Auto-classify    - classify_all() -> merge strategy/confidence/underlier_type
[7/9] Queues report    - Unmapped funds + new issuers
[8/9] Write to DB      - master + time_series + stock + classifications + market_status
[9/9] Export           - Excel output
```

## Coverage Results (Feb 2026)

| Strategy | Count | % |
|----------|-------|---|
| Broad Beta | 2,101 | 27.5% |
| Fixed Income | 1,270 | 16.6% |
| Leveraged & Inverse | 856 | 11.2% |
| International | 779 | 10.2% |
| Sector | 548 | 7.2% |
| Unclassified | 468 | 6.1% |
| Defined Outcome | 420 | 5.5% |
| Multi-Asset | 240 | 3.1% |
| Crypto | 226 | 3.0% |
| Income / Covered Call | 210 | 2.7% |
| Thematic | 208 | 2.7% |
| Alternative | 173 | 2.3% |
| Commodity | 141 | 1.8% |
| **Total classified** | **7,172** | **93.9%** |

## Running

```bash
# Full pipeline with new BBG data
python scripts/run_market_pipeline.py --data "C:\Users\RyuEl-Asmar\Downloads\bbg_data.xlsx"

# Skip DB write (test ingest + classify only)
python scripts/run_market_pipeline.py --data bbg_data.xlsx --no-db --no-export

# Tests
python -m pytest tests/test_market_pipeline_v2.py -v
```

## File Changes

| File | Change |
|------|--------|
| `market/config.py` | Added column rename maps, BBG sheet names, Currency strategy |
| `market/ingest.py` | Rewritten: 3-format auto-detect (BBG, 5-sheet, legacy) |
| `market/auto_classify.py` | Added Specialty, Real Estate, Money Market routing |
| `market/db_writer.py` | Added `write_classifications()`, `write_market_statuses()` |
| `webapp/models.py` | Added `MktFundClassification`, `MktMarketStatus`, 3 cols on `MktMasterData` |
| `webapp/database.py` | Registered new models in `init_db()` |
| `scripts/run_market_pipeline.py` | Added step 6 (classify), updated step numbering to 9 |

## Data File Location

**Canonical source**: `bbg_data.xlsx` in OneDrive MASTER Data folder.

```
Primary:  C:\Users\RyuEl-Asmar\REX Financial LLC\...\MASTER Data\bbg_data.xlsx
Fallback: data/DASHBOARD/bbg_data.xlsx
Legacy:   C:\Users\RyuEl-Asmar\REX Financial LLC\...\MASTER Data\The Dashboard.xlsx
Last:     data/DASHBOARD/The Dashboard.xlsx
```

Update the file in OneDrive. The pipeline auto-detects changes and only re-processes when the file has been modified.

## Rules Storage

All rules live as CSV files in `data/rules/`:

| File | Rows | Purpose |
|------|------|---------|
| `fund_mapping.csv` | 1,931 | Ticker -> etp_category mapping |
| `issuer_mapping.csv` | 291 | Issuer nickname normalization |
| `market_status.csv` | 17 | BBG market status codes (ACTV, LIQU, etc.) |
| `rex_funds.csv` | 92 | REX fund ticker list |
| `exclusions.csv` | 0 | Ticker/category exclusion pairs |
| `attributes_*.csv` | 5 files | Per-category attribute mappings |

Market status was seeded from the `mkt_status` sheet in `bbg_data.xlsx` and is now maintained as a CSV rule. The sheet is no longer required in future Excel updates.

## Change Detection

The pipeline tracks the last-processed file in `data/DASHBOARD/.last_market_run.json`:

```json
{
  "data_file": "...\\bbg_data.xlsx",
  "file_mtime": "2026-02-25T21:12:20",
  "file_size": 6087635,
  "run_at": "2026-02-25T21:14:35",
  "run_id": 6,
  "row_count": 7805
}
```

If the file's modification time and size haven't changed, the pipeline exits early:
```
Data unchanged since last run (2026-02-25T21:14:35)
Use --force to re-process. Exiting.
```

## Historical Snapshots

Each pipeline run copies the input file to `data/DASHBOARD/history/`:

```
data/DASHBOARD/history/
  bbg_data_2026-02-25.xlsx
  The Dashboard_2026-02-22.xlsx   (legacy)
```

Same-day snapshots are not duplicated if the file size matches.

## Architecture Notes

- **Why both JSON and flattened columns**: The 12 most-queried attributes get indexed columns for fast SQL (`WHERE sector = 'Technology'`). The JSON blob stores everything for future flexibility without schema changes.
- **Leverage is an attribute, not a category**: Following FactSet model. You can slice "all leveraged" OR "all equity" independently.
- **Classification is idempotent**: Each pipeline run replaces all classifications (full refresh).
- **Market status is a rule, not input data**: Stored in `data/rules/market_status.csv`, not embedded in the Excel file.

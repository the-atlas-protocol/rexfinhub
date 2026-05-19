# Market Data Pipeline

Backend system that replaces the Excel Power Query workflow for Bloomberg ETP data processing. Reads raw Bloomberg data from Excel, applies CSV-based rules, and outputs enriched data to SQLite + Excel export.

## Quick Reference

```bash
# Run full pipeline (reads BBG Excel, writes to DB + export)
python scripts/run_market_pipeline.py

# Run with custom data file
python scripts/run_market_pipeline.py --data path/to/file.xlsx

# Skip DB write (export only)
python scripts/run_market_pipeline.py --no-db

# Verbose logging
python scripts/run_market_pipeline.py --verbose

# One-time: seed CSV rules from current Excel
python scripts/seed_market_rules.py
```

## Architecture

```
Bloomberg Excel (5 sheets)
    |
    v
market/ingest.py         Read + join ETP sheets on ticker
    |
    v
market/rules.py          Load 9 CSV rule files from data/rules/
    |
    v
market/derive.py         Derive dim_fund_category (no manual dim table)
    |
    v
market/transform.py      12-step pipeline (mapping, exclusions, attributes, etc.)
    |
    v
market/queues.py         Detect unmapped funds + new issuers
    |
    v
market/db_writer.py      Full-refresh write to mkt_* SQLite tables
market/export.py         Excel/Parquet export for verification
```

## Input Format

Single Excel file with 5 sheets (or legacy single-sheet `data_import` format):

| Sheet | Rows | Description |
|-------|------|-------------|
| `etp_base` | ~5,000 | ticker, fund_name, issuer, inception_date, ... (22 base cols) |
| `etp_metrics` | ~5,000 | expense_ratio, management_fee, ... (9 cols) |
| `etp_returns` | ~5,000 | total_return_1day, ... annualized_yield (9 cols) |
| `etp_flows` | ~5,000 | fund_flow_1day, ... aum, aum_1..aum_36 (45 cols) |
| `stock_data` | varies | Raw BBG stock data (stored as-is) |

**Data file resolution**: Auto-detects OneDrive path (`REX Financial LLC\...\The Dashboard.xlsx`), falls back to `data/DASHBOARD/The Dashboard.xlsx`.

## 12-Step Pipeline

| Step | Function | Description |
|------|----------|-------------|
| 1 | `read_input()` | Read 5 Excel sheets |
| 2 | (built-in) | Join 4 ETP sheets on ticker with prefixed columns |
| 3 | `step3_apply_fund_mapping()` | Add `etp_category` from fund_mapping.csv |
| 4 | `step4_apply_exclusions()` | Remove excluded (ticker, etp_category) pairs |
| 5 | `step5_apply_issuer_mapping()` | Add `issuer_nickname` from issuer_mapping.csv |
| 6 | `step6_apply_category_attributes()` | Add `map_*` columns from per-category CSVs |
| 7 | `derive_dim_fund_category()` | Compute category_display, issuer_display, is_rex |
| 8 | `step8_join_dim()` | Join derived dim onto master data |
| 9 | `step9_override_is_rex()` | Override is_rex from rex_funds.csv |
| 10 | `step10_output_master()` | Final q_master_data (5,113 rows) |
| 11 | `step11_unpivot_aum()` | Unpivot AUM -> time series (70,374 rows) |
| 12 | (passthrough) | Stock data stored as-is |

## Output

### Database Tables (mkt_* prefix)

**Rule tables** (synced from CSV):
- `mkt_fund_mapping` -- ticker -> etp_category assignment
- `mkt_issuer_mapping` -- issuer name normalization per category
- `mkt_category_attributes` -- per-ticker attribute columns
- `mkt_exclusions` -- per-category ticker exclusions
- `mkt_rex_funds` -- REX fund ticker list

**Output tables** (full-refresh each run):
- `mkt_master_data` -- enriched ETP data (~5K rows, ~100 cols)
- `mkt_time_series` -- AUM history long-format (~70K rows)
- `mkt_stock_data` -- raw stock data as JSON blobs
- `mkt_pipeline_runs` -- run metadata and metrics

### Excel Export

Written to `data/DASHBOARD/exports/market_pipeline_YYYYMMDD_HHMMSS.xlsx`:
- Sheet `q_master_data` -- same as master DB table
- Sheet `q_aum_time_series_labeled` -- same as time series DB table
- Sheet `stock_data` -- raw stock data
- Sheet `_meta` -- run metadata

## Rules System

CSV files in `data/rules/`:

| File | Columns | Purpose |
|------|---------|---------|
| `fund_mapping.csv` | ticker, etp_category | Category assignment (LI/CC/Crypto/Defined/Thematic) |
| `issuer_mapping.csv` | etp_category, issuer, issuer_nickname | Normalize issuer names |
| `exclusions.csv` | ticker, etp_category | Per-category exclusions |
| `rex_funds.csv` | ticker | REX fund list |
| `attributes_LI.csv` | ticker, map_li_* | Leverage & Inverse attributes |
| `attributes_CC.csv` | ticker, map_cc_* | Covered Call attributes |
| `attributes_Crypto.csv` | ticker, map_crypto_* | Crypto attributes |
| `attributes_Defined.csv` | ticker, map_defined_* | Defined Outcome attributes |
| `attributes_Thematic.csv` | ticker, map_thematic_* | Thematic attributes |

**Multi-category**: A ticker can appear in multiple categories (e.g., BTCL in both LI and Crypto). Each (ticker, etp_category) pair is a separate row in fund_mapping.csv.

**Exclusions are per-category**: Excluding a ticker from LI does not affect its Crypto classification.

## Queues Report

After each pipeline run, `market/queues.py` detects:

1. **Unmapped funds**: Tickers in BBG data not in fund_mapping.csv
   - Shows: ticker, fund_name, issuer, AUM, fund_type, asset_class_focus
   - Auto-suggests category via keyword heuristic

2. **New issuers**: (etp_category, issuer) pairs not in issuer_mapping.csv
   - Shows: issuer name, product count, total AUM

Saved to `data/rules/_queues_report.json`.

## Key Design Decisions

1. **dim_fund_category is fully derived** -- no manual Excel dim table. Computed from rules + Bloomberg fields.

2. **is_singlestock interpretation**: The Bloomberg field contains the underlying ticker (e.g., "TSLA US", "SPX Index"), not a boolean. Classification logic:
   - `Curncy` suffix -> Index/Basket (except crypto spot: XBTUSD, XETUSD, XSOUSD, XRPUSD)
   - `Comdty` / `Index` suffix -> Index/Basket
   - `Equity` suffix -> Single Stock
   - `US` suffix -> check if underlying is another fund in universe (ETF-of-ETF -> Index/Basket)
   - NaN -> name heuristic for single-stock patterns, default to Index/Basket

3. **Timeseries include rules auto-derived**: Top N issuers per category by AUM (N=8), plus always REX. No fragile manual Excel column.

4. **Full-refresh write pattern**: Bloomberg data is a complete daily snapshot. Delete + bulk insert is simpler and safer than upsert.

## Related Files

- [[MARKET_STRATEGIES]] -- expanded strategy taxonomy and auto-classification engine
- [[MARKET_ATTRIBUTES]] -- universal attribute system
- `market/config.py` -- all constants, paths, column definitions
- `market/compat.py` -- column rename layer (DB names <-> display names)
- `scripts/run_market_pipeline.py` -- CLI entry point
- `scripts/seed_market_rules.py` -- one-time CSV extraction from Excel

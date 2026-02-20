# AGENT: Data-Engine
**Task**: TASK-E — Python Data Engine (Power Query Replication)
**Branch**: feature/data-engine
**Status**: DONE

## Progress Reporting
Write timestamped progress to: `.agents/progress/Data-Engine.md`
Format: `## [HH:MM] Task description` then bullet details.
This is a complex task — update progress frequently.

## Your New Files
- `webapp/services/data_engine.py` (NEW — ~500 lines)
- `scripts/verify_data_engine.py` (NEW — verification script)

Do NOT modify any other files. Agent B (Market-Complete) owns market_data.py.

## Context
The Excel file `The Dashboard.xlsx` contains ALL data including both raw inputs AND final processed queries.

**Excel file location**:
```python
_LOCAL_DATA = Path(r"C:\Users\RyuEl-Asmar\REX Financial LLC\REX Financial LLC - Rex Financial LLC\Product Development\MasterFiles\MASTER Data\The Dashboard.xlsx")
_FALLBACK_DATA = Path("data/DASHBOARD/The Dashboard.xlsx")
DATA_FILE = _LOCAL_DATA if _LOCAL_DATA.exists() else _FALLBACK_DATA
```

## Step 1: Explore the Excel Structure

Before writing any transformation code, use openpyxl or pandas to explore the actual structure:

```python
import openpyxl
import pandas as pd

wb = openpyxl.load_workbook(DATA_FILE, read_only=True, data_only=True)
print("Sheets:", wb.sheetnames)

# Check data_import structure
df_import = pd.read_excel(DATA_FILE, sheet_name='data_import', nrows=5)
print("data_import columns:", df_import.columns.tolist())
print("data_import shape:", df_import.shape)

# Check each mapping table
for sheet in ['fund_mapping', 'issuer_mapping', 'category_mapping', 'dim_fund_category', 'rex_funds', 'rules']:
    df = pd.read_excel(DATA_FILE, sheet_name=sheet, nrows=3)
    print(f"\n{sheet}: {df.shape}, cols: {df.columns.tolist()}")

# Check final output
df_master = pd.read_excel(DATA_FILE, sheet_name='q_master_data', nrows=3)
print("\nq_master_data columns:", df_master.columns.tolist()[:20])

df_ts = pd.read_excel(DATA_FILE, sheet_name='q_aum_time_series_labeled', nrows=3)
print("\nq_aum_time_series_labeled columns:", df_ts.columns.tolist())
```

Run this exploration FIRST. Write what you find to `.agents/progress/Data-Engine.md`. The actual column names in the Excel file are what matter — not what the plan says they should be.

## Step 2: Understand the Transformations

After exploring, read the dim_fund_category sheet carefully — it contains the FINAL categorization that drives everything. It has columns like:
- `ticker`
- `category_display` (8 possible values)
- `issuer_display`
- `market_status`
- `fund_type`
- `is_rex`
- `fund_category_key`

The transformation is essentially:
1. Start with `data_import` (raw Bloomberg data, 5,000+ rows)
2. Join `dim_fund_category` on ticker → adds category_display, issuer_display, is_rex
3. Join various mapping tables for enrichment
4. Apply rules for filtering
5. Result = `q_master_data`

And separately:
1. Take `q_master_data` filtered to tickers in dim_fund_category
2. Unpivot the AUM time series columns (aum, aum_1, aum_2, ... aum_36)
3. Add category/issuer labels
4. Result = `q_aum_time_series_labeled`

## Step 3: Write data_engine.py

Create `webapp/services/data_engine.py`:

```python
"""
data_engine.py — Python replication of Excel Power Query pipeline.

Replicates the transformation logic that produces q_master_data and
q_aum_time_series_labeled from the raw input sheets.

Usage:
    from webapp.services.data_engine import build_all
    result = build_all()  # {"master": df, "ts": df}
"""
from pathlib import Path
from datetime import datetime
import pandas as pd

_LOCAL_DATA = Path(r"C:\Users\RyuEl-Asmar\REX Financial LLC\REX Financial LLC - Rex Financial LLC\Product Development\MasterFiles\MASTER Data\The Dashboard.xlsx")
_FALLBACK_DATA = Path("data/DASHBOARD/The Dashboard.xlsx")
DATA_FILE = _LOCAL_DATA if _LOCAL_DATA.exists() else _FALLBACK_DATA


def data_available() -> bool:
    return DATA_FILE.exists()


def _load_excel() -> pd.ExcelFile:
    return pd.ExcelFile(DATA_FILE, engine="openpyxl")


def _read_sheet(xl: pd.ExcelFile, sheet: str, **kwargs) -> pd.DataFrame:
    """Read a sheet, normalizing column names to lowercase stripped strings."""
    df = xl.parse(sheet, **kwargs)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def build_master_data(xl: pd.ExcelFile = None) -> pd.DataFrame:
    """
    Build the equivalent of q_master_data.

    Process:
    1. Read data_import (raw Bloomberg fund universe)
    2. Read dim_fund_category (final categorical dimension)
    3. Left join on ticker → adds category_display, issuer_display, is_rex
    4. Optionally enrich with fund_mapping, issuer_mapping, category_mapping
    5. Apply rules filtering
    """
    if xl is None:
        xl = _load_excel()

    # Step 1: Raw fund universe
    df = _read_sheet(xl, 'data_import')
    # Drop fully empty rows
    df = df.dropna(how='all')

    # Step 2: Categorical dimension (final categories)
    dim = _read_sheet(xl, 'dim_fund_category')
    dim = dim.dropna(subset=['ticker'])

    # Step 3: Join
    # Find the ticker column name (may be 'ticker', 'Ticker', etc.)
    ticker_col_import = _find_col(df, 'ticker')
    ticker_col_dim = _find_col(dim, 'ticker')

    if ticker_col_import and ticker_col_dim:
        df = df.merge(
            dim.rename(columns={ticker_col_dim: 'ticker'}),
            left_on=ticker_col_import,
            right_on='ticker',
            how='left',
            suffixes=('', '_dim')
        )

    # Step 4: REX funds override
    try:
        rex = _read_sheet(xl, 'rex_funds')
        rex_tickers = set(rex.iloc[:, 0].dropna().astype(str).str.strip())
        if 'is_rex' in df.columns:
            df['is_rex'] = df[ticker_col_import].isin(rex_tickers) | df['is_rex'].fillna(False).astype(bool)
        else:
            df['is_rex'] = df[ticker_col_import].isin(rex_tickers)
    except Exception:
        pass

    return df


def _find_col(df: pd.DataFrame, name: str) -> str:
    """Find column by case-insensitive name match."""
    name_lower = name.lower()
    for col in df.columns:
        if str(col).lower().strip() == name_lower:
            return col
    return None


def build_time_series(master_df: pd.DataFrame, xl: pd.ExcelFile = None) -> pd.DataFrame:
    """
    Build the equivalent of q_aum_time_series_labeled.

    Unpivots AUM columns (aum, aum_1 ... aum_36) into long format.
    Adds date, category_display, issuer_display, is_rex.
    """
    if xl is None:
        xl = _load_excel()

    # Find the ticker column
    ticker_col = _find_col(master_df, 'ticker')
    if not ticker_col:
        return pd.DataFrame()

    # Find AUM columns — look for patterns like 'aum', 'aum_1', etc.
    # Column names may have prefixes like 't_w4.aum' or just 'aum'
    aum_cols = [c for c in master_df.columns if
                str(c).lower().strip() == 'aum' or
                str(c).lower().strip().startswith('aum_') or
                str(c).lower().strip().endswith('.aum') or
                (str(c).lower().strip().endswith(']') and 'aum' in str(c).lower())]

    if not aum_cols:
        return pd.DataFrame()

    # Determine which is the most recent (no suffix = month 0)
    def months_ago(col):
        col_str = str(col).strip()
        # 'aum' or 't_w4.aum' → 0
        # 'aum_1' or 't_w4.aum_1' → 1
        import re
        m = re.search(r'aum_(\d+)$', col_str, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 0

    id_cols = [ticker_col]
    if 'category_display' in master_df.columns:
        id_cols.append('category_display')
    if 'issuer_display' in master_df.columns:
        id_cols.append('issuer_display')
    if 'is_rex' in master_df.columns:
        id_cols.append('is_rex')

    ts = pd.melt(
        master_df[[c for c in id_cols + aum_cols if c in master_df.columns]],
        id_vars=[c for c in id_cols if c in master_df.columns],
        var_name='aum_col',
        value_name='aum_value'
    )

    ts['months_ago'] = ts['aum_col'].apply(months_ago)
    as_of = datetime.now()
    ts['date'] = ts['months_ago'].apply(
        lambda m: pd.Timestamp(as_of) - pd.DateOffset(months=m)
    )
    ts['as_of_date'] = pd.Timestamp(as_of)

    return ts.rename(columns={ticker_col: 'ticker'})


def build_all(data_file: Path = None) -> dict:
    """
    Build all outputs.
    Returns: {"master": DataFrame, "ts": DataFrame}
    """
    global DATA_FILE
    if data_file:
        DATA_FILE = data_file

    if not DATA_FILE.exists():
        return {"master": pd.DataFrame(), "ts": pd.DataFrame()}

    xl = _load_excel()
    master = build_master_data(xl)
    ts = build_time_series(master, xl)

    return {"master": master, "ts": ts}
```

## Step 4: IMPORTANT — Adapt to Actual Structure

After writing the initial code above, RUN it and check the output:
```python
from webapp.services.data_engine import build_all
result = build_all()
print("master shape:", result['master'].shape)
print("master cols:", result['master'].columns.tolist()[:20])
print("ts shape:", result['ts'].shape)
```

Fix any issues. The column names in the plan above are ESTIMATES — the actual Excel structure determines the real column names.

## Step 5: Write scripts/verify_data_engine.py

```python
"""
Verify that data_engine.py output matches Excel's pre-computed q_master_data.
Run: python scripts/verify_data_engine.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from webapp.services.data_engine import DATA_FILE, _load_excel, build_master_data

def verify():
    if not DATA_FILE.exists():
        print(f"ERROR: Data file not found: {DATA_FILE}")
        return

    xl = _load_excel()

    # Load Excel's pre-computed q_master_data
    print("Loading Excel q_master_data...")
    excel_master = xl.parse('q_master_data')
    excel_master.columns = [str(c).strip() for c in excel_master.columns]
    print(f"Excel q_master_data: {excel_master.shape}")

    # Load Python-generated master
    print("Running data_engine.build_master_data()...")
    py_master = build_master_data(xl)
    print(f"Python master: {py_master.shape}")

    # Compare ticker counts
    excel_ticker_col = next((c for c in excel_master.columns if c.lower() == 'ticker'), None)
    py_ticker_col = next((c for c in py_master.columns if c.lower() == 'ticker'), None)

    if excel_ticker_col and py_ticker_col:
        excel_tickers = set(excel_master[excel_ticker_col].dropna().astype(str))
        py_tickers = set(py_master[py_ticker_col].dropna().astype(str))

        only_excel = excel_tickers - py_tickers
        only_python = py_tickers - excel_tickers
        shared = excel_tickers & py_tickers

        match_pct = len(shared) / len(excel_tickers) * 100 if excel_tickers else 0

        print(f"\nTicker comparison:")
        print(f"  Excel tickers: {len(excel_tickers)}")
        print(f"  Python tickers: {len(py_tickers)}")
        print(f"  Shared: {len(shared)} ({match_pct:.1f}% match)")
        print(f"  Only in Excel: {len(only_excel)}")
        print(f"  Only in Python: {len(only_python)}")

        if only_excel:
            print(f"  Sample only in Excel: {list(only_excel)[:10]}")
        if only_python:
            print(f"  Sample only in Python: {list(only_python)[:10]}")

    # Check category_display if available
    if 'category_display' in excel_master.columns and 'category_display' in py_master.columns:
        print("\nCategory distribution in Excel:")
        print(excel_master['category_display'].value_counts().head(10))
        print("\nCategory distribution in Python:")
        print(py_master['category_display'].value_counts().head(10))

    print("\nVerification complete.")

if __name__ == '__main__':
    verify()
```

## Step 6: Run Verification

```bash
python scripts/verify_data_engine.py
```

Record results in `.agents/progress/Data-Engine.md`. Target: >90% ticker match. If significantly lower, investigate why and fix `build_master_data()`.

## Commit Convention
```
git add webapp/services/data_engine.py scripts/verify_data_engine.py
git commit -m "feat: Python data engine - Power Query replication with verification script"
```

## Done Criteria
- [x] `webapp/services/data_engine.py` created with `build_all()`, `build_master_data()`, `build_time_series()`, `data_available()`
- [x] `scripts/verify_data_engine.py` created
- [x] Verification shows >90% ticker match with Excel q_master_data (100% match achieved)
- [x] `build_all()` returns dict with "master" and "ts" DataFrames
- [x] No import errors

## Log
- 4fbbdd9 feat: add data_engine.py - Power Query replication
- 9668491 fix: data_engine join logic for exact match with Excel output
- 98f2cbd feat: add verify_data_engine.py verification script
- FINAL: All checks passed, 100% match on both master and time series

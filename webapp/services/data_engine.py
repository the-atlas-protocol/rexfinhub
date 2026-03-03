"""
data_engine.py -- Python replication of Excel Power Query pipeline.

Replicates the transformation logic that produces q_master_data and
q_aum_time_series_labeled from the raw input sheets in bloomberg_daily_file.xlsm.

Input sheets used:
  - w1                   Base fund info (ticker, issuer, etc.)
  - w2                   Metrics (expense ratio, spread, etc.)
  - w3                   Returns (1D, 1W, 1M, etc.)
  - w4                   Flows + AUM history
  - fund_mapping         Ticker -> etp_category mapping (creates duplicates for multi-category tickers)
  - issuer_mapping       (etp_category, issuer) -> issuer_nickname
  - category_mapping     Per-category attribute mappings (LI/CC/Crypto/Defined/Thematic blocks)
  - dim_fund_category    Final categorical dimension (category_display, issuer_display, is_rex)
  - rex_funds            REX fund tickers
  - rules                t_timeseries_include filter rules

Output tables:
  - q_master_data                Enriched fund universe with categories + attributes
  - q_aum_time_series_labeled    Unpivoted AUM time series filtered to tracked universe

Usage:
    from webapp.services.data_engine import build_all
    result = build_all()  # {"master": df, "ts": df}
"""
from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# Data file resolution -- single source of truth: bloomberg_daily_file.xlsm
# ---------------------------------------------------------------------------
_ONEDRIVE_BBG_DAILY = Path(
    r"C:\Users\RyuEl-Asmar\REX Financial LLC"
    r"\REX Financial LLC - Rex Financial LLC"
    r"\Product Development\MasterFiles\MASTER Data\bloomberg_daily_file.xlsm"
)
_LOCAL_BBG_DAILY = Path("data/DASHBOARD/bloomberg_daily_file.xlsm")

def _resolve_engine_data_file() -> Path:
    for p in (_ONEDRIVE_BBG_DAILY, _LOCAL_BBG_DAILY):
        if p.exists():
            try:
                with open(p, "rb") as f:
                    f.read(4)
                return p
            except PermissionError:
                continue
    return _LOCAL_BBG_DAILY

DATA_FILE = _resolve_engine_data_file()


def data_available() -> bool:
    """Return True if the source Excel file exists."""
    return DATA_FILE.exists()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _load_excel() -> pd.ExcelFile:
    return pd.ExcelFile(DATA_FILE, engine="openpyxl")


def _read_sheet(xl: pd.ExcelFile, sheet: str, **kwargs) -> pd.DataFrame:
    """Read a sheet, stripping whitespace from column names."""
    df = xl.parse(sheet, **kwargs)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _find_col(df: pd.DataFrame, name: str) -> str | None:
    """Find column by case-insensitive name match."""
    name_lower = name.lower()
    for col in df.columns:
        if str(col).lower().strip() == name_lower:
            return col
    return None


# ---------------------------------------------------------------------------
# Column renaming: data_import block structure -> q_master_data flat columns
# ---------------------------------------------------------------------------
# data_import has 4 "work table" blocks separated by unnamed columns:
#   Block 1 (w1): ticker..fund_description  (base fund info, kept as-is)
#   Block 2 (w2): ticker.1..open_interest   (expense/spread metrics -> t_w2.*)
#   Block 3 (w3): ticker.2..annualized_yield (returns -> t_w3.*)
#   Block 4 (w4): ticker.3..aum_36          (flows + AUM -> t_w4.*)
# After those blocks there are helper columns (CopyPaste, Ticker, etc.) to drop.

_W2_FIELDS = [
    "expense_ratio", "management_fee", "average_bidask_spread",
    "nav_tracking_error", "percentage_premium",
    "average_percent_premium_52week", "average_vol_30day",
    "percent_short_interest", "open_interest",
]

_W3_FIELDS = [
    "total_return_1day", "total_return_1week", "total_return_1month",
    "total_return_3month", "total_return_6month", "total_return_ytd",
    "total_return_1year", "total_return_3year", "annualized_yield",
]

_W4_FIELDS = [
    "fund_flow_1day", "fund_flow_1week", "fund_flow_1month",
    "fund_flow_3month", "fund_flow_6month", "fund_flow_ytd",
    "fund_flow_1year", "fund_flow_3year",
    "aum",
] + [f"aum_{i}" for i in range(1, 37)]

# Base columns to keep (block 1)
_BASE_FIELDS = [
    "ticker", "fund_name", "issuer", "listed_exchange", "inception_date",
    "fund_type", "asset_class_focus", "regulatory_structure",
    "index_weighting_methodology", "underlying_index", "is_singlestock",
    "is_active", "uses_derivatives", "uses_swaps", "is_40act",
    "uses_leverage", "leverage_amount", "outcome_type", "is_crypto",
    "cusip", "market_status", "fund_description",
]


def _build_from_split_sheets(xl: pd.ExcelFile) -> pd.DataFrame:
    """Build the equivalent of cleaned data_import from separate w1-w4 sheets.

    The bloomberg_daily_file.xlsm splits the old data_import into 4 sheets.
    Read each, rename columns to canonical names, merge on ticker, then
    apply the same t_w2/t_w3/t_w4 prefixing that _clean_data_import does.
    """
    from market.config import W1_COL_MAP, W2_COL_MAP, W3_COL_MAP, W4_FLOW_COL_MAP

    w1 = _read_sheet(xl, "w1").rename(columns=W1_COL_MAP)
    w1 = w1.dropna(subset=["ticker"])

    w2 = _read_sheet(xl, "w2").rename(columns=W2_COL_MAP)
    if "Fund Name" in w2.columns:
        w2 = w2.drop(columns=["Fund Name"])

    w3 = _read_sheet(xl, "w3").rename(columns=W3_COL_MAP)
    if "Fund Name" in w3.columns:
        w3 = w3.drop(columns=["Fund Name"])

    w4 = _read_sheet(xl, "w4").rename(columns=W4_FLOW_COL_MAP)
    if "Fund Name" in w4.columns:
        w4 = w4.drop(columns=["Fund Name"])
    # AUM is the first non-flow, non-ticker column
    for col in w4.columns:
        if col not in W4_FLOW_COL_MAP.values() and col not in ("ticker", "Fund Name"):
            w4 = w4.rename(columns={col: "aum"})
            break
    if "aum" not in w4.columns and len(w4.columns) > 10:
        w4 = w4.rename(columns={w4.columns[10]: "aum"})
    # Also pick up aum_1..aum_36 from remaining positional columns
    aum_idx = 1
    found_aum = False
    for col in w4.columns:
        if col == "aum":
            found_aum = True
            continue
        if found_aum and col not in W4_FLOW_COL_MAP.values() and col != "ticker":
            w4 = w4.rename(columns={col: f"aum_{aum_idx}"})
            aum_idx += 1

    # Merge on ticker
    df = w1.copy()
    for sheet_df in [w2, w3, w4]:
        if "ticker" in sheet_df.columns:
            merge_cols = [c for c in sheet_df.columns if c != "ticker" and c not in df.columns]
            if merge_cols:
                df = df.merge(sheet_df[["ticker"] + merge_cols], on="ticker", how="left")

    # Apply t_w2/t_w3/t_w4 prefixes (same as _clean_data_import)
    rename_map = {}
    for field in _W2_FIELDS:
        if field in df.columns:
            rename_map[field] = f"t_w2.{field}"
    for field in _W3_FIELDS:
        if field in df.columns:
            rename_map[field] = f"t_w3.{field}"
    for field in _W4_FIELDS:
        if field in df.columns:
            rename_map[field] = f"t_w4.{field}"
    df = df.rename(columns=rename_map)

    return df


def _clean_data_import(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean data_import: keep only useful columns, rename work-table columns
    with t_w2/t_w3/t_w4 prefixes, and drop separator/helper columns.
    """
    # Build rename map for work-table columns
    rename_map = {}
    for field in _W2_FIELDS:
        rename_map[field] = f"t_w2.{field}"
    for field in _W3_FIELDS:
        rename_map[field] = f"t_w3.{field}"
    for field in _W4_FIELDS:
        rename_map[field] = f"t_w4.{field}"

    # Identify columns to keep: base fields + work-table fields
    # Only keep the FIRST occurrence of each field name to avoid duplicates
    # (data_import has ticker, ticker.1, ticker.2, Ticker, etc.)
    base_lower = {f.lower() for f in _BASE_FIELDS}
    w2_lower = {f.lower() for f in _W2_FIELDS}
    w3_lower = {f.lower() for f in _W3_FIELDS}
    w4_lower = {f.lower() for f in _W4_FIELDS}
    seen_lower = set()
    keep_cols = []
    for col in df.columns:
        col_lower = col.lower().strip()
        if col_lower in seen_lower:
            continue
        if col_lower in base_lower or col_lower in w2_lower or \
           col_lower in w3_lower or col_lower in w4_lower:
            keep_cols.append(col)
            seen_lower.add(col_lower)

    df = df[keep_cols].copy()

    # Apply rename: use case-insensitive lookup
    actual_rename = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if col_lower in {f.lower(): f"t_w2.{f}" for f in _W2_FIELDS}:
            actual_rename[col] = f"t_w2.{col_lower}"
        elif col_lower in {f.lower(): f"t_w3.{f}" for f in _W3_FIELDS}:
            actual_rename[col] = f"t_w3.{col_lower}"
        elif col_lower in {f.lower(): f"t_w4.{f}" for f in _W4_FIELDS}:
            actual_rename[col] = f"t_w4.{col_lower}"

    df = df.rename(columns=actual_rename)
    return df


# ---------------------------------------------------------------------------
# Category mapping extraction
# ---------------------------------------------------------------------------
def _build_category_attributes(xl: pd.ExcelFile) -> pd.DataFrame:
    """
    Parse the category_mapping sheet which has 5 side-by-side blocks:
      LI:      ticker, map_li_*
      CC:      ticker.1, map_cc_*
      Crypto:  ticker.2, map_crypto_*
      Defined: ticker.3, map_defined_category
      Thematic: ticker.4, map_thematic_category

    Returns a single DataFrame keyed on ticker with all map_* columns.
    """
    cm = _read_sheet(xl, "category_mapping")

    # --- LI block ---
    li_cols = ["ticker", "map_li_category", "map_li_subcategory",
               "map_li_direction", "map_li_leverage_amount", "map_li_underlier"]
    li_available = [c for c in li_cols if c in cm.columns]
    if "ticker" in li_available:
        li_df = cm[li_available].dropna(subset=["ticker"]).copy()
        li_df = li_df.rename(columns={"ticker": "_ticker"})
    else:
        li_df = pd.DataFrame(columns=["_ticker"])

    # --- CC block ---
    cc_cols_map = {
        "ticker.1": "_ticker",
        "map_cc_underlier": "map_cc_underlier",
        "map_cc_index": "map_cc_index",
    }
    # Note: map_cc_category exists in the sheet but is NOT in q_master_data output
    cc_available = {k: v for k, v in cc_cols_map.items() if k in cm.columns}
    if "ticker.1" in cc_available:
        cc_df = cm[list(cc_available.keys())].dropna(subset=["ticker.1"]).copy()
        cc_df = cc_df.rename(columns=cc_available)
    else:
        cc_df = pd.DataFrame(columns=["_ticker"])

    # --- Crypto block ---
    crypto_cols_map = {
        "ticker.2": "_ticker",
        "map_crypto_is_spot": "map_crypto_is_spot",
        "map_crypto_underlier": "map_crypto_underlier",
    }
    crypto_available = {k: v for k, v in crypto_cols_map.items() if k in cm.columns}
    if "ticker.2" in crypto_available:
        crypto_df = cm[list(crypto_available.keys())].dropna(subset=["ticker.2"]).copy()
        crypto_df = crypto_df.rename(columns=crypto_available)
    else:
        crypto_df = pd.DataFrame(columns=["_ticker"])

    # --- Defined block ---
    def_cols_map = {
        "ticker.3": "_ticker",
        "map_defined_category": "map_defined_category",
    }
    def_available = {k: v for k, v in def_cols_map.items() if k in cm.columns}
    if "ticker.3" in def_available:
        def_df = cm[list(def_available.keys())].dropna(subset=["ticker.3"]).copy()
        def_df = def_df.rename(columns=def_available)
    else:
        def_df = pd.DataFrame(columns=["_ticker"])

    # --- Thematic block ---
    thm_cols_map = {
        "ticker.4": "_ticker",
        "map_thematic_category": "map_thematic_category",
    }
    thm_available = {k: v for k, v in thm_cols_map.items() if k in cm.columns}
    if "ticker.4" in thm_available:
        thm_df = cm[list(thm_available.keys())].dropna(subset=["ticker.4"]).copy()
        thm_df = thm_df.rename(columns=thm_available)
    else:
        thm_df = pd.DataFrame(columns=["_ticker"])

    # Merge all blocks on _ticker (outer join to preserve all)
    result = li_df
    for block_df in [cc_df, crypto_df, def_df, thm_df]:
        if not block_df.empty and "_ticker" in block_df.columns:
            result = result.merge(block_df, on="_ticker", how="outer",
                                  suffixes=("", "_dup"))
            # Drop any _dup columns
            result = result[[c for c in result.columns if not c.endswith("_dup")]]

    if "_ticker" in result.columns:
        result = result.rename(columns={"_ticker": "ticker"})

    return result


# ---------------------------------------------------------------------------
# Build q_master_data
# ---------------------------------------------------------------------------
def build_master_data(xl: pd.ExcelFile = None) -> pd.DataFrame:
    """
    Build the equivalent of q_master_data.

    Process:
    1. Read data_import (raw Bloomberg fund universe)
    2. Clean columns and rename with t_w2/t_w3/t_w4 prefixes
    3. Left join fund_mapping on ticker -> adds etp_category (may duplicate rows)
    4. Left join issuer_mapping on (etp_category, issuer) -> adds issuer_nickname
    5. Left join category_attributes on ticker -> adds map_* columns
    6. Left join dim_fund_category on ticker -> adds category_display, issuer_display, is_rex, fund_category_key
    7. Override is_rex from rex_funds
    """
    if xl is None:
        xl = _load_excel()

    # Step 1 & 2: Read and clean data_import (or build from w1-w4)
    if "data_import" in xl.sheet_names:
        raw = _read_sheet(xl, "data_import")
        raw = raw.dropna(subset=["ticker"])
        df = _clean_data_import(raw)
    elif "w1" in xl.sheet_names:
        df = _build_from_split_sheets(xl)
    else:
        raise ValueError("Data file has neither data_import nor w1 sheet")

    # Step 3: Join fund_mapping -> adds etp_category
    # Deduplicate on (ticker, etp_category) to avoid row multiplication
    try:
        fm = _read_sheet(xl, "fund_mapping")
        fm = fm[["etp_category", "ticker"]].dropna(subset=["ticker"])
        fm = fm.drop_duplicates(subset=["ticker", "etp_category"])
        df = df.merge(fm, on="ticker", how="left")
    except Exception:
        # Fall back to CSV rules
        from market.config import RULES_DIR
        csv_path = RULES_DIR / "fund_mapping.csv"
        if csv_path.exists():
            fm = pd.read_csv(csv_path, engine="python", on_bad_lines="skip")
            if {"ticker", "etp_category"}.issubset(fm.columns):
                fm = fm[["ticker", "etp_category"]].dropna(subset=["ticker"])
                fm = fm.drop_duplicates(subset=["ticker", "etp_category"])
                df = df.merge(fm, on="ticker", how="left")
            else:
                df["etp_category"] = pd.NA
        else:
            df["etp_category"] = pd.NA

    # Step 4: Join issuer_mapping -> adds issuer_nickname
    # Deduplicate to avoid row multiplication
    try:
        im = _read_sheet(xl, "issuer_mapping")
        im = im[["etp_category", "issuer", "issuer_nickname"]].dropna(
            subset=["etp_category", "issuer"]
        )
        im = im.drop_duplicates(subset=["etp_category", "issuer"])
        df = df.merge(im, on=["etp_category", "issuer"], how="left")
    except Exception:
        # Fall back to CSV rules
        from market.config import RULES_DIR
        csv_path = RULES_DIR / "issuer_mapping.csv"
        if csv_path.exists():
            im = pd.read_csv(csv_path, engine="python", on_bad_lines="skip")
            if {"etp_category", "issuer", "issuer_nickname"}.issubset(im.columns):
                im = im[["etp_category", "issuer", "issuer_nickname"]].dropna(subset=["etp_category", "issuer"])
                im = im.drop_duplicates(subset=["etp_category", "issuer"])
                df = df.merge(im, on=["etp_category", "issuer"], how="left")
            else:
                df["issuer_nickname"] = pd.NA
        else:
            df["issuer_nickname"] = pd.NA

    # Step 5: Join category_attributes -> adds map_* columns
    try:
        cat_attrs = _build_category_attributes(xl)
        if not cat_attrs.empty and "ticker" in cat_attrs.columns:
            # Prefix attribute columns for output format
            attr_cols = [c for c in cat_attrs.columns if c != "ticker"]
            attr_rename = {c: f"q_category_attributes.{c}" for c in attr_cols}
            cat_attrs = cat_attrs.rename(columns=attr_rename)
            df = df.merge(cat_attrs, on="ticker", how="left")
    except Exception:
        pass

    # Step 6: Join dim_fund_category -> adds category_display, issuer_display, is_rex, fund_category_key
    # dim_fund_category has multiple rows per ticker (one per category_display).
    # To avoid many-to-many explosion, build a join key using fund_category_key.
    # fund_category_key format: "TICKER|category_display"
    # We map etp_category -> category_display pattern to find the right dim row.
    try:
        dim = _read_sheet(xl, "dim_fund_category")
        dim = dim.dropna(subset=["ticker"])

        # Build a lookup from fund_category_key -> dim row
        if "fund_category_key" in dim.columns:
            # For tickers with only one dim row, simple ticker join works
            # For tickers with multiple dim rows, we need to match via category
            dim_single = dim.drop_duplicates(subset=["ticker"], keep=False)
            dim_multi = dim[dim.duplicated(subset=["ticker"], keep=False)]

            # Build _fck on master side for multi-category tickers
            # etp_category -> possible category_display values
            _ETP_TO_CATS = {
                "LI": {"Leverage & Inverse - Single Stock",
                        "Leverage & Inverse - Index/Basket/ETF Based",
                        "Leverage & Inverse - Unknown/Miscellaneous"},
                "CC": {"Income - Single Stock",
                       "Income - Index/Basket/ETF Based",
                       "Income - Unknown/Miscellaneous"},
                "Crypto": {"Crypto"},
                "Defined": {"Defined Outcome"},
                "Thematic": {"Thematic"},
            }

            # For multi-category dim rows, create a lookup: (ticker, etp_category) -> dim row
            multi_rows = []
            for _, row in dim_multi.iterrows():
                cat_display = row.get("category_display", "")
                # Find which etp_category this category_display belongs to
                etp_cat = None
                for etp, cats in _ETP_TO_CATS.items():
                    if cat_display in cats:
                        etp_cat = etp
                        break
                if etp_cat:
                    row_dict = row.to_dict()
                    row_dict["_etp_category"] = etp_cat
                    multi_rows.append(row_dict)

            # Join single-dim tickers on ticker only
            dim_single_cols = [c for c in dim_single.columns
                               if c not in ("market_status", "fund_type")]
            df = df.merge(
                dim_single[dim_single_cols],
                on="ticker", how="left",
            )

            # Join multi-dim tickers on (ticker, etp_category)
            if multi_rows:
                dim_multi_df = pd.DataFrame(multi_rows)
                dim_multi_cols = [c for c in dim_multi_df.columns
                                  if c not in ("market_status", "fund_type")]
                dim_multi_df = dim_multi_df[dim_multi_cols]
                # Merge on ticker + etp_category match
                df = df.merge(
                    dim_multi_df.rename(columns={"_etp_category": "etp_category"}),
                    on=["ticker", "etp_category"],
                    how="left",
                    suffixes=("", "_multi"),
                )
                # Fill in from multi where single was NaN
                for col in ["category_display", "issuer_display", "is_rex",
                            "fund_category_key"]:
                    multi_col = f"{col}_multi"
                    if multi_col in df.columns:
                        df[col] = df[col].fillna(df[multi_col])
                        df = df.drop(columns=[multi_col])
        else:
            # Fallback: simple ticker join (may create duplicates)
            dim_cols_use = [c for c in dim.columns
                           if c not in ("market_status", "fund_type")]
            df = df.merge(dim[dim_cols_use], on="ticker", how="left")
    except Exception:
        pass

    # Step 7: Override is_rex from rex_funds
    try:
        rex = _read_sheet(xl, "rex_funds")
        rex_tickers = set(rex["ticker"].dropna().astype(str).str.strip())
        if "is_rex" in df.columns:
            existing = df["is_rex"].map(
                lambda v: bool(v) if pd.notna(v) else False
            )
            df["is_rex"] = df["ticker"].isin(rex_tickers) | existing
        else:
            df["is_rex"] = df["ticker"].isin(rex_tickers)
    except Exception:
        pass

    return df


# ---------------------------------------------------------------------------
# Build q_aum_time_series (unpivot)
# ---------------------------------------------------------------------------
def _unpivot_aum(master_df: pd.DataFrame) -> pd.DataFrame:
    """
    Unpivot AUM columns (t_w4.aum, t_w4.aum_1 .. t_w4.aum_36) into long format.
    Returns columns: ticker, date, months_ago, aum_value, as_of_date
    """
    # Find AUM columns
    aum_cols = [c for c in master_df.columns
                if re.match(r"t_w4\.aum(_\d+)?$", c, re.IGNORECASE)]

    if not aum_cols:
        return pd.DataFrame(columns=["ticker", "date", "months_ago",
                                      "aum_value", "as_of_date"])

    id_col = "ticker"
    ts = pd.melt(
        master_df[[id_col] + aum_cols],
        id_vars=[id_col],
        var_name="aum_col",
        value_name="aum_value",
    )

    # Parse months_ago from column name
    def _months_ago(col: str) -> int:
        m = re.search(r"aum_(\d+)$", col, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    ts["months_ago"] = ts["aum_col"].apply(_months_ago)

    # Compute date: as_of_date minus months_ago months
    as_of = pd.Timestamp(datetime.now().date())
    ts["as_of_date"] = as_of
    ts["date"] = ts["months_ago"].apply(
        lambda m: as_of - pd.DateOffset(months=m)
    )

    ts = ts.drop(columns=["aum_col"])
    return ts


# ---------------------------------------------------------------------------
# Build q_aum_time_series_labeled
# ---------------------------------------------------------------------------
def _load_ts_include_rules(xl: pd.ExcelFile) -> pd.DataFrame:
    """
    Parse the rules sheet to get t_timeseries_include filter pairs:
    (category_display, issuer_display) combinations that should be included
    in the labeled time series.
    """
    rules = _read_sheet(xl, "rules")
    # The t_timeseries_include rules are in columns 'category_display' and 'issuer_display'
    # stored under headers t_timeseries_include and Unnamed: 11
    if "t_timeseries_include" not in rules.columns:
        return pd.DataFrame(columns=["category_display", "issuer_display"])

    # First row is headers ("category_display", "issuer_display"), skip it
    ts_rules = rules[["t_timeseries_include", "Unnamed: 11"]].copy()
    ts_rules.columns = ["category_display", "issuer_display"]
    # Drop the header row and NaN rows
    ts_rules = ts_rules.iloc[1:].dropna(subset=["category_display", "issuer_display"])
    return ts_rules.reset_index(drop=True)


def _load_issuer_group_rules(xl: pd.ExcelFile) -> set[str]:
    """
    Parse the rules sheet to extract the set of issuers that get their own
    issuer_group. Others become 'Other'.
    """
    rules = _read_sheet(xl, "rules")
    if "Unnamed: 11" not in rules.columns:
        return set()
    # Column Unnamed: 11 contains issuer_display values used in ts_include
    issuers = rules["Unnamed: 11"].dropna().astype(str).str.strip()
    # First row is "issuer_display" header, skip it
    issuers = issuers.iloc[1:]
    return set(issuers)


def build_time_series(master_df: pd.DataFrame, xl: pd.ExcelFile = None) -> pd.DataFrame:
    """
    Build the equivalent of q_aum_time_series_labeled.

    Process:
    1. Deduplicate master to one row per ticker (take first = base data_import row)
    2. Filter to tickers present in dim_fund_category
    3. Unpivot AUM columns into long format (ticker, months_ago, aum_value)
    4. Join dim_fund_category on ticker (many-to-one: multi-category tickers get
       one row per fund_category_key per month = 1903 keys * 37 months = 70,411 rows)
    5. Add issuer_group (known issuers keep name, others become 'Other')
    """
    if xl is None:
        xl = _load_excel()

    # Step 1: Deduplicate master to one row per ticker
    # (multi-category tickers have duplicate AUM values, keep first)
    deduped = master_df.drop_duplicates(subset=["ticker"], keep="first")

    # Step 2: Filter to dim_fund_category tickers
    try:
        dim = _read_sheet(xl, "dim_fund_category")
        dim = dim.dropna(subset=["ticker"])
        dim_tickers = set(dim["ticker"].astype(str).str.strip())
        deduped = deduped[deduped["ticker"].isin(dim_tickers)].copy()
    except Exception:
        return pd.DataFrame()

    # Step 3: Unpivot AUM columns
    ts = _unpivot_aum(deduped)
    if ts.empty:
        return ts

    # Step 4: Join dim_fund_category (this expands multi-category tickers)
    dim_join_cols = ["ticker", "category_display", "issuer_display",
                     "is_rex", "fund_category_key"]
    dim_available = [c for c in dim_join_cols if c in dim.columns]
    ts = ts.merge(dim[dim_available], on="ticker", how="inner")

    # Step 5: Add issuer_group
    # issuer_group = issuer_display ONLY when the specific
    # (category_display, issuer_display) pair is in the t_timeseries_include rules.
    # Otherwise issuer_group = "Other".
    try:
        include_rules = _load_ts_include_rules(xl)
        if not include_rules.empty and "issuer_display" in ts.columns:
            rule_set = set(
                zip(include_rules["category_display"], include_rules["issuer_display"])
            )
            ts["issuer_group"] = ts.apply(
                lambda r: r["issuer_display"]
                if (r.get("category_display"), r.get("issuer_display")) in rule_set
                else "Other",
                axis=1,
            )
        elif "issuer_display" in ts.columns:
            ts["issuer_group"] = ts["issuer_display"]
    except Exception:
        if "issuer_group" not in ts.columns:
            ts["issuer_group"] = pd.NA

    # Reorder columns to match Excel output
    desired_order = [
        "ticker", "date", "months_ago", "aum_value", "as_of_date",
        "category_display", "issuer_display", "is_rex",
        "issuer_group", "fund_category_key",
    ]
    available_order = [c for c in desired_order if c in ts.columns]
    ts = ts[available_order]

    return ts.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Memory optimisation helpers
# ---------------------------------------------------------------------------
def _optimise_master_dtypes(df: pd.DataFrame) -> None:
    """Convert object columns to efficient dtypes in-place.

    Metric columns (t_w2.*, t_w3.*, t_w4.*) become float32 (4 bytes vs ~50).
    Low-cardinality string columns become category (~1 byte vs ~50).
    """
    _NUMERIC_PREFIXES = ("t_w2.", "t_w3.", "t_w4.")
    _CATEGORY_COLS = {
        "etp_category", "category_display", "issuer_display", "issuer",
        "issuer_nickname", "fund_type",
        "q_category_attributes.map_li_category",
        "q_category_attributes.map_li_subcategory",
        "q_category_attributes.map_li_direction",
        "q_category_attributes.map_cc_index",
        "q_category_attributes.map_cc_underlier",
        "q_category_attributes.cc_type",
        "q_category_attributes.cc_category",
    }
    _SENTINEL_VALUES = ("Unknown", "")
    for col in list(df.columns):
        if col in _CATEGORY_COLS:
            cat = df[col].astype("category")
            missing = [v for v in _SENTINEL_VALUES if v not in cat.cat.categories]
            if missing:
                cat = cat.cat.add_categories(missing)
            df[col] = cat
        elif df[col].dtype == object and any(col.startswith(p) for p in _NUMERIC_PREFIXES):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")


def _optimise_ts_dtypes(ts: pd.DataFrame) -> None:
    """Optimise time series DataFrame dtypes in-place."""
    if "ticker" in ts.columns:
        ts["ticker"] = ts["ticker"].astype("category")
    if "aum_value" in ts.columns:
        ts["aum_value"] = pd.to_numeric(
            ts["aum_value"], errors="coerce"
        ).astype("float32")
    if "months_ago" in ts.columns:
        ts["months_ago"] = ts["months_ago"].astype("int16")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_all_from_csvs(csv_dir: Path) -> dict:
    """Build master + time series from pre-exported CSV sheets.

    No openpyxl needed -- reads w1-w4 CSVs and config/rules/ CSVs.
    Returns {"master": DataFrame, "ts": DataFrame} matching build_all() output.
    """
    from market.config import (
        W1_COL_MAP, W2_COL_MAP, W3_COL_MAP, W4_FLOW_COL_MAP,
        RULES_DIR, ATTR_PREFIX,
    )
    csv_dir = Path(csv_dir)

    # --- Read w1-w4 CSVs -------------------------------------------------
    w1 = pd.read_csv(csv_dir / "w1.csv", engine="python", on_bad_lines="skip")
    w2 = pd.read_csv(csv_dir / "w2.csv", engine="python", on_bad_lines="skip")
    w3 = pd.read_csv(csv_dir / "w3.csv", engine="python", on_bad_lines="skip")
    w4 = pd.read_csv(csv_dir / "w4.csv", engine="python", on_bad_lines="skip")

    # Drop unnamed index column that to_csv(index=True) produces
    for sheet in [w1, w2, w3, w4]:
        if sheet.columns[0].startswith("Unnamed"):
            sheet.drop(columns=[sheet.columns[0]], inplace=True)

    # Rename columns
    w1 = w1.rename(columns=W1_COL_MAP)
    if "ticker" in w1.columns:
        w1 = w1.dropna(subset=["ticker"])

    w2 = w2.rename(columns=W2_COL_MAP)
    if "Fund Name" in w2.columns:
        w2 = w2.drop(columns=["Fund Name"])

    w3 = w3.rename(columns=W3_COL_MAP)
    if "Fund Name" in w3.columns:
        w3 = w3.drop(columns=["Fund Name"])

    w4 = w4.rename(columns=W4_FLOW_COL_MAP)
    if "Fund Name" in w4.columns:
        w4 = w4.drop(columns=["Fund Name"])

    # Find AUM column in w4 (first non-flow, non-ticker column)
    for col in list(w4.columns):
        if col not in W4_FLOW_COL_MAP.values() and col not in ("ticker", "Fund Name"):
            w4 = w4.rename(columns={col: "aum"})
            break
    if "aum" not in w4.columns and len(w4.columns) > 10:
        w4 = w4.rename(columns={w4.columns[10]: "aum"})

    # Rename historical AUM columns: aum_1..aum_36
    aum_idx = 1
    found_aum = False
    for col in list(w4.columns):
        if col == "aum":
            found_aum = True
            continue
        if found_aum and col not in W4_FLOW_COL_MAP.values() and col != "ticker":
            w4 = w4.rename(columns={col: f"aum_{aum_idx}"})
            aum_idx += 1

    # --- Merge on ticker --------------------------------------------------
    df = w1.copy()
    for sheet_df in [w2, w3, w4]:
        if "ticker" in sheet_df.columns:
            merge_cols = [c for c in sheet_df.columns
                         if c != "ticker" and c not in df.columns]
            if merge_cols:
                df = df.merge(sheet_df[["ticker"] + merge_cols],
                              on="ticker", how="left")

    # --- Apply t_w2/t_w3/t_w4 prefixes -----------------------------------
    rename_map = {}
    for field in _W2_FIELDS:
        if field in df.columns:
            rename_map[field] = f"t_w2.{field}"
    for field in _W3_FIELDS:
        if field in df.columns:
            rename_map[field] = f"t_w3.{field}"
    for field in _W4_FIELDS:
        if field in df.columns:
            rename_map[field] = f"t_w4.{field}"
    df = df.rename(columns=rename_map)

    # --- Join rules CSVs --------------------------------------------------
    def _csv(name: str) -> pd.DataFrame:
        p = RULES_DIR / name
        if not p.exists():
            return pd.DataFrame()
        return pd.read_csv(p, engine="python", on_bad_lines="skip")

    # fund_mapping -> etp_category
    fm = _csv("fund_mapping.csv")
    if {"ticker", "etp_category"}.issubset(fm.columns):
        fm = fm[["ticker", "etp_category"]].dropna(subset=["ticker"])
        fm = fm.drop_duplicates(subset=["ticker", "etp_category"])
        df = df.merge(fm, on="ticker", how="left")
    else:
        df["etp_category"] = pd.NA

    # issuer_mapping -> issuer_nickname / issuer_display
    im = _csv("issuer_mapping.csv")
    if {"etp_category", "issuer", "issuer_nickname"}.issubset(im.columns):
        im = im[["etp_category", "issuer", "issuer_nickname"]].dropna(
            subset=["etp_category", "issuer"])
        im = im.drop_duplicates(subset=["etp_category", "issuer"])
        df = df.merge(im, on=["etp_category", "issuer"], how="left")
        df["issuer_display"] = df["issuer_nickname"].fillna(
            df.get("issuer", ""))
    else:
        df["issuer_display"] = df.get("issuer", "")

    # Category attribute CSVs -> q_category_attributes.* columns
    _ATTR_FILES = {
        "attributes_LI.csv": ["map_li_category", "map_li_subcategory",
                              "map_li_direction", "map_li_leverage_amount",
                              "map_li_underlier"],
        "attributes_CC.csv": ["map_cc_underlier", "map_cc_index",
                              "cc_type", "cc_category"],
        "attributes_Crypto.csv": ["map_crypto_is_spot", "map_crypto_underlier"],
        "attributes_Defined.csv": ["map_defined_category"],
        "attributes_Thematic.csv": ["map_thematic_category"],
    }
    for fname, attr_cols in _ATTR_FILES.items():
        attrs = _csv(fname)
        if "ticker" in attrs.columns:
            rename_a = {c: f"{ATTR_PREFIX}{c}" for c in attrs.columns
                        if c != "ticker" and c in attr_cols}
            attrs = attrs.rename(columns=rename_a)
            df = df.merge(attrs, on="ticker", how="left", suffixes=("", "_dup"))
            df = df[[c for c in df.columns if not c.endswith("_dup")]]

    # rex_funds -> is_rex
    rf = _csv("rex_funds.csv")
    if "ticker" in rf.columns:
        rex_set = set(rf["ticker"].dropna().astype(str).str.strip())
        existing = df["is_rex"].map(
            lambda v: bool(v) if pd.notna(v) else False
        ) if "is_rex" in df.columns else False
        df["is_rex"] = df["ticker"].isin(rex_set) | existing
    else:
        df["is_rex"] = False

    # --- Derive category_display from etp_category + attributes -----------
    _derive_category_display(df)

    # --- Optimise dtypes to reduce memory (object -> float32/category) ----
    _optimise_master_dtypes(df)

    # --- Build time series ------------------------------------------------
    # Deduplicate by ticker BEFORE unpivoting -- multi-category tickers have
    # identical AUM values; unpivoting duplicates wastes ~5x memory.
    deduped = df.drop_duplicates(subset=["ticker"], keep="first")
    ts = _unpivot_aum(deduped)
    _optimise_ts_dtypes(ts)

    return {"master": df, "ts": ts}


def _derive_category_display(df: pd.DataFrame) -> None:
    """Derive category_display from etp_category + attribute columns.

    Replicates what dim_fund_category provides when loaded from Excel.
    """
    from market.config import (
        CAT_LI_SS, CAT_LI_INDEX, CAT_LI_OTHER,
        CAT_CC_SS, CAT_CC_INDEX, CAT_CC_OTHER,
        CAT_CRYPTO, CAT_DEFINED, CAT_THEMATIC, ATTR_PREFIX,
    )

    cat = df.get("etp_category", pd.Series(dtype=str)).fillna("").astype(str)
    df["category_display"] = ""

    # LI
    li_mask = cat == "LI"
    if li_mask.any():
        sub_col = None
        for c in [f"{ATTR_PREFIX}map_li_subcategory", "map_li_subcategory"]:
            if c in df.columns:
                sub_col = c
                break
        if sub_col:
            sub = df[sub_col].fillna("").astype(str).str.lower()
            df.loc[li_mask & sub.str.contains("single", na=False),
                   "category_display"] = CAT_LI_SS
            df.loc[li_mask & ~sub.str.contains("single", na=False)
                   & (sub != "") & (sub != "nan"),
                   "category_display"] = CAT_LI_INDEX
            df.loc[li_mask & ((sub == "") | (sub == "nan")),
                   "category_display"] = CAT_LI_OTHER
        else:
            df.loc[li_mask, "category_display"] = CAT_LI_OTHER

    # CC
    cc_mask = cat == "CC"
    if cc_mask.any():
        cc_col = None
        for c in [f"{ATTR_PREFIX}cc_category", "cc_category"]:
            if c in df.columns:
                cc_col = c
                break
        if cc_col:
            cc_val = df[cc_col].fillna("").astype(str).str.lower()
            df.loc[cc_mask & cc_val.str.contains("single", na=False),
                   "category_display"] = CAT_CC_SS
            df.loc[cc_mask & ~cc_val.str.contains("single", na=False)
                   & (cc_val != "") & (cc_val != "nan"),
                   "category_display"] = CAT_CC_INDEX
            df.loc[cc_mask & ((cc_val == "") | (cc_val == "nan")),
                   "category_display"] = CAT_CC_OTHER
        else:
            df.loc[cc_mask, "category_display"] = CAT_CC_OTHER

    # Simple mappings
    df.loc[cat == "Crypto", "category_display"] = CAT_CRYPTO
    df.loc[cat == "Defined", "category_display"] = CAT_DEFINED
    df.loc[cat == "Thematic", "category_display"] = CAT_THEMATIC


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

"""12-step market data transformation pipeline."""
from __future__ import annotations

import logging
import re
from datetime import datetime

import pandas as pd

from market.config import (
    ETP_TO_CATS, W2_PREFIX, W3_PREFIX, W4_PREFIX, ATTR_PREFIX,
    TOP_N_ISSUERS, ALL_ATTR_COLS,
)

log = logging.getLogger(__name__)


def run_transform(
    etp_combined: pd.DataFrame,
    rules: dict[str, pd.DataFrame],
    dim: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Execute the full 12-step pipeline.

    Args:
        etp_combined: Joined ETP data (from ingest.read_input)
        rules: Dict of rule DataFrames (from rules.load_all_rules)
        dim: Derived dim_fund_category (from derive.derive_dim_fund_category)

    Returns:
        {"master": DataFrame, "ts": DataFrame}
    """
    log.info("[1/12] Starting transform pipeline")
    df = etp_combined.copy()
    log.info("  Input: %d rows x %d cols", *df.shape)

    # Step 2: Already done in ingest (join of 4 sheets)
    log.info("[2/12] ETP sheets already joined")

    # Step 3: Apply fund_mapping -> adds etp_category
    df = step3_apply_fund_mapping(df, rules["fund_mapping"])

    # Step 4: Apply exclusions
    df = step4_apply_exclusions(df, rules["exclusions"])

    # Step 5: Apply issuer_mapping -> adds issuer_nickname
    df = step5_apply_issuer_mapping(df, rules["issuer_mapping"])

    # Step 6: Apply category attributes
    df = step6_apply_category_attributes(df, rules["category_attributes"])

    # Step 7: Derive dim_fund_category (already done externally)
    log.info("[7/12] dim_fund_category: %d rows", len(dim))

    # Step 8: Join derived dim
    df = step8_join_dim(df, dim)

    # Step 9: Override is_rex from rex_funds
    df = step9_override_is_rex(df, rules["rex_funds"])

    # Step 10: Output q_master_data
    master = step10_output_master(df)

    # Step 11: Unpivot AUM -> q_aum_time_series_labeled
    ts = step11_unpivot_aum(master, dim)

    # Step 12: Passthrough stock_data (handled externally)
    log.info("[12/12] Transform complete: master=%d rows, ts=%d rows",
             len(master), len(ts))

    return {"master": master, "ts": ts}


# ---------------------------------------------------------------------------
# Individual steps
# ---------------------------------------------------------------------------

def step3_apply_fund_mapping(df: pd.DataFrame, fund_mapping: pd.DataFrame) -> pd.DataFrame:
    """Step 3: Join fund_mapping on ticker to add etp_category.

    Multi-category tickers get duplicated rows (one per etp_category).
    """
    if fund_mapping.empty:
        df["etp_category"] = pd.NA
        log.info("[3/12] fund_mapping empty, skipped")
        return df

    fm = fund_mapping[["ticker", "etp_category"]].drop_duplicates(
        subset=["ticker", "etp_category"]
    )
    before = len(df)
    df = df.merge(fm, on="ticker", how="left")
    after = len(df)
    log.info("[3/12] fund_mapping applied: %d -> %d rows (+%d from multi-category)",
             before, after, after - before)
    return df


def step4_apply_exclusions(df: pd.DataFrame, exclusions: pd.DataFrame) -> pd.DataFrame:
    """Step 4: Remove (ticker, etp_category) pairs from exclusions."""
    if exclusions.empty:
        log.info("[4/12] No exclusions")
        return df

    excl_set = set(
        zip(exclusions["ticker"].astype(str), exclusions["etp_category"].astype(str))
    )
    before = len(df)
    mask = df.apply(
        lambda r: (str(r.get("ticker", "")), str(r.get("etp_category", ""))) not in excl_set,
        axis=1,
    )
    df = df[mask].copy()
    removed = before - len(df)
    log.info("[4/12] Exclusions applied: removed %d rows", removed)
    return df


def step5_apply_issuer_mapping(df: pd.DataFrame, issuer_mapping: pd.DataFrame) -> pd.DataFrame:
    """Step 5: Join issuer_mapping on (etp_category, issuer) to add issuer_nickname."""
    if issuer_mapping.empty:
        df["issuer_nickname"] = pd.NA
        log.info("[5/12] issuer_mapping empty, skipped")
        return df

    im = issuer_mapping[["etp_category", "issuer", "issuer_nickname"]].drop_duplicates(
        subset=["etp_category", "issuer"]
    )
    df = df.merge(im, on=["etp_category", "issuer"], how="left")
    mapped = df["issuer_nickname"].notna().sum()
    log.info("[5/12] issuer_mapping applied: %d/%d rows mapped", mapped, len(df))
    return df


def step6_apply_category_attributes(
    df: pd.DataFrame, category_attributes: pd.DataFrame
) -> pd.DataFrame:
    """Step 6: Join category attributes on ticker to add map_* columns."""
    if category_attributes.empty:
        log.info("[6/12] category_attributes empty, skipped")
        return df

    # Prefix attribute columns for output format
    attrs = category_attributes.copy()
    attr_cols = [c for c in attrs.columns if c != "ticker"]
    attr_rename = {c: f"{ATTR_PREFIX}{c}" for c in attr_cols}
    attrs = attrs.rename(columns=attr_rename)

    df = df.merge(attrs, on="ticker", how="left")
    log.info("[6/12] category_attributes applied: %d attribute columns", len(attr_cols))
    return df


def step8_join_dim(df: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    """Step 8: Join derived dim_fund_category.

    Uses the same single/multi split strategy as data_engine.py:
    - Tickers with one dim row: join on ticker only
    - Tickers with multiple dim rows: join on (ticker, etp_category)
    """
    if dim.empty:
        log.info("[8/12] dim empty, skipped")
        return df

    dim_cols = ["ticker", "etp_category", "category_display", "issuer_display",
                "is_rex", "fund_category_key"]
    dim_available = [c for c in dim_cols if c in dim.columns]
    dim_use = dim[dim_available].copy()

    # Join columns to add (exclude join keys)
    join_add_cols = [c for c in dim_available if c not in ("ticker", "etp_category")]

    # Split into single-dim and multi-dim tickers
    dim_single = dim_use.drop_duplicates(subset=["ticker"], keep=False)
    dim_multi = dim_use[dim_use.duplicated(subset=["ticker"], keep=False)]

    # Join single-dim on ticker only
    if not dim_single.empty:
        single_join = dim_single.drop(columns=["etp_category"], errors="ignore")
        df = df.merge(single_join, on="ticker", how="left")

    # Join multi-dim on (ticker, etp_category)
    if not dim_multi.empty:
        df = df.merge(
            dim_multi,
            on=["ticker", "etp_category"],
            how="left",
            suffixes=("", "_multi"),
        )
        # Fill from multi where single was NaN
        for col in join_add_cols:
            multi_col = f"{col}_multi"
            if multi_col in df.columns:
                df[col] = df[col].fillna(df[multi_col])
                df = df.drop(columns=[multi_col])

    log.info("[8/12] dim joined: %d rows with category_display",
             df["category_display"].notna().sum() if "category_display" in df.columns else 0)
    return df


def step9_override_is_rex(df: pd.DataFrame, rex_funds: pd.DataFrame) -> pd.DataFrame:
    """Step 9: Override is_rex from rex_funds list."""
    if rex_funds.empty:
        log.info("[9/12] rex_funds empty, skipped")
        return df

    rex_tickers = set(rex_funds["ticker"].astype(str).str.strip())

    if "is_rex" in df.columns:
        existing = df["is_rex"].map(lambda v: bool(v) if pd.notna(v) else False)
        df["is_rex"] = df["ticker"].isin(rex_tickers) | existing
    else:
        df["is_rex"] = df["ticker"].isin(rex_tickers)

    rex_count = df["is_rex"].sum()
    log.info("[9/12] is_rex override: %d REX funds", rex_count)
    return df


def step10_output_master(df: pd.DataFrame) -> pd.DataFrame:
    """Step 10: Final q_master_data output."""
    log.info("[10/12] Master data: %d rows x %d cols", *df.shape)
    return df


def step11_unpivot_aum(
    master_df: pd.DataFrame,
    dim: pd.DataFrame,
) -> pd.DataFrame:
    """Step 11: Unpivot AUM columns and build q_aum_time_series_labeled.

    Process:
    1. Deduplicate master to one row per ticker
    2. Filter to dim tickers
    3. Unpivot AUM columns
    4. Join dim on ticker (expands multi-category)
    5. Add issuer_group (top N per category by AUM + always REX)
    """
    # Step 1: Deduplicate
    deduped = master_df.drop_duplicates(subset=["ticker"], keep="first")

    # Step 2: Filter to dim tickers
    dim_tickers = set(dim["ticker"].astype(str).str.strip()) if not dim.empty else set()
    deduped = deduped[deduped["ticker"].isin(dim_tickers)].copy()

    if deduped.empty:
        log.info("[11/12] No tickers in dim, empty time series")
        return pd.DataFrame(columns=[
            "ticker", "date", "months_ago", "aum_value", "as_of_date",
            "category_display", "issuer_display", "is_rex",
            "issuer_group", "fund_category_key",
        ])

    # Step 3: Unpivot AUM columns
    ts = _unpivot_aum(deduped)
    if ts.empty:
        return ts

    # Step 4: Join dim on ticker (inner join to expand multi-category)
    dim_join_cols = ["ticker", "category_display", "issuer_display",
                     "is_rex", "fund_category_key"]
    dim_available = [c for c in dim_join_cols if c in dim.columns]
    ts = ts.merge(dim[dim_available], on="ticker", how="inner")

    # Step 5: Add issuer_group (auto-derived from top N per category)
    ts = _add_issuer_group(ts, master_df)

    # Reorder columns
    desired_order = [
        "ticker", "date", "months_ago", "aum_value", "as_of_date",
        "category_display", "issuer_display", "is_rex",
        "issuer_group", "fund_category_key",
    ]
    available_order = [c for c in desired_order if c in ts.columns]
    ts = ts[available_order]

    log.info("[11/12] Time series: %d rows", len(ts))
    return ts.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unpivot_aum(master_df: pd.DataFrame) -> pd.DataFrame:
    """Unpivot AUM columns into long format.

    Replicates data_engine._unpivot_aum() exactly.
    """
    aum_cols = [c for c in master_df.columns
                if re.match(r"t_w4\.aum(_\d+)?$", c, re.IGNORECASE)]

    if not aum_cols:
        return pd.DataFrame(columns=["ticker", "date", "months_ago",
                                      "aum_value", "as_of_date"])

    ts = pd.melt(
        master_df[["ticker"] + aum_cols],
        id_vars=["ticker"],
        var_name="aum_col",
        value_name="aum_value",
    )

    def _months_ago(col: str) -> int:
        m = re.search(r"aum_(\d+)$", col, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    ts["months_ago"] = ts["aum_col"].apply(_months_ago)

    as_of = pd.Timestamp(datetime.now().date())
    ts["as_of_date"] = as_of
    ts["date"] = ts["months_ago"].apply(
        lambda m: as_of - pd.DateOffset(months=m)
    )

    ts = ts.drop(columns=["aum_col"])
    return ts


def _add_issuer_group(ts: pd.DataFrame, master_df: pd.DataFrame) -> pd.DataFrame:
    """Auto-derive issuer_group: top N issuers per category by AUM + always REX.

    Instead of reading from a fragile Excel rules column, we compute:
    1. For each category_display, rank issuers by total AUM
    2. Top N get their own issuer_group name
    3. REX issuers always get their own name
    4. Everyone else -> "Other"
    """
    if "category_display" not in ts.columns or "issuer_display" not in ts.columns:
        ts["issuer_group"] = ts.get("issuer_display", pd.NA)
        return ts

    # Build include set: top N issuers per category + REX
    include_set = set()

    # Get current AUM per (category_display, issuer_display)
    current_aum = ts[ts["months_ago"] == 0].groupby(
        ["category_display", "issuer_display"]
    )["aum_value"].sum().reset_index()

    for cat in ts["category_display"].unique():
        cat_issuers = current_aum[current_aum["category_display"] == cat]
        cat_issuers = cat_issuers.sort_values("aum_value", ascending=False)
        top_n = cat_issuers.head(TOP_N_ISSUERS)
        for _, row in top_n.iterrows():
            include_set.add((row["category_display"], row["issuer_display"]))

    # Always include REX issuers
    if "is_rex" in ts.columns:
        rex_pairs = ts[ts["is_rex"] == True][  # noqa: E712
            ["category_display", "issuer_display"]
        ].drop_duplicates()
        for _, row in rex_pairs.iterrows():
            include_set.add((row["category_display"], row["issuer_display"]))

    # Apply
    ts["issuer_group"] = ts.apply(
        lambda r: r["issuer_display"]
        if (r.get("category_display"), r.get("issuer_display")) in include_set
        else "Other",
        axis=1,
    )

    return ts

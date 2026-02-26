"""Column rename layer: DB names <-> market_data.py names.

market_data.py expects columns like:
  t_w2.expense_ratio, t_w3.total_return_1day, t_w4.aum, t_w4.aum_1,
  q_category_attributes.map_li_category, etc.

DB columns are flat:
  expense_ratio, total_return_1day, aum, map_li_category, etc.

This module provides bidirectional conversion.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

from market.config import (
    W2_FIELDS, W3_FIELDS, W4_FIELDS, ALL_ATTR_COLS,
    W2_PREFIX, W3_PREFIX, W4_PREFIX, ATTR_PREFIX,
)

log = logging.getLogger(__name__)


def db_to_display(df: pd.DataFrame) -> pd.DataFrame:
    """Convert DB flat column names to display (prefixed) format.

    Used when reading from DB to produce the same column names that
    market_data.py expects from the Excel file.
    """
    rename = {}

    for col in W2_FIELDS:
        if col in df.columns:
            rename[col] = f"{W2_PREFIX}{col}"

    for col in W3_FIELDS:
        if col in df.columns:
            rename[col] = f"{W3_PREFIX}{col}"

    # W4 fields: flow columns + aum + aum_1..aum_36
    w4_flow = [f for f in W4_FIELDS if not f.startswith("aum_") and f != "aum"]
    for col in w4_flow:
        if col in df.columns:
            rename[col] = f"{W4_PREFIX}{col}"
    if "aum" in df.columns:
        rename["aum"] = f"{W4_PREFIX}aum"

    # Expand aum_history_json into individual t_w4.aum_N columns
    if "aum_history_json" in df.columns:
        df = _expand_aum_history(df)

    # Attribute columns
    for col in ALL_ATTR_COLS:
        if col in df.columns:
            rename[col] = f"{ATTR_PREFIX}{col}"

    df = df.rename(columns=rename)

    # Drop helper columns
    for drop_col in ["aum_history_json", "id", "pipeline_run_id", "updated_at"]:
        if drop_col in df.columns:
            df = df.drop(columns=[drop_col])

    return df


def display_to_db(df: pd.DataFrame) -> pd.DataFrame:
    """Convert display (prefixed) column names to DB flat format.

    Used when importing from existing Excel output into DB.
    """
    rename = {}

    for col in df.columns:
        if col.startswith(W2_PREFIX):
            rename[col] = col[len(W2_PREFIX):]
        elif col.startswith(W3_PREFIX):
            rename[col] = col[len(W3_PREFIX):]
        elif col.startswith(W4_PREFIX):
            rename[col] = col[len(W4_PREFIX):]
        elif col.startswith(ATTR_PREFIX):
            rename[col] = col[len(ATTR_PREFIX):]

    df = df.rename(columns=rename)
    return df


def _expand_aum_history(df: pd.DataFrame) -> pd.DataFrame:
    """Expand aum_history_json column into individual t_w4.aum_N columns."""
    if "aum_history_json" not in df.columns:
        return df

    # Parse JSON and expand
    for i in range(1, 37):
        col_name = f"{W4_PREFIX}aum_{i}"
        df[col_name] = df["aum_history_json"].apply(
            lambda j: _extract_aum(j, f"aum_{i}")
        )

    return df


def _extract_aum(json_str, key: str) -> float | None:
    """Extract a single AUM value from a JSON string."""
    if pd.isna(json_str) or json_str is None:
        return None
    try:
        data = json.loads(json_str) if isinstance(json_str, str) else {}
        val = data.get(key)
        return float(val) if val is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

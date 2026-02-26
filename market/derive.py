"""Derive dim_fund_category from rules (no manual dim table needed)."""
from __future__ import annotations

import logging
import re

import pandas as pd

from market.config import (
    ETP_TO_CATS, CAT_LI_SS, CAT_LI_INDEX, CAT_LI_OTHER,
    CAT_CC_SS, CAT_CC_INDEX, CAT_CC_OTHER,
    CAT_CRYPTO, CAT_DEFINED, CAT_THEMATIC,
    LI_ATTR_COLS, CC_ATTR_COLS, CRYPTO_ATTR_COLS,
)

log = logging.getLogger(__name__)


def derive_dim_fund_category(
    fund_mapping: pd.DataFrame,
    issuer_mapping: pd.DataFrame,
    rex_funds: pd.DataFrame,
    category_attributes: pd.DataFrame,
    etp_combined: pd.DataFrame,
) -> pd.DataFrame:
    """Build dim_fund_category from rule tables + raw ETP data.

    For each (ticker, etp_category) pair in fund_mapping, determines:
    - category_display: specific category name (e.g., "Leverage & Inverse - Single Stock")
    - issuer_display: normalized issuer name (from issuer_mapping)
    - is_rex: whether the fund is a REX fund
    - fund_category_key: "TICKER|category_display"

    Returns DataFrame with columns:
        ticker, etp_category, category_display, issuer_display, is_rex, fund_category_key
    """
    if fund_mapping.empty:
        return pd.DataFrame(columns=[
            "ticker", "etp_category", "category_display",
            "issuer_display", "is_rex", "fund_category_key",
        ])

    df = fund_mapping[["ticker", "etp_category"]].copy()

    # Determine category_display from etp_category + attributes
    df["category_display"] = df.apply(
        lambda r: _resolve_category_display(
            r["ticker"], r["etp_category"], category_attributes, etp_combined
        ),
        axis=1,
    )

    # Determine issuer_display via issuer_mapping
    # First get the raw issuer from etp_combined
    if "issuer" in etp_combined.columns:
        issuer_lookup = etp_combined[["ticker", "issuer"]].drop_duplicates(
            subset=["ticker"], keep="first"
        )
        df = df.merge(issuer_lookup, on="ticker", how="left")
    else:
        df["issuer"] = pd.NA

    # Join issuer_mapping on (etp_category, issuer)
    if not issuer_mapping.empty:
        df = df.merge(
            issuer_mapping[["etp_category", "issuer", "issuer_nickname"]],
            on=["etp_category", "issuer"],
            how="left",
        )
        df["issuer_display"] = df["issuer_nickname"].fillna(df["issuer"])
        df = df.drop(columns=["issuer_nickname"])
    else:
        df["issuer_display"] = df["issuer"]

    # Drop raw issuer column
    if "issuer" in df.columns:
        df = df.drop(columns=["issuer"])

    # Determine is_rex
    rex_set = set(rex_funds["ticker"].astype(str).str.strip()) if not rex_funds.empty else set()
    df["is_rex"] = df["ticker"].isin(rex_set)

    # Build fund_category_key
    df["fund_category_key"] = df["ticker"] + "|" + df["category_display"].fillna("")

    log.info("dim_fund_category: %d rows derived", len(df))
    return df


def _resolve_category_display(
    ticker: str,
    etp_category: str,
    category_attributes: pd.DataFrame,
    etp_combined: pd.DataFrame,
) -> str:
    """Resolve the specific category_display for a (ticker, etp_category) pair.

    Logic:
    - LI: check is_singlestock attribute -> Single Stock vs Index/Basket
    - CC: check is_singlestock -> Single Stock vs Index/Basket
    - Crypto/Defined/Thematic: direct mapping (one-to-one)
    """
    cats = ETP_TO_CATS.get(etp_category, set())
    if len(cats) == 1:
        return next(iter(cats))

    # Multi-option categories: LI and CC need is_singlestock disambiguation
    is_ss = _check_singlestock(ticker, etp_combined)

    if etp_category == "LI":
        if is_ss is True:
            return CAT_LI_SS
        elif is_ss is False:
            return CAT_LI_INDEX
        else:
            return CAT_LI_OTHER

    if etp_category == "CC":
        if is_ss is True:
            return CAT_CC_SS
        elif is_ss is False:
            return CAT_CC_INDEX
        else:
            return CAT_CC_OTHER

    # Fallback
    return next(iter(cats)) if cats else etp_category


def _check_singlestock(ticker: str, etp_combined: pd.DataFrame) -> bool | None:
    """Check if a ticker is single-stock from the ETP data.

    The is_singlestock column contains the underlying Bloomberg ticker:
      - "TSLA US"       -> single stock (US equity)
      - "EURUSD Curncy" -> currency    -> Index/Basket
      - "NGA Comdty"    -> commodity   -> Index/Basket
      - "SPX Index"     -> index       -> Index/Basket
      - "SPY US"        -> another ETF -> Index/Basket (ETF-of-ETF)
      - NaN             -> check fund_name for single-stock patterns

    Returns True, False, or None (unknown).
    """
    if "is_singlestock" not in etp_combined.columns:
        return _guess_singlestock_from_name(ticker, etp_combined)

    row = etp_combined.loc[etp_combined["ticker"] == ticker]
    if row.empty:
        return None

    val = row["is_singlestock"].iloc[0]
    if pd.isna(val) or not str(val).strip():
        # No underlying populated.  Most NaN tickers are index/basket funds.
        # Only override to Single Stock when the name heuristic positively
        # matches single-stock patterns (covers ~37 newer funds).
        guess = _guess_singlestock_from_name(ticker, etp_combined)
        return guess if guess is True else False

    val_str = str(val).strip()

    # Non-US suffixes are generally Index/Basket, except crypto spot
    # prices which Excel treats as Single Stock (single-asset products).
    if val_str.endswith(" Curncy"):
        crypto_spot = {
            "XBTUSD Curncy", "XETUSD Curncy",   # Bitcoin, Ethereum
            "XSOUSD Curncy", "XRPUSD Curncy",   # Solana, XRP
        }
        return True if val_str in crypto_spot else False
    if val_str.endswith(" Comdty") or val_str.endswith(" Index"):
        return False

    # Bloomberg "Equity" suffix (e.g. "ABNB US Equity") = stock
    if val_str.endswith(" Equity"):
        return True

    # US suffix: check if the underlying is itself in the fund universe
    # (meaning it's another ETF, not a single stock).
    # Exclude self-references (Bloomberg quirk where ticker = underlying).
    if val_str.endswith(" US"):
        if val_str == ticker:
            return True  # self-reference = single stock
        all_tickers = set(etp_combined["ticker"].astype(str).str.strip())
        if val_str in all_tickers:
            return False  # underlying is another fund in the universe

    return True  # US equity underlying = single stock


# Single-stock patterns in fund names (for NaN is_singlestock fallback).
# Must be very specific to avoid matching index/basket products.
# Match "YIELDMAX NVDA" (ticker-like word) but not "YIELDMAX SEMICONDUCTOR".
# Match "2X LONG TSLA DAILY" (ticker in position) but not "2X LONG INNOVATION 100".
_SS_NAME_PATTERNS = re.compile(
    r"(?i)("
    # YieldMax/YieldBoost + 2-5 letter ticker-like word (not descriptive words)
    r"yield(?:max|boost)\s+[A-Z]{2,5}\b(?!\s+(?:portfolio|miners|semiconductor|industry|target|dorsey|crypto|nasdaq|russell|100|50|500))"
    # "2X (Long|Short) TICKER Daily" -- ticker is 2-5 uppercase letters
    r"|(?:2x|3x|-2x|-3x)\s+(?:long|short)\s+[A-Z]{2,5}\s+daily"
    # "Target 2X (Long|Short) TICKER"
    r"|target\s+(?:2x|3x)\s+(?:long|short)\s+[A-Z]{2,5}\b"
    # Explicit "single stock"
    r"|single\s+stock"
    r")"
)


def _guess_singlestock_from_name(ticker: str, etp_combined: pd.DataFrame) -> bool | None:
    """Heuristic: guess single-stock from fund_name when is_singlestock is NaN.

    Newer single-stock funds often don't have the Bloomberg field populated.
    Their names usually contain patterns like "2X Long TSLA", "YieldMax NVDA",
    "Option Income Strategy", etc.
    """
    if "fund_name" not in etp_combined.columns:
        return None

    row = etp_combined.loc[etp_combined["ticker"] == ticker, "fund_name"]
    if row.empty:
        return None

    name = str(row.iloc[0]).strip()
    if not name or name == "nan":
        return None

    if _SS_NAME_PATTERNS.search(name):
        return True

    return None  # genuinely unknown

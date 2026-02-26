"""Queues report: detect unmapped funds and new issuers for triage."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from market.config import RULES_DIR

log = logging.getLogger(__name__)

# Category suggestion heuristics (keyword -> suggested category)
_CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
    "solana", "sol", "xrp", "ripple", "litecoin", "dogecoin",
]
_LI_KEYWORDS = [
    "2x", "3x", "4x", "-2x", "-3x", "bull", "bear", "inverse",
    "leveraged", "ultra", "direxion", "proshares ultra",
]
_CC_KEYWORDS = [
    "covered call", "income", "premium", "buywrite", "buy-write",
    "dividend", "yield", "enhanced",
]
_DEFINED_KEYWORDS = [
    "buffer", "floor", "defined outcome", "barrier", "accelerated",
    "cap", "downside",
]
_THEMATIC_KEYWORDS = [
    "thematic", "innovation", "genomic", "space", "robotics",
    "fintech", "cloud", "metaverse", "ai ", "artificial intelligence",
    "clean energy", "solar", "cannabis", "cybersecurity",
]


def detect_unmapped_funds(
    etp_combined: pd.DataFrame,
    fund_mapping: pd.DataFrame,
) -> pd.DataFrame:
    """Find tickers in the ETP data that are not in fund_mapping.

    Returns DataFrame with columns:
        ticker, fund_name, issuer, aum, fund_type, asset_class_focus,
        fund_description, suggested_category
    """
    mapped_tickers = set(fund_mapping["ticker"].astype(str).str.strip()) if not fund_mapping.empty else set()
    all_tickers = set(etp_combined["ticker"].astype(str).str.strip()) if not etp_combined.empty else set()

    unmapped = all_tickers - mapped_tickers
    if not unmapped:
        return pd.DataFrame(columns=[
            "ticker", "fund_name", "issuer", "aum", "fund_type",
            "asset_class_focus", "fund_description", "suggested_category",
        ])

    df = etp_combined[etp_combined["ticker"].isin(unmapped)].copy()
    df = df.drop_duplicates(subset=["ticker"], keep="first")

    # Select display columns
    display_cols = ["ticker"]
    for col in ["fund_name", "issuer", "fund_type", "asset_class_focus", "fund_description"]:
        if col in df.columns:
            display_cols.append(col)

    # AUM column may be prefixed
    aum_col = None
    for candidate in ["t_w4.aum", "aum"]:
        if candidate in df.columns:
            aum_col = candidate
            break
    if aum_col:
        display_cols.append(aum_col)

    df = df[display_cols].copy()
    if aum_col and aum_col != "aum":
        df = df.rename(columns={aum_col: "aum"})

    # Coerce AUM to numeric
    if "aum" in df.columns:
        df["aum"] = pd.to_numeric(df["aum"], errors="coerce")

    # Suggest category via heuristics
    df["suggested_category"] = df.apply(_suggest_category, axis=1)

    # Sort by AUM descending (biggest unmapped first)
    if "aum" in df.columns:
        df = df.sort_values("aum", ascending=False, na_position="last")

    log.info("Unmapped funds: %d", len(df))
    return df.reset_index(drop=True)


def detect_new_issuers(
    etp_combined: pd.DataFrame,
    fund_mapping: pd.DataFrame,
    issuer_mapping: pd.DataFrame,
) -> pd.DataFrame:
    """Find (etp_category, issuer) pairs in the data that are not in issuer_mapping.

    Returns DataFrame with columns:
        etp_category, issuer, product_count, total_aum
    """
    if fund_mapping.empty or "issuer" not in etp_combined.columns:
        return pd.DataFrame(columns=["etp_category", "issuer", "product_count", "total_aum"])

    # Get all (ticker, etp_category) from fund_mapping, join issuer from ETP data
    mapped = fund_mapping[["ticker", "etp_category"]].copy()
    issuer_lookup = etp_combined[["ticker", "issuer"]].drop_duplicates(
        subset=["ticker"], keep="first"
    )
    mapped = mapped.merge(issuer_lookup, on="ticker", how="left")
    mapped = mapped.dropna(subset=["issuer"])

    # Get known (etp_category, issuer) pairs
    if not issuer_mapping.empty:
        known = set(
            zip(issuer_mapping["etp_category"].astype(str),
                issuer_mapping["issuer"].astype(str))
        )
    else:
        known = set()

    # Find new pairs
    mapped["_key"] = mapped["etp_category"].astype(str) + "|" + mapped["issuer"].astype(str)
    known_keys = {f"{c}|{i}" for c, i in known}
    new_mask = ~mapped["_key"].isin(known_keys)
    new_pairs = mapped[new_mask].copy()

    if new_pairs.empty:
        return pd.DataFrame(columns=["etp_category", "issuer", "product_count", "total_aum"])

    # Aggregate: count products and total AUM per (etp_category, issuer)
    aum_col = None
    for candidate in ["t_w4.aum", "aum"]:
        if candidate in etp_combined.columns:
            aum_col = candidate
            break

    if aum_col:
        aum_lookup = etp_combined[["ticker", aum_col]].copy()
        aum_lookup[aum_col] = pd.to_numeric(aum_lookup[aum_col], errors="coerce")
        new_pairs = new_pairs.merge(aum_lookup, on="ticker", how="left")
        result = new_pairs.groupby(["etp_category", "issuer"]).agg(
            product_count=("ticker", "count"),
            total_aum=(aum_col, "sum"),
        ).reset_index()
    else:
        result = new_pairs.groupby(["etp_category", "issuer"]).agg(
            product_count=("ticker", "count"),
        ).reset_index()
        result["total_aum"] = 0

    result = result.sort_values("total_aum", ascending=False, na_position="last")
    log.info("New issuers: %d", len(result))
    return result.reset_index(drop=True)


def build_queues_report(
    etp_combined: pd.DataFrame,
    fund_mapping: pd.DataFrame,
    issuer_mapping: pd.DataFrame,
) -> dict:
    """Build the full queues report and save to JSON.

    Returns dict with keys: unmapped_funds, new_issuers, summary
    """
    unmapped = detect_unmapped_funds(etp_combined, fund_mapping)
    new_issuers = detect_new_issuers(etp_combined, fund_mapping, issuer_mapping)

    report = {
        "unmapped_funds": unmapped.to_dict(orient="records"),
        "new_issuers": new_issuers.to_dict(orient="records"),
        "summary": {
            "unmapped_count": len(unmapped),
            "new_issuer_count": len(new_issuers),
        },
    }

    # Save to JSON
    out_path = RULES_DIR / "_queues_report.json"
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("Queues report saved: %s", out_path)

    return report


def _suggest_category(row: pd.Series) -> str:
    """Suggest an etp_category based on fund name/description keywords."""
    text = " ".join(
        str(row.get(c, "")).lower()
        for c in ["fund_name", "fund_description", "asset_class_focus"]
        if pd.notna(row.get(c))
    )

    if any(kw in text for kw in _CRYPTO_KEYWORDS):
        return "Crypto"
    if any(kw in text for kw in _LI_KEYWORDS):
        return "LI"
    if any(kw in text for kw in _CC_KEYWORDS):
        return "CC"
    if any(kw in text for kw in _DEFINED_KEYWORDS):
        return "Defined"
    if any(kw in text for kw in _THEMATIC_KEYWORDS):
        return "Thematic"
    return ""

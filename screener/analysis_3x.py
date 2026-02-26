"""3x & 4x Leveraged ETF filing recommendation analysis (V2).

Provides data functions for the 3x/4x filing recommendation PDF report:
  - Market snapshot (3x + 2x KPIs)
  - Top 2x single stock ETFs by AUM (with REX 2x status)
  - Underlier popularity (no gap column - all are first-mover)
  - REX track record (ALL products, not just single-stock)
  - 3x filing score (40% fundamentals + 60% 2x AUM percentile)
  - 3x filing candidates (tiered: 50/50/100 = 200 total)
  - 4x filing candidates (low-vol 2x successes)
  - Blow-up risk analysis with exec-friendly "Extreme Day Odds"
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from screener.config import (
    FILING_SCORE_WEIGHTS,
    FOUR_X_CRITERIA,
    RISK_THRESHOLDS,
    SCORING_WEIGHTS,
    TIER_CUTOFFS,
)

log = logging.getLogger(__name__)

UNDERLIER_COL = "q_category_attributes.map_li_underlier"
DIRECTION_COL = "q_category_attributes.map_li_direction"
LEVERAGE_COL = "q_category_attributes.map_li_leverage_amount"
SUBCAT_COL = "q_category_attributes.map_li_subcategory"


def _clean_underlier(raw: str) -> str:
    """Strip ' US' / ' Curncy' suffix and uppercase."""
    return str(raw).replace(" US", "").replace(" Curncy", "").upper().strip()


def _build_rex_2x_status(etp_df: pd.DataFrame) -> dict[str, str]:
    """Build underlier -> REX 2x filing status lookup.

    Returns: "Yes" (trading), "Filed" (pending/delayed), or absent (no 2x).
    """
    rex_mask = (
        (etp_df.get("is_rex") == True)
        & (etp_df.get("uses_leverage") == True)
        & (pd.to_numeric(etp_df.get(LEVERAGE_COL, 0), errors="coerce") == 2.0)
    )
    rex_2x = etp_df[rex_mask]
    aum_col = "t_w4.aum"

    status = {}
    for _, row in rex_2x.iterrows():
        underlier = _clean_underlier(row.get(UNDERLIER_COL, ""))
        if not underlier:
            continue
        aum = pd.to_numeric(row.get(aum_col, 0), errors="coerce") or 0
        # If it has AUM, it's trading; otherwise it's filed but not trading yet
        if aum > 0:
            status[underlier] = "Yes"
        elif underlier not in status:
            status[underlier] = "Filed"

    # Also check pipeline DB for PENDING 2x filings
    try:
        from screener.filing_match import get_filing_status_by_underlier
        db_map = get_filing_status_by_underlier()
        for key, entries in db_map.items():
            clean_key = key.upper()
            if clean_key in status:
                continue
            for entry in entries:
                fname = (entry.get("fund_name") or "").upper()
                if "2X" in fname and ("T-REX" in fname or "REX" in fname):
                    st = entry.get("status", "")
                    if st == "EFFECTIVE":
                        status[clean_key] = "Yes"
                    elif st in ("PENDING", "DELAYED"):
                        status.setdefault(clean_key, "Filed")
    except Exception:
        pass

    return status


# Ticker aliases: SEC filing name -> Bloomberg underlier
_TICKER_ALIASES = {"GOOG": "GOOGL"}


def _build_rex_4x_status(etp_df: pd.DataFrame) -> dict[str, str]:
    """Build underlier -> REX 4x filing status lookup.

    Returns: "Yes" (trading), "Filed" (pending/delayed), or absent (no 4x).
    """
    rex_mask = (
        (etp_df.get("is_rex") == True)
        & (etp_df.get("uses_leverage") == True)
        & (pd.to_numeric(etp_df.get(LEVERAGE_COL, 0), errors="coerce") == 4.0)
    )
    rex_4x = etp_df[rex_mask]
    aum_col = "t_w4.aum"

    status = {}
    for _, row in rex_4x.iterrows():
        underlier = _clean_underlier(row.get(UNDERLIER_COL, ""))
        if not underlier:
            continue
        aum = pd.to_numeric(row.get(aum_col, 0), errors="coerce") or 0
        if aum > 0:
            status[underlier] = "Yes"
        elif underlier not in status:
            status[underlier] = "Filed"

    # Also check pipeline DB for PENDING 4x filings
    try:
        from screener.filing_match import get_filing_status_by_underlier
        db_map = get_filing_status_by_underlier()
        for key, entries in db_map.items():
            clean_key = _TICKER_ALIASES.get(key.upper(), key.upper())
            if clean_key in status:
                continue
            for entry in entries:
                fname = (entry.get("fund_name") or "").upper()
                if "4X" in fname and ("T-REX" in fname or "REX" in fname):
                    st = entry.get("status", "")
                    if st == "EFFECTIVE":
                        status[clean_key] = "Yes"
                    elif st in ("PENDING", "DELAYED"):
                        status.setdefault(clean_key, "Filed")
    except Exception:
        pass

    return status


def _build_2x_aum_lookup(etp_df: pd.DataFrame) -> dict[str, dict]:
    """Build underlier -> {aum_2x, count_2x} from all 2x single-stock products."""
    aum_col = "t_w4.aum"
    mask = (
        (etp_df.get("uses_leverage") == True)
        & (pd.to_numeric(etp_df.get(LEVERAGE_COL, 0), errors="coerce") == 2.0)
        & (etp_df[UNDERLIER_COL].notna())
        & (etp_df[UNDERLIER_COL] != "")
        & (
            (etp_df.get(SUBCAT_COL, "").fillna("").str.contains("Single Stock", case=False, na=False))
            | (etp_df.get("is_singlestock", False) == True)
        )
    )
    lev = etp_df[mask].copy()
    lev["_aum"] = pd.to_numeric(lev.get(aum_col, 0), errors="coerce").fillna(0)

    lookup = {}
    for underlier_raw, group in lev.groupby(UNDERLIER_COL):
        uc = _clean_underlier(underlier_raw)
        lookup[uc] = {
            "aum_2x": round(group["_aum"].sum(), 1),
            "count_2x": len(group),
        }
    return lookup


# ---------------------------------------------------------------------------
# NEW: 3x Filing Score (40% fundamentals + 60% 2x AUM percentile)
# ---------------------------------------------------------------------------

def compute_3x_filing_score(
    scored_df: pd.DataFrame,
    etp_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute 3x filing score blending stock fundamentals with 2x AUM demand.

    Score = 40% * composite_score_pctl + 60% * aum_2x_pctl

    This ensures stocks with massive 2x product AUM (TSLA, NVDA) rank above
    stocks with strong fundamentals but weak 2x market validation.

    Adds columns: aum_2x, count_2x, aum_2x_pctl, composite_score_pctl, filing_score_3x
    """
    df = scored_df.copy()

    # Build 2x AUM lookup
    aum_lookup = _build_2x_aum_lookup(etp_df)

    # Map 2x AUM and count to each stock
    ticker_col = "ticker_clean" if "ticker_clean" in df.columns else "Ticker"
    df["aum_2x"] = df[ticker_col].apply(
        lambda t: aum_lookup.get(str(t).upper(), {}).get("aum_2x", 0)
    )
    df["count_2x"] = df[ticker_col].apply(
        lambda t: aum_lookup.get(str(t).upper(), {}).get("count_2x", 0)
    )

    # Percentile rank composite score
    df["composite_score_pctl"] = df["composite_score"].rank(pct=True, na_option="bottom") * 100

    # Percentile rank 2x AUM: stocks with 0 AUM get 0, others ranked among peers with AUM > 0.
    # This ensures TSLA ($6.9B) >> BABA ($168M) instead of both getting ~99th percentile.
    has_aum = df["aum_2x"] > 0
    df["aum_2x_pctl"] = 0.0
    if has_aum.any():
        df.loc[has_aum, "aum_2x_pctl"] = (
            df.loc[has_aum, "aum_2x"].rank(pct=True, ascending=True) * 100
        )

    # Blend: 40% fundamentals + 60% 2x AUM
    w_composite = FILING_SCORE_WEIGHTS["composite_pctl"]
    w_aum = FILING_SCORE_WEIGHTS["aum_2x_pctl"]
    df["filing_score_3x"] = (
        df["composite_score_pctl"] * w_composite
        + df["aum_2x_pctl"] * w_aum
    ).round(1)

    # Sort by filing score
    df = df.sort_values("filing_score_3x", ascending=False).reset_index(drop=True)
    df["filing_rank_3x"] = range(1, len(df) + 1)

    top5 = df.head(5)
    log.info("3x filing score computed. Top 5: %s",
             ", ".join(f"{r[ticker_col]}({r['filing_score_3x']:.0f})" for _, r in top5.iterrows()))

    return df


# ---------------------------------------------------------------------------
# Section 1: Executive Summary KPIs
# ---------------------------------------------------------------------------

def get_3x_market_snapshot(etp_df: pd.DataFrame) -> dict:
    """Compute 3x AND 2x market overview KPIs.

    Returns dict with keys for both 3x and 2x markets:
    3x: total_aum, product_count, single_stock_count, single_stock_aum,
        rex_aum, rex_count, top_issuers
    2x: total_2x_aum, total_2x_count, ss_2x_aum, ss_2x_count,
        rex_2x_aum, rex_2x_count
    """
    lev_col = LEVERAGE_COL
    aum_col = "t_w4.aum"

    def _market_kpis(leverage_amount: float) -> dict:
        mask = (
            (etp_df.get("uses_leverage") == True)
            & (pd.to_numeric(etp_df.get(lev_col, 0), errors="coerce") == leverage_amount)
        )
        products = etp_df[mask].copy()
        products["_aum"] = pd.to_numeric(products.get(aum_col, 0), errors="coerce").fillna(0)

        is_rex = products.get("is_rex", pd.Series(False, index=products.index)).fillna(False)
        rex = products[is_rex == True]

        is_ss = products.get(SUBCAT_COL, pd.Series("", index=products.index)).fillna("")
        ss_mask = is_ss.str.contains("Single Stock", case=False, na=False) | (products.get("is_singlestock", False) == True)
        ss = products[ss_mask]

        return {
            "total_aum": round(products["_aum"].sum(), 1),
            "count": len(products),
            "rex_aum": round(rex["_aum"].sum(), 1),
            "rex_count": len(rex),
            "ss_aum": round(ss["_aum"].sum(), 1),
            "ss_count": len(ss),
        }

    kpi_3x = _market_kpis(3.0)
    kpi_2x = _market_kpis(2.0)

    # Top 3x issuers by AUM
    mask_3x = (
        (etp_df.get("uses_leverage") == True)
        & (pd.to_numeric(etp_df.get(lev_col, 0), errors="coerce") == 3.0)
    )
    products_3x = etp_df[mask_3x].copy()
    products_3x["_aum"] = pd.to_numeric(products_3x.get(aum_col, 0), errors="coerce").fillna(0)

    top_issuers = []
    if "issuer" in products_3x.columns:
        issuer_agg = (
            products_3x.groupby("issuer")
            .agg(issuer_aum=("_aum", "sum"), issuer_count=("_aum", "count"))
            .sort_values("issuer_aum", ascending=False)
            .head(5)
        )
        top_issuers = [
            {"issuer": name, "aum": round(row["issuer_aum"], 1), "count": int(row["issuer_count"])}
            for name, row in issuer_agg.iterrows()
        ]

    return {
        # 3x KPIs
        "total_aum": kpi_3x["total_aum"],
        "product_count": kpi_3x["count"],
        "rex_aum": kpi_3x["rex_aum"],
        "rex_count": kpi_3x["rex_count"],
        "single_stock_count": kpi_3x["ss_count"],
        "single_stock_aum": kpi_3x["ss_aum"],
        "top_issuers": top_issuers,
        # 2x KPIs
        "total_2x_aum": kpi_2x["total_aum"],
        "total_2x_count": kpi_2x["count"],
        "ss_2x_aum": kpi_2x["ss_aum"],
        "ss_2x_count": kpi_2x["ss_count"],
        "rex_2x_aum": kpi_2x["rex_aum"],
        "rex_2x_count": kpi_2x["rex_count"],
    }


# ---------------------------------------------------------------------------
# Section 2: Market Landscape
# ---------------------------------------------------------------------------

def get_top_2x_single_stock(etp_df: pd.DataFrame, n: int = 100) -> list[dict]:
    """Top N 2x single-stock ETFs sorted by AUM descending.

    Includes REX 2x status column.
    """
    aum_col = "t_w4.aum"
    flow_col = "t_w4.fund_flow_1month"

    mask = (
        (etp_df.get("uses_leverage") == True)
        & (pd.to_numeric(etp_df.get(LEVERAGE_COL, 0), errors="coerce") == 2.0)
        & (
            (etp_df.get(SUBCAT_COL, "").fillna("").str.contains("Single Stock", case=False, na=False))
            | (etp_df.get("is_singlestock", False) == True)
        )
    )
    df = etp_df[mask].copy()
    df["_aum"] = pd.to_numeric(df.get(aum_col, 0), errors="coerce").fillna(0)
    df = df[df["_aum"] > 0].sort_values("_aum", ascending=False).head(n)

    # REX 2x status lookup
    rex_2x = _build_rex_2x_status(etp_df)

    results = []
    for _, row in df.iterrows():
        aum = float(row["_aum"])
        flow = pd.to_numeric(row.get(flow_col, 0), errors="coerce")
        underlier = _clean_underlier(row.get(UNDERLIER_COL, ""))
        is_rex = row.get("is_rex", False)

        results.append({
            "ticker": str(row.get("ticker", "")),
            "fund_name": str(row.get("fund_name", ""))[:50],
            "issuer": str(row.get("issuer", "")),
            "underlier": underlier,
            "direction": str(row.get(DIRECTION_COL, "")),
            "aum": round(aum, 1),
            "flow_1m": round(float(flow), 1) if pd.notna(flow) else 0,
            "is_rex": bool(is_rex) if pd.notna(is_rex) else False,
            "rex_2x": rex_2x.get(underlier, "No"),
        })

    log.info("Top %d 2x single-stock ETFs (of %d with AUM>0)", len(results), mask.sum())
    return results


def get_underlier_popularity(
    etp_df: pd.DataFrame,
    stock_df: pd.DataFrame | None = None,
    top_n: int = 50,
) -> list[dict]:
    """Group 2x single-stock products by underlier.

    No gap column (all are first-mover since zero 3x SS exist).
    Includes REX 2x status and filing_score_3x.
    """
    aum_col = "t_w4.aum"

    mask_lev = (
        (etp_df.get("uses_leverage") == True)
        & (pd.to_numeric(etp_df.get(LEVERAGE_COL, 0), errors="coerce") == 2.0)
        & (etp_df[UNDERLIER_COL].notna())
        & (etp_df[UNDERLIER_COL] != "")
        & (
            (etp_df.get(SUBCAT_COL, "").fillna("").str.contains("Single Stock", case=False, na=False))
            | (etp_df.get("is_singlestock", False) == True)
        )
    )
    lev = etp_df[mask_lev].copy()
    lev["_aum"] = pd.to_numeric(lev.get(aum_col, 0), errors="coerce").fillna(0)

    # Sector lookup
    sector_lookup = {}
    if stock_df is not None and "ticker_clean" in stock_df.columns and "GICS Sector" in stock_df.columns:
        for _, row in stock_df.iterrows():
            tc = str(row["ticker_clean"]).upper()
            sector = row.get("GICS Sector")
            if pd.notna(sector):
                sector_lookup[tc] = str(sector)

    # REX 2x status
    rex_2x = _build_rex_2x_status(etp_df)

    results = []
    for underlier_raw, group in lev.groupby(UNDERLIER_COL):
        uc = _clean_underlier(underlier_raw)
        count_2x = len(group)
        aum_2x = group["_aum"].sum()

        if count_2x == 0:
            continue

        results.append({
            "underlier": uc,
            "sector": sector_lookup.get(uc, "-"),
            "count_2x": count_2x,
            "aum_2x": round(aum_2x, 1),
            "rex_2x": rex_2x.get(uc, "No"),
        })

    results.sort(key=lambda x: x["aum_2x"], reverse=True)
    results = results[:top_n]
    log.info("Underlier popularity: %d underliers (top %d by 2x AUM)", len(results), top_n)
    return results


# ---------------------------------------------------------------------------
# Section 3: REX Track Record (ALL T-REX products)
# ---------------------------------------------------------------------------

def get_rex_track_record(
    etp_df: pd.DataFrame,
    scored_df: pd.DataFrame,
) -> list[dict]:
    """ALL T-REX branded products with AUM and 3x filing score.

    Filters to fund_name containing 'T-REX' (excludes MicroSectors, Osprey, etc).
    Includes ALL products even those with AUM=0 (recently launched).
    Includes category column (Single Stock / Index / Crypto / etc).
    Uses filing_score_3x when available, falls back to composite_score.
    """
    aum_col = "t_w4.aum"

    # Only T-REX branded products (not MicroSectors, Osprey, FEPI, etc)
    fname_col = etp_df.get("fund_name", pd.Series("", index=etp_df.index)).fillna("")
    mask = fname_col.str.contains("T-REX", case=False, na=False)
    rex = etp_df[mask].copy()
    rex["_aum"] = pd.to_numeric(rex.get(aum_col, 0), errors="coerce").fillna(0)
    rex = rex.sort_values("_aum", ascending=False)

    # Build score lookup from scored_df
    score_lookup = {}
    if "ticker_clean" in scored_df.columns:
        score_col = "filing_score_3x" if "filing_score_3x" in scored_df.columns else "composite_score"
        rank_col = "filing_rank_3x" if "filing_rank_3x" in scored_df.columns else "rank"
        for _, row in scored_df.iterrows():
            tc = str(row["ticker_clean"]).upper()
            score_lookup[tc] = {
                "score": float(row.get(score_col, 0)),
                "rank": int(row.get(rank_col, 0)),
            }

    seen = set()
    results = []
    for _, row in rex.iterrows():
        t = row.get("ticker", "")
        if t in seen:
            continue
        seen.add(t)

        underlier_raw = str(row.get(UNDERLIER_COL, ""))
        underlier_clean = _clean_underlier(underlier_raw) if underlier_raw else ""
        lev_amount = pd.to_numeric(row.get(LEVERAGE_COL, 0), errors="coerce")
        lev_str = f"{lev_amount:.0f}x" if pd.notna(lev_amount) and lev_amount > 0 else "-"

        # Determine category
        subcat = str(row.get(SUBCAT_COL, "")) if pd.notna(row.get(SUBCAT_COL)) else ""
        uses_lev = row.get("uses_leverage", False)
        if "Single Stock" in subcat:
            category = "Single Stock"
        elif "Crypto" in subcat or "Bitcoin" in subcat.lower() or "Ethereum" in subcat.lower():
            category = "Crypto"
        elif uses_lev:
            category = "Index"
        else:
            category = "Unleveraged"

        s = score_lookup.get(underlier_clean, {})

        results.append({
            "fund_ticker": str(t),
            "underlier": underlier_clean,
            "leverage": lev_str,
            "direction": str(row.get(DIRECTION_COL, "")),
            "aum": round(float(row["_aum"]), 1),
            "score": s.get("score"),
            "rank": s.get("rank"),
            "category": category,
        })

    log.info("REX track record: %d T-REX products (%d with AUM > 0)",
             len(results), sum(1 for r in results if r["aum"] > 0))
    return results


# ---------------------------------------------------------------------------
# Section 4: 3x Filing Candidates (Tiered - 50/50/100)
# ---------------------------------------------------------------------------

def get_3x_candidates(
    scored_df: pd.DataFrame,
    etp_df: pd.DataFrame,
) -> dict[str, list[dict]]:
    """Compute tiered 3x filing recommendations.

    ALL tiers sorted by filing_score_3x (consistent scoring across tiers).
    Tier 1: Stocks with PROVEN 2x demand (AUM > 0) - highest filing_score_3x.
    Tier 2: Next best stocks by filing_score_3x (may lack 2x history).
    Tier 3: Monitor list - remaining stocks by filing_score_3x.

    Risk is INFORMATIONAL ONLY - not used as a filter.
    Targets: 50 Tier 1, 50 Tier 2, 100 Tier 3 = 200 total.
    Returns {"tier_1": [...], "tier_2": [...], "tier_3": [...]}.
    """
    t1_max = TIER_CUTOFFS["tier_1_count"]
    t2_max = TIER_CUTOFFS["tier_2_count"]
    t3_max = TIER_CUTOFFS["tier_3_count"]

    # Only stocks passing threshold filters, with real vol & OI data
    df = scored_df[scored_df.get("passes_filters", True) == True].copy()
    vol_col = "Volatility 30D"
    oi_col = "Total OI"
    if vol_col in df.columns:
        df = df[pd.to_numeric(df[vol_col], errors="coerce").fillna(0) > 0]
    if oi_col in df.columns:
        df = df[pd.to_numeric(df[oi_col], errors="coerce").fillna(0) > 0]

    # REX 2x status
    rex_2x = _build_rex_2x_status(etp_df)

    # Build risk lookup (informational only - no filtering)
    risk_df = compute_blowup_risk(scored_df)
    risk_lookup = {}
    if not risk_df.empty:
        for _, row in risk_df.iterrows():
            tc = str(row.get("ticker_clean", "")).upper()
            risk_lookup[tc] = row.get("risk_level", "LOW")

    # Sector abbreviations
    sector_abbrev = {
        "Information Technology": "Info Tech",
        "Communication Services": "Comm Svcs",
        "Consumer Discretionary": "Cons Disc",
        "Consumer Staples": "Cons Staples",
    }

    # Use filing_score_3x consistently for ALL tiers
    filing_col = "filing_score_3x" if "filing_score_3x" in df.columns else "composite_score"

    def _make_candidate(row):
        tc = str(row.get("ticker_clean", row.get("Ticker", ""))).upper()
        score = float(row.get(filing_col, 0))
        mkt_cap = float(row.get("Mkt Cap", 0)) if pd.notna(row.get("Mkt Cap")) else 0
        sector_raw = str(row.get("GICS Sector", "")) if pd.notna(row.get("GICS Sector")) else "-"
        sector = sector_abbrev.get(sector_raw, sector_raw[:14]) if sector_raw != "-" else "-"
        aum_2x = float(row.get("aum_2x", 0)) if "aum_2x" in row.index else 0
        count_2x = int(row.get("count_2x", 0)) if "count_2x" in row.index else 0
        risk = risk_lookup.get(tc, "LOW")

        return {
            "ticker": tc,
            "sector": sector,
            "score": round(score, 1),
            "mkt_cap": round(mkt_cap, 0),
            "aum_2x": round(aum_2x, 1),
            "count_2x": count_2x,
            "rex_2x": rex_2x.get(tc, "No"),
            "risk": risk,
        }

    # --- Tier 1: Proven 2x demand stocks, sorted by filing_score_3x ---
    # No risk filtering - risk is informational only
    has_aum = df["aum_2x"] > 0 if "aum_2x" in df.columns else pd.Series(False, index=df.index)
    tier1_pool = df[has_aum].sort_values(filing_col, ascending=False)

    tier_1 = []
    tier1_tickers = set()
    for _, row in tier1_pool.iterrows():
        tc = str(row.get("ticker_clean", row.get("Ticker", ""))).upper()
        candidate = _make_candidate(row)
        tier_1.append(candidate)
        tier1_tickers.add(tc)
        if len(tier_1) >= t1_max:
            break

    # --- Tier 2 & 3: Remaining stocks sorted by filing_score_3x ---
    # Consistent scoring: same filing_score_3x column, no risk filtering
    ticker_col = "ticker_clean" if "ticker_clean" in df.columns else "Ticker"
    remaining = df[~df[ticker_col].str.upper().isin(tier1_tickers)]
    remaining = remaining.sort_values(filing_col, ascending=False)

    tier_2 = []
    tier_3 = []
    for _, row in remaining.iterrows():
        tc = str(row.get("ticker_clean", row.get("Ticker", ""))).upper()
        if tc in tier1_tickers:
            continue
        candidate = _make_candidate(row)

        if len(tier_2) < t2_max:
            tier_2.append(candidate)
        elif len(tier_3) < t3_max:
            tier_3.append(candidate)

        if len(tier_2) >= t2_max and len(tier_3) >= t3_max:
            break

    log.info("3x candidates: Tier 1=%d, Tier 2=%d, Tier 3=%d (target %d/%d/%d)",
             len(tier_1), len(tier_2), len(tier_3), t1_max, t2_max, t3_max)

    return {"tier_1": tier_1, "tier_2": tier_2, "tier_3": tier_3}


# ---------------------------------------------------------------------------
# NEW Section 5: 4x Filing Candidates
# ---------------------------------------------------------------------------

def get_4x_candidates(
    etp_df: pd.DataFrame,
    stock_df: pd.DataFrame,
) -> list[dict]:
    """Identify 4x filing candidates: 2x successes with manageable volatility.

    Any stock with existing 2x products and daily vol < 20%.
    4x amplifies daily moves by 4. Risk column is informational.

    Returns list of dicts sorted by 2x AUM descending.
    """
    min_aum = FOUR_X_CRITERIA["min_2x_aum"]
    max_daily_vol = FOUR_X_CRITERIA["max_daily_vol"]

    # Build 2x AUM lookup
    aum_lookup = _build_2x_aum_lookup(etp_df)

    # Compute risk for all stocks
    risk_df = compute_blowup_risk(stock_df)
    risk_lookup = {}
    vol_lookup = {}
    daily_vol_lookup = {}
    if not risk_df.empty:
        for _, row in risk_df.iterrows():
            tc = str(row.get("ticker_clean", "")).upper()
            risk_lookup[tc] = row.get("risk_level", "LOW")
            vol_lookup[tc] = float(row.get("vol_30d", 0))
            daily_vol_lookup[tc] = float(row.get("daily_vol", 0))

    # REX 2x and 4x status
    rex_2x = _build_rex_2x_status(etp_df)
    rex_4x = _build_rex_4x_status(etp_df)

    # Sector lookup
    sector_lookup = {}
    if "ticker_clean" in stock_df.columns and "GICS Sector" in stock_df.columns:
        for _, row in stock_df.iterrows():
            tc = str(row["ticker_clean"]).upper()
            sector = row.get("GICS Sector")
            if pd.notna(sector):
                sector_lookup[tc] = str(sector)

    sector_abbrev = {
        "Information Technology": "Info Tech",
        "Communication Services": "Comm Svcs",
        "Consumer Discretionary": "Cons Disc",
        "Consumer Staples": "Cons Staples",
    }

    results = []
    for underlier, info in aum_lookup.items():
        aum_2x = info["aum_2x"]
        count_2x = info["count_2x"]

        if aum_2x < min_aum:
            continue

        daily_vol = daily_vol_lookup.get(underlier, 0)
        if daily_vol <= 0 or daily_vol > max_daily_vol:
            continue

        vol_30d = vol_lookup.get(underlier, 0)
        risk = risk_lookup.get(underlier, "LOW")
        sector_raw = sector_lookup.get(underlier, "-")
        sector = sector_abbrev.get(sector_raw, sector_raw[:14]) if sector_raw != "-" else "-"

        results.append({
            "ticker": underlier,
            "sector": sector,
            "aum_2x": round(aum_2x, 1),
            "vol_30d": round(vol_30d, 1),
            "daily_vol": round(daily_vol, 2),
            "count_2x": count_2x,
            "rex_2x": rex_2x.get(underlier, "No"),
            "rex_4x": rex_4x.get(underlier, "No"),
            "risk": risk,
        })

    results.sort(key=lambda x: x["aum_2x"], reverse=True)

    # Cap at max_candidates (sorted by 2x AUM, so top names preserved)
    max_candidates = FOUR_X_CRITERIA.get("max_candidates", 100)
    if len(results) > max_candidates:
        log.info("4x candidates: %d qualified, capped at %d", len(results), max_candidates)
        results = results[:max_candidates]

    log.info("4x candidates: %d stocks (2x products, daily vol < %.0f%%)", len(results), max_daily_vol)
    return results


# ---------------------------------------------------------------------------
# Section 6: Blow-up Risk
# ---------------------------------------------------------------------------

def _risk_to_odds(prob_pct: float) -> str:
    """Convert probability percentage to exec-friendly 'Extreme Day Odds'.

    0.01% -> '1 in 10,000'
    0.1% -> '1 in 1,000'
    1% -> '1 in 100'
    5% -> '1 in 20'
    >10% -> 'Elevated'
    <0.001% -> 'Rare'
    """
    if prob_pct <= 0 or prob_pct < 0.001:
        return "Rare"
    if prob_pct >= 10:
        return "Elevated"
    ratio = 100.0 / prob_pct
    if ratio >= 10000:
        return "Rare"
    elif ratio >= 1000:
        return f"1 in {int(round(ratio, -2)):,}"
    elif ratio >= 100:
        return f"1 in {int(round(ratio, -1)):,}"
    elif ratio >= 10:
        return f"1 in {int(round(ratio)):,}"
    else:
        return "Elevated"


def compute_blowup_risk(stock_df: pd.DataFrame) -> pd.DataFrame:
    """Compute blow-up risk from Vol 30D.

    A 3x fund hits +-30% when the stock moves +-10%.
    A 3x fund NAV goes to zero if stock declines -33.3% in a day.

    Returns DataFrame with: ticker_clean, vol_30d, daily_vol, prob_10pct_day,
    extreme_day_odds, impact_3x, risk_level.
    """
    df = stock_df.copy()
    vol_col = "Volatility 30D"

    if vol_col not in df.columns:
        log.warning("Volatility 30D column not found")
        return pd.DataFrame()

    df["_vol30d"] = pd.to_numeric(df[vol_col], errors="coerce")
    df = df.dropna(subset=["_vol30d"])
    df = df[df["_vol30d"] > 0]

    # Daily vol = annualized vol / sqrt(252)
    sqrt_252 = math.sqrt(252)
    df["daily_vol"] = df["_vol30d"] / sqrt_252

    # Probability of +-10% daily move
    from scipy.stats import norm
    df["prob_10pct_day"] = df["daily_vol"].apply(
        lambda dv: 2 * (1 - norm.cdf(10.0, 0, dv)) * 100 if dv > 0 else 0
    )

    # Exec-friendly odds
    df["extreme_day_odds"] = df["prob_10pct_day"].apply(_risk_to_odds)

    # Risk levels
    low_max = RISK_THRESHOLDS["low_max_daily_vol"]
    med_max = RISK_THRESHOLDS["medium_max_daily_vol"]
    high_max = RISK_THRESHOLDS["high_max_daily_vol"]

    def _risk(dv):
        if dv < low_max:
            return "LOW"
        elif dv < med_max:
            return "MEDIUM"
        elif dv < high_max:
            return "HIGH"
        else:
            return "EXTREME"

    df["risk_level"] = df["daily_vol"].apply(_risk)

    # 3x impact annotation
    df["impact_3x"] = df["daily_vol"].apply(
        lambda dv: f"+-{dv * 3:.1f}% fund day"
    )

    ticker_col = "ticker_clean" if "ticker_clean" in df.columns else "Ticker"
    out = df[[ticker_col, "_vol30d", "daily_vol", "prob_10pct_day", "extreme_day_odds", "impact_3x", "risk_level"]].copy()
    out.columns = ["ticker_clean", "vol_30d", "daily_vol", "prob_10pct_day", "extreme_day_odds", "impact_3x", "risk_level"]
    out = out.sort_values("daily_vol", ascending=False).reset_index(drop=True)

    log.info("Blow-up risk: %d stocks, %d MEDIUM+, %d HIGH+, %d EXTREME",
             len(out),
             len(out[out["risk_level"].isin(["MEDIUM", "HIGH", "EXTREME"])]),
             len(out[out["risk_level"].isin(["HIGH", "EXTREME"])]),
             len(out[out["risk_level"] == "EXTREME"]))
    return out

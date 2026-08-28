"""Candidate Evaluation Engine - evaluate specific tickers for filing/launch decisions.

Takes a list of tickers and evaluates each across 4 pillars:
  1. Demand Signal (from stock_data)
  2. Competitive Landscape (from etp_data)
  3. Market Feedback (from etp_data AUM/flows)
  4. Filing Status (from pipeline DB)

Returns per-ticker verdicts: RECOMMEND / NEUTRAL / CAUTION
"""
from __future__ import annotations

import logging

import pandas as pd

from screener.config import SCORING_WEIGHTS, INVERTED_FACTORS, DEMAND_THRESHOLDS

log = logging.getLogger(__name__)

UNDERLIER_COL = "q_category_attributes.map_li_underlier"


def evaluate_candidates(
    tickers: list[str],
    stock_df: pd.DataFrame | None = None,
    etp_df: pd.DataFrame | None = None,
) -> list[dict]:
    """Evaluate candidate tickers for ETF filing/launch decision.

    Args:
        tickers: List of ticker symbols (e.g. ["SCCO", "AMPX", "BHP"])
        stock_df: Stock data (if None, loaded from datatest.xlsx)
        etp_df: ETP data (if None, loaded from datatest.xlsx)

    Returns:
        List of evaluation dicts, one per ticker.
    """
    if stock_df is None or etp_df is None:
        from screener.data_loader import load_all
        data = load_all()
        stock_df = stock_df if stock_df is not None else data["stock_data"]
        etp_df = etp_df if etp_df is not None else data["etp_data"]

    # Pre-compute universe percentiles for demand scoring
    percentiles = _compute_universe_percentiles(stock_df)

    # Get filing status from pipeline DB (by ticker + by fund name/underlier)
    db_status = _get_filing_status()
    db_by_underlier = _get_filing_status_by_underlier()

    # Get REX underlier mapping (from etp_data - trading funds only)
    from screener.filing_match import get_rex_underlier_map
    rex_underlier_map = get_rex_underlier_map(etp_df)

    results = []
    for raw_ticker in tickers:
        ticker = raw_ticker.strip().upper()
        log.info("Evaluating candidate: %s", ticker)

        # Normalize: strip " US" suffix for matching
        ticker_clean = ticker.replace(" US", "")
        ticker_bbg = f"{ticker_clean} US"

        # Find in stock_data
        stock_row = _find_stock(stock_df, ticker_clean)

        # Evaluate each pillar
        demand = _evaluate_demand(stock_row, percentiles, ticker_clean)
        competition = _evaluate_competition(etp_df, ticker_bbg)
        market = _evaluate_market_feedback(etp_df, ticker_bbg)
        filing = _evaluate_filing(ticker_clean, rex_underlier_map, db_status, db_by_underlier)

        # Compute overall verdict
        verdict, reason = _compute_verdict(demand, competition, market, filing, ticker_clean)

        results.append({
            "ticker": ticker_bbg,
            "ticker_clean": ticker_clean,
            "company_name": _get_company_name(stock_row),
            "data_coverage": "full" if stock_row is not None else "none",
            "demand": demand,
            "competition": competition,
            "market_feedback": market,
            "filing": filing,
            "verdict": verdict,
            "reason": reason,
        })

    # Summary
    recs = sum(1 for r in results if r["verdict"] == "RECOMMEND")
    log.info("Evaluation complete: %d tickers, %d RECOMMEND, %d NEUTRAL, %d CAUTION",
             len(results), recs,
             sum(1 for r in results if r["verdict"] == "NEUTRAL"),
             sum(1 for r in results if r["verdict"] == "CAUTION"))

    return results


def _find_stock(stock_df: pd.DataFrame, ticker_clean: str) -> pd.Series | None:
    """Find a stock in stock_data by clean ticker."""
    if "ticker_clean" in stock_df.columns:
        match = stock_df[stock_df["ticker_clean"].str.upper() == ticker_clean.upper()]
    else:
        match = stock_df[stock_df["Ticker"].str.replace(" US", "").str.upper() == ticker_clean.upper()]

    if len(match) == 1:
        return match.iloc[0]
    elif len(match) > 1:
        return match.iloc[0]
    return None


def _get_company_name(stock_row: pd.Series | None) -> str:
    """Try to extract company name from stock row."""
    if stock_row is None:
        return ""
    # stock_data doesn't have a name column, return sector as context
    sector = stock_row.get("GICS Sector", "")
    return str(sector) if pd.notna(sector) else ""


def _compute_universe_percentiles(stock_df: pd.DataFrame) -> dict[str, pd.Series]:
    """Pre-compute percentile ranks for all scoring factors."""
    result = {}
    for factor in SCORING_WEIGHTS:
        if factor not in stock_df.columns:
            continue
        values = pd.to_numeric(stock_df[factor], errors="coerce")
        if factor in INVERTED_FACTORS:
            pctl = values.rank(pct=True, ascending=True, na_option="bottom") * 100
        else:
            pctl = values.rank(pct=True, na_option="bottom") * 100
        result[factor] = pctl
    return result


def _evaluate_demand(
    stock_row: pd.Series | None,
    percentiles: dict[str, pd.Series],
    ticker_clean: str,
) -> dict:
    """Pillar 1: Demand Signal from stock_data metrics."""
    if stock_row is None:
        return {
            "verdict": "DATA_UNAVAILABLE",
            "note": "Not in US equity universe",
            "metrics": {},
        }

    metrics = {}
    pctl_values = []

    # Get percentile for this stock's position in the universe
    stock_idx = stock_row.name  # index in the dataframe

    for factor, weight in SCORING_WEIGHTS.items():
        raw_val = stock_row.get(factor)
        if pd.notna(raw_val):
            raw_val = float(raw_val)
        else:
            raw_val = None

        pctl = None
        if factor in percentiles and stock_idx in percentiles[factor].index:
            pctl = float(percentiles[factor].loc[stock_idx])
            pctl_values.append(pctl)

        metrics[factor] = {
            "value": raw_val,
            "percentile": round(pctl, 1) if pctl is not None else None,
        }

    # Also include useful display fields not in scoring
    for col in ["Mkt Cap", "Total OI", "Turnover / Traded Value", "Volatility 30D",
                "Total Call OI", "Total Put OI", "Short Interest Ratio", "GICS Sector"]:
        if col not in metrics and col in stock_row.index:
            val = stock_row.get(col)
            if pd.notna(val):
                metrics.setdefault(col, {"value": float(val) if not isinstance(val, str) else val})

    # Weighted average percentile
    if pctl_values:
        weights_list = list(SCORING_WEIGHTS.values())
        weighted_avg = sum(p * w for p, w in zip(pctl_values, weights_list)) / sum(weights_list[:len(pctl_values)])
    else:
        weighted_avg = 0

    high = DEMAND_THRESHOLDS["high_pctl"]
    medium = DEMAND_THRESHOLDS["medium_pctl"]

    if weighted_avg >= high:
        verdict = "HIGH"
    elif weighted_avg >= medium:
        verdict = "MEDIUM"
    else:
        verdict = "LOW"

    return {
        "verdict": verdict,
        "weighted_pctl": round(weighted_avg, 1),
        "metrics": metrics,
    }


def _evaluate_competition(etp_df: pd.DataFrame, underlier_bbg: str) -> dict:
    """Pillar 2: Competitive Landscape from etp_data."""
    from screener.competitive import compute_competitive_density

    # Get all leveraged products for this underlier
    density = compute_competitive_density(etp_df)

    match = density[density["underlier"] == underlier_bbg]
    if match.empty:
        return {
            "verdict": "FIRST_MOVER",
            "product_count": 0,
            "competitor_count": 0,
            "rex_count": 0,
            "total_aum": 0,
            "competitor_aum": 0,
            "rex_aum": 0,
            "leader": None,
            "leader_share": 0,
            "leader_is_rex": False,
        }

    row = match.iloc[0]
    comp_count = int(row.get("competitor_product_count", 0))
    rex_count = int(row.get("rex_product_count", 0))

    # Verdict based on competitor count (REX products don't count against us)
    if comp_count == 0:
        verdict = "FIRST_MOVER"
    elif comp_count <= 2 and row.get("competitor_aum", 0) < 500:
        verdict = "EARLY_STAGE"
    elif comp_count <= 4:
        verdict = "COMPETITIVE"
    else:
        verdict = "CROWDED"

    return {
        "verdict": verdict,
        "product_count": int(row.get("product_count", 0)),
        "competitor_count": comp_count,
        "rex_count": rex_count,
        "total_aum": float(row.get("total_aum", 0)),
        "competitor_aum": float(row.get("competitor_aum", 0)),
        "rex_aum": float(row.get("rex_aum", 0)),
        "leader": row.get("leader_ticker", ""),
        "leader_share": float(row.get("leader_share", 0)),
        "leader_is_rex": bool(row.get("leader_is_rex", False)),
    }


def _evaluate_market_feedback(etp_df: pd.DataFrame, underlier_bbg: str) -> dict:
    """Pillar 3: Market Feedback from existing product performance."""
    from screener.competitive import compute_market_feedback
    return compute_market_feedback(etp_df, underlier_bbg)


def _evaluate_filing(
    ticker_clean: str,
    rex_underlier_map: dict[str, str],
    db_status: dict[str, dict],
    db_by_underlier: dict[str, list[dict]] | None = None,
) -> dict:
    """Pillar 4: Filing Status from pipeline DB.

    Checks two paths:
    1. etp_data underlier map -> DB by ticker (for trading funds)
    2. DB by fund name pattern (for PENDING funds with no ticker)
    """
    fund_info = None
    rex_ticker = rex_underlier_map.get(ticker_clean.upper())

    # Path 1: etp_data -> DB by ticker
    if rex_ticker:
        rex_clean = rex_ticker.replace(" US", "").upper()
        fund_info = db_status.get(rex_clean) or db_status.get(rex_ticker.upper())

    # Path 2: DB by underlier name pattern (catches PENDING funds with ticker=None)
    if not fund_info and db_by_underlier:
        entries = db_by_underlier.get(ticker_clean.upper(), [])
        if entries:
            fund_info = entries[0]

    if not fund_info and not rex_ticker:
        return {
            "verdict": "NOT_FILED",
            "rex_ticker": None,
            "status": None,
            "effective_date": None,
            "latest_form": None,
        }

    if not fund_info:
        return {
            "verdict": "FILED",
            "rex_ticker": rex_ticker,
            "status": "UNKNOWN",
            "effective_date": None,
            "latest_form": None,
        }

    status = fund_info.get("status", "UNKNOWN")
    if status == "EFFECTIVE":
        verdict = "ALREADY_TRADING"
    elif status in ("PENDING", "DELAYED"):
        verdict = "FILED"
    else:
        verdict = "FILED"

    return {
        "verdict": verdict,
        "rex_ticker": rex_ticker or fund_info.get("ticker"),
        "status": status,
        "effective_date": fund_info.get("effective_date"),
        "latest_form": fund_info.get("latest_form"),
        "fund_name": fund_info.get("fund_name"),
    }


def _get_filing_status() -> dict[str, dict]:
    """Get filing status from pipeline DB, with graceful failure."""
    try:
        from screener.filing_match import get_filing_status_from_db
        return get_filing_status_from_db()
    except Exception as e:
        log.warning("Could not query filing DB: %s", e)
        return {}


def _get_filing_status_by_underlier() -> dict[str, list[dict]]:
    """Get filing status by underlier from DB (catches PENDING funds)."""
    try:
        from screener.filing_match import get_filing_status_by_underlier
        return get_filing_status_by_underlier()
    except Exception as e:
        log.warning("Could not query filing DB by underlier: %s", e)
        return {}


def _compute_verdict(
    demand: dict,
    competition: dict,
    market: dict,
    filing: dict,
    ticker: str,
) -> tuple[str, str]:
    """Compute overall verdict and one-line recommendation."""
    d_verdict = demand["verdict"]
    c_verdict = competition["verdict"]
    m_verdict = market["verdict"]
    f_verdict = filing["verdict"]

    # Already trading - not a filing candidate
    if f_verdict == "ALREADY_TRADING":
        return "NEUTRAL", f"Already trading as {filing.get('rex_ticker', '?')}. Monitor performance."

    # RECOMMEND: good demand + low competition + not rejected by market
    if d_verdict in ("HIGH", "MEDIUM", "DATA_UNAVAILABLE") and \
       c_verdict in ("FIRST_MOVER", "EARLY_STAGE") and \
       m_verdict != "REJECTED":
        filed_note = ""
        if f_verdict == "FILED":
            status = filing.get("status", "")
            filed_note = f" Filed ({status})." if status else " Filed."
        if c_verdict == "FIRST_MOVER":
            if d_verdict == "DATA_UNAVAILABLE":
                reason = f"Uncontested underlier.{filed_note} No US equity data - add to market data pull."
            elif d_verdict == "HIGH":
                reason = f"Strong demand, no competitors.{filed_note} First-mover opportunity."
            else:
                reason = f"Moderate demand, no competitors.{filed_note} First-mover opportunity."
        else:
            reason = f"Early stage competition with viable demand.{filed_note}"
        return "RECOMMEND", reason

    # CAUTION: crowded, rejected, or low demand
    if c_verdict == "CROWDED":
        comp_count = competition.get("competitor_count", 0)
        return "CAUTION", f"Crowded market ({comp_count} competitors). Late entry risks low market share."

    if m_verdict == "REJECTED":
        total_aum = market.get("total_aum", 0)
        return "CAUTION", f"Existing products have only ${total_aum:.0f}M AUM. Market has not validated demand."

    if d_verdict == "LOW":
        return "CAUTION", "Low demand signal (options OI, turnover, market cap below median)."

    # NEUTRAL: everything else
    if f_verdict == "FILED":
        return "NEUTRAL", f"Already filed ({filing.get('status', '?')}). Awaiting effectiveness."

    if d_verdict == "DATA_UNAVAILABLE":
        if c_verdict in ("COMPETITIVE", "CROWDED"):
            return "CAUTION", f"No US equity data + {c_verdict.lower()} market. Needs manual review."
        return "NEUTRAL", "No US equity data available. Cannot fully assess demand."

    return "NEUTRAL", "Mixed signals across evaluation pillars."

"""Cross-reference screener candidates with SEC filing data from pipeline DB.

Instead of parsing fund names from a CSV, this queries the FundStatus table
populated by the ETP filing tracker pipeline.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def get_filing_status_from_db() -> dict[str, dict]:
    """Query FundStatus from pipeline DB for all REX trusts.

    Returns a dict mapping fund ticker -> filing info.
    """
    try:
        from webapp.database import SessionLocal
        from webapp.models import FundStatus, Trust
    except ImportError:
        log.warning("Cannot import webapp models - filing match unavailable")
        return {}

    db = SessionLocal()
    try:
        rex_funds = (
            db.query(FundStatus)
            .join(Trust)
            .filter(Trust.is_rex == True)
            .all()
        )

        result = {}
        for f in rex_funds:
            if f.ticker:
                result[f.ticker.upper()] = {
                    "status": f.status,
                    "effective_date": f.effective_date,
                    "latest_form": f.latest_form,
                    "fund_name": f.fund_name,
                    "latest_filing_date": f.latest_filing_date,
                }

        log.info("Filing DB query: %d REX funds with tickers", len(result))
        return result
    except Exception as e:
        log.warning("Failed to query filing DB: %s", e)
        return {}
    finally:
        db.close()


def get_rex_underlier_map(etp_df: pd.DataFrame) -> dict[str, str]:
    """Build underlier_clean -> REX fund ticker mapping from etp_data.

    For each REX single-stock leveraged fund, maps the underlier (e.g. "NVDA")
    to the REX fund ticker (e.g. "NVDX US").
    """
    subcat_col = "q_category_attributes.map_li_subcategory"
    underlier_col = "q_category_attributes.map_li_underlier"

    rex_li = etp_df[
        (etp_df.get("is_rex") == True)
        & (etp_df.get("uses_leverage") == True)
        & (etp_df.get(subcat_col) == "Single Stock")
        & (etp_df[underlier_col].notna())
    ]

    underlier_to_rex = {}
    for _, row in rex_li.iterrows():
        underlier = row.get("underlier_clean", "")
        if underlier:
            underlier_to_rex[underlier.upper()] = row["ticker"]

    return underlier_to_rex


def match_filings(
    candidates_df: pd.DataFrame,
    etp_df: pd.DataFrame,
) -> pd.DataFrame:
    """Cross-reference candidate stocks with REX filing data.

    Uses two data sources:
    1. etp_data (is_rex=True) to map underliers to REX fund tickers
    2. Pipeline DB (FundStatus) to get filing status for those tickers

    Adds 'filing_status' column to candidates:
      - "REX Filed - Effective"
      - "REX Filed - Pending"
      - "REX Filed - [status]"
      - "Not Filed"
    """
    df = candidates_df.copy()
    df["filing_status"] = "Not Filed"

    # Get REX underlier mapping from etp_data
    underlier_to_rex = get_rex_underlier_map(etp_df)
    log.info("REX underlier map: %d entries", len(underlier_to_rex))

    # Get filing status from pipeline DB
    db_status = get_filing_status_from_db()

    # Match candidates by ticker_clean -> underlier -> REX fund ticker -> DB status
    ticker_col = "ticker_clean" if "ticker_clean" in df.columns else "Ticker"

    for idx, row in df.iterrows():
        candidate_ticker = str(row.get(ticker_col, "")).upper()
        if not candidate_ticker:
            continue

        # Check if this stock is a REX underlier
        rex_ticker = underlier_to_rex.get(candidate_ticker)
        if not rex_ticker:
            continue

        # Look up filing status for the REX fund
        rex_ticker_clean = rex_ticker.replace(" US", "").upper()
        fund_info = db_status.get(rex_ticker_clean) or db_status.get(rex_ticker.upper())

        if fund_info:
            status = fund_info.get("status", "UNKNOWN")
            if status == "EFFECTIVE":
                df.at[idx, "filing_status"] = "REX Filed - Effective"
            elif status == "PENDING":
                eff_date = fund_info.get("effective_date")
                if eff_date:
                    df.at[idx, "filing_status"] = f"REX Filed - Pending ({eff_date})"
                else:
                    df.at[idx, "filing_status"] = "REX Filed - Pending"
            elif status == "DELAYED":
                df.at[idx, "filing_status"] = "REX Filed - Delayed"
            else:
                df.at[idx, "filing_status"] = f"REX Filed - {status}"
        else:
            # We know REX has a fund for this underlier (from etp_data) but no DB status
            df.at[idx, "filing_status"] = "REX Filed"

    # Stats
    status_counts = df["filing_status"].value_counts()
    log.info("Filing match results: %s", status_counts.to_dict())

    return df

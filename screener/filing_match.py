"""Cross-reference screener candidates with SEC filing data from pipeline DB.

Two data sources:
  1. Pipeline DB (FundStatus) - includes PENDING funds with no ticker
  2. etp_data (is_rex=True) - ETP universe for trading funds

The DB has funds with ticker=None (485APOS initial filings) where the underlier
must be extracted from the fund name: "T-REX 2X LONG SCCO DAILY TARGET ETF" -> "SCCO"
"""
from __future__ import annotations

import logging
import re

import pandas as pd

log = logging.getLogger(__name__)

# Pattern to extract underlier from T-REX fund names:
# "T-REX 2X LONG SCCO DAILY TARGET ETF" -> "SCCO"
# "T-REX 2X INVERSE NVDA DAILY TARGET ETF" -> "NVDA"
_TREX_NAME_RE = re.compile(
    r"T-REX\s+\d+X\s+(?:LONG|INVERSE|SHORT)\s+(\S+)\s+DAILY",
    re.IGNORECASE,
)


def _extract_underlier_from_name(fund_name: str) -> str | None:
    """Extract underlier ticker from a T-REX fund name."""
    m = _TREX_NAME_RE.search(fund_name or "")
    return m.group(1).upper() if m else None


def get_filing_status_from_db() -> dict[str, dict]:
    """Query FundStatus from pipeline DB for all REX trusts.

    Returns a dict mapping fund ticker (uppercase) -> filing info.
    Includes ticker-less funds indexed by extracted underlier.
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
            info = {
                "status": f.status,
                "effective_date": f.effective_date,
                "latest_form": f.latest_form,
                "fund_name": f.fund_name,
                "latest_filing_date": f.latest_filing_date,
                "ticker": f.ticker,
            }
            if f.ticker:
                result[f.ticker.upper()] = info

        log.info("Filing DB query: %d REX funds with tickers", len(result))
        return result
    except Exception as e:
        log.warning("Failed to query filing DB: %s", e)
        return {}
    finally:
        db.close()


def get_filing_status_by_underlier() -> dict[str, dict]:
    """Query FundStatus and build a map of underlier -> filing info.

    This catches PENDING funds that have no ticker assigned yet by
    extracting the underlier from the fund name.
    """
    try:
        from webapp.database import SessionLocal
        from webapp.models import FundStatus, Trust
    except ImportError:
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
            info = {
                "status": f.status,
                "effective_date": f.effective_date,
                "latest_form": f.latest_form,
                "fund_name": f.fund_name,
                "latest_filing_date": f.latest_filing_date,
                "ticker": f.ticker,
            }

            # Index by ticker if available
            if f.ticker:
                ticker_clean = f.ticker.replace(" US", "").upper()
                result.setdefault(ticker_clean, []).append(info)

            # Also extract underlier from fund name (catches ticker-less funds)
            underlier = _extract_underlier_from_name(f.fund_name)
            if underlier:
                result.setdefault(underlier, []).append(info)

        log.info("Filing DB underlier map: %d keys", len(result))
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


def get_launched_underliers(etp_df: pd.DataFrame) -> set[str]:
    """Return set of underlier tickers where REX has EFFECTIVE (trading) products.

    These should be excluded from "opportunity" rankings since they're already launched.
    """
    underlier_map = get_rex_underlier_map(etp_df)

    # Also check DB for EFFECTIVE funds
    db_by_underlier = get_filing_status_by_underlier()

    launched = set()

    # From etp_data: any underlier with a REX fund means it's trading
    for underlier in underlier_map:
        launched.add(underlier.upper())

    # From DB: EFFECTIVE status
    for key, entries in db_by_underlier.items():
        for entry in entries:
            if entry["status"] == "EFFECTIVE":
                launched.add(key.upper())

    return launched


def match_filings(
    candidates_df: pd.DataFrame,
    etp_df: pd.DataFrame,
) -> pd.DataFrame:
    """Cross-reference candidate stocks with REX filing data.

    Uses three data sources:
    1. etp_data (is_rex=True) to map underliers to REX fund tickers
    2. Pipeline DB by ticker (FundStatus.ticker)
    3. Pipeline DB by fund name pattern (for PENDING funds without tickers)

    Adds 'filing_status' column to candidates.
    """
    df = candidates_df.copy()
    df["filing_status"] = "Not Filed"

    # Get REX underlier mapping from etp_data (trading funds)
    underlier_to_rex = get_rex_underlier_map(etp_df)
    log.info("REX underlier map: %d entries", len(underlier_to_rex))

    # Get filing status from pipeline DB (by ticker)
    db_status = get_filing_status_from_db()

    # Get filing status by underlier (catches PENDING funds with no ticker)
    db_by_underlier = get_filing_status_by_underlier()

    ticker_col = "ticker_clean" if "ticker_clean" in df.columns else "Ticker"

    for idx, row in df.iterrows():
        candidate_ticker = str(row.get(ticker_col, "")).upper()
        if not candidate_ticker:
            continue

        fund_info = None
        source = None

        # Path 1: Check etp_data underlier map -> DB ticker lookup
        rex_ticker = underlier_to_rex.get(candidate_ticker)
        if rex_ticker:
            rex_ticker_clean = rex_ticker.replace(" US", "").upper()
            fund_info = db_status.get(rex_ticker_clean) or db_status.get(rex_ticker.upper())
            source = "etp+db"

        # Path 2: Check DB by underlier (catches PENDING funds with no ticker)
        if not fund_info and candidate_ticker in db_by_underlier:
            entries = db_by_underlier[candidate_ticker]
            if entries:
                # Pick the most recent / most relevant entry
                fund_info = entries[0]
                source = "db_name"

        if fund_info:
            from market.definitions import canonical_status
            status = canonical_status(fund_info.get("status", ""))  # never Pending/Delayed
            eff_date = fund_info.get("effective_date")
            if status == "Effective":
                df.at[idx, "filing_status"] = "REX Filed - Effective"
            elif eff_date:
                df.at[idx, "filing_status"] = f"REX Filed ({eff_date})"
            else:
                df.at[idx, "filing_status"] = "REX Filed"
        elif rex_ticker:
            # etp_data has a REX fund but no DB entry
            df.at[idx, "filing_status"] = "REX Filed"

    status_counts = df["filing_status"].value_counts()
    log.info("Filing match results: %s", status_counts.to_dict())

    return df

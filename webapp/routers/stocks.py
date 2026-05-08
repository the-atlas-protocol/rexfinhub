"""Stock detail surface — Bloomberg DES-style data dump.

URL design:
    /stocks/{ticker}        -> detail page (canonical at root)
    /market/stocks/         -> browse-all index (lives in Market pillar)

PR 2c of the v3 architecture migration. See
docs/website_FINAL_PLAN_2026-05-08.md Section 3 PR 2c.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

router = APIRouter(tags=["stocks"])
templates = Jinja2Templates(directory="webapp/templates")

PARQUET_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "analysis"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_parquet(name: str) -> pd.DataFrame:
    """Best-effort parquet load. Returns empty DataFrame on any failure."""
    p = PARQUET_DIR / name
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _safe_float(v: Any) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _safe_str(v: Any) -> str:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v)
    except Exception:
        return ""


def _row_to_dict(row: pd.Series) -> dict[str, Any]:
    """Convert a parquet row to a JSON-safe dict (NaN -> None)."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, float) and pd.isna(v):
            out[k] = None
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/stocks/{ticker}")
def stock_detail(request: Request, ticker: str, db: Session = Depends(get_db)):
    """Per-stock data dump — Bloomberg DES style.

    Combines:
      - Bloomberg signals (whitespace_v4 / launch_candidates parquet)
      - ETP coverage (mkt_master_data join on map_li_underlier / map_cc_underlier)
      - Filing whitespace (filed_underliers parquet)
    """
    ticker_upper = (ticker or "").upper().strip()

    whitespace = _load_parquet("whitespace_v4.parquet")
    launches = _load_parquet("launch_candidates.parquet")
    filed = _load_parquet("filed_underliers.parquet")

    # --- Signal lookup (whitespace first, fall back to launch_candidates) ---
    signal: dict[str, Any] | None = None
    source: str | None = None
    if not whitespace.empty and ticker_upper in whitespace.index:
        signal = _row_to_dict(whitespace.loc[ticker_upper])
        source = "whitespace"
    elif not launches.empty and ticker_upper in launches.index:
        signal = _row_to_dict(launches.loc[ticker_upper])
        source = "launches"

    # --- ETP coverage from mkt_master_data ---
    etp_rows = db.execute(sa_text(
        """
        SELECT ticker, fund_name, issuer_display, aum,
               map_li_underlier, map_li_direction, map_li_leverage_amount,
               map_cc_underlier
        FROM mkt_master_data
        WHERE UPPER(TRIM(map_li_underlier)) = :t
           OR UPPER(TRIM(map_cc_underlier)) = :t
        ORDER BY aum DESC NULLS LAST
        LIMIT 100
        """
    ), {"t": ticker_upper}).fetchall()

    etps: list[dict[str, Any]] = []
    for row in etp_rows:
        is_li = bool(row.map_li_underlier and str(row.map_li_underlier).strip())
        etps.append({
            "ticker": row.ticker or "",
            "ticker_clean": (row.ticker or "").replace(" US", "").strip(),
            "fund_name": row.fund_name or "",
            "issuer": row.issuer_display or "",
            "aum": _safe_float(row.aum),
            "leverage": row.map_li_leverage_amount or "",
            "direction": row.map_li_direction or "",
            "is_li": is_li,
            "coverage_type": "L&I" if is_li else "Covered Call",
        })

    # --- Filing whitespace (485APOS filings naming this underlier) ---
    filing_data: dict[str, Any] | None = None
    if not filed.empty and ticker_upper in filed.index:
        filing_data = _row_to_dict(filed.loc[ticker_upper])

    # --- Header derived fields ---
    header_name = ""
    header_sector = ""
    header_exchange = ""
    header_market_cap = 0.0
    if signal:
        header_name = _safe_str(signal.get("name") or signal.get("rex_fund_name"))
        header_sector = _safe_str(signal.get("sector"))
        header_exchange = _safe_str(signal.get("exchange"))
        header_market_cap = _safe_float(signal.get("market_cap"))

    has_data = signal is not None or len(etps) > 0 or filing_data is not None

    return templates.TemplateResponse("stocks/detail.html", {
        "request": request,
        "ticker": ticker_upper,
        "header_name": header_name,
        "header_sector": header_sector,
        "header_exchange": header_exchange,
        "header_market_cap": header_market_cap,
        "signal": signal,
        "signal_source": source,
        "etps": etps,
        "etp_count": len(etps),
        "filing_data": filing_data,
        "has_data": has_data,
    })


@router.get("/market/stocks/")
def stocks_index(
    request: Request,
    db: Session = Depends(get_db),
    q: str = Query(default=""),
    sector: str = Query(default=""),
    has_etp: str = Query(default=""),
):
    """Browse-all stocks page (lives in Market pillar)."""
    whitespace = _load_parquet("whitespace_v4.parquet")
    launches = _load_parquet("launch_candidates.parquet")

    all_stocks: dict[str, dict[str, Any]] = {}

    if not whitespace.empty:
        for tk, row in whitespace.iterrows():
            tk_str = str(tk).strip().upper()
            if not tk_str:
                continue
            all_stocks[tk_str] = {
                "ticker": tk_str,
                "name": _safe_str(row.get("name")),
                "sector": _safe_str(row.get("sector")),
                "market_cap": _safe_float(row.get("market_cap")),
                "composite_score": _safe_float(row.get("composite_score")),
                "score_pct": _safe_float(row.get("score_pct")),
                "themes": _safe_str(row.get("themes")),
                "mentions_24h": _safe_float(row.get("mentions_24h")),
                "has_signal": True,
                "signal_source": "whitespace",
            }

    if not launches.empty:
        for tk, row in launches.iterrows():
            tk_str = str(tk).strip().upper()
            if not tk_str or tk_str in all_stocks:
                continue
            all_stocks[tk_str] = {
                "ticker": tk_str,
                "name": _safe_str(row.get("rex_fund_name")),
                "sector": _safe_str(row.get("sector")),
                "market_cap": _safe_float(row.get("market_cap")),
                "composite_score": _safe_float(row.get("composite_score")),
                "score_pct": _safe_float(row.get("score_pct")),
                "themes": _safe_str(row.get("themes")),
                "mentions_24h": _safe_float(row.get("mentions_24h")),
                "has_signal": True,
                "signal_source": "launches",
            }

    # Pull every map_li / map_cc underlier from mkt_master_data so stocks
    # with ETP coverage but no signal still appear in the index.
    underlier_rows = db.execute(sa_text(
        """
        SELECT UPPER(TRIM(map_li_underlier)) AS u, COUNT(*) AS n
        FROM mkt_master_data
        WHERE map_li_underlier IS NOT NULL AND TRIM(map_li_underlier) != ''
        GROUP BY UPPER(TRIM(map_li_underlier))
        UNION ALL
        SELECT UPPER(TRIM(map_cc_underlier)) AS u, COUNT(*) AS n
        FROM mkt_master_data
        WHERE map_cc_underlier IS NOT NULL AND TRIM(map_cc_underlier) != ''
        GROUP BY UPPER(TRIM(map_cc_underlier))
        """
    )).fetchall()

    etp_counts: dict[str, int] = {}
    for r in underlier_rows:
        if not r.u:
            continue
        etp_counts[r.u] = etp_counts.get(r.u, 0) + int(r.n or 0)

    # Add stocks present only in mkt_master_data (no parquet signal)
    for tk_str, n in etp_counts.items():
        if tk_str not in all_stocks:
            all_stocks[tk_str] = {
                "ticker": tk_str,
                "name": "",
                "sector": "",
                "market_cap": 0.0,
                "composite_score": 0.0,
                "score_pct": 0.0,
                "themes": "",
                "mentions_24h": 0.0,
                "has_signal": False,
                "signal_source": None,
            }

    # Annotate every row with its ETP count
    for tk_str, rec in all_stocks.items():
        rec["n_etps"] = etp_counts.get(tk_str, 0)

    # --- Filters ---
    stocks = list(all_stocks.values())
    if q:
        ql = q.lower().strip()
        stocks = [
            s for s in stocks
            if ql in s["ticker"].lower() or ql in (s["name"] or "").lower()
        ]
    if sector:
        stocks = [s for s in stocks if s["sector"] == sector]
    if has_etp == "yes":
        stocks = [s for s in stocks if s.get("n_etps", 0) > 0]
    elif has_etp == "no":
        stocks = [s for s in stocks if s.get("n_etps", 0) == 0]

    # Sort: composite_score desc (tickers without signals fall to the bottom)
    stocks.sort(key=lambda s: (s.get("composite_score", 0.0), s.get("n_etps", 0)), reverse=True)

    sectors = sorted({s["sector"] for s in all_stocks.values() if s["sector"]})

    return templates.TemplateResponse("stocks/index.html", {
        "request": request,
        "stocks": stocks[:500],  # cap rendering for browser perf
        "total_count": len(all_stocks),
        "filtered_count": len(stocks),
        "render_count": min(len(stocks), 500),
        "sectors": sectors,
        "q": q,
        "sector": sector,
        "has_etp": has_etp,
    })

"""Strategy router — L&I Whitespace Engine web views.

Routes:
    /strategy                 -> overview dashboard
    /strategy/whitespace      -> ranked whitespace candidate table
    /strategy/race            -> filing race clock (competitor 485APOS cadence)
    /strategy/ticker/{t}      -> per-ticker deep-dive card

All pages read from the parquet artifacts produced by
screener.li_engine.analysis.* modules. No DB writes.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

_ROOT = Path(__file__).resolve().parent.parent.parent
WS_PARQUET = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"
WS_V1_PARQUET = _ROOT / "data" / "analysis" / "whitespace_candidates.parquet"
RACE_PARQUET = _ROOT / "data" / "analysis" / "filing_race.parquet"
CADENCE_PARQUET = _ROOT / "data" / "analysis" / "issuer_cadence.parquet"


def _load_whitespace() -> pd.DataFrame:
    """Prefer v4; fall back to v1 if v4 not built yet."""
    if WS_PARQUET.exists():
        return pd.read_parquet(WS_PARQUET)
    if WS_V1_PARQUET.exists():
        return pd.read_parquet(WS_V1_PARQUET)
    return pd.DataFrame()


def _fmt_mcap(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    if v >= 100_000:
        return f"${v/1000:.0f}B"
    if v >= 1000:
        return f"${v/1000:.1f}B"
    return f"${v:,.0f}M"


def _fmt_pct(v, places=0) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.{places}f}%"


def _fmt_num(v, places=0) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{places}f}"


@router.get("/strategy", response_class=HTMLResponse)
def strategy_home(request: Request):
    ws = _load_whitespace()

    if ws.empty:
        return templates.TemplateResponse("strategy/empty.html", {
            "request": request,
            "message": "No whitespace parquet yet. Run /li-report or whitespace_v4 first.",
        })

    # KPIs
    n_universe = len(ws)
    n_thematic = int((ws.get("is_thematic", 0) == 1).sum()) if "is_thematic" in ws.columns else 0
    n_high_mentions = int((ws.get("mentions_24h", 0) >= 10).sum()) if "mentions_24h" in ws.columns else 0

    # Age of data
    file_mtime = WS_PARQUET.stat().st_mtime if WS_PARQUET.exists() else WS_V1_PARQUET.stat().st_mtime
    data_age_hours = (datetime.now().timestamp() - file_mtime) / 3600

    # Top 10 for preview
    top10 = ws.sort_values("composite_score", ascending=False).head(10)
    top10_rows = []
    for ticker in top10.index:
        r = top10.loc[ticker]
        top10_rows.append({
            "ticker": ticker,
            "sector": r.get("sector") or "—",
            "mcap": _fmt_mcap(r.get("market_cap")),
            "rvol": _fmt_num(r.get("rvol_90d")),
            "ret_1m": _fmt_pct(r.get("ret_1m")),
            "ret_1y": _fmt_pct(r.get("ret_1y")),
            "mentions": int(r.get("mentions_24h", 0) or 0),
            "themes": r.get("themes", "") or "",
            "score": f"{r.get('composite_score', 0):+.2f}",
            "is_thematic": bool(r.get("is_thematic", 0)),
        })

    return templates.TemplateResponse("strategy/home.html", {
        "request": request,
        "n_universe": n_universe,
        "n_thematic": n_thematic,
        "n_high_mentions": n_high_mentions,
        "data_age_hours": f"{data_age_hours:.1f}",
        "data_source": "whitespace_v4" if WS_PARQUET.exists() else "whitespace_v1",
        "top10": top10_rows,
    })


@router.get("/strategy/whitespace", response_class=HTMLResponse)
def strategy_whitespace(
    request: Request,
    sector: str | None = Query(None),
    min_mcap: float | None = Query(None, description="Min market cap in $M"),
    require_mentions: bool = Query(False),
    require_theme: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
):
    ws = _load_whitespace()
    if ws.empty:
        return templates.TemplateResponse("strategy/empty.html", {
            "request": request,
            "message": "No whitespace data. Run /li-report first.",
        })

    # Filters
    filtered = ws.copy()
    if sector:
        filtered = filtered[filtered["sector"] == sector]
    if min_mcap is not None:
        filtered = filtered[filtered["market_cap"] >= min_mcap]
    if require_mentions:
        filtered = filtered[filtered.get("mentions_24h", 0) > 0]
    if require_theme and "is_thematic" in filtered.columns:
        filtered = filtered[filtered["is_thematic"] == 1]

    filtered = filtered.sort_values("composite_score", ascending=False)
    total = len(filtered)
    start = (page - 1) * per_page
    end = start + per_page
    page_rows = filtered.iloc[start:end]

    rows = []
    for ticker in page_rows.index:
        r = page_rows.loc[ticker]
        rows.append({
            "ticker": ticker,
            "sector": r.get("sector") or "—",
            "mcap": _fmt_mcap(r.get("market_cap")),
            "mcap_raw": r.get("market_cap") or 0,
            "rvol_90d": _fmt_num(r.get("rvol_90d")),
            "ret_1m": _fmt_pct(r.get("ret_1m")),
            "ret_1y": _fmt_pct(r.get("ret_1y")),
            "total_oi": _fmt_num(r.get("total_oi")),
            "si_ratio": f"{r.get('si_ratio', 0) or 0:.1f}",
            "inst_own": f"{r.get('inst_own_pct', 0) or 0:.0f}%",
            "mentions": int(r.get("mentions_24h", 0) or 0),
            "themes": r.get("themes", "") or "",
            "is_thematic": bool(r.get("is_thematic", 0)),
            "score": f"{r.get('composite_score', 0):+.2f}",
            "score_pct": int(r.get("score_pct", 0) or 0),
        })

    sectors = sorted({s for s in ws.get("sector", []).dropna() if s})
    n_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse("strategy/whitespace.html", {
        "request": request,
        "rows": rows,
        "total": total,
        "page": page,
        "n_pages": n_pages,
        "per_page": per_page,
        "sectors": sectors,
        "current_sector": sector,
        "current_min_mcap": min_mcap,
        "require_mentions": require_mentions,
        "require_theme": require_theme,
    })


@router.get("/strategy/ticker/{ticker}", response_class=HTMLResponse)
def strategy_ticker(request: Request, ticker: str):
    """Per-ticker deep-dive. Uses ticker_analyze on demand."""
    ticker = ticker.upper().strip()
    try:
        from screener.li_engine.analysis.ticker_analyze import rank_against_universe
        data = rank_against_universe([ticker])
    except Exception as e:
        log.exception("ticker_analyze failed")
        return templates.TemplateResponse("strategy/empty.html", {
            "request": request,
            "message": f"Error analyzing {ticker}: {e}",
        })

    info = data.get(ticker, {})
    if "error" in info:
        return templates.TemplateResponse("strategy/ticker.html", {
            "request": request,
            "ticker": ticker,
            "error": info["error"],
            "info": None,
        })

    # Format for template
    signals = info.get("signals") or {}
    coverage = info.get("product_coverage") or {}
    score = info.get("score") or {}
    display = {
        "ticker": ticker,
        "sector": signals.get("sector") or "—",
        "market_cap": _fmt_mcap(signals.get("market_cap")),
        "last_price": _fmt_num(signals.get("last_price"), 2),
        "pct_of_52w_high": f"{(info.get('pct_of_52w_high') or 0):.0%}" if info.get("pct_of_52w_high") else "—",
        "ret_1m": _fmt_pct(signals.get("ret_1m")),
        "ret_3m": _fmt_pct(signals.get("ret_3m")),
        "ret_6m": _fmt_pct(signals.get("ret_6m")),
        "ret_1y": _fmt_pct(signals.get("ret_1y")),
        "rvol_30d": _fmt_num(signals.get("rvol_30d")),
        "rvol_90d": _fmt_num(signals.get("rvol_90d")),
        "total_oi": _fmt_num(signals.get("total_oi")),
        "call_oi": _fmt_num(signals.get("call_oi")),
        "put_oi": _fmt_num(signals.get("put_oi")),
        "si_ratio": _fmt_num(signals.get("si_ratio"), 2),
        "insider_pct": f"{signals.get('insider_pct') or 0:.1f}%" if signals.get("insider_pct") else "—",
        "inst_own_pct": f"{signals.get('inst_own_pct') or 0:.0f}%" if signals.get("inst_own_pct") else "—",
        "themes": info.get("themes") or [],
        "mentions_24h": info.get("mentions_24h") or 0,
        "news_sentiment": _fmt_num(signals.get("news_sentiment_bbg"), 2),
    }

    return templates.TemplateResponse("strategy/ticker.html", {
        "request": request,
        "ticker": ticker,
        "info": display,
        "coverage": coverage,
        "score": score,
        "error": None,
    })


@router.get("/strategy/race", response_class=HTMLResponse)
def strategy_race(request: Request):
    cadence = None
    if CADENCE_PARQUET.exists():
        cadence = pd.read_parquet(CADENCE_PARQUET).reset_index().rename(columns={"index": "issuer"})

    race = None
    if RACE_PARQUET.exists():
        race = pd.read_parquet(RACE_PARQUET)

    cadence_rows = []
    if cadence is not None and not cadence.empty:
        for _, r in cadence.iterrows():
            cadence_rows.append({
                "issuer": r.get("issuer"),
                "is_rex": bool(r.get("is_rex")),
                "n_launches": int(r.get("n_launches", 0)),
                "median_days": int(r.get("median_days", 0)),
                "min_days": int(r.get("min_days", 0)),
                "max_days": int(r.get("max_days", 0)),
            })

    race_rows = []
    if race is not None and not race.empty:
        today = pd.Timestamp.today()
        upcoming = race[race["days_until_launch"] > 0].copy() if "days_until_launch" in race.columns else race.head(0)
        for _, r in upcoming.head(30).iterrows():
            urgency = "urgent" if r.get("days_until_launch", 0) < 30 else "normal"
            race_rows.append({
                "filing_date": str(r["filing_date"].date()) if pd.notna(r.get("filing_date")) else "—",
                "underlier": r.get("underlier", "—"),
                "registrant": (r.get("registrant") or "—")[:40],
                "projected_launch": str(r["projected_launch"].date()) if pd.notna(r.get("projected_launch")) else "—",
                "days_until_launch": int(r.get("days_until_launch", 0)),
                "rex_has_reacted": bool(r.get("rex_has_reacted", False)),
                "urgency": urgency,
            })

    return templates.TemplateResponse("strategy/race.html", {
        "request": request,
        "cadence": cadence_rows,
        "race": race_rows,
        "race_available": race is not None and not race.empty,
    })

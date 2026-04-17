"""
Dashboard router - Home page, API endpoints, and redirect for /dashboard.

The dashboard handler has moved to webapp.routers.filings (GET /filings/dashboard).
This module retains the home page, API routes, and a 301 redirect.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus, Filing

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")


# ---------------------------------------------------------------------------
# Home page — morning brief
# ---------------------------------------------------------------------------

def _generate_morning_brief(db: Session) -> str:
    """Generate a rule-based intelligence brief for the home page."""
    parts = []
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Count today's filings
    try:
        recent_count = db.execute(
            select(func.count(Filing.id)).where(Filing.filing_date == today)
        ).scalar() or 0

        if recent_count > 0:
            parts.append(
                f"{recent_count} filing{'s' if recent_count != 1 else ''} recorded today."
            )
        else:
            # Check yesterday if today is empty
            yest_count = db.execute(
                select(func.count(Filing.id)).where(Filing.filing_date == yesterday)
            ).scalar() or 0
            if yest_count > 0:
                parts.append(
                    f"{yest_count} filing{'s' if yest_count != 1 else ''} recorded yesterday."
                )
    except Exception:
        pass

    # Count active trusts
    try:
        trust_count = db.execute(
            select(func.count(Trust.id)).where(Trust.is_active == True)
        ).scalar() or 0
        if trust_count > 0 and not parts:
            parts.append(f"SEC filing monitor active across {trust_count:,} trusts.")
    except Exception:
        pass

    # Fallback
    if not parts:
        parts.append("SEC filing monitor active.")

    return " ".join(parts)


def _get_notes_stats() -> dict:
    """Get date + product count from the structured notes DB."""
    result = {"date": None, "product_count": 0}
    try:
        import sqlite3
        for db_path in [Path("D:/sec-data/databases/structured_notes.db"), Path("data/structured_notes.db")]:
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                row = conn.execute("SELECT MAX(filing_date) FROM filings WHERE extracted = 1").fetchone()
                result["date"] = row[0] if row and row[0] else None
                row = conn.execute("SELECT COUNT(*) FROM products").fetchone()
                result["product_count"] = row[0] if row else 0
                conn.close()
                break
    except Exception:
        pass
    return result


@router.get("/")
def home_page(request: Request, db: Session = Depends(get_db)):
    """Home page — morning brief with KPI strip and pillar quick links."""
    brief_text = _generate_morning_brief(db)

    # Data freshness: latest filing date
    filing_date = None
    try:
        filing_date = db.execute(
            select(func.max(Filing.filing_date))
        ).scalar()
    except Exception:
        pass

    # Data freshness: latest market data
    market_date = None
    try:
        from webapp.models import MktTimeSeries
        market_date = db.execute(
            select(func.max(MktTimeSeries.as_of_date))
        ).scalar()
    except Exception:
        pass
    if not market_date:
        try:
            from webapp.models import MktPipelineRun
            latest_run = db.execute(
                select(MktPipelineRun.finished_at)
                .where(MktPipelineRun.status == "completed")
                .order_by(MktPipelineRun.finished_at.desc())
                .limit(1)
            ).scalar()
            if latest_run:
                market_date = (
                    latest_run.strftime("%Y-%m-%d")
                    if isinstance(latest_run, datetime)
                    else str(latest_run)
                )
        except Exception:
            pass

    # Data freshness + product count: structured notes
    notes_stats = _get_notes_stats()
    notes_date = notes_stats["date"]
    notes_product_count = notes_stats["product_count"]

    # last_sync_date for footer
    last_sync_date = str(filing_date) if filing_date else str(date.today())

    # This week's fund filings (trust-level, last 7 days)
    week_ago = date.today() - timedelta(days=7)
    weekly_fund_filings = []
    try:
        rows = db.execute(text("""
            SELECT t.name, t.slug, COUNT(DISTINCT f.id) as cnt,
                   GROUP_CONCAT(DISTINCT f.form) as forms,
                   MAX(f.filing_date) as latest
            FROM filings f
            JOIN trusts t ON f.trust_id = t.id
            WHERE f.filing_date >= :cutoff AND t.is_active = 1
            GROUP BY t.id ORDER BY latest DESC LIMIT 10
        """), {"cutoff": str(week_ago)}).fetchall()
        weekly_fund_filings = [
            {"name": r[0], "slug": r[1], "count": r[2], "forms": r[3], "date": r[4]}
            for r in rows
        ]
    except Exception:
        pass

    # This week's structured notes filings
    weekly_notes_filings = []
    try:
        import sqlite3
        for db_path in [Path("D:/sec-data/databases/structured_notes.db"), Path("data/structured_notes.db")]:
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                rows = conn.execute("""
                    SELECT p.parent_issuer, COUNT(*) as cnt, MAX(f.filing_date) as latest
                    FROM products p JOIN filings f ON p.filing_id = f.id
                    WHERE f.filing_date >= date('now', '-7 days') AND f.extracted = 1
                    GROUP BY p.parent_issuer ORDER BY cnt DESC LIMIT 10
                """).fetchall()
                weekly_notes_filings = [
                    {"issuer": r[0], "count": r[1], "date": r[2]}
                    for r in rows
                ]
                conn.close()
                break
    except Exception:
        pass

    # KPI: new fund filings this week (funds that appeared for the first time)
    weekly_new_fund_count = 0
    try:
        weekly_new_fund_count = db.execute(text("""
            SELECT COUNT(DISTINCT fe.series_name)
            FROM fund_extractions fe
            JOIN filings f ON fe.filing_id = f.id
            WHERE f.filing_date >= :cutoff
            AND fe.series_name NOT IN (
                SELECT DISTINCT fe2.series_name FROM fund_extractions fe2
                JOIN filings f2 ON fe2.filing_id = f2.id
                WHERE f2.filing_date < :cutoff AND fe2.series_name IS NOT NULL
            )
            AND fe.series_name IS NOT NULL AND fe.series_name != ''
        """), {"cutoff": str(week_ago)}).scalar() or 0
    except Exception:
        pass

    # KPI: structured notes filed this week
    weekly_notes_count = sum(n["count"] for n in weekly_notes_filings)

    # AUM goal tracker
    aum_goals = None
    try:
        from webapp.services.market_data import get_aum_goals
        aum_goals = get_aum_goals(db)
    except Exception:
        pass

    # Capital Markets product count
    capm_product_count = 0
    try:
        from webapp.models import CapMProduct
        capm_product_count = db.query(CapMProduct).count()
    except Exception:
        pass

    enable_13f = os.environ.get("ENABLE_13F", "0") == "1"

    return templates.TemplateResponse("home.html", {
        "request": request,
        "brief_text": brief_text,
        "market_date": market_date,
        "filing_date": filing_date,
        "ownership_date": "Q4 2025",
        "enable_13f": enable_13f,
        "notes_date": notes_date,
        "notes_product_count": notes_product_count,
        "capm_product_count": capm_product_count,
        "last_sync_date": last_sync_date,
        "weekly_fund_filings": weekly_fund_filings,
        "weekly_notes_filings": weekly_notes_filings,
        "weekly_new_fund_count": weekly_new_fund_count,
        "weekly_notes_count": weekly_notes_count,
        "aum_goals": aum_goals,
    })


# ---------------------------------------------------------------------------
# Dashboard redirect — handler moved to filings router
# ---------------------------------------------------------------------------

@router.get("/dashboard")
def dashboard_redirect(request: Request):
    qs = str(request.url.query)
    url = "/filings/dashboard" + ("?" + qs if qs else "")
    return RedirectResponse(url=url, status_code=301)


# ---------------------------------------------------------------------------
# API routes (stay here — used by home page)
# ---------------------------------------------------------------------------

@router.get("/api/v1/home-kpis")
def api_home_kpis(db: Session = Depends(get_db)):
    """Aggregate KPIs for the home page. Lives here (not holdings.py) so it
    works on Render even without ENABLE_13F."""
    import logging
    log = logging.getLogger(__name__)

    rex_aum = None
    rex_aum_change_pct = None
    weekly_flows = None
    try:
        from webapp.services.market_data import get_rex_summary
        summary = get_rex_summary(db, fund_structure="ETF,ETN", etn_overrides=True)
        if summary:
            kpis = summary.get("kpis", {})
            rex_aum = kpis.get("total_aum_fmt", "--")
            rex_aum_change_pct = kpis.get("aum_mom_pct", 0)
            weekly_flows = kpis.get("flow_1w_fmt", "--")
    except Exception:
        log.debug("Market data unavailable for home KPIs")

    todays_filings = 0
    try:
        todays_filings = db.execute(
            select(func.count(Filing.id)).where(Filing.filing_date == date.today())
        ).scalar() or 0
    except Exception:
        pass

    institutions_count = 0
    total_13f_value = 0
    try:
        from webapp.models import Holding
        from sqlalchemy import distinct
        latest_q = db.execute(
            select(func.max(Holding.report_date)).where(Holding.is_tracked == True)
        ).scalar()
        if latest_q:
            institutions_count = db.execute(
                select(func.count(distinct(Holding.institution_id)))
                .where(Holding.report_date == latest_q, Holding.is_tracked == True)
            ).scalar() or 0
            total_13f_value = db.execute(
                select(func.sum(Holding.value_usd))
                .where(Holding.report_date == latest_q, Holding.is_tracked == True)
            ).scalar() or 0
    except Exception:
        pass

    pipeline_last_run = None
    try:
        import json
        from pathlib import Path
        summary_path = Path("outputs/_run_summary.json")
        if summary_path.exists():
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            pipeline_last_run = data.get("finished_at") or data.get("started_at")
    except Exception:
        pass

    # Data freshness dates
    market_date = None
    try:
        result = db.execute(text("SELECT MAX(as_of_date) FROM mkt_master_data")).scalar()
        if result:
            market_date = str(result)
    except Exception:
        pass

    filing_date = None
    try:
        result = db.execute(text("SELECT MAX(filed_date) FROM filing")).scalar()
        if result:
            filing_date = str(result)
    except Exception:
        pass

    notes_date = None
    try:
        import sqlite3
        notes_db = Path("D:/sec-data/databases/structured_notes.db")
        if notes_db.exists():
            conn = sqlite3.connect(str(notes_db))
            row = conn.execute("SELECT MAX(filing_date) FROM filings WHERE extracted = 1").fetchone()
            conn.close()
            if row and row[0]:
                notes_date = str(row[0])
    except Exception:
        pass

    return {
        "rex_aum": rex_aum,
        "rex_aum_change_pct": rex_aum_change_pct,
        "weekly_flows": weekly_flows,
        "todays_filings": todays_filings,
        "institutions_count": institutions_count,
        "total_13f_value": round(total_13f_value, 0) if total_13f_value else 0,
        "pipeline_last_run": pipeline_last_run,
        "market_date": market_date,
        "filing_date": filing_date,
        "notes_date": notes_date,
    }


_ticker_cache: dict = {"data": None, "ts": 0}
_TICKER_CACHE_TTL = 300  # 5 minutes


def _fetch_market_indices() -> list[dict]:
    """Fetch major market indices via yfinance. Returns empty list on failure."""
    indices = []
    try:
        import yfinance as yf
    except ImportError:
        return indices

    symbols = {
        "^GSPC": "S&P 500",
        "^IXIC": "NASDAQ",
        "^VIX": "VIX",
        "GC=F": "Gold",
        "BTC-USD": "BTC",
    }
    for sym, name in symbols.items():
        try:
            ticker = yf.Ticker(sym)
            info = ticker.fast_info
            price = getattr(info, "last_price", 0) or 0
            prev = getattr(info, "previous_close", price) or price
            change_pct = ((price - prev) / prev * 100) if prev else 0

            if price > 10000:
                val_str = f"{price:,.0f}"
            elif price > 100:
                val_str = f"{price:,.1f}"
            else:
                val_str = f"{price:,.2f}"

            indices.append({
                "name": name,
                "value": val_str,
                "change_pct": round(change_pct, 2),
            })
        except Exception:
            pass

    return indices


@router.get("/api/v1/ticker-strip")
def api_ticker_strip(db: Session = Depends(get_db)):
    """Ticker strip data — market indices + top REX products."""
    now = time.time()
    if _ticker_cache["data"] and (now - _ticker_cache["ts"]) < _TICKER_CACHE_TTL:
        return _ticker_cache["data"]

    from webapp.models import MktMasterData
    products = []
    try:
        rows = db.execute(
            select(
                MktMasterData.ticker,
                MktMasterData.aum,
                MktMasterData.total_return_1week,
                MktMasterData.rex_suite,
            )
            .where(MktMasterData.is_rex == True)
            .order_by(MktMasterData.aum.desc())
            .limit(12)
        ).all()
        for ticker, aum, ret_1w, suite in rows:
            tk = (ticker or "").replace(" US", "")
            aum_val = float(str(aum).replace(",", "").replace("$", "")) if aum else 0
            ret = float(ret_1w) if ret_1w else 0
            if aum_val >= 100:
                aum_str = f"${aum_val/1000:.1f}B" if aum_val >= 1000 else f"${aum_val:.0f}M"
            else:
                aum_str = f"${aum_val:.0f}M"
            products.append({
                "ticker": tk,
                "value": aum_str,
                "change_pct": round(ret, 2),
                "suite": suite or "",
            })
    except Exception:
        pass

    if not products:
        products = [
            {"ticker": "GDXU", "value": "$2.1B", "change_pct": -23.8, "suite": "MicroSectors"},
            {"ticker": "BULZ", "value": "$1.8B", "change_pct": -5.3, "suite": "MicroSectors"},
            {"ticker": "FEPI", "value": "$598M", "change_pct": 0.1, "suite": "Income"},
            {"ticker": "NVDX", "value": "$516M", "change_pct": 0.2, "suite": "T-REX"},
        ]

    # Market indices (yfinance) — slow call, cached
    indices = _fetch_market_indices()

    result = {"indices": indices, "products": products}
    _ticker_cache["data"] = result
    _ticker_cache["ts"] = now
    return result

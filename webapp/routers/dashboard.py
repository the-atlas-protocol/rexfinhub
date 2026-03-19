"""
Dashboard router - Main page with KPIs, trust list, recent activity.
"""
from __future__ import annotations

import logging
import math
import time
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Filing
from webapp.fund_filters import MUTUAL_FUND_EXCLUSIONS
from webapp.models import Trust, FundStatus, Filing, FundExtraction

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

# REX trusts appear first
_PRIORITY_TRUSTS = ["REX ETF Trust", "ETF Opportunities Trust"]

_ALLOWED_DAYS = {7, 14, 30, 90, 0}

_TYPE_LABELS = {
    "etf_trust": "ETF Trust",
    "mutual_fund": "Mutual Fund",
    "grantor_trust": "Grantor Trust",
    "unknown": "Unknown",
}


def _get_act_type(trust) -> str:
    """Get act type from trust model (DB-driven, replaces trusts.py lookup)."""
    if trust.regulatory_act:
        return "33" if trust.regulatory_act == "33_act" else "40"
    # Fallback for trusts without regulatory_act set
    try:
        from etp_tracker.trusts import get_act_type
        return get_act_type(trust.cik)
    except Exception:
        return "40"


def _trust_stats(db: Session) -> list[dict]:
    """Get per-trust fund counts, ordered: priority trusts first, then alpha."""
    # Subquery: ETF-only fund stats (excludes mutual fund share classes + blank names)
    fund_q = (
        select(
            FundStatus.trust_id,
            func.count(FundStatus.id).label("total"),
            func.sum(func.iif(FundStatus.status == "EFFECTIVE", 1, 0)).label("effective"),
            func.sum(func.iif(FundStatus.status == "PENDING", 1, 0)).label("pending"),
            func.sum(func.iif(FundStatus.status == "DELAYED", 1, 0)).label("delayed"),
        )
        .where(FundStatus.fund_name != "")
    )
    for pattern in MUTUAL_FUND_EXCLUSIONS:
        fund_q = fund_q.where(~FundStatus.fund_name.ilike(pattern))
    fund_sq = fund_q.group_by(FundStatus.trust_id).subquery()

    # Subquery: filing counts per trust
    filing_count_sq = (
        select(
            Filing.trust_id,
            func.count(Filing.id).label("fc"),
        )
        .group_by(Filing.trust_id)
        .subquery()
    )

    # Main query: all active trusts with left-joined stats
    query = (
        select(
            Trust.id,
            Trust.name,
            Trust.slug,
            Trust.cik,
            Trust.is_rex,
            Trust.entity_type,
            Trust.regulatory_act,
            Trust.last_filed,
            Trust.source,
            func.coalesce(fund_sq.c.total, 0).label("total"),
            func.coalesce(fund_sq.c.effective, 0).label("effective"),
            func.coalesce(fund_sq.c.pending, 0).label("pending"),
            func.coalesce(fund_sq.c.delayed, 0).label("delayed"),
            func.coalesce(filing_count_sq.c.fc, 0).label("filing_count"),
        )
        .outerjoin(fund_sq, fund_sq.c.trust_id == Trust.id)
        .outerjoin(filing_count_sq, filing_count_sq.c.trust_id == Trust.id)
        .where(Trust.is_active == True)
    )

    rows = db.execute(query).all()

    trusts = []
    for r in rows:
        # Determine act type from DB column or fallback
        if hasattr(r, 'regulatory_act') and r.regulatory_act:
            act_type = "33" if r.regulatory_act == "33_act" else "40"
        else:
            try:
                from etp_tracker.trusts import get_act_type as _legacy_act_type
                act_type = _legacy_act_type(r.cik)
            except Exception:
                act_type = "40"
        t = {
            "id": r.id, "name": r.name, "slug": r.slug, "is_rex": r.is_rex,
            "total": r.total or 0, "effective": r.effective or 0,
            "pending": r.pending or 0, "delayed": r.delayed or 0,
            "filing_count": r.filing_count or 0,
            "act_type": act_type,
            "entity_type": getattr(r, 'entity_type', None),
            "last_filed": getattr(r, 'last_filed', None),
            "source": getattr(r, 'source', None),
        }
        # Classify scrape status for display
        if t["filing_count"] == 0:
            t["scrape_status"] = "pending"
        elif t["total"] == 0 and t["act_type"] == "33":
            t["scrape_status"] = "s1_filer"
        elif t["total"] == 0:
            t["scrape_status"] = "no_etf_funds"
        else:
            t["scrape_status"] = "active"
        trusts.append(t)

    # Sort: priority trusts first, then alphabetical
    def sort_key(t):
        if t["name"] in _PRIORITY_TRUSTS:
            return (0, _PRIORITY_TRUSTS.index(t["name"]))
        return (1, t["name"])

    trusts.sort(key=sort_key)
    return trusts


_ALLOWED_TRUST_FILTERS = {"all", "active", "pending", "no_etf_funds", "s1_filer"}

_STATUS_LABELS = {
    "all": "All",
    "active": "With ETF Funds",
    "pending": "Awaiting Scrape",
    "no_etf_funds": "No ETF Funds",
    "s1_filer": "33 Act Filers",
}

TRUST_PAGE_SIZE = 60


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


def _get_notes_date() -> str | None:
    """Get the latest filing date from the structured notes DB on D: drive."""
    try:
        import sqlite3
        notes_db = Path("D:/sec-data/databases/structured_notes.db")
        if not notes_db.exists():
            return None
        conn = sqlite3.connect(str(notes_db))
        row = conn.execute(
            "SELECT MAX(filing_date) FROM filings WHERE extracted = 1"
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


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

    # Data freshness: structured notes
    notes_date = _get_notes_date()

    # last_sync_date for footer
    last_sync_date = str(filing_date) if filing_date else str(date.today())

    return templates.TemplateResponse("home.html", {
        "request": request,
        "brief_text": brief_text,
        "market_date": market_date,
        "filing_date": filing_date,
        "ownership_date": "Q4 2025",
        "notes_date": notes_date,
        "last_sync_date": last_sync_date,
    })


@router.get("/dashboard")
def dashboard(
    request: Request,
    added: str = "",
    days: int = 7,
    form_type: str = "",
    filing_trust_id: int = 0,
    entity_type: str = "",
    trust_filter: str = "active",
    trust_page: int = Query(default=1, ge=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=10, le=200),
    db: Session = Depends(get_db),
):
    all_trusts = _trust_stats(db)

    # Filter by entity_type if specified
    if entity_type:
        all_trusts = [t for t in all_trusts if t.get("entity_type") == entity_type]

    # KPIs always reflect full (entity-filtered) set
    total_funds = sum(t["total"] for t in all_trusts)
    total_effective = sum(t["effective"] for t in all_trusts)
    total_pending = sum(t["pending"] for t in all_trusts)
    total_delayed = sum(t["delayed"] for t in all_trusts)

    # Weekly filing delta for KPI trend
    cutoff_7d = date.today() - timedelta(days=7)
    new_filings_7d = db.execute(
        select(func.count(Filing.id)).where(Filing.filing_date >= cutoff_7d)
    ).scalar() or 0

    # Status counts for filter buttons (before applying trust_filter)
    status_counts = {}
    for t in all_trusts:
        s = t["scrape_status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    # Apply trust_filter to the card grid
    if trust_filter not in _ALLOWED_TRUST_FILTERS:
        trust_filter = "active"
    if trust_filter != "all":
        trust_list = [t for t in all_trusts if t["scrape_status"] == trust_filter]
    else:
        trust_list = all_trusts

    # Server-side pagination for trust grid
    total_trust_count = len(trust_list)
    total_trust_pages = max(1, math.ceil(total_trust_count / TRUST_PAGE_SIZE))
    trust_page = min(trust_page, total_trust_pages)
    trust_list = trust_list[(trust_page - 1) * TRUST_PAGE_SIZE : trust_page * TRUST_PAGE_SIZE]

    # Validate days parameter
    if days not in _ALLOWED_DAYS:
        days = 7

    # Recent filings with configurable filters (prospectus-related only)
    _PROSPECTUS_PREFIXES = ("485A", "485B", "497", "S-1", "S-3", "EFFECT", "POS AM")
    filing_query = (
        select(
            Filing,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
            func.group_concat(FundExtraction.series_name.distinct()).label("fund_names"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
    )

    if days > 0:
        cutoff = date.today() - timedelta(days=days)
        filing_query = filing_query.where(Filing.filing_date >= cutoff)

    if form_type:
        filing_query = filing_query.where(Filing.form.ilike(f"{form_type}%"))
    else:
        # Default: only prospectus-related forms on the dashboard
        filing_query = filing_query.where(
            or_(*(Filing.form.ilike(f"{p}%") for p in _PROSPECTUS_PREFIXES))
        )

    if filing_trust_id:
        filing_query = filing_query.where(Filing.trust_id == filing_trust_id)

    filing_query = (
        filing_query
        .group_by(Filing.id)
        .order_by(Filing.filing_date.desc())
    )

    # Count total filings before applying pagination
    total_filings = db.execute(
        select(func.count()).select_from(filing_query.subquery())
    ).scalar() or 0
    total_pages = max(1, math.ceil(total_filings / per_page))
    page = min(page, total_pages)

    # Apply pagination
    recent_filings = db.execute(
        filing_query.offset((page - 1) * per_page).limit(per_page)
    ).all()

    # Trust list for filing filter dropdown
    filing_trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

    # Build query string preserving current filters (without page)
    qs_params = {}
    if days != 7:
        qs_params["days"] = days
    if form_type:
        qs_params["form_type"] = form_type
    if filing_trust_id:
        qs_params["filing_trust_id"] = filing_trust_id
    if entity_type:
        qs_params["entity_type"] = entity_type
    if per_page != 10:
        qs_params["per_page"] = per_page
    if trust_filter != "active":
        qs_params["trust_filter"] = trust_filter
    base_qs = urllib.parse.urlencode(qs_params)

    # Entity type counts (reuse all_trusts, no second query)
    type_counts = {}
    for t in all_trusts:
        et = t.get("entity_type") or "unknown"
        type_counts[et] = type_counts.get(et, 0) + 1

    # Trust grid query string (preserves trust_filter + entity_type, resets trust_page)
    trust_qs_params = {}
    if trust_filter != "active":
        trust_qs_params["trust_filter"] = trust_filter
    if entity_type:
        trust_qs_params["entity_type"] = entity_type
    if days != 7:
        trust_qs_params["days"] = days
    if form_type:
        trust_qs_params["form_type"] = form_type
    if filing_trust_id:
        trust_qs_params["filing_trust_id"] = filing_trust_id
    if per_page != 10:
        trust_qs_params["per_page"] = per_page
    trust_base_qs = urllib.parse.urlencode(trust_qs_params)

    # Today's filing count (for KPI strip)
    todays_filings = db.execute(
        select(func.count(Filing.id)).where(Filing.filing_date == date.today())
    ).scalar() or 0

    # Count distinct trusts that filed today (for KPI context line)
    todays_trust_count = db.execute(
        select(func.count(func.distinct(Filing.trust_id)))
        .where(Filing.filing_date == date.today())
    ).scalar() or 0

    # Competitor filings this week (non-REX trusts, grouped by trust)
    week_ago = date.today() - timedelta(days=7)
    competitor_filings = db.execute(text("""
        SELECT t.name as trust_name, t.id as trust_id,
               COUNT(*) as filing_count,
               GROUP_CONCAT(DISTINCT f.form) as form_types
        FROM filings f
        JOIN trusts t ON f.trust_id = t.id
        WHERE f.filing_date >= :week_ago
        AND t.name NOT LIKE '%REX%'
        AND t.name NOT LIKE '%T-REX%'
        AND t.name NOT LIKE '%MicroSectors%'
        AND t.is_active = 1
        GROUP BY t.id
        ORDER BY COUNT(*) DESC
        LIMIT 10
    """), {"week_ago": str(week_ago)}).fetchall()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "trusts": trust_list,
        "total_funds": total_funds,
        "total_effective": total_effective,
        "total_pending": total_pending,
        "total_delayed": total_delayed,
        "recent_filings": recent_filings,
        "today": date.today(),
        "added": added,
        "days": days,
        "form_type": form_type,
        "filing_trust_id": filing_trust_id,
        "filing_trusts": filing_trusts,
        "entity_type": entity_type,
        "type_counts": type_counts,
        "type_labels": _TYPE_LABELS,
        "page": page,
        "per_page": per_page,
        "total_filings": total_filings,
        "total_pages": total_pages,
        "base_qs": base_qs,
        "trust_filter": trust_filter,
        "trust_page": trust_page,
        "total_trust_count": total_trust_count,
        "total_trust_pages": total_trust_pages,
        "trust_base_qs": trust_base_qs,
        "status_counts": status_counts,
        "status_labels": _STATUS_LABELS,
        "new_filings_7d": new_filings_7d,
        "todays_filings": todays_filings,
        "todays_trust_count": todays_trust_count,
        "competitor_filings": competitor_filings,
    })


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
        summary = get_rex_summary(db, fund_structure="ETF")
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

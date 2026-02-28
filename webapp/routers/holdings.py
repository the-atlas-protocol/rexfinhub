"""
Holdings router - Institutional holdings from 13F-HR filings.

Page routes (order matters for FastAPI path matching):
  /holdings/              - Institution list
  /holdings/fund/<ticker> - Fund-level holders view
  /holdings/<cik>/history - Institution history with QoQ changes
  /holdings/<cik>         - Institution detail (catch-all, must be last)

API routes:
  /api/v1/holdings/by-fund?ticker=SOXL
  /api/v1/holdings/<cik>/changes?quarter=2025-12-31
  /api/v1/holdings/<cik>/trend
  /api/v1/holdings/search-funds?q=SOX
  /api/v1/home-kpis
"""
from __future__ import annotations

import logging
import math
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, desc, distinct
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Institution, Holding, CusipMapping, FundStatus, Filing, Trust

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")


# =========================================================================
# Helpers
# =========================================================================

def _fmt_value(val: float | None) -> str:
    """Format USD value for display (values in thousands as reported in 13F)."""
    if val is None:
        return "--"
    v = val * 1000  # 13F reports in thousands
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"


def _pct_change(current: float, prior: float) -> float | None:
    if not prior:
        return None
    return round((current - prior) / abs(prior) * 100, 1)


def _get_latest_report_date(db: Session, cusip: str | None = None, institution_id: int | None = None) -> date | None:
    q = select(func.max(Holding.report_date))
    if cusip:
        q = q.where(Holding.cusip == cusip)
    if institution_id:
        q = q.where(Holding.institution_id == institution_id)
    return db.execute(q).scalar()


def _get_prior_report_date(db: Session, before: date, cusip: str | None = None, institution_id: int | None = None) -> date | None:
    q = select(func.max(Holding.report_date)).where(Holding.report_date < before)
    if cusip:
        q = q.where(Holding.cusip == cusip)
    if institution_id:
        q = q.where(Holding.institution_id == institution_id)
    return db.execute(q).scalar()


def _build_holders(db: Session, cusip: str, latest: date, prior: date | None) -> tuple[list[dict], float]:
    """Build holder list with QoQ deltas. Returns (holders, total_value)."""
    rows = db.execute(
        select(Holding, Institution.name.label("inst_name"), Institution.cik.label("inst_cik"))
        .join(Institution, Institution.id == Holding.institution_id)
        .where(Holding.cusip == cusip, Holding.report_date == latest)
        .order_by(desc(Holding.value_usd))
    ).all()

    prior_map: dict[int, Holding] = {}
    if prior:
        prior_rows = db.execute(
            select(Holding).where(Holding.cusip == cusip, Holding.report_date == prior)
        ).scalars().all()
        prior_map = {h.institution_id: h for h in prior_rows}

    holders = []
    total_value = 0.0
    for row in rows:
        h = row.Holding
        val = h.value_usd or 0
        total_value += val
        prior_h = prior_map.get(h.institution_id)
        prior_val = (prior_h.value_usd or 0) if prior_h else 0
        delta = val - prior_val
        pct = _pct_change(val, prior_val)
        if not prior_h:
            change_type = "NEW"
        elif delta > 0:
            change_type = "INCREASED"
        elif delta < 0:
            change_type = "DECREASED"
        else:
            change_type = "UNCHANGED"

        holders.append({
            "institution_name": row.inst_name,
            "cik": row.inst_cik,
            "value": val,
            "value_fmt": _fmt_value(val),
            "shares": h.shares or 0,
            "qoq_value_change": delta,
            "qoq_value_change_fmt": _fmt_value(abs(delta)) if delta else "--",
            "qoq_value_pct": pct,
            "change_type": change_type,
        })

    return holders, total_value


def _build_position_changes(db: Session, institution_id: int, latest: date, prior: date | None) -> tuple[list[dict], int]:
    """Build position changes list between two quarters. Returns (changes, net_new)."""
    current_holdings = db.execute(
        select(Holding).where(
            Holding.institution_id == institution_id,
            Holding.report_date == latest,
        )
    ).scalars().all()
    current_map = {h.cusip: h for h in current_holdings if h.cusip}

    prior_map: dict[str, Holding] = {}
    if prior:
        prior_holdings = db.execute(
            select(Holding).where(
                Holding.institution_id == institution_id,
                Holding.report_date == prior,
            )
        ).scalars().all()
        prior_map = {h.cusip: h for h in prior_holdings if h.cusip}

    all_cusips = set(current_map.keys()) | set(prior_map.keys())
    cusip_ticker_map: dict[str, str] = {}
    if all_cusips:
        mappings = db.execute(
            select(CusipMapping).where(CusipMapping.cusip.in_(list(all_cusips)))
        ).scalars().all()
        cusip_ticker_map = {m.cusip: m.ticker for m in mappings if m.ticker}

    changes = []
    net_new = 0
    for cusip in all_cusips:
        curr = current_map.get(cusip)
        prev = prior_map.get(cusip)
        curr_val = (curr.value_usd or 0) * 1000 if curr else 0
        prev_val = (prev.value_usd or 0) * 1000 if prev else 0
        delta = curr_val - prev_val
        pct = _pct_change(curr_val, prev_val)
        issuer = (curr.issuer_name if curr else prev.issuer_name) or cusip

        if curr and not prev:
            action = "NEW"
            net_new += 1
        elif prev and not curr:
            action = "EXITED"
            net_new -= 1
        elif delta > 0:
            action = "INCREASED"
        elif delta < 0:
            action = "DECREASED"
        else:
            action = "UNCHANGED"

        changes.append({
            "issuer_name": issuer,
            "cusip": cusip,
            "fund_match_ticker": cusip_ticker_map.get(cusip, ""),
            "current_value": curr_val,
            "current_value_fmt": _fmt_value(curr.value_usd) if curr else "--",
            "prior_value": prev_val,
            "prior_value_fmt": _fmt_value(prev.value_usd) if prev else "--",
            "change_value": delta,
            "change_pct": pct,
            "action": action,
        })

    changes.sort(key=lambda c: abs(c["change_value"]), reverse=True)
    return changes, net_new


# =========================================================================
# PAGE ROUTES
# =========================================================================

@router.get("/holdings/")
def holdings_list(
    request: Request,
    q: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=10, le=200),
    sort: str = "aum",
    db: Session = Depends(get_db),
):
    """List institutions with their holdings summary."""
    holdings_sq = (
        select(
            Holding.institution_id,
            func.count(Holding.id).label("holding_count"),
            func.sum(Holding.value_usd).label("total_value"),
        )
        .group_by(Holding.institution_id)
        .subquery()
    )

    query = (
        select(
            Institution,
            func.coalesce(holdings_sq.c.holding_count, 0).label("holding_count"),
            func.coalesce(holdings_sq.c.total_value, 0).label("total_value"),
        )
        .outerjoin(holdings_sq, holdings_sq.c.institution_id == Institution.id)
    )

    if q.strip():
        query = query.where(Institution.name.ilike(f"%{q}%"))

    if sort == "name":
        query = query.order_by(Institution.name)
    elif sort == "filings":
        query = query.order_by(desc(Institution.filing_count))
    elif sort == "last_filed":
        query = query.order_by(desc(Institution.last_filed))
    else:
        query = query.order_by(desc(func.coalesce(holdings_sq.c.total_value, 0)))

    total_results = db.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar() or 0
    total_pages = max(1, math.ceil(total_results / per_page))
    page = min(page, total_pages)

    results = db.execute(
        query.offset((page - 1) * per_page).limit(per_page)
    ).all()

    total_institutions = db.execute(
        select(func.count(Institution.id))
    ).scalar() or 0

    total_holdings_value = db.execute(
        select(func.sum(Holding.value_usd))
    ).scalar() or 0

    matched_cusips = db.execute(
        select(func.count(CusipMapping.id)).where(CusipMapping.trust_id.isnot(None))
    ).scalar() or 0

    return templates.TemplateResponse("holdings.html", {
        "request": request,
        "institutions": results,
        "q": q,
        "sort": sort,
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": total_pages,
        "total_institutions": total_institutions,
        "total_holdings_value": _fmt_value(total_holdings_value),
        "matched_cusips": matched_cusips,
        "fmt_value": _fmt_value,
    })


# --- /holdings/fund/{ticker} MUST come before /holdings/{cik} ---

@router.get("/holdings/fund/{ticker}")
def holdings_fund_page(
    ticker: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Fund-level institutional holdings page."""
    ticker = ticker.upper()
    mapping = db.execute(
        select(CusipMapping).where(func.upper(CusipMapping.ticker) == ticker)
    ).scalar_one_or_none()

    if not mapping or not mapping.cusip:
        raise HTTPException(status_code=404, detail=f"No CUSIP mapping for ticker {ticker}")

    cusip = mapping.cusip
    latest = _get_latest_report_date(db, cusip=cusip)
    prior = _get_prior_report_date(db, latest, cusip=cusip) if latest else None

    holders = []
    total_value = 0.0
    if latest:
        holders, total_value = _build_holders(db, cusip, latest, prior)

    # Prior quarter total for QoQ
    prior_total = 0.0
    if prior:
        prior_total = db.execute(
            select(func.sum(Holding.value_usd))
            .where(Holding.cusip == cusip, Holding.report_date == prior)
        ).scalar() or 0

    qoq_change = total_value - prior_total
    qoq_pct = _pct_change(total_value * 1000, prior_total * 1000)

    # Trend
    trend_rows = db.execute(
        select(
            Holding.report_date,
            func.sum(Holding.value_usd).label("total_value"),
            func.count(distinct(Holding.institution_id)).label("holder_count"),
        )
        .where(Holding.cusip == cusip)
        .group_by(Holding.report_date)
        .order_by(Holding.report_date)
    ).all()

    return templates.TemplateResponse("holdings_fund.html", {
        "request": request,
        "ticker": ticker,
        "fund_name": mapping.fund_name or ticker,
        "cusip": cusip,
        "latest_date": latest,
        "prior_date": prior,
        "holders": holders,
        "holder_count": len(holders),
        "total_value": total_value,
        "total_value_fmt": _fmt_value(total_value),
        "qoq_change": qoq_change,
        "qoq_change_fmt": _fmt_value(abs(qoq_change)) if qoq_change else "--",
        "qoq_pct": qoq_pct,
        "qoq_positive": qoq_change >= 0,
        "trend_labels": [str(r.report_date) for r in trend_rows],
        "trend_values": [round((r.total_value or 0) * 1000, 0) for r in trend_rows],
        "fmt_value": _fmt_value,
    })


# --- /holdings/{cik}/history MUST come before /holdings/{cik} ---

@router.get("/holdings/{cik}/history")
def institution_history_page(
    cik: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Institution history page with QoQ position changes."""
    institution = db.execute(
        select(Institution).where(Institution.cik == cik)
    ).scalar_one_or_none()
    if not institution:
        raise HTTPException(status_code=404, detail="Institution not found")

    latest = _get_latest_report_date(db, institution_id=institution.id)
    prior = _get_prior_report_date(db, latest, institution_id=institution.id) if latest else None

    trend_rows = db.execute(
        select(
            Holding.report_date,
            func.sum(Holding.value_usd).label("total_value"),
            func.count(Holding.id).label("position_count"),
        )
        .where(Holding.institution_id == institution.id)
        .group_by(Holding.report_date)
        .order_by(Holding.report_date)
    ).all()

    trend_labels = [str(r.report_date) for r in trend_rows]
    trend_aum = [round((r.total_value or 0) * 1000, 0) for r in trend_rows]
    trend_positions = [r.position_count for r in trend_rows]

    current_aum = trend_aum[-1] if trend_aum else 0
    prior_aum = trend_aum[-2] if len(trend_aum) >= 2 else 0
    qoq_change = current_aum - prior_aum
    qoq_pct = _pct_change(current_aum, prior_aum)

    changes = []
    net_new = 0
    if latest:
        changes, net_new = _build_position_changes(db, institution.id, latest, prior)

    return templates.TemplateResponse("institution_history.html", {
        "request": request,
        "institution": institution,
        "latest_date": latest,
        "prior_date": prior,
        "current_aum": current_aum,
        "current_aum_fmt": _fmt_value(current_aum / 1000) if current_aum else "--",
        "qoq_change": qoq_change,
        "qoq_pct": qoq_pct,
        "qoq_positive": qoq_change >= 0,
        "quarters_on_file": len(trend_rows),
        "net_new": net_new,
        "trend_labels": trend_labels,
        "trend_aum": trend_aum,
        "trend_positions": trend_positions,
        "changes": changes,
        "fmt_value": _fmt_value,
    })


# --- Catch-all institution detail (MUST be last /holdings/{cik} route) ---

@router.get("/holdings/{cik}")
def institution_detail(
    cik: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Institution detail page with all holdings."""
    institution = db.execute(
        select(Institution).where(Institution.cik == cik)
    ).scalar_one_or_none()

    if not institution:
        raise HTTPException(status_code=404, detail="Institution not found")

    latest_date = db.execute(
        select(func.max(Holding.report_date))
        .where(Holding.institution_id == institution.id)
    ).scalar()

    holdings_query = (
        select(Holding)
        .where(Holding.institution_id == institution.id)
    )
    if latest_date:
        holdings_query = holdings_query.where(Holding.report_date == latest_date)
    holdings_query = holdings_query.order_by(desc(Holding.value_usd))

    holdings = db.execute(holdings_query).scalars().all()

    cusip_map = {}
    if holdings:
        cusips = [h.cusip for h in holdings if h.cusip]
        if cusips:
            mappings = db.execute(
                select(CusipMapping).where(CusipMapping.cusip.in_(cusips))
            ).scalars().all()
            cusip_map = {m.cusip: m for m in mappings}

    matched_holdings = []
    unmatched_holdings = []
    for h in holdings:
        mapping = cusip_map.get(h.cusip)
        if mapping and mapping.trust_id:
            fund = db.execute(
                select(FundStatus)
                .where(FundStatus.trust_id == mapping.trust_id)
                .where(FundStatus.ticker == mapping.ticker)
            ).scalar_one_or_none()
            matched_holdings.append({"holding": h, "mapping": mapping, "fund": fund})
        else:
            unmatched_holdings.append(h)

    total_value = sum(h.value_usd or 0 for h in holdings)
    total_positions = len(holdings)

    return templates.TemplateResponse("institution.html", {
        "request": request,
        "institution": institution,
        "holdings": holdings,
        "matched_holdings": matched_holdings,
        "unmatched_holdings": unmatched_holdings,
        "latest_date": latest_date,
        "total_value": _fmt_value(total_value),
        "total_positions": total_positions,
        "matched_count": len(matched_holdings),
        "fmt_value": _fmt_value,
    })


# =========================================================================
# API ENDPOINTS
# =========================================================================

@router.get("/api/v1/holdings/by-fund")
def api_holdings_by_fund(
    ticker: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Get institutional holders for a specific fund ticker."""
    ticker = ticker.upper()
    mapping = db.execute(
        select(CusipMapping).where(func.upper(CusipMapping.ticker) == ticker)
    ).scalar_one_or_none()

    if not mapping or not mapping.cusip:
        return JSONResponse({"error": f"No CUSIP mapping for {ticker}"}, status_code=404)

    cusip = mapping.cusip
    latest = _get_latest_report_date(db, cusip=cusip)
    if not latest:
        return {"ticker": ticker, "fund_name": mapping.fund_name, "cusip": cusip,
                "total_value": 0, "total_holders": 0, "trend": {"labels": [], "values": []}, "holders": []}

    prior = _get_prior_report_date(db, latest, cusip=cusip)

    rows = db.execute(
        select(Holding, Institution.name.label("inst_name"), Institution.cik.label("inst_cik"))
        .join(Institution, Institution.id == Holding.institution_id)
        .where(Holding.cusip == cusip, Holding.report_date == latest)
        .order_by(desc(Holding.value_usd))
    ).all()

    prior_map: dict[int, Holding] = {}
    if prior:
        prior_rows = db.execute(
            select(Holding).where(Holding.cusip == cusip, Holding.report_date == prior)
        ).scalars().all()
        prior_map = {h.institution_id: h for h in prior_rows}

    holders = []
    total_value = 0.0
    for row in rows:
        h = row.Holding
        val = h.value_usd or 0
        total_value += val
        prior_h = prior_map.get(h.institution_id)
        prior_val = (prior_h.value_usd or 0) if prior_h else 0
        delta = val - prior_val
        pct = _pct_change(val, prior_val)
        change_type = "NEW" if not prior_h else ("INCREASED" if delta > 0 else ("DECREASED" if delta < 0 else "UNCHANGED"))

        holders.append({
            "institution_name": row.inst_name,
            "cik": row.inst_cik,
            "value": val * 1000,
            "shares": h.shares or 0,
            "qoq_value_change": delta * 1000,
            "qoq_value_pct": pct,
            "change_type": change_type,
        })

    trend_rows = db.execute(
        select(
            Holding.report_date,
            func.sum(Holding.value_usd).label("total_value"),
            func.count(distinct(Holding.institution_id)).label("holder_count"),
        )
        .where(Holding.cusip == cusip)
        .group_by(Holding.report_date)
        .order_by(Holding.report_date)
    ).all()

    return {
        "ticker": ticker,
        "fund_name": mapping.fund_name,
        "cusip": cusip,
        "total_value": total_value * 1000,
        "total_holders": len(holders),
        "trend": {
            "labels": [str(r.report_date) for r in trend_rows],
            "values": [round((r.total_value or 0) * 1000, 0) for r in trend_rows],
        },
        "holders": holders,
    }


@router.get("/api/v1/holdings/{cik}/changes")
def api_institution_changes(
    cik: str,
    quarter: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Get position changes for an institution between quarters."""
    institution = db.execute(
        select(Institution).where(Institution.cik == cik)
    ).scalar_one_or_none()
    if not institution:
        return JSONResponse({"error": "Institution not found"}, status_code=404)

    if quarter:
        try:
            report_date = date.fromisoformat(quarter)
        except ValueError:
            return JSONResponse({"error": "Invalid quarter format, use YYYY-MM-DD"}, status_code=400)
    else:
        report_date = _get_latest_report_date(db, institution_id=institution.id)

    if not report_date:
        return {"institution": institution.name, "quarter": None, "prior_quarter": None,
                "total_value": 0, "qoq_change": 0, "positions": []}

    prior_date = _get_prior_report_date(db, report_date, institution_id=institution.id)

    current = db.execute(
        select(Holding).where(
            Holding.institution_id == institution.id,
            Holding.report_date == report_date,
        )
    ).scalars().all()
    current_map = {h.cusip: h for h in current if h.cusip}

    prior_map: dict[str, Holding] = {}
    if prior_date:
        prior_list = db.execute(
            select(Holding).where(
                Holding.institution_id == institution.id,
                Holding.report_date == prior_date,
            )
        ).scalars().all()
        prior_map = {h.cusip: h for h in prior_list if h.cusip}

    all_cusips = set(current_map.keys()) | set(prior_map.keys())
    cusip_ticker: dict[str, str] = {}
    if all_cusips:
        mappings = db.execute(
            select(CusipMapping).where(CusipMapping.cusip.in_(list(all_cusips)))
        ).scalars().all()
        cusip_ticker = {m.cusip: m.ticker for m in mappings if m.ticker}

    positions = []
    total_value = 0.0
    for cusip in all_cusips:
        curr = current_map.get(cusip)
        prev = prior_map.get(cusip)
        curr_val = (curr.value_usd or 0) * 1000 if curr else 0
        prev_val = (prev.value_usd or 0) * 1000 if prev else 0
        delta = curr_val - prev_val
        pct = _pct_change(curr_val, prev_val)
        total_value += curr_val

        if curr and not prev:
            action = "NEW"
        elif prev and not curr:
            action = "EXITED"
        elif delta > 0:
            action = "INCREASED"
        elif delta < 0:
            action = "DECREASED"
        else:
            action = "UNCHANGED"

        issuer = (curr.issuer_name if curr else prev.issuer_name) or cusip
        positions.append({
            "issuer_name": issuer,
            "cusip": cusip,
            "fund_match_ticker": cusip_ticker.get(cusip, ""),
            "current_value": curr_val,
            "prior_value": prev_val,
            "change_value": delta,
            "change_pct": pct,
            "action": action,
        })

    positions.sort(key=lambda p: abs(p["change_value"]), reverse=True)

    prior_total = sum((h.value_usd or 0) * 1000 for h in prior_map.values()) if prior_map else 0
    qoq_change = total_value - prior_total

    return {
        "institution": institution.name,
        "quarter": str(report_date),
        "prior_quarter": str(prior_date) if prior_date else None,
        "total_value": total_value,
        "qoq_change": qoq_change,
        "positions": positions,
    }


@router.get("/api/v1/holdings/{cik}/trend")
def api_institution_trend(
    cik: str,
    db: Session = Depends(get_db),
):
    """Get quarterly trend for an institution."""
    institution = db.execute(
        select(Institution).where(Institution.cik == cik)
    ).scalar_one_or_none()
    if not institution:
        return JSONResponse({"error": "Institution not found"}, status_code=404)

    rows = db.execute(
        select(
            Holding.report_date,
            func.sum(Holding.value_usd).label("total_value"),
            func.count(Holding.id).label("position_count"),
        )
        .where(Holding.institution_id == institution.id)
        .group_by(Holding.report_date)
        .order_by(Holding.report_date)
    ).all()

    return {
        "cik": cik,
        "institution_name": institution.name,
        "quarters": [
            {
                "date": str(r.report_date),
                "total_value": round((r.total_value or 0) * 1000, 0),
                "position_count": r.position_count,
            }
            for r in rows
        ],
    }


@router.get("/api/v1/holdings/search-funds")
def api_search_funds(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Search CusipMappings and return holder stats."""
    mappings = db.execute(
        select(CusipMapping).where(
            (CusipMapping.ticker.ilike(f"%{q}%")) | (CusipMapping.fund_name.ilike(f"%{q}%"))
        ).limit(50)
    ).scalars().all()

    if not mappings:
        return {"results": []}

    latest_global = db.execute(select(func.max(Holding.report_date))).scalar()

    results = []
    for m in mappings:
        if not m.cusip:
            continue
        stats = None
        if latest_global:
            stats = db.execute(
                select(
                    func.count(distinct(Holding.institution_id)).label("holder_count"),
                    func.sum(Holding.value_usd).label("total_value"),
                )
                .where(Holding.cusip == m.cusip)
                .where(Holding.report_date == latest_global)
            ).one_or_none()

        results.append({
            "ticker": m.ticker,
            "fund_name": m.fund_name,
            "cusip": m.cusip,
            "holder_count": (stats.holder_count or 0) if stats else 0,
            "total_value": round(((stats.total_value or 0) * 1000), 0) if stats else 0,
        })

    results.sort(key=lambda r: r["total_value"], reverse=True)
    return {"results": results}


@router.get("/api/v1/home-kpis")
def api_home_kpis(db: Session = Depends(get_db)):
    """Aggregate KPIs for the home page."""
    rex_aum = None
    rex_aum_change_pct = None
    weekly_flows = None
    try:
        from webapp.services.market_data import get_rex_summary
        summary = get_rex_summary()
        if summary:
            rex_aum = summary.get("total_aum_fmt", "--")
            rex_aum_change_pct = summary.get("aum_mom_pct", 0)
            weekly_flows = summary.get("flow_1w_fmt", "--")
    except Exception:
        log.debug("Market data unavailable for home KPIs")

    todays_filings = db.execute(
        select(func.count(Filing.id)).where(Filing.filing_date == date.today())
    ).scalar() or 0

    latest_q = db.execute(select(func.max(Holding.report_date))).scalar()
    institutions_count = 0
    total_13f_value = 0
    if latest_q:
        institutions_count = db.execute(
            select(func.count(distinct(Holding.institution_id)))
            .where(Holding.report_date == latest_q)
        ).scalar() or 0
        total_13f_value = db.execute(
            select(func.sum(Holding.value_usd))
            .where(Holding.report_date == latest_q)
        ).scalar() or 0

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

    return {
        "rex_aum": rex_aum,
        "rex_aum_change_pct": rex_aum_change_pct,
        "weekly_flows": weekly_flows,
        "todays_filings": todays_filings,
        "institutions_count": institutions_count,
        "total_13f_value": round(total_13f_value * 1000, 0) if total_13f_value else 0,
        "pipeline_last_run": pipeline_last_run,
    }

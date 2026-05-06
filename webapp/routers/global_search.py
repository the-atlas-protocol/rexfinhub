"""
Global search API - unified search across site pages, market products,
trusts, funds, and filings.  Ctrl+K palette in the frontend hits this endpoint.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus, Filing

router = APIRouter(prefix="/api/v1", tags=["search"])
log = logging.getLogger(__name__)

# ── Static site pages for quick-nav search ──────────────────────────────────
_SITE_PAGES = [
    {"name": "Market Intelligence - REX View", "url": "/market/rex", "keywords": "market rex view intelligence products suite"},
    {"name": "Market Intelligence - Category", "url": "/market/category", "keywords": "market category leveraged inverse income crypto"},
    {"name": "Market Intelligence - Issuer Analysis", "url": "/market/issuer", "keywords": "market issuer analysis share"},
    {"name": "Market Intelligence - Underlier", "url": "/market/underlier", "keywords": "market underlier index underlying exposure"},
    {"name": "ETP Launch Calendar", "url": "/calendar/", "keywords": "calendar launches inception effective date etp tracker"},
    {"name": "Market Intelligence - Calendar", "url": "/market/calendar", "keywords": "market calendar launches inception"},
    {"name": "Market Intelligence - Compare", "url": "/market/compare", "keywords": "market compare products head to head"},
    {"name": "Trust Dashboard", "url": "/dashboard", "keywords": "dashboard trust filings overview status"},
    {"name": "Trust Universe", "url": "/universe/", "keywords": "universe trusts all issuers browse"},
    {"name": "Fund Lookup", "url": "/funds/", "keywords": "fund lookup search name ticker series"},
    {"name": "Filing Explorer", "url": "/filings/", "keywords": "filing explorer accession sec edgar browse"},
    {"name": "Filing Screener", "url": "/screener/", "keywords": "screener filing landscape ipo pre-ipo"},
    {"name": "3x Analysis", "url": "/screener/3x-analysis", "keywords": "screener 3x triple leveraged analysis"},
    {"name": "Candidate Evaluator", "url": "/screener/evaluate", "keywords": "screener candidate evaluate score"},
    {"name": "13F Institutional Holders", "url": "/holdings/", "keywords": "holdings 13f institutional holders ownership"},
    {"name": "Institutional Crossover", "url": "/holdings/crossover", "keywords": "holdings crossover institutional overlap"},
    {"name": "Analytics", "url": "/analytics", "keywords": "analytics trends data"},
    {"name": "Data & API", "url": "/downloads/", "keywords": "exports downloads csv data api screener"},
]


def _search_pages(q: str, limit: int = 5) -> list[dict]:
    """Match query against site page names and keywords."""
    ql = q.lower()
    results = []
    for page in _SITE_PAGES:
        name_l = page["name"].lower()
        kw_l = page["keywords"]
        # Exact substring in name gets priority
        if ql in name_l:
            results.append({"name": page["name"], "url": page["url"], "score": 2})
        elif ql in kw_l:
            results.append({"name": page["name"], "url": page["url"], "score": 1})
    results.sort(key=lambda x: -x["score"])
    return [{"name": r["name"], "url": r["url"]} for r in results[:limit]]


def _search_products(q: str, db: Session, limit: int = 8) -> list[dict]:
    """Search market products by ticker or fund name from mkt_master_data."""
    try:
        ql = q.strip()
        # Exact ticker match first, then LIKE on name
        rows = db.execute(
            text("""
                SELECT ticker, fund_name, issuer, category_display, is_rex,
                       aum, fund_type
                FROM mkt_master_data
                WHERE ticker LIKE :pat COLLATE NOCASE
                   OR fund_name LIKE :pat COLLATE NOCASE
                ORDER BY
                    CASE WHEN UPPER(ticker) = UPPER(:exact) THEN 0 ELSE 1 END,
                    aum DESC NULLS LAST
                LIMIT :lim
            """),
            {"pat": f"%{ql}%", "exact": ql, "lim": limit},
        ).fetchall()
        results = []
        seen = set()
        for r in rows:
            ticker = r[0] or ""
            # Strip Bloomberg region suffix (e.g. "SOXL US" -> "SOXL")
            ticker_clean = ticker.rsplit(" ", 1)[0] if " " in ticker else ticker
            if ticker_clean in seen:
                continue
            seen.add(ticker_clean)
            aum_val = r[5]
            if aum_val and aum_val >= 1000:
                aum_fmt = f"${aum_val / 1000:,.1f}B"
            elif aum_val and aum_val >= 1:
                aum_fmt = f"${aum_val:,.0f}M"
            elif aum_val:
                aum_fmt = f"${aum_val:.1f}M"
            else:
                aum_fmt = ""
            results.append({
                "ticker": ticker_clean,
                "fund_name": r[1] or "",
                "issuer": r[2] or "",
                "category": r[3] or "",
                "is_rex": bool(r[4]),
                "aum_fmt": aum_fmt,
                "fund_type": r[6] or "",
            })
        return results
    except Exception as e:
        log.warning("Product search failed (table may not exist): %s", e)
        return []


@router.get("/search")
def global_search(
    q: str = Query("", min_length=0, max_length=200),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    q = q.strip()
    if not q:
        return {"pages": [], "products": [], "trusts": [], "funds": [], "filings": []}

    # 1. Site pages (instant, no DB)
    pages = _search_pages(q)

    # 2. Market products (from Bloomberg data)
    products = _search_products(q, db, limit=8)

    # 3. SEC trusts
    pattern = f"%{q}%"
    trust_rows = db.execute(
        select(Trust.id, Trust.name, Trust.slug, Trust.cik, Trust.entity_type)
        .where(Trust.is_active == True)
        .where((Trust.name.ilike(pattern)) | (Trust.cik.ilike(pattern)))
        .order_by(Trust.name)
        .limit(min(limit, 8))
    ).all()
    trusts = [
        {"name": r.name, "slug": r.slug, "cik": r.cik, "entity_type": r.entity_type}
        for r in trust_rows
    ]

    # 4. SEC funds (only if few product results, to avoid duplication)
    funds = []
    if len(products) < 3:
        fund_rows = db.execute(
            select(
                FundStatus.series_id,
                FundStatus.fund_name,
                FundStatus.ticker,
                FundStatus.status,
                Trust.name.label("trust_name"),
                Trust.slug.label("trust_slug"),
            )
            .join(Trust, Trust.id == FundStatus.trust_id)
            .where(
                (FundStatus.fund_name.ilike(pattern))
                | (FundStatus.ticker.ilike(pattern))
            )
            .order_by(FundStatus.fund_name)
            .limit(min(limit, 6))
        ).all()
        funds = [
            {
                "series_id": r.series_id,
                "fund_name": r.fund_name,
                "ticker": r.ticker,
                "status": r.status,
                "trust_name": r.trust_name,
                "trust_slug": r.trust_slug,
            }
            for r in fund_rows
        ]

    # 5. Filings (only for accession number patterns, not general text)
    filings = []
    if any(c.isdigit() for c in q) and len(q) >= 4:
        filing_rows = db.execute(
            select(
                Filing.id,
                Filing.accession_number,
                Filing.form,
                Filing.filing_date,
                Trust.name.label("trust_name"),
            )
            .join(Trust, Trust.id == Filing.trust_id)
            .where(Filing.accession_number.ilike(pattern))
            .order_by(Filing.filing_date.desc())
            .limit(min(limit, 5))
        ).all()
        filings = [
            {
                "id": r.id,
                "accession": r.accession_number,
                "form": r.form,
                "filing_date": str(r.filing_date) if r.filing_date else None,
                "trust_name": r.trust_name,
            }
            for r in filing_rows
        ]

    return {
        "pages": pages,
        "products": products,
        "trusts": trusts,
        "funds": funds,
        "filings": filings,
    }

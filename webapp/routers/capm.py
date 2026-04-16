"""Capital Markets product list routes.

Public (site-auth) page showing all CapM products in a tabbed, sortable table.
Admin users can edit individual product records inline.

Routes:
    GET  /capm/           — Main product list (filterable by suite, searchable)
    GET  /capm/export.csv — CSV export with current filters
    POST /capm/update/{id} — Admin-only product update
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/capm", tags=["capm"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

VALID_SUITES = ["T-REX", "REX", "REX-OSPREY", "BMO"]


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def capm_page(
    request: Request,
    suite: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """Capital Markets product list page."""
    from webapp.models import CapMProduct

    query = db.query(CapMProduct)

    if suite and suite in VALID_SUITES:
        query = query.filter(CapMProduct.suite_source == suite)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            CapMProduct.fund_name.ilike(like),
            CapMProduct.ticker.ilike(like),
            CapMProduct.underlying_name.ilike(like),
            CapMProduct.underlying_ticker.ilike(like),
            CapMProduct.lmm.ilike(like),
            CapMProduct.custodian.ilike(like),
        ))

    products = query.order_by(
        CapMProduct.suite_source.asc().nulls_last(),
        CapMProduct.ticker.asc(),
    ).all()

    # Summary stats
    total = db.query(CapMProduct).count()
    suite_counts = dict(
        db.query(CapMProduct.suite_source, func.count(CapMProduct.id))
        .filter(CapMProduct.suite_source.isnot(None))
        .group_by(CapMProduct.suite_source)
        .all()
    )

    # Average fixed fee (numeric only)
    avg_fees = {}
    for s in VALID_SUITES:
        rows = (
            db.query(CapMProduct.fixed_fee)
            .filter(CapMProduct.suite_source == s)
            .filter(CapMProduct.fixed_fee.isnot(None))
            .all()
        )
        nums = []
        for (fee_str,) in rows:
            try:
                nums.append(float(str(fee_str).replace(",", "").replace("$", "")))
            except (ValueError, TypeError):
                pass
        avg_fees[s] = round(sum(nums) / len(nums)) if nums else None

    is_admin = request.session.get("is_admin", False)

    return templates.TemplateResponse("capm.html", {
        "request": request,
        "products": products,
        "total": total,
        "filtered_count": len(products),
        "suite_counts": suite_counts,
        "avg_fees": avg_fees,
        "valid_suites": VALID_SUITES,
        "filter_suite": suite or "",
        "filter_q": q or "",
        "is_admin": is_admin,
    })


@router.get("/export.csv")
def export_csv(
    request: Request,
    suite: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """Export filtered product list as CSV."""
    from webapp.models import CapMProduct

    query = db.query(CapMProduct)

    if suite and suite in VALID_SUITES:
        query = query.filter(CapMProduct.suite_source == suite)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            CapMProduct.fund_name.ilike(like),
            CapMProduct.ticker.ilike(like),
            CapMProduct.underlying_name.ilike(like),
            CapMProduct.underlying_ticker.ilike(like),
            CapMProduct.lmm.ilike(like),
            CapMProduct.custodian.ilike(like),
        ))

    products = query.order_by(CapMProduct.suite_source, CapMProduct.ticker).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Ticker", "Fund Name", "Suite", "BB Ticker", "Inception Date",
        "Trust", "Issuer", "Exchange", "CU Size", "Fixed Fee", "Variable Fee",
        "Cut Off", "Custodian", "LMM", "Category", "Direction", "Leverage",
        "Underlying Ticker", "Underlying Name", "Expense Ratio",
        "Competitor Products", "BMO Suite", "Prospectus",
    ])
    for p in products:
        writer.writerow([
            p.ticker or "",
            p.fund_name or "",
            p.suite_source or "",
            p.bb_ticker or "",
            p.inception_date.isoformat() if p.inception_date else "",
            p.trust or "",
            p.issuer or "",
            p.exchange or "",
            p.cu_size or "",
            p.fixed_fee or "",
            p.variable_fee or "",
            p.cut_off or "",
            p.custodian or "",
            p.lmm or "",
            p.category or "",
            p.direction or "",
            p.leverage or "",
            p.underlying_ticker or "",
            p.underlying_name or "",
            f"{p.expense_ratio:.4f}" if p.expense_ratio is not None else "",
            p.competitor_products or "",
            p.bmo_suite or "",
            p.prospectus_link or "",
        ])

    output.seek(0)
    filename = f"capm_products_{date.today().isoformat()}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/update/{product_id}")
def update_product(
    product_id: int,
    request: Request,
    fund_name: str = Form(...),
    ticker: str = Form(""),
    suite_source: str = Form(""),
    exchange: str = Form(""),
    cu_size: str = Form(""),
    fixed_fee: str = Form(""),
    variable_fee: str = Form(""),
    custodian: str = Form(""),
    lmm: str = Form(""),
    direction: str = Form(""),
    leverage: str = Form(""),
    underlying_ticker: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """Admin-only: update a CapM product record."""
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from webapp.models import CapMProduct

    p = db.query(CapMProduct).filter(CapMProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")

    p.fund_name = fund_name
    p.ticker = ticker or None
    p.suite_source = suite_source or None
    p.exchange = exchange or None
    p.cu_size = cu_size or None
    p.fixed_fee = fixed_fee or None
    p.variable_fee = variable_fee or None
    p.custodian = custodian or None
    p.lmm = lmm or None
    p.direction = direction or None
    p.leverage = leverage or None
    p.underlying_ticker = underlying_ticker or None
    p.notes = notes or None
    p.updated_at = datetime.utcnow()

    db.commit()

    # Redirect back with filter params preserved
    suite_param = f"&suite={suite_source}" if suite_source else ""
    return RedirectResponse(url=f"/capm/?msg=updated{suite_param}", status_code=302)

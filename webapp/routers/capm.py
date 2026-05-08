"""Capital Markets product list routes.

Public (site-auth) page showing all CapM products in a tabbed, sortable table.
Admin users can edit individual product records inline.

Phase 1 of the v3 URL migration: the handler implementations have been
renamed to ``_*_impl`` and are imported by ``webapp.routers.operations``
to be mounted under ``/operations/products``. The old ``/capm/*`` routes
shrink to 301/307 redirects pointing at the new canonical URLs.

Legacy URL → new canonical URL:
    GET  /capm/                     → /operations/products
    GET  /capm/export.csv           → /operations/products/export.csv
    POST /capm/update/{product_id}  → /operations/products/update/{product_id}
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
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


def _capm_index_impl(
    request: Request,
    suite: str | None = None,
    q: str | None = None,
    tab: str | None = None,
    db: Session = Depends(get_db),
):
    """Capital Markets product list page. Mounted at /operations/products in PR 1."""
    from webapp.models import CapMProduct, CapMTrustAP

    active_tab = "trust_aps" if tab == "trust_aps" else "products"

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

    # Trust & APs — always loaded so the tab is instantly available
    trust_aps = (
        db.query(CapMTrustAP)
        .order_by(
            CapMTrustAP.trust_name.asc(),
            CapMTrustAP.sort_order.asc().nulls_last(),
            CapMTrustAP.ap_name.asc(),
        )
        .all()
    )

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

    # Count distinct trusts shown on the Trust & APs tab
    trust_count = len({r.trust_name for r in trust_aps})

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
        "trust_aps": trust_aps,
        "trust_count": trust_count,
        "active_tab": active_tab,
    })


def _capm_export_impl(
    request: Request,
    suite: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """Export filtered product list as CSV. Mounted at /operations/products/export.csv in PR 1."""
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


# Whitelist of fields that the products-update endpoint will write.
# Maps form field name -> (CapMProduct attribute, type_coercer).
# Anything not in this map is silently ignored — keeps injection attack
# surface tight.
_CAPM_UPDATE_FIELDS = {
    "fund_name":         ("fund_name",         "str_required"),
    "ticker":            ("ticker",            "str_or_none"),
    "bb_ticker":         ("bb_ticker",         "str_or_none"),
    "suite_source":      ("suite_source",      "suite_or_none"),
    "exchange":          ("exchange",          "str_or_none"),
    "cu_size":           ("cu_size",           "str_or_none"),
    "fixed_fee":         ("fixed_fee",         "str_or_none"),
    "variable_fee":      ("variable_fee",      "str_or_none"),
    "cut_off":           ("cut_off",           "str_or_none"),
    "custodian":         ("custodian",         "str_or_none"),
    "lmm":               ("lmm",               "str_or_none"),
    "direction":         ("direction",         "str_or_none"),
    "leverage":          ("leverage",          "str_or_none"),
    "underlying_ticker": ("underlying_ticker", "str_or_none"),
    "underlying_name":   ("underlying_name",   "str_or_none"),
    "inception_date":    ("inception_date",    "date"),
    "notes":             ("notes",             "str_or_none"),
}


def _coerce_capm(coerce_type: str, raw: str):
    """Coerce a raw form string into a CapMProduct attribute value."""
    s = (raw or "").strip()
    if coerce_type == "str_required":
        if not s:
            raise HTTPException(400, "Value cannot be empty")
        return s
    if coerce_type == "str_or_none":
        return s or None
    if coerce_type == "suite_or_none":
        if not s:
            return None
        if s not in VALID_SUITES:
            raise HTTPException(400, f"Invalid suite. Valid: {VALID_SUITES}")
        return s
    if coerce_type == "date":
        return _parse_date(s)
    raise HTTPException(500, f"Unknown coercer: {coerce_type}")


async def _capm_update_impl(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Admin-only: update a CapM product record.

    Mounted at /operations/products/update/{product_id} in PR 1.

    Accepts partial updates — only fields that appear in the submitted form
    are modified. This supports inline cell-by-cell editing on the
    /operations/products page while remaining compatible with full-form
    submissions.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from webapp.models import CapMProduct

    form = await request.form()
    submitted = {k: v for k, v in form.items() if k in _CAPM_UPDATE_FIELDS}
    if not submitted:
        raise HTTPException(400, "No valid fields submitted")

    p = db.query(CapMProduct).filter(CapMProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")

    for form_key, raw_val in submitted.items():
        attr, coercer = _CAPM_UPDATE_FIELDS[form_key]
        setattr(p, attr, _coerce_capm(coercer, raw_val if isinstance(raw_val, str) else ""))

    p.updated_at = datetime.utcnow()
    db.commit()

    # Inline fetch() call: return JSON rather than redirect.
    if len(submitted) <= 2:
        return {"ok": True, "updated": list(submitted.keys())}

    # Full-form submission (legacy): redirect with filter params preserved.
    suite_param = ""
    if "suite_source" in submitted and submitted["suite_source"]:
        suite_param = f"&suite={submitted['suite_source']}"
    return RedirectResponse(url=f"/operations/products/?msg=updated{suite_param}", status_code=302)


# ---------------------------------------------------------------------------
# Phase 1 legacy redirects (old URL → new canonical URL).
# GET → 301 (permanent). POST → 307 (preserve method).
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def capm_index_redirect():
    return RedirectResponse("/operations/products", status_code=301)


@router.get("/export.csv")
def capm_export_redirect():
    return RedirectResponse("/operations/products/export.csv", status_code=301)


@router.post("/update/{product_id}")
def capm_update_redirect(product_id: int):
    return RedirectResponse(f"/operations/products/update/{product_id}", status_code=307)

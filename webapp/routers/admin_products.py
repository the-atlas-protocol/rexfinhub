"""Product Pipeline admin routes.

Dedicated admin section for managing the REX product pipeline (rex_products table).
Separate from admin.py to avoid file contention.

Features:
  - Editable table with extended filters (status, suite, search, urgency, date range)
  - CSV export
  - Full edit modal covering manual fields (seed_date, target_listing_date, etc)
  - Prospectus links auto-updated from SEC via _run_sync_from_sec() (called from this route)
  - Add new product
  - Delete (soft) — sets status=Delisted
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.services.admin_auth import load_admin_password

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/products", tags=["admin-products"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

ADMIN_PASSWORD = load_admin_password()
# Single source of truth in pipeline_calendar.py (15 lifecycle statuses, additive
# expansion 2026-05-11 added Counsel Review/Approved/Withdrawn, Pending Board,
# Board Approved, Not Approved by Board, Filed (485A)/(485B), Effective).
from webapp.routers.pipeline_calendar import VALID_STATUSES
VALID_SUITES = ["T-REX", "Premium Income", "Growth & Income", "IncomeMax", "Crypto", "Thematic", "Autocallable", "T-Bill", "MicroSectors ETN"]


def _check_auth(request: Request) -> bool:
    return (
        request.cookies.get("admin_auth") == ADMIN_PASSWORD
        or request.session.get("is_admin") is True
    )


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _build_query(db: Session, status, suite, q, urgency, date_from, date_to):
    """Build the filtered query for products."""
    from webapp.models import RexProduct

    query = db.query(RexProduct)

    if status:
        query = query.filter(RexProduct.status == status)
    if suite:
        query = query.filter(RexProduct.product_suite == suite)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            RexProduct.name.ilike(like),
            RexProduct.ticker.ilike(like),
            RexProduct.underlier.ilike(like),
            RexProduct.trust.ilike(like),
        ))

    today = date.today()
    if urgency == "urgent":
        # Effective in next 14 days
        cutoff = today + timedelta(days=14)
        query = query.filter(
            RexProduct.status.in_(["Filed", "Awaiting Effective"]),
            RexProduct.estimated_effective_date.between(today, cutoff),
        )
    elif urgency == "upcoming":
        cutoff = today + timedelta(days=60)
        query = query.filter(
            RexProduct.status.in_(["Filed", "Awaiting Effective"]),
            RexProduct.estimated_effective_date.between(today, cutoff),
        )
    elif urgency == "overdue":
        query = query.filter(
            RexProduct.status != "Listed",
            RexProduct.status != "Delisted",
            RexProduct.target_listing_date.isnot(None),
            RexProduct.target_listing_date < today,
        )
    elif urgency == "recent_filings":
        recent_cutoff = today - timedelta(days=14)
        query = query.filter(RexProduct.initial_filing_date >= recent_cutoff)
    elif urgency == "recent_launches":
        recent_cutoff = today - timedelta(days=30)
        query = query.filter(RexProduct.official_listed_date >= recent_cutoff)

    if date_from:
        query = query.filter(
            or_(
                RexProduct.initial_filing_date >= date_from,
                RexProduct.estimated_effective_date >= date_from,
                RexProduct.official_listed_date >= date_from,
            )
        )
    if date_to:
        query = query.filter(
            or_(
                RexProduct.initial_filing_date <= date_to,
                RexProduct.estimated_effective_date <= date_to,
                RexProduct.official_listed_date <= date_to,
            )
        )

    return query


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def products_page(
    request: Request,
    status: str | None = None,
    suite: str | None = None,
    q: str | None = None,
    urgency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
):
    """Product pipeline management page."""
    if not _check_auth(request):
        return RedirectResponse(url="/admin/", status_code=302)

    from webapp.models import RexProduct

    date_from_d = _parse_date(date_from)
    date_to_d = _parse_date(date_to)

    query = _build_query(db, status, suite, q, urgency, date_from_d, date_to_d)
    products = query.order_by(
        RexProduct.status.asc(),
        RexProduct.estimated_effective_date.asc().nulls_last(),
        RexProduct.name.asc(),
    ).limit(1000).all()

    # Summary stats (unfiltered)
    total = db.query(RexProduct).count()
    status_counts = dict(db.query(RexProduct.status, func.count(RexProduct.id)).group_by(RexProduct.status).all())
    suite_counts = dict(db.query(RexProduct.product_suite, func.count(RexProduct.id)).group_by(RexProduct.product_suite).all())

    today = date.today()
    urgency_counts = {
        "urgent": db.query(RexProduct)
            .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
            .filter(RexProduct.estimated_effective_date.between(today, today + timedelta(days=14)))
            .count(),
        "overdue": db.query(RexProduct)
            .filter(RexProduct.status.notin_(["Listed", "Delisted"]))
            .filter(RexProduct.target_listing_date.isnot(None))
            .filter(RexProduct.target_listing_date < today)
            .count(),
        "recent_filings": db.query(RexProduct)
            .filter(RexProduct.initial_filing_date >= today - timedelta(days=14))
            .count(),
    }

    msg = request.query_params.get("msg", "")

    return templates.TemplateResponse("admin_products.html", {
        "request": request,
        "products": products,
        "total": total,
        "filtered_count": len(products),
        "status_counts": status_counts,
        "suite_counts": suite_counts,
        "urgency_counts": urgency_counts,
        "valid_statuses": VALID_STATUSES,
        "valid_suites": VALID_SUITES,
        "filter_status": status or "",
        "filter_suite": suite or "",
        "filter_q": q or "",
        "filter_urgency": urgency or "",
        "filter_date_from": date_from or "",
        "filter_date_to": date_to or "",
        "msg": msg,
        "today": today,
    })


@router.get("/export.csv")
def export_csv(
    request: Request,
    status: str | None = None,
    suite: str | None = None,
    q: str | None = None,
    urgency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
):
    """Export the filtered product list as CSV."""
    if not _check_auth(request):
        return RedirectResponse(url="/admin/", status_code=302)

    date_from_d = _parse_date(date_from)
    date_to_d = _parse_date(date_to)

    query = _build_query(db, status, suite, q, urgency, date_from_d, date_to_d)
    products = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Ticker", "Name", "Suite", "Status", "Underlier", "Direction",
        "Trust", "Initial Filing Date", "Estimated Effective Date",
        "Target Listing Date", "Seed Date", "Official Listed Date",
        "Latest Form", "Latest Prospectus Link", "CIK", "Series ID",
        "Class Contract ID", "LMM", "Exchange", "Mgt Fee",
        "Tracking Index", "Fund Admin", "CU Size", "Starting NAV", "Notes",
    ])
    for p in products:
        writer.writerow([
            p.ticker or "",
            p.name or "",
            p.product_suite or "",
            p.status or "",
            p.underlier or "",
            p.direction or "",
            p.trust or "",
            p.initial_filing_date.isoformat() if p.initial_filing_date else "",
            p.estimated_effective_date.isoformat() if p.estimated_effective_date else "",
            p.target_listing_date.isoformat() if p.target_listing_date else "",
            p.seed_date.isoformat() if p.seed_date else "",
            p.official_listed_date.isoformat() if p.official_listed_date else "",
            p.latest_form or "",
            p.latest_prospectus_link or "",
            p.cik or "",
            p.series_id or "",
            p.class_contract_id or "",
            p.lmm or "",
            p.exchange or "",
            p.mgt_fee if p.mgt_fee is not None else "",
            p.tracking_index or "",
            p.fund_admin or "",
            p.cu_size if p.cu_size is not None else "",
            p.starting_nav if p.starting_nav is not None else "",
            p.notes or "",
        ])

    output.seek(0)
    filename = f"rex_pipeline_{date.today().isoformat()}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# Whitelist of fields that can be updated via /update/{id}.
# Maps form field name -> (RexProduct attribute, type_coercer).
# Only fields in this map are writable — anything else is silently ignored.
_REX_UPDATE_FIELDS = {
    "name":                     ("name",                     "str_required"),
    "status":                   ("status",                   "status"),
    "product_suite":            ("product_suite",            "suite"),
    "ticker":                   ("ticker",                   "str_or_none"),
    "underlier":                ("underlier",                "str_or_none"),
    "direction":                ("direction",                "str_or_none"),
    "trust":                    ("trust",                    "str_or_none"),
    "initial_filing_date":      ("initial_filing_date",      "date"),
    "estimated_effective_date": ("estimated_effective_date", "date"),
    "target_listing_date":      ("target_listing_date",      "date"),
    "seed_date":                ("seed_date",                "date"),
    "official_listed_date":     ("official_listed_date",     "date"),
    "mgt_fee":                  ("mgt_fee",                  "float_or_none"),
    "lmm":                      ("lmm",                      "str_or_none"),
    "exchange":                 ("exchange",                 "str_or_none"),
    "notes":                    ("notes",                    "str_or_none"),
}


def _coerce(coerce_type: str, raw: str):
    """Coerce a raw form string into the target Python value."""
    s = (raw or "").strip()
    if coerce_type == "str_required":
        if not s:
            raise HTTPException(400, "Value cannot be empty")
        return s
    if coerce_type == "str_or_none":
        return s or None
    if coerce_type == "status":
        if s not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status. Valid: {VALID_STATUSES}")
        return s
    if coerce_type == "suite":
        if s not in VALID_SUITES:
            raise HTTPException(400, f"Invalid suite. Valid: {VALID_SUITES}")
        return s
    if coerce_type == "date":
        return _parse_date(s)
    if coerce_type == "float_or_none":
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            raise HTTPException(400, f"Invalid numeric value: {s!r}")
    raise HTTPException(500, f"Unknown coercer: {coerce_type}")


@router.post("/update/{product_id}")
async def update_product(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update a product record.

    Accepts partial updates — only fields present in the form are modified.
    This supports inline cell-by-cell editing on the pipeline-products page
    while remaining compatible with full-form submissions.
    """
    if not _check_auth(request):
        # Inline fetch() clients want a clean 403, not a redirect.
        if request.headers.get("accept", "").startswith("application/json") or \
           request.headers.get("x-requested-with") == "fetch":
            raise HTTPException(403, "Admin access required")
        return RedirectResponse(url="/admin/", status_code=302)

    from webapp.models import RexProduct

    form = await request.form()
    submitted = {k: v for k, v in form.items() if k in _REX_UPDATE_FIELDS}
    if not submitted:
        raise HTTPException(400, "No valid fields submitted")

    p = db.query(RexProduct).filter(RexProduct.id == product_id).first()
    if not p:
        raise HTTPException(404, "Product not found")

    for form_key, raw_val in submitted.items():
        attr, coercer = _REX_UPDATE_FIELDS[form_key]
        setattr(p, attr, _coerce(coercer, raw_val if isinstance(raw_val, str) else ""))

    p.updated_at = datetime.utcnow()
    db.commit()

    # If this looks like an inline fetch() call (single field, not a full form),
    # return a tiny 200 instead of a redirect.
    if len(submitted) <= 2:
        return {"ok": True, "updated": list(submitted.keys())}

    return RedirectResponse(url="/admin/products/?msg=updated", status_code=302)


@router.post("/add")
def add_product(
    request: Request,
    name: str = Form(...),
    product_suite: str = Form(...),
    status: str = Form("Research"),
    ticker: str = Form(""),
    underlier: str = Form(""),
    direction: str = Form(""),
    trust: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """Add a new product."""
    if not _check_auth(request):
        return RedirectResponse(url="/admin/", status_code=302)

    from webapp.models import RexProduct

    if status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {VALID_STATUSES}")
    if product_suite not in VALID_SUITES:
        raise HTTPException(400, f"Invalid suite. Valid: {VALID_SUITES}")

    p = RexProduct(
        name=name,
        product_suite=product_suite,
        status=status,
        ticker=ticker or None,
        underlier=underlier or None,
        direction=direction or None,
        trust=trust or None,
        notes=notes or None,
    )
    db.add(p)
    db.commit()
    return RedirectResponse(url="/admin/products/?msg=added", status_code=302)


@router.post("/sync-from-sec")
def sync_from_sec(request: Request, db: Session = Depends(get_db)):
    """Sync REX product data from SEC pipeline (uses rex_product_sync service)."""
    if not _check_auth(request):
        return RedirectResponse(url="/admin/", status_code=302)

    try:
        from webapp.services.rex_product_sync import sync_rex_products_from_sec
        result = sync_rex_products_from_sec(db)
        added = result.get("added", 0)
        updated = result.get("updated", 0)
        return RedirectResponse(
            url=f"/admin/products/?msg=synced_added_{added}_updated_{updated}",
            status_code=302,
        )
    except Exception as e:
        log.error("sync_from_sec failed: %s", e, exc_info=True)
        return RedirectResponse(url="/admin/products/?msg=sync_failed", status_code=302)

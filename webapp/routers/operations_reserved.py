"""REX Reserved Symbols — admin-editable registry of REX's own ticker reservations.

URL: /operations/reserved-symbols

Distinct from /tools/tickers which scans the full CBOE universe. This is REX's
CURATED list (~282 symbols) with our internal rationale + suite tagging.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import ReservedSymbol

log = logging.getLogger(__name__)
router = APIRouter(prefix="/operations/reserved-symbols", tags=["operations-reserved"])
templates = Jinja2Templates(directory="webapp/templates")

VALID_STATUSES = ["Reserved", "Active", "Expired", "Released"]


def _safe_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def reserved_symbols_index(
    request: Request,
    db: Session = Depends(get_db),
    exchange: str = Query(default=""),
    suite: str = Query(default=""),
    status: str = Query(default=""),
    q: str = Query(default=""),
):
    """Reserved Symbols index — table view, filterable + admin-editable."""
    is_admin = bool(request.session.get("is_admin"))
    today = date.today()

    qry = db.query(ReservedSymbol)
    if exchange:
        qry = qry.filter(ReservedSymbol.exchange == exchange)
    if suite:
        qry = qry.filter(ReservedSymbol.suite == suite)
    if status:
        qry = qry.filter(ReservedSymbol.status == status)
    if q:
        like = f"%{q}%"
        qry = qry.filter(
            (ReservedSymbol.symbol.ilike(like)) |
            (ReservedSymbol.rationale.ilike(like))
        )

    rows = qry.order_by(ReservedSymbol.end_date.asc().nulls_last(), ReservedSymbol.symbol).all()

    # KPI counts
    counts_q = db.execute(
        select(ReservedSymbol.status, func.count(ReservedSymbol.id)).group_by(ReservedSymbol.status)
    ).all()
    counts_by_status = {s or "(none)": n for s, n in counts_q}

    exchanges = [r[0] for r in db.execute(
        select(ReservedSymbol.exchange).distinct().where(ReservedSymbol.exchange.isnot(None))
    ).all()]
    suites = [r[0] for r in db.execute(
        select(ReservedSymbol.suite).distinct().where(ReservedSymbol.suite.isnot(None))
    ).all() if r[0]]

    # Decorate rows with days_until_expiry
    decorated = []
    for r in rows:
        days_left = (r.end_date - today).days if r.end_date else None
        urgency = None
        if days_left is not None:
            if days_left < 0:
                urgency = "expired"
            elif days_left <= 30:
                urgency = "soon"
            elif days_left <= 90:
                urgency = "warning"
            else:
                urgency = "ok"
        decorated.append({
            "row": r,
            "days_left": days_left,
            "urgency": urgency,
        })

    total = db.query(ReservedSymbol).count()
    n_reserved = counts_by_status.get("Reserved", 0)
    n_expired = sum(1 for d in decorated if d["urgency"] == "expired")
    n_soon = sum(1 for d in decorated if d["urgency"] == "soon")

    return templates.TemplateResponse("operations_reserved_symbols.html", {
        "request": request,
        "rows": decorated,
        "total": total,
        "filtered_count": len(rows),
        "n_reserved": n_reserved,
        "n_expired": n_expired,
        "n_soon": n_soon,
        "counts_by_status": counts_by_status,
        "exchanges": sorted(exchanges),
        "suites": sorted(suites),
        "valid_statuses": VALID_STATUSES,
        "filter_exchange": exchange,
        "filter_suite": suite,
        "filter_status": status,
        "filter_q": q,
        "is_admin": is_admin,
        "today": today,
    })


@router.post("/update/{row_id}")
def reserved_symbol_update(row_id: int, request: Request, db: Session = Depends(get_db),
                           payload: dict = Body(...)):
    """Admin-only: update a single reserved-symbol row via inline edit."""
    if not request.session.get("is_admin"):
        raise HTTPException(403, "Admin only.")
    row = db.get(ReservedSymbol, row_id)
    if not row:
        raise HTTPException(404, "Reserved symbol not found.")

    EDITABLE = {"exchange", "symbol", "end_date", "status", "rationale", "suite",
                "linked_filing_id", "linked_product_id", "notes"}
    DATE_FIELDS = {"end_date"}
    INT_FIELDS = {"linked_filing_id", "linked_product_id"}

    changed = []
    for field, value in payload.items():
        if field not in EDITABLE:
            continue
        if field in DATE_FIELDS:
            new_val = _safe_date(value)
        elif field in INT_FIELDS:
            try:
                new_val = int(value) if value else None
            except (TypeError, ValueError):
                new_val = None
        elif field == "symbol":
            new_val = str(value).strip().upper() if value else None
        else:
            new_val = str(value).strip() if value else None

        old_val = getattr(row, field)
        if old_val != new_val:
            setattr(row, field, new_val)
            changed.append(field)

    if changed:
        row.updated_at = datetime.utcnow()
        db.commit()

    return JSONResponse({"ok": True, "changed": changed, "row_id": row_id})


@router.post("/add")
def reserved_symbol_add(request: Request, db: Session = Depends(get_db),
                        exchange: str = Form(...),
                        symbol: str = Form(...),
                        end_date: str | None = Form(None),
                        status: str | None = Form("Reserved"),
                        rationale: str | None = Form(None),
                        suite: str | None = Form(None)):
    """Admin-only: add a new reserved symbol."""
    if not request.session.get("is_admin"):
        raise HTTPException(403, "Admin only.")
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(400, "Symbol required.")
    existing = db.execute(
        select(ReservedSymbol).where(ReservedSymbol.exchange == exchange).where(ReservedSymbol.symbol == sym)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Already exists: {exchange}/{sym}")
    row = ReservedSymbol(
        exchange=exchange.strip() if exchange else None,
        symbol=sym,
        end_date=_safe_date(end_date),
        status=status.strip() if status else "Reserved",
        rationale=rationale.strip() if rationale else None,
        suite=suite.strip() if suite else None,
    )
    db.add(row)
    db.commit()
    return JSONResponse({"ok": True, "row_id": row.id})


@router.post("/delete/{row_id}")
def reserved_symbol_delete(row_id: int, request: Request, db: Session = Depends(get_db)):
    """Admin-only: delete a reserved symbol (e.g. after release)."""
    if not request.session.get("is_admin"):
        raise HTTPException(403, "Admin only.")
    row = db.get(ReservedSymbol, row_id)
    if not row:
        raise HTTPException(404, "Not found.")
    db.delete(row)
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/export.csv")
def reserved_symbols_export(db: Session = Depends(get_db)):
    rows = db.query(ReservedSymbol).order_by(ReservedSymbol.exchange, ReservedSymbol.symbol).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Exchange", "Symbol", "End Date", "Status", "Rationale", "Suite",
                "Linked Filing ID", "Linked Product ID", "Notes", "Updated At"])
    for r in rows:
        w.writerow([
            r.exchange or "", r.symbol or "",
            r.end_date.isoformat() if r.end_date else "",
            r.status or "", r.rationale or "", r.suite or "",
            r.linked_filing_id or "", r.linked_product_id or "",
            r.notes or "",
            r.updated_at.isoformat() if r.updated_at else "",
        ])
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="rex_reserved_symbols.csv"'},
    )

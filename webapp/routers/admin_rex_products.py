"""Admin endpoint for editing rex_products rows from the /operations/pipeline page.

Mirrors webapp/routers/capm.py:_capm_update_impl for the parallel rex_products
table. The pipeline page's inline cell-edit + status-dropdown JS POSTs to
``/admin/rex-products/update/{id}`` (this module). Updates:

- Whitelist-validated single-field or multi-field writes against RexProduct.
- Audit-logged to capm_audit_log so the same admin trail covers both tables.
- ``manually_edited_fields`` (JSON list of column names) gets the edited
  attribute name appended, so the daily classifier + bloomberg-chain sweeps
  know to skip this column on this row.
- Status changes additionally append a row to ``rex_product_status_history``
  for the lifecycle timeline.

The endpoint returns JSON for single-field inline edits (1-2 fields submitted)
and a 302 redirect for full-form submissions.

Mounted at ``/admin/rex-products/*`` in webapp/main.py.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

router = APIRouter(prefix="/admin/rex-products", tags=["admin-rex-products"])


# Whitelist of fields that the endpoint will write.
# Maps form field name -> (RexProduct attribute, type_coercer).
# Anything not in this map is silently ignored.
_REX_UPDATE_FIELDS: dict[str, tuple[str, str]] = {
    "name":                     ("name", "str_required"),
    "trust":                    ("trust", "str_or_none"),
    "product_suite":            ("product_suite", "str_or_none"),
    "status":                   ("status", "status_or_none"),
    "ticker":                   ("ticker", "str_or_none"),
    "underlier":                ("underlier", "str_or_none"),
    "direction":                ("direction", "str_or_none"),
    "initial_filing_date":      ("initial_filing_date", "date_or_none"),
    "estimated_effective_date": ("estimated_effective_date", "date_or_none"),
    "target_listing_date":      ("target_listing_date", "date_or_none"),
    "seed_date":                ("seed_date", "date_or_none"),
    "official_listed_date":     ("official_listed_date", "date_or_none"),
    "latest_form":              ("latest_form", "str_or_none"),
    "latest_prospectus_link":   ("latest_prospectus_link", "str_or_none"),
    "cik":                      ("cik", "str_or_none"),
    "series_id":                ("series_id", "str_or_none"),
    "class_contract_id":        ("class_contract_id", "str_or_none"),
    "lmm":                      ("lmm", "str_or_none"),
    "exchange":                 ("exchange", "str_or_none"),
    "mgt_fee":                  ("mgt_fee", "float_or_none"),
    "tracking_index":           ("tracking_index", "str_or_none"),
    "fund_admin":               ("fund_admin", "str_or_none"),
    "cu_size":                  ("cu_size", "int_or_none"),
    "starting_nav":             ("starting_nav", "float_or_none"),
    "notes":                    ("notes", "str_or_none"),
    "competitors":              ("competitors", "str_or_none"),
}

_VALID_STATUSES = {
    "Under Consideration", "Target List", "Filed", "Effective", "Listed", "Delisted",
}


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        raise HTTPException(400, f"Invalid date: {s!r} (expected YYYY-MM-DD)")


def _coerce(coerce_type: str, raw: str):
    s = (raw or "").strip()
    if coerce_type == "str_required":
        if not s:
            raise HTTPException(400, "Value cannot be empty")
        return s
    if coerce_type == "str_or_none":
        return s or None
    if coerce_type == "status_or_none":
        if not s:
            return None
        if s not in _VALID_STATUSES:
            raise HTTPException(400, f"Invalid status: {s!r}")
        return s
    if coerce_type == "date_or_none":
        return _parse_date(s)
    if coerce_type == "int_or_none":
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            raise HTTPException(400, f"Invalid int: {s!r}")
    if coerce_type == "float_or_none":
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            raise HTTPException(400, f"Invalid float: {s!r}")
    raise HTTPException(500, f"Unknown coercer: {coerce_type}")


def _stringify(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def _parse_overrides(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _audit_log(
    db: Session,
    *,
    action: str,
    row_id: int,
    field_name: str,
    old_value: str | None,
    new_value: str | None,
    row_label: str,
    changed_by: str,
) -> None:
    from webapp.models import CapMAuditLog
    db.add(CapMAuditLog(
        action=action,
        table_name="rex_products",
        row_id=row_id,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        row_label=row_label,
        changed_by=changed_by,
        created_at=datetime.utcnow(),
    ))


def _status_history(
    db: Session,
    *,
    rex_product_id: int,
    old_status: str | None,
    new_status: str | None,
    changed_by: str,
    notes: str | None = None,
) -> None:
    from webapp.models import RexProductStatusHistory
    db.add(RexProductStatusHistory(
        rex_product_id=rex_product_id,
        old_status=old_status,
        new_status=new_status,
        changed_at=datetime.utcnow(),
        changed_by=changed_by,
        notes=notes,
    ))


@router.post("/update/{product_id}")
async def rex_product_update(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Admin-only: update a rex_products row.

    Partial updates supported — only fields submitted in the form are written.
    Returns JSON for 1-2 field submissions (inline edits), 302 redirect for
    larger submissions (full-form).
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from webapp.models import RexProduct

    form = await request.form()
    submitted = {k: v for k, v in form.items() if k in _REX_UPDATE_FIELDS}
    if not submitted:
        raise HTTPException(400, "No valid fields submitted")

    p = db.query(RexProduct).filter(RexProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="REX product not found")

    changed_by = request.session.get("user") or "admin"
    overrides = set(_parse_overrides(p.manually_edited_fields))
    row_label = p.ticker or (p.name[:40] if p.name else f"#{p.id}")

    actually_changed: list[str] = []
    status_change: tuple[str | None, str | None] | None = None
    for form_key, raw_val in submitted.items():
        attr, coercer = _REX_UPDATE_FIELDS[form_key]
        old_val = getattr(p, attr, None)
        new_val = _coerce(coercer, raw_val if isinstance(raw_val, str) else "")
        if old_val == new_val:
            continue
        setattr(p, attr, new_val)
        actually_changed.append(form_key)
        overrides.add(attr)
        _audit_log(
            db,
            action="UPDATE",
            row_id=p.id,
            field_name=attr,
            old_value=_stringify(old_val),
            new_value=_stringify(new_val),
            row_label=row_label,
            changed_by=changed_by,
        )
        if attr == "status":
            status_change = (
                _stringify(old_val),
                _stringify(new_val),
            )

    if actually_changed:
        p.manually_edited_fields = json.dumps(sorted(overrides))
        p.updated_at = datetime.utcnow()
        if status_change is not None:
            _status_history(
                db,
                rex_product_id=p.id,
                old_status=status_change[0],
                new_status=status_change[1],
                changed_by=changed_by,
                notes="admin inline edit",
            )
        db.commit()

    if len(submitted) <= 2:
        return {
            "ok": True,
            "updated": list(submitted.keys()),
            "changed": actually_changed,
            "overrides": sorted(overrides),
        }

    return RedirectResponse(url="/operations/pipeline?msg=updated", status_code=302)

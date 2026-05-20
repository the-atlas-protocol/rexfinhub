"""Phase 6 Stage 4 (ADR 0009): admin endpoint for classification overrides.

POST /admin/classify-override/{canonical_id} — write a single override row
into classification_override via webapp/services/classification_resolver.py.

GET  /admin/classify-overrides/{canonical_id} — read all override rows for
a single product (used by the future inline-edit UI).

Writes are audit-logged to capm_audit_log with table_name='classification_override'
so the same audit-history view surfaces them.

This is the WRITE half of Phase 6 Stage 4. The READ half (inline-edit UI on
/operations/products) will hook into these endpoints via HTMX in a follow-up.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

router = APIRouter(prefix="/admin", tags=["admin-classify"])


# Whitelist of override-able field names. Matches the classification_override
# field_name enum from ADR 0009. New fields require explicit addition here
# to avoid accidentally creating override rows for typos.
_FIELD_NAMES = {
    "etp_category", "issuer_display", "is_rex",
    "primary_strategy", "asset_class", "sub_strategy",
    "mechanism", "direction", "leverage_ratio",
    "reset_period", "cap_pct", "buffer_pct", "barrier_pct",
    "concentration", "region", "duration_bucket",
    "credit_quality", "underlier_id", "expense_ratio_override",
}


def _is_admin(request: Request) -> bool:
    return request.session.get("is_admin", False)


def _audit_log(db: Session, *, canonical_id: str, field: str,
               old_value: Any, new_value: Any, reason: str, changed_by: str):
    from webapp.models import CapMAuditLog
    db.add(CapMAuditLog(
        action="UPDATE" if old_value is not None else "ADD",
        table_name="classification_override",
        row_id=None,  # composite PK; record via row_label instead
        field_name=field,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        row_label=f"{canonical_id[:8]}/{field}",
        changed_by=f"admin:{changed_by}",
        changed_at=datetime.utcnow(),
    ))


@router.get("/classify-overrides/{canonical_id}")
def get_overrides(
    canonical_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """List all classification_override rows for a canonical_id."""
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required")

    conn = db.connection().connection
    rows = conn.execute("""
        SELECT field_name, value, set_by, set_at, reason
        FROM classification_override
        WHERE canonical_id = ?
        ORDER BY field_name
    """, (canonical_id,)).fetchall()
    return {
        "canonical_id": canonical_id,
        "overrides": [
            {"field_name": r[0], "value": r[1], "set_by": r[2],
             "set_at": r[3], "reason": r[4]}
            for r in rows
        ],
    }


@router.post("/classify-override/{canonical_id}")
async def set_override(
    canonical_id: str,
    request: Request,
    field_name: str = Form(...),
    value: str = Form(""),
    reason: str = Form(""),
    blacklist: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Upsert a classification_override row.

    Args:
        field_name: which override field — must be in _FIELD_NAMES whitelist
        value: the override value (string). Ignored when blacklist=True.
        reason: free-text justification (admin-supplied; surfaced in audit)
        blacklist: if True, sets value=NULL = "explicitly do not classify this field"

    Audit log records the (canonical_id, field_name) pair + old/new value.
    """
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required")

    field_name = (field_name or "").strip()
    if field_name not in _FIELD_NAMES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown field_name {field_name!r}. "
                                   f"Allowed: {sorted(_FIELD_NAMES)}")

    # Validate canonical_id exists
    conn = db.connection().connection
    pm_row = conn.execute(
        "SELECT canonical_id FROM product_master WHERE canonical_id = ?",
        (canonical_id,),
    ).fetchone()
    if not pm_row:
        raise HTTPException(status_code=404,
                            detail=f"Unknown canonical_id {canonical_id!r}")

    # Determine the value to write
    write_value = None if blacklist else (value.strip() or None)

    # Existing value (for audit log diff)
    prev_row = conn.execute(
        "SELECT value FROM classification_override "
        "WHERE canonical_id = ? AND field_name = ?",
        (canonical_id, field_name),
    ).fetchone()
    old_value = prev_row[0] if prev_row else None

    if old_value == write_value:
        # No-op; idempotent return
        return {"ok": True, "canonical_id": canonical_id,
                "field_name": field_name, "value": write_value, "changed": False}

    # Upsert + audit
    from webapp.services.classification_resolver import set_override as svc_set
    svc_set(canonical_id, field_name, write_value,
            set_by=f"admin:{request.session.get('user', 'admin')}",
            reason=reason, db=conn)

    _audit_log(db, canonical_id=canonical_id, field=field_name,
               old_value=old_value, new_value=write_value,
               reason=reason, changed_by=request.session.get("user", "admin"))
    db.commit()

    return {"ok": True, "canonical_id": canonical_id,
            "field_name": field_name, "value": write_value, "changed": True}


@router.delete("/classify-override/{canonical_id}")
async def delete_override(
    canonical_id: str,
    request: Request,
    field_name: str,
    db: Session = Depends(get_db),
):
    """Remove an override row (revert to Bloomberg/auto-classifier value)."""
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required")

    if field_name not in _FIELD_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown field_name {field_name!r}")

    conn = db.connection().connection
    prev = conn.execute(
        "SELECT value FROM classification_override "
        "WHERE canonical_id = ? AND field_name = ?",
        (canonical_id, field_name),
    ).fetchone()
    if not prev:
        return JSONResponse({"ok": True, "deleted": False, "note": "no override existed"})

    conn.execute(
        "DELETE FROM classification_override "
        "WHERE canonical_id = ? AND field_name = ?",
        (canonical_id, field_name),
    )
    _audit_log(db, canonical_id=canonical_id, field=field_name,
               old_value=prev[0], new_value=None,
               reason="override deleted", changed_by=request.session.get("user", "admin"))
    db.commit()
    return {"ok": True, "deleted": True,
            "previous_value": prev[0]}

"""Phase 7B Stage 2 (ADR 0010): admin page exposing system_flags +
preflight_run + system_event tables.

Read-only dashboard at /admin/system-state. Operator uses this to verify
flag values, inspect recent preflight runs, and audit system_event history.

Each table's last 20 rows. Flag-set actions are wired to the system_flags
helper via POST /admin/system-flag/{flag_name}.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin-system-state"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _is_admin(request: Request) -> bool:
    return request.session.get("is_admin", False)


@router.get("/system-state")
def system_state_page(request: Request, db: Session = Depends(get_db)):
    if not _is_admin(request):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})

    conn = db.connection().connection

    # Flags + their file-fallback diagnostic
    try:
        from webapp.services.system_flags import list_flags
        flags_diag = list_flags()
    except ImportError:
        flags_diag = {}

    flags_rows = conn.execute("""
        SELECT flag_name, is_set, set_at, set_by, notes
        FROM system_flags ORDER BY flag_name
    """).fetchall()

    preflight_rows = conn.execute("""
        SELECT id, ran_at, result_status, decision, decision_token
        FROM preflight_run
        ORDER BY ran_at DESC LIMIT 20
    """).fetchall()

    event_rows = conn.execute("""
        SELECT id, event_type, event_at, details
        FROM system_event
        ORDER BY event_at DESC LIMIT 30
    """).fetchall()

    event_counts = conn.execute("""
        SELECT event_type, COUNT(*) AS n
        FROM system_event GROUP BY event_type ORDER BY n DESC
    """).fetchall()

    return templates.TemplateResponse("admin_system_state.html", {
        "request": request,
        "flags_rows": flags_rows,
        "flags_diag": flags_diag,
        "preflight_rows": preflight_rows,
        "event_rows": event_rows,
        "event_counts": event_counts,
    })


@router.post("/system-flag/{flag_name}")
def set_system_flag(
    flag_name: str,
    request: Request,
    value: str = Form("0"),
    notes: str = Form(""),
):
    """Toggle a system flag. value='1' or 'true' = set; anything else = clear."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.system_flags import set_flag, FLAG_FILES
    except ImportError:
        return RedirectResponse("/admin/system-state?err=helper_missing", status_code=303)
    if flag_name not in FLAG_FILES:
        return RedirectResponse(f"/admin/system-state?err=unknown_flag", status_code=303)
    bool_val = value.strip().lower() in ("1", "true", "on", "yes")
    user = request.session.get("user", "admin")
    try:
        set_flag(flag_name, bool_val, set_by=f"admin:{user}",
                 notes=notes or "")
    except Exception as e:
        log.error("set_flag %s failed: %s", flag_name, e)
        return RedirectResponse(f"/admin/system-state?err=set_failed", status_code=303)
    return RedirectResponse(f"/admin/system-state?ok={flag_name}", status_code=303)

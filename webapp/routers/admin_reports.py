"""Admin reports preview page.

One place to review the WIP reports (Intelligence Brief, Filing Candidates,
Product Pipeline) before approving them for production send. Renders the
HTML inline with a picker for which report to show.

Separate router file so we don't touch the main admin.py.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

ADMIN_PASSWORD = "ryu123"


def _check_auth(request: Request) -> bool:
    return (
        request.cookies.get("admin_auth") == ADMIN_PASSWORD
        or request.session.get("is_admin") is True
    )


REPORT_REGISTRY = {
    "intelligence": {
        "name": "Filing Intelligence Brief",
        "description": "Daily — action required, competitive races, effectives this week",
        "cadence": "Daily",
        "list_type": "intelligence",
        "builder": "etp_tracker.intelligence_brief:build_intelligence_brief",
        "needs_db": True,
    },
    "screener": {
        "name": "Filing Candidates",
        "description": "Weekly — top 5 filing picks from foundation_scorer",
        "cadence": "Weekly",
        "list_type": "screener",
        "builder": "screener.filing_screener_report:build_filing_screener_report",
        "needs_db": False,
    },
    "pipeline": {
        "name": "Product Pipeline",
        "description": "Monday — REX product lifecycle (Listed / Awaiting / Filed)",
        "cadence": "Monday",
        "list_type": "pipeline",
        "builder": "etp_tracker.product_status_report:build_product_status_report",
        "needs_db": True,
    },
}


def _render_report(report_key: str, db: Session) -> tuple[str, str | None]:
    """Call the report builder for the given key. Returns (html, error)."""
    meta = REPORT_REGISTRY.get(report_key)
    if not meta:
        return "", f"Unknown report: {report_key}"

    module_path, func_name = meta["builder"].split(":")
    try:
        import importlib
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)

        if meta["needs_db"]:
            html = func(db)
        else:
            html = func()
        return html, None
    except Exception as e:
        log.error("Report render failed for %s: %s", report_key, e, exc_info=True)
        return "", str(e)


@router.get("/preview", response_class=HTMLResponse)
def preview_landing(
    request: Request,
    report: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Landing page + inline preview of a selected report."""
    if not _check_auth(request):
        return RedirectResponse("/admin/", status_code=302)

    selected_html = None
    selected_error = None
    selected_meta = None
    if report and report in REPORT_REGISTRY:
        selected_meta = REPORT_REGISTRY[report]
        selected_html, selected_error = _render_report(report, db)

    return templates.TemplateResponse("admin_reports_preview.html", {
        "request": request,
        "reports": REPORT_REGISTRY,
        "selected_key": report,
        "selected_meta": selected_meta,
        "selected_html": selected_html,
        "selected_error": selected_error,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })


@router.get("/preview/{report_key}/raw", response_class=HTMLResponse)
def preview_raw(
    report_key: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Render a report directly with no admin wrapper. For iframe embedding."""
    if not _check_auth(request):
        return HTMLResponse("<h2>Unauthorized</h2>", status_code=401)

    html, error = _render_report(report_key, db)
    if error:
        return HTMLResponse(
            f"<h2>Report error</h2><pre style='color:#dc2626'>{error}</pre>",
            status_code=500,
        )
    return HTMLResponse(html)

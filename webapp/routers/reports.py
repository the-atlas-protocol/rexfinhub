"""Bloomberg weekly report pages: L&I, Covered Call, Single-Stock."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory="webapp/templates")


def _svc():
    from webapp.services import report_data
    return report_data


@router.get("/")
def reports_index():
    return RedirectResponse("/reports/li", status_code=302)


@router.get("/li")
def li_report(request: Request):
    try:
        svc = _svc()
        data = svc.get_li_report()
        return templates.TemplateResponse("reports/leveraged_inverse.html", {
            "request": request, "active_tab": "li", **data,
        })
    except Exception as e:
        log.error("L&I report error: %s", e, exc_info=True)
        return templates.TemplateResponse("reports/leveraged_inverse.html", {
            "request": request, "active_tab": "li",
            "available": False, "data_as_of": "", "error": str(e),
        })


@router.get("/cc")
def cc_report(request: Request):
    try:
        svc = _svc()
        data = svc.get_cc_report()
        return templates.TemplateResponse("reports/covered_call.html", {
            "request": request, "active_tab": "cc", **data,
        })
    except Exception as e:
        log.error("CC report error: %s", e, exc_info=True)
        return templates.TemplateResponse("reports/covered_call.html", {
            "request": request, "active_tab": "cc",
            "available": False, "data_as_of": "", "error": str(e),
        })


@router.get("/ss")
def ss_report(request: Request):
    try:
        svc = _svc()
        data = svc.get_ss_report()
        return templates.TemplateResponse("reports/single_stock.html", {
            "request": request, "active_tab": "ss", **data,
        })
    except Exception as e:
        log.error("SS report error: %s", e, exc_info=True)
        return templates.TemplateResponse("reports/single_stock.html", {
            "request": request, "active_tab": "ss",
            "available": False, "data_as_of": "", "error": str(e),
        })

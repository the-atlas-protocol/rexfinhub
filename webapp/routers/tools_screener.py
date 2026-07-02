"""Tools > Screener — the full live-fund landscape with a one-click L&I filter.

Reuses the complete, full-featured screener (column picker, presets, pagination,
scope/strategy/asset-class filters, drag-reorder, CSV) that already backs
/market/rex-performance, served here under Tools with an added prominent L&I
quick-filter. The fund data comes from /market/api/screener-data.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

router = APIRouter(prefix="/tools", tags=["tools-screener"])
templates = Jinja2Templates(directory="webapp/templates")


@router.get("/screener")
def screener(request: Request, db: Session = Depends(get_db)):
    from webapp.routers.market import _svc
    svc = _svc()
    return templates.TemplateResponse("market/rex_performance.html", {
        "request": request,
        "available": svc.data_available(db),
        "active_tab": "screener",
        "data_as_of": svc.get_data_as_of(db),
    })

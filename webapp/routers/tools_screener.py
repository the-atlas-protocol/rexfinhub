"""Tools > Screener — the full live-fund landscape with a one-click L&I filter.

The heavy lifting (the fund data) is served by the existing
``/market/api/screener-data`` endpoint; this route just renders the on-brand,
L&I-first screener shell. Replaces the /market/rex-performance experience and
lives under Tools.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/tools", tags=["tools-screener"])
templates = Jinja2Templates(directory="webapp/templates")


@router.get("/screener")
def screener(request: Request):
    return templates.TemplateResponse("tools/screener.html", {"request": request})

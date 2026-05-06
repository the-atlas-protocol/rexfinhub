"""Top-level Calendar pillar mounted at /calendar/.

Promoted from /market/calendar per IA decision (Fix 3).
/market/calendar remains active for backward compatibility.
Old /pipeline/ routes (pipeline_calendar.py) are unchanged — that is the
internal REX product pipeline view; this calendar is the ETP launch tracker.

301 redirect from /pipeline/calendar -> /calendar/ is intentionally NOT added
because /pipeline/ is a different page (internal product pipeline), not the
SEC-filing calendar.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from webapp.dependencies import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
def calendar_root(
    request: Request,
    db: Session = Depends(get_db),
    month: int = Query(default=None),
    year: int = Query(default=None),
    issuer: str = Query(default=""),
    primary_strategy: str = Query(default=""),
    sub_strategy: str = Query(default=""),
    status: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
):
    """Top-level ETP Launch Calendar pillar."""
    # Delegate directly to the market_advanced calendar_view function
    from webapp.routers.market_advanced import calendar_view
    return calendar_view(
        request=request,
        db=db,
        month=month,
        year=year,
        issuer=issuer,
        primary_strategy=primary_strategy,
        sub_strategy=sub_strategy,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )

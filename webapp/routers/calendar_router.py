"""Legacy /calendar/ redirect — Phase 1 of the v3 URL migration.

The universe-wide ETP calendar now lives at /tools/calendar (see
webapp.routers.tools_calendar). This router exists solely to 301
the old /calendar/ URL forward.

The REX-only pipeline calendar (board approvals, fiscal year ends,
distribution dates) lives at /operations/calendar — a separate
backend with separate data (see webapp.routers.pipeline_calendar /
future webapp.routers.operations).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
def _calendar_legacy_redirect():
    """301 /calendar/ -> /tools/calendar."""
    return RedirectResponse("/tools/calendar", status_code=301)

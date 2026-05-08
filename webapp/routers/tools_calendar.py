"""Universe-wide ETP launch + filing calendar.

Note: this is the public ETP calendar at /tools/calendar.
The REX-only pipeline calendar (with board approvals + fiscal year ends
+ distribution dates) lives at /operations/calendar — that's a different
backend with different data, handled by webapp.routers.operations.
"""
from fastapi import APIRouter

from webapp.routers.market_advanced import _calendar_view_impl

router = APIRouter(prefix="/tools/calendar", tags=["tools-calendar"])
router.add_api_route("", _calendar_view_impl, methods=["GET"])
router.add_api_route("/", _calendar_view_impl, methods=["GET"])

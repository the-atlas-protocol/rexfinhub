"""Tools — CBOE Symbol Reservation Tickers — Phase 1 of the v3 URL migration.

Imports handler implementation from webapp.routers.filings and
re-registers it under the new /tools/tickers URL.

URL map
-------
GET /tools/tickers  → _symbols_impl

Old /filings/symbols continues to work via a 301 redirect in
webapp.routers.filings.
"""
from fastapi import APIRouter

from webapp.routers.filings import _symbols_impl

router = APIRouter(prefix="/tools", tags=["tools-tickers"])
router.add_api_route("/tickers", _symbols_impl, methods=["GET"])

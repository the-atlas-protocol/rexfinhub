"""SEC ETP Intelligence — Phase 1 of the v3 URL migration.

Imports handler implementations from webapp.routers.filings and
re-registers them under the new /sec/etp/ prefix.

URL map
-------
GET /sec/etp/                              → _dashboard_impl
GET /sec/etp/filings                       → _filing_explorer_impl
GET /sec/etp/leverageandinverse            → _landscape_impl
GET /sec/etp/leverageandinverse/export     → _landscape_export_impl

Old /filings/dashboard, /filings/explorer, /filings/landscape, and
/filings/landscape/export URLs continue to work via 301 redirects in
webapp.routers.filings.
"""
from fastapi import APIRouter

from webapp.routers.filings import (
    _dashboard_impl,
    _filing_explorer_impl,
    _landscape_impl,
    _landscape_export_impl,
)

router = APIRouter(prefix="/sec/etp", tags=["sec-etp"])
router.add_api_route("/", _dashboard_impl, methods=["GET"])
router.add_api_route("/filings", _filing_explorer_impl, methods=["GET"])
router.add_api_route("/leverageandinverse", _landscape_impl, methods=["GET"])
router.add_api_route("/leverageandinverse/export", _landscape_export_impl, methods=["GET"])

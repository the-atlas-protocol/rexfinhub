"""REX Operations — Phase 1 of the v3 URL migration.

This is the new ``/operations/*`` pillar. It re-registers handler
implementations from two source routers under the canonical URLs:

Products section (sourced from webapp.routers.capm):
    GET  /operations/products                        — index page
    GET  /operations/products/export.csv             — CSV export
    POST /operations/products/update/{product_id}    — admin update

Pipeline section (sourced from webapp.routers.pipeline_calendar):
    GET  /operations/pipeline                        — KPI + table view

Calendar section (sourced from webapp.routers.pipeline_calendar):
    GET  /operations/calendar                        — current month
    GET  /operations/calendar/summary                — legacy summary alias
    GET  /operations/calendar/distributions/export.csv  — distributions CSV
    GET  /operations/calendar/{year}/{month}         — specific month

The old /capm/* and /pipeline/* paths are preserved as 301/307 redirects
in their original modules until PR 5 deletes them entirely.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from webapp.routers.capm import (
    _capm_export_impl,
    _capm_index_impl,
    _capm_update_impl,
)
from webapp.routers.pipeline_calendar import (
    _pipeline_distributions_impl,
    _pipeline_month_impl,
    _pipeline_products_impl,
    _pipeline_root_impl,
    _pipeline_summary_impl,
)

router = APIRouter(tags=["operations"])

# Products section ----------------------------------------------------------
router.add_api_route(
    "/operations/products",
    _capm_index_impl,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/operations/products/export.csv",
    _capm_export_impl,
    methods=["GET"],
)
router.add_api_route(
    "/operations/products/update/{product_id}",
    _capm_update_impl,
    methods=["POST"],
)

# Pipeline section ----------------------------------------------------------
router.add_api_route(
    "/operations/pipeline",
    _pipeline_products_impl,
    methods=["GET"],
    response_class=HTMLResponse,
)

# Calendar section ----------------------------------------------------------
router.add_api_route(
    "/operations/calendar",
    _pipeline_root_impl,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/operations/calendar/summary",
    _pipeline_summary_impl,
    methods=["GET"],
)
router.add_api_route(
    "/operations/calendar/distributions/export.csv",
    _pipeline_distributions_impl,
    methods=["GET"],
)
router.add_api_route(
    "/operations/calendar/{year}/{month}",
    _pipeline_month_impl,
    methods=["GET"],
    response_class=HTMLResponse,
)

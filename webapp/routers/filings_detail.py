"""Single filing detail at /filings/{filing_id} — Phase 1 of the v3 URL migration.

Handles /filings/{filing_id} GET + POST. Imports impls from
webapp.routers.analysis. The /analysis/filing/{id} URL becomes a 301.
"""
from fastapi import APIRouter

from webapp.routers.analysis import (
    _filing_analysis_get_impl,
    _filing_analysis_post_impl,
)

router = APIRouter(tags=["filings-detail"])
router.add_api_route("/filings/{filing_id}", _filing_analysis_get_impl, methods=["GET"])
router.add_api_route("/filings/{filing_id}", _filing_analysis_post_impl, methods=["POST"])

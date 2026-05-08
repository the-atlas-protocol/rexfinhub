"""Tools — Leverage & Inverse Filing Candidates — Phase 1 of the v3 URL migration.

Imports handler implementations from webapp.routers.filings and
re-registers them under the new /tools/li/ prefix.

URL map
-------
GET  /tools/li/candidates  → _candidates_impl       (renders the candidates page)
POST /tools/li/candidates  → _evaluator_post_impl   (evaluator form/JSON submit)

Note on the candidates/evaluator merge:
- For PR 1, GET /tools/li/candidates serves the candidates view only.
- The evaluator panel (currently _evaluator_get_impl) will be folded into
  the same template in PR 3 (engine sync). _evaluator_get_impl is imported
  here so it remains discoverable for that future merge.
- POST submits to /tools/li/candidates and dispatch into the existing
  evaluator scoring path (_evaluator_post_impl).

Old /filings/candidates and /filings/evaluator URLs continue to work
via 301/308 redirects in webapp.routers.filings.
"""
from fastapi import APIRouter

from webapp.routers.filings import (
    _candidates_impl,
    _evaluator_get_impl,  # noqa: F401 — kept for PR 3 merge
    _evaluator_post_impl,
)

router = APIRouter(prefix="/tools/li", tags=["tools-li"])
router.add_api_route("/candidates", _candidates_impl, methods=["GET"])
router.add_api_route("/candidates", _evaluator_post_impl, methods=["POST"])

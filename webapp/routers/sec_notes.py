"""SEC Notes Intelligence — Phase 1 of the v3 URL migration.

Imports handler implementations from ``webapp.routers.notes`` and
re-registers them under the new ``/sec/notes/`` prefix. The
``/notes/`` + ``/notes/issuers`` merger happens fully in PR 2; for PR 1
we only expose the overview handler at ``/sec/notes/`` and the search
handler at ``/sec/notes/filings``.
"""
from __future__ import annotations

from fastapi import APIRouter

from webapp.routers.notes import _notes_overview_impl, _notes_search_impl

router = APIRouter(prefix="/sec/notes", tags=["sec-notes"])

router.add_api_route("/", _notes_overview_impl, methods=["GET"])
router.add_api_route("/filings", _notes_search_impl, methods=["GET"])

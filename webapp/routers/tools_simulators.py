"""Tools / Simulators — Phase 1 of the v3 URL migration.

Imports handler implementations from ``webapp.routers.notes_autocall``
and re-registers them under the new ``/tools/simulators/`` prefix.

Routes mounted:
    GET  /tools/simulators/autocall                  — page shell
    GET  /tools/simulators/autocall/data             — bootstrap JSON
    POST /tools/simulators/autocall/sweep            — distribution sweep
    POST /tools/simulators/autocall/suggest-coupon   — coupon heuristic
"""
from __future__ import annotations

from fastapi import APIRouter

from webapp.routers.notes_autocall import (
    _autocall_data_impl,
    _autocall_page_impl,
    _autocall_suggest_coupon_impl,
    _autocall_sweep_impl,
)

router = APIRouter(prefix="/tools/simulators", tags=["tools-simulators"])

router.add_api_route("/autocall", _autocall_page_impl, methods=["GET"])
router.add_api_route("/autocall/data", _autocall_data_impl, methods=["GET"])
router.add_api_route("/autocall/sweep", _autocall_sweep_impl, methods=["POST"])
router.add_api_route(
    "/autocall/suggest-coupon",
    _autocall_suggest_coupon_impl,
    methods=["POST"],
)

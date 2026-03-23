"""Shared helpers for screener/filing routes.

Extracted from screener.py so both filings.py and screener.py can import
without circular dependencies.
"""
from __future__ import annotations

import logging
import math
import os

log = logging.getLogger(__name__)

REX_ISSUERS = {"T-REX", "REX"}

_ON_RENDER = bool(os.environ.get("RENDER"))


def get_3x_data() -> dict | None:
    """Get cached 3x analysis. On Render, loads from DB. Locally, computes."""
    from webapp.services.screener_3x_cache import get_3x_analysis, compute_and_cache

    analysis = get_3x_analysis()
    if analysis is not None:
        return analysis

    # On Render: try DB, never compute from Excel
    if _ON_RENDER:
        from webapp.database import SessionLocal
        from webapp.services.screener_3x_cache import load_from_db, set_3x_analysis
        db = SessionLocal()
        try:
            data = load_from_db(db)
            if data:
                set_3x_analysis(data)
                return data
        finally:
            db.close()
        return None

    # Local: compute from Excel
    try:
        return compute_and_cache()
    except FileNotFoundError:
        return None
    except Exception as e:
        log.error("Failed to compute 3x analysis: %s", e)
        return None


def data_available() -> bool:
    """Check if Bloomberg data is available (file on disk or cached in DB)."""
    try:
        from screener.config import DATA_FILE
        if DATA_FILE.exists():
            return True
    except Exception:
        pass
    # On Render (no Excel file): check if DB cache has screener data
    if _ON_RENDER:
        return get_3x_data() is not None
    return False


def cache_warming() -> bool:
    """No longer has a warming state - cache is either populated or empty."""
    return False


def serialize_eval(r: dict) -> dict:
    """Convert evaluation result to JSON-safe dict."""

    def _clean(v):
        if v is None:
            return None
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, 2)
        if isinstance(v, dict):
            return {k: _clean(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_clean(vv) for vv in v]
        if hasattr(v, 'item'):  # numpy scalar
            return _clean(v.item())
        return v

    return _clean(r)

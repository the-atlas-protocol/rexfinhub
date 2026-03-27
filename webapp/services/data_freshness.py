"""Data freshness tracking — per-source timestamps for every page.

Each page declares which data source it uses. This module provides the
actual timestamp for that source, queried from the DB (not pipeline run
metadata, which can be misleading).

Sources:
  sec_filings  — MAX(Filing.filing_date): newest SEC filing in the DB
  market_data  — MktPipelineRun.finished_at: when Bloomberg data was last loaded
  screener     — cache computed_at: when screener analysis was last built
  notes        — MAX(filing_date) from structured_notes.db
  ownership    — MAX(report_date) from 13f_holdings.db
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Cache freshness data for 60 seconds (avoid re-querying every request)
_cache: dict = {}
_cache_ts: float = 0
_CACHE_TTL = 60


def get_freshness(db: Session) -> dict[str, dict]:
    """Return freshness info for all data sources.

    Returns::
        {
            "sec_filings": {"date": date, "label": "Mar 26, 2026", "age_days": 0, "status": "fresh"},
            "market_data": {"date": datetime, "label": "Mar 26, 2026 09:22", "age_days": 0, "status": "fresh"},
            "screener":    {"date": datetime, "label": "Mar 26, 2026 09:41", "age_days": 0, "status": "fresh"},
            "notes":       {"date": date, "label": "Mar 25, 2026", "age_days": 1, "status": "recent"},
            "ownership":   {"date": date, "label": "Dec 31, 2025", "age_days": 85, "status": "quarterly"},
        }

    Status values: "fresh" (today), "recent" (1-3 days), "stale" (3+ days),
                   "quarterly" (13F data, expected to be old), "unknown" (no data).
    """
    global _cache, _cache_ts
    now = time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    result = {}
    today = date.today()

    # --- SEC Filings: newest filing date in DB ---
    try:
        from webapp.models import Filing
        latest_filing = db.execute(
            select(func.max(Filing.filing_date))
        ).scalar()
        result["sec_filings"] = _build_entry(latest_filing, today)
    except Exception as e:
        log.debug("Freshness: sec_filings failed: %s", e)
        result["sec_filings"] = _unknown()

    # --- Market Data: last successful pipeline run ---
    try:
        from webapp.models import MktPipelineRun
        run = db.execute(
            select(MktPipelineRun.finished_at)
            .where(MktPipelineRun.status == "completed")
            .order_by(MktPipelineRun.finished_at.desc())
            .limit(1)
        ).scalar()
        result["market_data"] = _build_entry(run, today)
    except Exception as e:
        log.debug("Freshness: market_data failed: %s", e)
        result["market_data"] = _unknown()

    # --- Screener Cache: computed_at from cache metadata ---
    try:
        from webapp.services.screener_helpers import get_3x_data
        cache_data = get_3x_data()
        if cache_data:
            computed = cache_data.get("computed_at", "")
            data_date = cache_data.get("data_date", "")
            # computed_at format: "Mar 26, 2026 09:41"
            dt = None
            for fmt in ("%b %d, %Y %H:%M", "%B %d, %Y", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(computed, fmt) if computed else None
                    break
                except ValueError:
                    continue
            if not dt and data_date:
                for fmt in ("%b %d, %Y %H:%M", "%B %d, %Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(data_date, fmt)
                        break
                    except ValueError:
                        continue
            result["screener"] = _build_entry(dt, today)
        else:
            result["screener"] = _unknown()
    except Exception as e:
        log.debug("Freshness: screener failed: %s", e)
        result["screener"] = _unknown()

    # --- Structured Notes: MAX(filing_date) from notes DB ---
    try:
        notes_path = Path("D:/sec-data/databases/structured_notes.db")
        if not notes_path.exists():
            notes_path = Path("data/structured_notes.db")
        if notes_path.exists():
            conn = sqlite3.connect(str(notes_path))
            row = conn.execute(
                "SELECT MAX(filing_date) FROM filings WHERE extracted = 1"
            ).fetchone()
            conn.close()
            if row and row[0]:
                dt = datetime.strptime(row[0][:10], "%Y-%m-%d").date()
                result["notes"] = _build_entry(dt, today)
            else:
                result["notes"] = _unknown()
        else:
            result["notes"] = _unknown()
    except Exception as e:
        log.debug("Freshness: notes failed: %s", e)
        result["notes"] = _unknown()

    # --- Ownership (13F): MAX(report_date) from holdings DB ---
    try:
        from webapp.database import HOLDINGS_DB_PATH
        if HOLDINGS_DB_PATH.exists():
            conn = sqlite3.connect(str(HOLDINGS_DB_PATH))
            row = conn.execute(
                "SELECT MAX(report_date) FROM holdings WHERE is_tracked = 1"
            ).fetchone()
            conn.close()
            if row and row[0]:
                dt = datetime.strptime(row[0][:10], "%Y-%m-%d").date()
                entry = _build_entry(dt, today)
                # 13F is quarterly — adjust status thresholds
                entry["status"] = "quarterly"
                result["ownership"] = entry
            else:
                result["ownership"] = _unknown()
        else:
            result["ownership"] = _unknown()
    except Exception as e:
        log.debug("Freshness: ownership failed: %s", e)
        result["ownership"] = _unknown()

    _cache = result
    _cache_ts = now
    return result


def _build_entry(dt: date | datetime | None, today: date) -> dict:
    """Build a freshness entry from a date/datetime."""
    if dt is None:
        return _unknown()

    if isinstance(dt, datetime):
        d = dt.date()
        label = dt.strftime("%b %d, %Y %H:%M")
    else:
        d = dt
        label = dt.strftime("%b %d, %Y")

    age = (today - d).days

    if age <= 0:
        status = "fresh"
    elif age <= 3:
        status = "recent"
    else:
        status = "stale"

    return {"date": dt, "label": label, "age_days": age, "status": status}


def _unknown() -> dict:
    return {"date": None, "label": "No data", "age_days": -1, "status": "unknown"}


# Page → data source mapping
# Used by the template partial to know which timestamp to show
PAGE_SOURCES = {
    # Filings pillar
    "/filings/": ["sec_filings"],
    "/filings/dashboard": ["sec_filings"],
    "/filings/explorer": ["sec_filings"],
    "/filings/landscape": ["sec_filings", "screener"],
    "/filings/candidates": ["screener"],
    "/filings/evaluator": ["screener"],
    # Market pillar
    "/screener/market": ["screener"],
    "/screener/rex-funds": ["market_data", "screener"],
    "/screener/risk": ["screener"],
    "/market/": ["market_data"],
    "/market/rex": ["market_data"],
    "/market/category": ["market_data"],
    "/market/issuer": ["market_data"],
    "/market/underlier": ["market_data"],
    # Notes
    "/notes/": ["notes"],
    "/notes/issuers": ["notes"],
    "/notes/search": ["notes"],
    # Ownership
    "/holdings/": ["ownership"],
    "/ownership/": ["ownership"],
    # Home
    "/": ["sec_filings", "market_data", "notes"],
}


def sources_for_path(path: str) -> list[str]:
    """Return the data source keys relevant to a given URL path."""
    # Exact match first
    if path in PAGE_SOURCES:
        return PAGE_SOURCES[path]
    # Prefix match (e.g. /market/issuer/detail matches /market/issuer)
    for prefix, sources in sorted(PAGE_SOURCES.items(), key=lambda x: -len(x[0])):
        if path.startswith(prefix) and prefix != "/":
            return sources
    return []

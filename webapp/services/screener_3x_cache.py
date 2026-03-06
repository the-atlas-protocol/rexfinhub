"""In-memory cache for 3x/4x analysis results.

Locally: computed from Bloomberg data, results stored in DB + memory.
On Render: loaded from mkt_report_cache (key='screener_3x'), no Excel needed.
No TTL - invalidated explicitly on data sync / admin action.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

log = logging.getLogger(__name__)

_cache: dict = {}
_lock = threading.Lock()
_ON_RENDER = bool(os.environ.get("RENDER"))


def get_3x_analysis() -> dict | None:
    """Return cached analysis dict, or None if empty."""
    return _cache or None


def set_3x_analysis(data: dict) -> None:
    """Store analysis result in memory."""
    global _cache
    with _lock:
        _cache = data
    log.info("3x analysis cached (%d keys)", len(data))


def invalidate_cache() -> None:
    """Clear cache (call on data upload/sync)."""
    global _cache
    with _lock:
        _cache = {}
    log.info("3x analysis cache invalidated")


def load_from_db(db) -> dict | None:
    """Load screener results from mkt_report_cache."""
    try:
        from sqlalchemy import select
        from webapp.models import MktReportCache

        row = db.execute(
            select(MktReportCache).where(MktReportCache.report_key == "screener_3x")
        ).scalar_one_or_none()
        if row and row.data_json:
            data = json.loads(row.data_json)
            log.info("Screener loaded from DB (%d keys)", len(data))
            return data
    except Exception as e:
        log.warning("Failed to load screener from DB: %s", e)
    return None


def save_to_db(db, data: dict) -> None:
    """Write screener results to mkt_report_cache."""
    try:
        from sqlalchemy import delete
        from webapp.models import MktReportCache

        db.execute(
            delete(MktReportCache).where(MktReportCache.report_key == "screener_3x")
        )
        row = MktReportCache(
            report_key="screener_3x",
            data_json=json.dumps(data, default=str),
            data_as_of=data.get("data_date", ""),
            updated_at=datetime.utcnow(),
        )
        db.add(row)
        db.flush()
        log.info("Screener cache saved to DB")
    except Exception as e:
        log.warning("Failed to save screener to DB: %s", e)


def warm_cache(db=None) -> None:
    """Pre-warm the cache at startup.

    On Render: load from DB (fast, no Excel).
    Locally: compute from Bloomberg data.
    """
    # Try DB first (works on both Render and local)
    if db is not None:
        data = load_from_db(db)
        if data:
            set_3x_analysis(data)
            if _ON_RENDER:
                return

    if _ON_RENDER:
        log.info("Screener: no DB cache on Render, will serve empty until next sync")
        return

    # Local: compute from Excel
    try:
        result = compute_and_cache()
        if db is not None:
            save_to_db(db, result)
    except FileNotFoundError:
        log.info("No Bloomberg data - screener unavailable")
    except Exception as e:
        log.warning("Cache warm compute failed: %s", e)


def compute_and_cache() -> dict:
    """Run full 3x analysis pipeline, cache result, return it.

    Double-checked locking: only one thread computes.
    Only runs locally (requires Bloomberg data on disk).
    """
    global _cache

    if _cache:
        return _cache

    with _lock:
        if _cache:
            return _cache

        from screener.data_loader import load_all
        from screener.scoring import (
            compute_percentile_scores,
            apply_threshold_filters,
            apply_competitive_penalty,
        )
        from screener.competitive import compute_competitive_density
        from screener.analysis_3x import (
            get_3x_market_snapshot,
            get_top_2x_single_stock,
            get_underlier_popularity,
            get_rex_track_record,
            get_3x_candidates,
            get_4x_candidates,
            compute_blowup_risk,
            compute_3x_filing_score,
            _build_2x_aum_lookup,
            _build_rex_2x_status,
        )
        from screener.config import DATA_FILE

        log.info("Computing 3x analysis (this takes ~20s)...")

        data = load_all()
        stock_df = data["stock_data"]
        etp_df = data["etp_data"]

        # Bloomberg data date
        data_date = None
        if DATA_FILE.exists():
            mtime = os.path.getmtime(DATA_FILE)
            data_date = datetime.fromtimestamp(mtime).strftime("%B %d, %Y")

        # Score stocks
        scored = compute_percentile_scores(stock_df)
        scored = apply_threshold_filters(scored, benchmarks=None)
        density = compute_competitive_density(etp_df)
        scored = apply_competitive_penalty(scored, density)
        scored = compute_3x_filing_score(scored, etp_df)

        # All analysis functions
        snapshot = get_3x_market_snapshot(etp_df)
        top_2x = get_top_2x_single_stock(etp_df, n=100)
        underlier_pop = get_underlier_popularity(etp_df, stock_df, top_n=50)
        rex_track = get_rex_track_record(etp_df, scored)
        tiers = get_3x_candidates(scored, etp_df)
        four_x = get_4x_candidates(etp_df, stock_df)
        risk_df = compute_blowup_risk(stock_df)

        # Scoped risk watchlist (Tier 1 + Tier 2 + top underliers)
        scope_tickers = set()
        for tier in ("tier_1", "tier_2"):
            for c in tiers.get(tier, []):
                scope_tickers.add(c["ticker"].upper())
        for r in underlier_pop:
            scope_tickers.add(r["underlier"].upper())

        aum_lookup = _build_2x_aum_lookup(etp_df)
        rex_2x_status = _build_rex_2x_status(etp_df)

        risk_watchlist = []
        for _, row in risk_df.iterrows():
            tc = str(row.get("ticker_clean", "")).upper()
            if tc not in scope_tickers:
                continue
            entry = row.to_dict()
            entry["aum_2x"] = aum_lookup.get(tc, {}).get("aum_2x", 0)
            entry["rex_2x"] = rex_2x_status.get(tc, "No")
            risk_watchlist.append(entry)

        risk_watchlist.sort(key=lambda x: x.get("aum_2x", 0), reverse=True)

        result = {
            "snapshot": snapshot,
            "top_2x": top_2x,
            "underlier_pop": underlier_pop,
            "rex_track": rex_track,
            "tiers": tiers,
            "four_x": four_x,
            "risk_watchlist": risk_watchlist,
            "data_date": data_date,
            "computed_at": datetime.now().strftime("%b %d, %Y %H:%M"),
        }

        _cache = result
        log.info("3x analysis complete: %d tier1, %d tier2, %d tier3, %d 4x, %d risk",
                 len(tiers.get("tier_1", [])), len(tiers.get("tier_2", [])),
                 len(tiers.get("tier_3", [])), len(four_x), len(risk_watchlist))
        return result

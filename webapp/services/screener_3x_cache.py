"""In-memory cache for 3x/4x analysis results.

The analysis pipeline takes ~20 seconds. Results only change when Bloomberg
data is re-uploaded, so we cache aggressively and invalidate on upload.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

_cache: dict = {}
_cache_lock = threading.Lock()
_cache_timestamp: float = 0
_CACHE_TTL = 3600  # 1 hour safety net


def get_3x_analysis() -> dict | None:
    """Return cached analysis dict, or None if stale/empty."""
    with _cache_lock:
        if _cache and (time.time() - _cache_timestamp) < _CACHE_TTL:
            return _cache
    return None


def set_3x_analysis(data: dict) -> None:
    """Store analysis result in cache."""
    global _cache, _cache_timestamp
    with _cache_lock:
        _cache = data
        _cache_timestamp = time.time()
    log.info("3x analysis cached (%d keys)", len(data))


def invalidate_cache() -> None:
    """Clear cache (call on Bloomberg upload)."""
    global _cache, _cache_timestamp
    with _cache_lock:
        _cache = {}
        _cache_timestamp = 0
    log.info("3x analysis cache invalidated")


def compute_and_cache() -> dict:
    """Run full 3x analysis pipeline, cache result, return it.

    Mirrors the pipeline in generate_report.py:run_3x_report().
    """
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
    import os

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

    set_3x_analysis(result)
    log.info("3x analysis complete: %d tier1, %d tier2, %d tier3, %d 4x, %d risk",
             len(tiers.get("tier_1", [])), len(tiers.get("tier_2", [])),
             len(tiers.get("tier_3", [])), len(four_x), len(risk_watchlist))
    return result

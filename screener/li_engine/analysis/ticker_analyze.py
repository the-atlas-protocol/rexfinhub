"""Ticker analysis module — data layer for the L&I report skill.

Given any list of tickers, returns a structured dict per ticker:
    - Basic: market cap, sector, last price, 52w high position
    - Liquidity: adv_30d, turnover, total_oi
    - Vol + returns: rvol_30d/90d, ret_1m/3m/6m/1y
    - Positioning: insider_pct, inst_own_pct, si_ratio
    - Retail: mentions_24h from ApeWisdom (live)
    - Product status: n_rex_products, n_comp_products, has_rex_filing_ever
    - Theme: matched from themes.yaml
    - Score: composite score + percentile rank against full universe
    - Flags: warnings (high SI, heavy inst, 3m run-up, etc.)
    - Drivers: top-3 positive score contributors

The LLM layer (the skill) uses this dict to write per-ticker research notes.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"

from screener.li_engine.analysis.whitespace_v2 import (
    _clean, _coerce, _zscore, load_themes, load_apewisdom_map,
)
from screener.li_engine.analysis.whitespace_v3 import (
    WEIGHTS, compute_score_v3, top_drivers_v3, negative_flags,
)


def _pull_stock_row(ticker_clean: str) -> dict | None:
    """Pull bbg stock metrics for a specific ticker (clean form, e.g. 'NOK')."""
    conn = sqlite3.connect(str(DB))
    try:
        run_id = conn.execute(
            "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
            "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]
        # Try exact match, then with ' US' suffix
        for candidate in (ticker_clean, f"{ticker_clean} US"):
            row = conn.execute(
                "SELECT ticker, data_json FROM mkt_stock_data "
                "WHERE pipeline_run_id=? AND ticker=?",
                (run_id, candidate),
            ).fetchone()
            if row:
                break
        else:
            return None
    finally:
        conn.close()

    _, blob = row
    if not blob:
        return None
    try:
        d = json.loads(blob)
        d = d[0] if isinstance(d, list) else d
    except json.JSONDecodeError:
        return None
    return d


def _product_coverage(ticker_clean: str) -> dict:
    conn = sqlite3.connect(str(DB))
    try:
        rows = conn.execute(
            """
            SELECT ticker, fund_name, issuer, is_rex, market_status, aum,
                   map_li_leverage_amount, map_li_direction
            FROM mkt_master_data
            WHERE primary_category = 'LI'
              AND (map_li_underlier = ? OR map_li_underlier = ?)
            """,
            (ticker_clean, f"{ticker_clean} US"),
        ).fetchall()
    finally:
        conn.close()

    rex_active = []
    comp_active = []
    rex_filed_any = []
    for ticker, fund_name, issuer, is_rex, mkt_status, aum, lev, direction in rows:
        entry = {
            "ticker": ticker.replace(" US", ""),
            "fund_name": fund_name,
            "issuer": issuer,
            "leverage": lev,
            "direction": direction,
            "market_status": mkt_status,
            "aum": float(aum) if aum else 0.0,
        }
        if is_rex:
            rex_filed_any.append(entry)
            if mkt_status in ("ACTV", "ACTIVE"):
                rex_active.append(entry)
        else:
            if mkt_status in ("ACTV", "ACTIVE"):
                comp_active.append(entry)

    return {
        "n_rex_active": len(rex_active),
        "n_competitor_active": len(comp_active),
        "n_rex_filed_any": len(rex_filed_any),
        "rex_active_products": rex_active,
        "competitor_active_products": comp_active[:5],  # cap for display
    }


def _extract_signals(d: dict) -> dict:
    insider = _coerce(d.get("% Insider Shares Outstanding"))
    if insider is not None and insider > 100:
        insider = None

    call_oi = _coerce(d.get("Total Call OI"))
    put_oi = _coerce(d.get("Total Put OI"))
    total_oi = _coerce(d.get("Total OI")) or ((call_oi or 0) + (put_oi or 0))

    return {
        "market_cap": _coerce(d.get("Mkt Cap")),
        "last_price": _coerce(d.get("Last Price")),
        "high_52w": _coerce(d.get("52W High")),
        "low_52w": _coerce(d.get("52W Low")),
        "sector": d.get("GICS Sector"),
        "adv_30d": _coerce(d.get("Avg Volume 30D")),
        "turnover": _coerce(d.get("Turnover / Traded Value")),
        "total_oi": total_oi,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "rvol_30d": _coerce(d.get("Volatility 30D")),
        "rvol_90d": _coerce(d.get("Volatility 90D")),
        "ret_1m": _coerce(d.get("1M Total Return")),
        "ret_3m": _coerce(d.get("3M Total Return")),
        "ret_6m": _coerce(d.get("6M Total Return")),
        "ret_1y": _coerce(d.get("1Y Total Return")),
        "si_ratio": _coerce(d.get("Short Interest Ratio")),
        "insider_pct": insider,
        "inst_own_pct": _coerce(d.get("Institutional Owner % Shares Outstanding")),
        "news_sentiment_bbg": _coerce(d.get("News Sentiment Daily Avg")),
    }


def _themes_for_ticker(ticker: str, themes: dict[str, list[str]]) -> list[str]:
    out = []
    for theme, tks in themes.items():
        if ticker in {_clean(t) for t in tks}:
            out.append(theme)
    return out


def analyze_tickers(tickers: list[str], fetch_mentions: bool = True) -> dict[str, dict]:
    """Return {ticker: analysis_dict} for each requested ticker."""
    tickers = [_clean(t) for t in tickers if _clean(t)]
    themes = load_themes()

    # Batch mention lookup
    mentions_map: dict[str, int] = {}
    if fetch_mentions:
        mentions_map = load_apewisdom_map(set(tickers))

    result: dict[str, dict] = {}

    for t in tickers:
        blob = _pull_stock_row(t)
        if blob is None:
            result[t] = {"error": "not_found_in_bbg_stock_data", "ticker": t}
            continue

        sigs = _extract_signals(blob)
        coverage = _product_coverage(t)
        tk_themes = _themes_for_ticker(t, themes)
        mentions_n = mentions_map.get(t, 0)
        pct_of_52w = None
        if sigs.get("last_price") and sigs.get("high_52w"):
            pct_of_52w = sigs["last_price"] / sigs["high_52w"]

        result[t] = {
            "ticker": t,
            "signals": sigs,
            "product_coverage": coverage,
            "themes": tk_themes,
            "mentions_24h": mentions_n,
            "pct_of_52w_high": pct_of_52w,
        }

    return result


def rank_against_universe(tickers: list[str]) -> dict[str, dict]:
    """Returns analysis dict augmented with the composite score + percentile
    against the full universe (same weights as v3)."""
    from screener.li_engine.analysis.whitespace_v2 import (
        load_universe, annotate_product_coverage,
    )

    # Build and score the full universe
    universe = load_universe()
    universe = annotate_product_coverage(universe)
    themes = load_themes()

    all_tickers = set(universe.index) | set(_clean(t) for t in tickers)
    mentions = load_apewisdom_map(all_tickers)
    scored_full = compute_score_v3(universe, themes, mentions)

    # Per-ticker rank lookup
    by_ticker = {}
    for t in tickers:
        tc = _clean(t)
        base = analyze_tickers([tc], fetch_mentions=False)[tc]
        if "error" in base:
            by_ticker[tc] = base
            continue

        if tc in scored_full.index:
            row = scored_full.loc[tc]
            base["score"] = {
                "composite": float(row["composite_score"]),
                "percentile": float(row["score_pct"]),
                "rank_in_universe": int((scored_full["composite_score"] > row["composite_score"]).sum()) + 1,
                "universe_size": int(len(scored_full)),
                "top_drivers": top_drivers_v3(row),
                "flags": negative_flags(row),
            }
        else:
            base["score"] = {"error": "ticker_below_liquidity_floor_or_missing"}
        base["mentions_24h"] = int(mentions.get(tc, 0))
        by_ticker[tc] = base

    return by_ticker


def format_text_card(ticker: str, analysis: dict) -> str:
    """Single-ticker markdown-formatted card for display."""
    if "error" in analysis:
        return f"### {ticker}\n**Error:** {analysis['error']}\n"

    s = analysis.get("signals", {})
    cov = analysis.get("product_coverage", {})
    themes = analysis.get("themes", [])
    mentions = analysis.get("mentions_24h", 0)
    score = analysis.get("score", {})

    lines = [f"### {ticker} — {s.get('sector') or '—'}"]

    # Quick metrics
    mc = s.get("market_cap") or 0
    mc_str = f"${mc/1000:.1f}B" if mc >= 1000 else f"${mc:,.0f}M"
    pct52 = analysis.get("pct_of_52w_high")
    pct52_str = f"{pct52:.0%} of 52w high" if pct52 else "—"

    lines.append(f"- Market cap: {mc_str}, {pct52_str}")
    lines.append(f"- 1m / 3m / 1y return: {s.get('ret_1m') or 0:+.0f}% / "
                 f"{s.get('ret_3m') or 0:+.0f}% / {s.get('ret_1y') or 0:+.0f}%")
    lines.append(f"- Realized vol 30d / 90d: {s.get('rvol_30d') or 0:.0f}% / "
                 f"{s.get('rvol_90d') or 0:.0f}%")
    lines.append(f"- Total OI: {(s.get('total_oi') or 0):,.0f}  |  "
                 f"Short Int ratio: {s.get('si_ratio') or 0:.1f}  |  "
                 f"Retail mentions (24h): {mentions}")
    lines.append(f"- Inst own / Insider: {s.get('inst_own_pct') or 0:.0f}% / "
                 f"{s.get('insider_pct') or 0:.1f}%")

    # Themes
    if themes:
        lines.append(f"- Themes: {', '.join(themes)}")

    # Product coverage
    rex_active = cov.get("rex_active_products", [])
    comp_active = cov.get("competitor_active_products", [])
    if rex_active:
        rex_str = ", ".join(f"{p['ticker']} ({p['leverage']}x {p['direction']}, ${p['aum']:.0f}M)"
                            for p in rex_active[:3])
        lines.append(f"- REX products: {rex_str}")
    if comp_active:
        comp_str = ", ".join(f"{p['ticker']} ({p['issuer']}, {p['leverage']}x)"
                             for p in comp_active[:3])
        lines.append(f"- Competitor products: {comp_str}")
    if not rex_active and not comp_active:
        lines.append(f"- **Whitespace** — zero active leveraged products on this underlier")

    # Score
    if isinstance(score, dict) and "percentile" in score:
        lines.append(f"- **Composite score:** {score['composite']:+.2f} "
                     f"(rank {score['rank_in_universe']} of {score['universe_size']}, "
                     f"{score['percentile']:.0f}th percentile)")
        if score.get("top_drivers"):
            lines.append(f"- Top drivers: {'; '.join(score['top_drivers'])}")
        if score.get("flags"):
            lines.append(f"- Flags: {'; '.join(score['flags'])}")

    return "\n".join(lines)


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("tickers", nargs="+", help="Tickers to analyze (comma or space separated)")
    p.add_argument("--rank", action="store_true",
                   help="Rank against full universe (slower — recomputes scores)")
    args = p.parse_args()

    raw_tickers = []
    for t in args.tickers:
        raw_tickers.extend(t.split(","))

    if args.rank:
        results = rank_against_universe(raw_tickers)
    else:
        results = analyze_tickers(raw_tickers)

    for ticker in [_clean(t) for t in raw_tickers]:
        if ticker in results:
            print(format_text_card(ticker, results[ticker]))
            print()


if __name__ == "__main__":
    main()

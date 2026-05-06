"""Whitespace candidate scorer.

The right question: which stocks have the demand profile of historically
successful 2x products AND don't already have REX exposure AND aren't
over-saturated by competitors?

Scoring uses the signals validated by the post-launch success analysis:
    turnover (+0.33), total_oi (+0.31), rvol_90d (+0.29),
    mentions_24h (current retail attention), insider_pct (+0.13)
    PENALIZE si_ratio (strongest failure predictor, -0.33)
    PENALIZE ret_3m (mean-reversion, -0.19)

Weights derived from the post-launch IC magnitudes.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT = _ROOT / "data" / "analysis" / "whitespace_candidates.parquet"


def _clean(t):
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


def _coerce(v):
    if v in (None, "", "#ERROR", "#N/A", "N/A"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_universe() -> pd.DataFrame:
    """All bbg stock_data tickers with market cap >= $1B and options activity."""
    conn = sqlite3.connect(str(DB))
    try:
        run_id = conn.execute(
            "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
            "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?", (run_id,),
        ).fetchall()
    finally:
        conn.close()

    recs = []
    for ticker, blob in rows:
        if not blob:
            continue
        try:
            d = json.loads(blob)
            d = d[0] if isinstance(d, list) else d
        except json.JSONDecodeError:
            continue

        mkt_cap = _coerce(d.get("Mkt Cap"))
        total_oi = _coerce(d.get("Total OI"))
        if mkt_cap is None or mkt_cap < 1000:  # $1B floor (in millions)
            continue
        if total_oi is None or total_oi < 1000:  # Any meaningful options activity
            continue

        recs.append({
            "ticker": _clean(ticker),
            "market_cap": mkt_cap,
            "turnover": _coerce(d.get("Turnover / Traded Value")),
            "total_oi": total_oi,
            "call_oi": _coerce(d.get("Total Call OI")),
            "put_oi": _coerce(d.get("Total Put OI")),
            "rvol_30d": _coerce(d.get("Volatility 30D")),
            "rvol_90d": _coerce(d.get("Volatility 90D")),
            "si_ratio": _coerce(d.get("Short Interest Ratio")),
            "ret_1m": _coerce(d.get("1M Total Return")),
            "ret_3m": _coerce(d.get("3M Total Return")),
            "ret_6m": _coerce(d.get("6M Total Return")),
            "ret_1y": _coerce(d.get("1Y Total Return")),
            "insider_pct": _coerce(d.get("% Insider Shares Outstanding")),
            "inst_own_pct": _coerce(d.get("Institutional Owner % Shares Outstanding")),
            "news_sentiment": _coerce(d.get("News Sentiment Daily Avg")),
            "sector": d.get("GICS Sector"),
            "last_price": _coerce(d.get("Last Price")),
            "high_52w": _coerce(d.get("52W High")),
        })
    df = pd.DataFrame(recs)
    df = df[df["ticker"] != ""].drop_duplicates("ticker").set_index("ticker")
    # Cap insider_pct at 100 (COIN et al have bad data)
    df.loc[df["insider_pct"] > 100, "insider_pct"] = np.nan
    log.info("Universe: %d tickers with mkt_cap >= $1B and options activity", len(df))
    return df


def annotate_product_coverage(universe: pd.DataFrame) -> pd.DataFrame:
    """For each ticker, count existing 2x/3x products: REX vs competitors."""
    conn = sqlite3.connect(str(DB))
    try:
        prods = pd.read_sql_query(
            """
            SELECT map_li_underlier AS underlier,
                   is_rex,
                   map_li_leverage_amount AS leverage,
                   market_status,
                   aum
            FROM mkt_master_data
            WHERE primary_category = 'LI'
              AND map_li_underlier IS NOT NULL
              AND map_li_underlier != ''
            """,
            conn,
        )
    finally:
        conn.close()

    prods["underlier"] = prods["underlier"].astype(str).map(_clean)
    prods = prods[prods["underlier"] != ""]

    # Count active products per underlier
    is_active = prods["market_status"].isin(["ACTV", "ACTIVE"])
    active_prods = prods[is_active]

    summary = active_prods.groupby("underlier").agg(
        n_rex_products=("is_rex", lambda x: int(x.sum())),
        n_comp_products=("is_rex", lambda x: int((1 - x).sum())),
        n_total_products=("is_rex", "count"),
        total_existing_aum=("aum", lambda x: float(x.fillna(0).sum())),
    )

    # Track REX-filed (including non-active) to know where we have pending/paused
    rex_any = prods[prods["is_rex"] == 1].groupby("underlier").size().rename("n_rex_filed_any")
    summary = summary.join(rex_any, how="left").fillna({"n_rex_filed_any": 0})

    annotated = universe.join(summary, how="left").fillna({
        "n_rex_products": 0,
        "n_comp_products": 0,
        "n_total_products": 0,
        "total_existing_aum": 0,
        "n_rex_filed_any": 0,
    })
    return annotated


def load_mentions(universe_tickers: set[str], max_pages: int = 5) -> dict[str, float]:
    """Fetch ApeWisdom mentions for universe tickers."""
    url = "https://apewisdom.io/api/v1.0/filter/{f}/page/{p}"
    recs: dict[str, float] = {}
    for filt in ("all-stocks", "wallstreetbets"):
        for page in range(1, max_pages + 1):
            try:
                r = requests.get(url.format(f=filt, p=page), timeout=10)
                if r.status_code != 200:
                    break
                items = r.json().get("results", [])
                if not items:
                    break
                for it in items:
                    t = _clean(it.get("ticker", ""))
                    if t not in universe_tickers:
                        continue
                    m = int(it.get("mentions", 0) or 0)
                    if t not in recs or m > recs[t]:
                        recs[t] = m
                time.sleep(0.15)
            except Exception as e:
                log.warning("apewisdom %s p%d: %s", filt, page, e)
                break
    return recs


def compute_score(df: pd.DataFrame) -> pd.DataFrame:
    """Apply post-launch-success-validated weights to produce a candidate score."""

    # Weights derived from post-launch IC magnitudes (normalized to sum = 1)
    POSITIVE_WEIGHTS = {
        "turnover": 0.22,          # IC 0.33
        "total_oi": 0.20,          # IC 0.31
        "rvol_90d": 0.19,          # IC 0.29
        "mentions_24h": 0.15,      # current-state signal
        "ret_1y": 0.08,            # IC 0.18 (keeps)
        "insider_pct": 0.08,       # IC 0.13
        "market_cap": 0.08,        # IC 0.19 (includes as a floor/scaling factor)
    }
    NEGATIVE_WEIGHTS = {
        "si_ratio": -0.15,         # IC -0.33
        "ret_3m": -0.10,           # IC -0.19
        "inst_own_pct": -0.08,     # IC -0.18
    }

    out = df.copy()

    # z-score each signal within the universe
    for col in list(POSITIVE_WEIGHTS.keys()) + list(NEGATIVE_WEIGHTS.keys()):
        if col not in out.columns:
            out[f"{col}__z"] = np.nan
            continue
        x = out[col].copy()
        # log1p-transform skewed magnitudes
        if col in ("turnover", "total_oi", "market_cap", "mentions_24h"):
            x = np.log1p(x.clip(lower=0))
        mu, sd = x.mean(skipna=True), x.std(skipna=True)
        if sd == 0 or np.isnan(sd):
            out[f"{col}__z"] = 0
        else:
            out[f"{col}__z"] = ((x - mu) / sd).clip(-3, 3)

    # Weighted composite
    out["composite_score"] = 0.0
    for col, w in POSITIVE_WEIGHTS.items():
        z = out[f"{col}__z"].fillna(0)
        out["composite_score"] += w * z
    for col, w in NEGATIVE_WEIGHTS.items():
        z = out[f"{col}__z"].fillna(0)
        out["composite_score"] += w * z

    # Percentile ranking
    out["score_pct"] = out["composite_score"].rank(pct=True) * 100
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    universe = load_universe()
    universe = annotate_product_coverage(universe)

    # Add live mentions
    log.info("Fetching ApeWisdom mentions...")
    mentions_map = load_mentions(set(universe.index))
    universe["mentions_24h"] = universe.index.map(lambda t: mentions_map.get(t, 0))
    log.info("Mentions coverage: %d tickers with >0 mentions", (universe["mentions_24h"] > 0).sum())

    scored = compute_score(universe)

    # Classify whitespace
    # Filter:
    # - No REX product (whitespace for us)
    # - Low competitor density (< 3 existing 2x products)
    whitespace = scored[
        (scored["n_rex_products"] == 0) &
        (scored["n_rex_filed_any"] == 0) &
        (scored["n_comp_products"] < 3)
    ].copy()

    log.info("Full universe: %d tickers", len(scored))
    log.info("Whitespace (no REX filing, <3 competitors): %d tickers", len(whitespace))

    # Save full output
    scored.to_parquet(OUT, compression="snappy")
    log.info("Saved full scored panel to %s", OUT)

    # Report
    print("\n" + "=" * 80)
    print("WHITESPACE CANDIDATE ANALYSIS")
    print("=" * 80)
    print(f"Universe: {len(scored)} stocks (mkt cap >= $1B, options active)")
    print(f"With any REX filing: {(scored['n_rex_filed_any'] > 0).sum()}")
    print(f"Saturated (>= 3 competitor products): {(scored['n_comp_products'] >= 3).sum()}")
    print(f"Whitespace (no REX + < 3 comp): {len(whitespace)}")

    print("\n" + "=" * 80)
    print("TOP 25 WHITESPACE CANDIDATES BY COMPOSITE SCORE")
    print("=" * 80)
    top = whitespace.sort_values("composite_score", ascending=False).head(25)
    cols_show = ["sector", "market_cap", "n_comp_products", "rvol_90d",
                 "total_oi", "turnover", "mentions_24h", "ret_1m", "ret_1y",
                 "si_ratio", "insider_pct", "composite_score", "score_pct"]
    cols_show = [c for c in cols_show if c in top.columns]
    for ticker in top.index:
        row = top.loc[ticker]
        parts = [f"{ticker:<7}"]
        parts.append(f"{(row.get('sector') or 'n/a')[:20]:<20}")
        parts.append(f"mc=${row['market_cap']:>8,.0f}M")
        parts.append(f"comp={int(row['n_comp_products'])}")
        parts.append(f"rvol={row['rvol_90d']:.0f}" if not pd.isna(row.get('rvol_90d')) else "rvol=—")
        parts.append(f"oi={int(row['total_oi']):>9,}" if not pd.isna(row.get('total_oi')) else "oi=—")
        parts.append(f"ret1y={row['ret_1y']:+.0f}%" if not pd.isna(row.get('ret_1y')) else "ret1y=—")
        parts.append(f"mentions={int(row.get('mentions_24h', 0))}")
        parts.append(f"score={row['composite_score']:+.2f}")
        print("  " + "  ".join(parts))

    print("\n" + "=" * 80)
    print("TOP 10 BY SECTOR")
    print("=" * 80)
    for sector, grp in whitespace.groupby("sector"):
        if pd.isna(sector) or len(grp) < 2:
            continue
        top_sector = grp.sort_values("composite_score", ascending=False).head(5)
        print(f"\n{sector} ({len(grp)} stocks in whitespace):")
        for t in top_sector.index:
            r = top_sector.loc[t]
            print(f"  {t:<7} mc=${r['market_cap']:>8,.0f}M  score={r['composite_score']:+.2f}")


if __name__ == "__main__":
    main()

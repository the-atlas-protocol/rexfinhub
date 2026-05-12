"""Whitespace scorer v4.

Changes from v3:
    1. $500M floor (was $1B)
    2. Universe is NASDAQ+NYSE (not bbg-dependent)
    3. Whitespace filter HARDENED: requires
         (a) zero active competitor products, AND
         (b) zero REX filings ever (any status), AND
         (c) zero competitor 485APOS in last 180 days
    4. Signal data still pulled from bbg stock_data WHERE AVAILABLE;
       tickers in NASDAQ universe but not in bbg are flagged
       "no_bbg_data" and marked for stooq backfill.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from screener.li_engine.analysis.universe_loader import latest_snapshot as load_nasdaq_universe
from screener.li_engine.analysis.whitespace_v2 import (
    _clean, _coerce, _zscore, load_themes, load_apewisdom_map,
)
from screener.li_engine.analysis.whitespace_v3 import (
    WEIGHTS, compute_score_v3, top_drivers_v3, negative_flags,
)

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"

MKT_CAP_FLOOR = 500  # $500M (was 1000)
OI_FLOOR = 5000
LOOKBACK_485APOS_DAYS = 180


def load_bbg_signals_df() -> pd.DataFrame:
    """Pull bbg stock_data metrics for every ticker bbg tracks — no floor yet."""
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

        insider = _coerce(d.get("% Insider Shares Outstanding"))
        if insider is not None and insider > 100:
            insider = None

        recs.append({
            "ticker": _clean(ticker),
            "market_cap": _coerce(d.get("Mkt Cap")),
            "total_oi": _coerce(d.get("Total OI")),
            "turnover": _coerce(d.get("Turnover / Traded Value")),
            "adv_30d": _coerce(d.get("Avg Volume 30D")),
            "last_price": _coerce(d.get("Last Price")),
            "high_52w": _coerce(d.get("52W High")),
            "low_52w": _coerce(d.get("52W Low")),
            "sector": d.get("GICS Sector"),
            "rvol_30d": _coerce(d.get("Volatility 30D")),
            "rvol_90d": _coerce(d.get("Volatility 90D")),
            "ret_1m": _coerce(d.get("1M Total Return")),
            "ret_3m": _coerce(d.get("3M Total Return")),
            "ret_1y": _coerce(d.get("1Y Total Return")),
            "si_ratio": _coerce(d.get("Short Interest Ratio")),
            "insider_pct": insider,
            "inst_own_pct": _coerce(d.get("Institutional Owner % Shares Outstanding")),
        })
    df = pd.DataFrame(recs)
    df = df[df["ticker"] != ""].drop_duplicates("ticker").set_index("ticker")
    return df


def load_product_coverage_with_filings() -> pd.DataFrame:
    """Full whitespace filter inputs:
        - n_active_competitor_products
        - n_rex_filings_ever
        - n_competitor_485apos_180d (NEW)
        - days_since_last_competitor_filing (NEW)
    """
    conn = sqlite3.connect(str(DB))
    try:
        prods = pd.read_sql_query(
            """
            SELECT map_li_underlier AS underlier, is_rex, market_status, aum
            FROM mkt_master_data
            WHERE primary_category = 'LI' AND map_li_underlier IS NOT NULL
              AND map_li_underlier != ''
            """,
            conn,
        )
        filings = pd.read_sql_query(
            """
            SELECT f.filing_date, f.form, fe.class_symbol, mmd.map_li_underlier,
                   mmd.is_rex
            FROM filings f
            JOIN fund_extractions fe ON fe.filing_id = f.id
            JOIN mkt_master_data mmd ON mmd.ticker = fe.class_symbol || ' US'
            WHERE mmd.map_li_underlier IS NOT NULL
              AND mmd.primary_category = 'LI'
              AND f.form IN ('485APOS', '485BPOS', '485BXT', 'N-1A', 'S-1')
            """,
            conn,
        )
    finally:
        conn.close()

    prods["underlier"] = prods["underlier"].astype(str).map(_clean)
    filings["underlier"] = filings["map_li_underlier"].astype(str).map(_clean)
    filings["filing_date"] = pd.to_datetime(filings["filing_date"], errors="coerce")
    filings = filings.dropna(subset=["filing_date"])

    today = pd.Timestamp.today()
    lookback_date = today - pd.Timedelta(days=LOOKBACK_485APOS_DAYS)

    # Active products
    active = prods[prods["market_status"].isin(["ACTV", "ACTIVE"])]
    active_summary = active.groupby("underlier").agg(
        n_rex_products=("is_rex", lambda x: int(x.sum())),
        n_comp_products=("is_rex", lambda x: int((1 - x).sum())),
    )

    # REX filings ever (any status)
    rex_any = prods[prods["is_rex"] == 1].groupby("underlier").size().rename("n_rex_filed_any")

    # Competitor 485APOS in last 180 days
    comp_485apos_recent = filings[
        (filings["is_rex"] == 0) &
        (filings["form"] == "485APOS") &
        (filings["filing_date"] >= lookback_date)
    ].groupby("underlier").size().rename("n_competitor_485apos_180d")

    # Days since last competitor filing (any form)
    comp_filings = filings[filings["is_rex"] == 0]
    if not comp_filings.empty:
        last_by_under = comp_filings.groupby("underlier")["filing_date"].max()
        days_since = ((today - last_by_under).dt.days).rename("days_since_last_competitor_filing")
    else:
        days_since = pd.Series(dtype=float, name="days_since_last_competitor_filing")

    out = active_summary.join([rex_any, comp_485apos_recent, days_since], how="outer")
    out = out.fillna({
        "n_rex_products": 0,
        "n_comp_products": 0,
        "n_rex_filed_any": 0,
        "n_competitor_485apos_180d": 0,
    })
    # Leave days_since NaN where no competitor has ever filed — that's clean whitespace
    return out


def build_universe() -> pd.DataFrame:
    """Join NASDAQ universe + bbg signals + product coverage."""
    nasdaq_uni = load_nasdaq_universe()
    if nasdaq_uni is None:
        raise RuntimeError("No NASDAQ universe snapshot. Run universe_loader first.")

    bbg = load_bbg_signals_df()
    coverage = load_product_coverage_with_filings()

    log.info("NASDAQ universe: %d, bbg signals: %d, coverage annotations: %d",
             len(nasdaq_uni), len(bbg), len(coverage))

    joined = nasdaq_uni[["name", "exchange"]].join(bbg, how="left")
    joined = joined.join(coverage, how="left")

    joined["has_bbg_data"] = joined["market_cap"].notna()
    joined = joined.fillna({
        "n_rex_products": 0, "n_comp_products": 0, "n_rex_filed_any": 0,
        "n_competitor_485apos_180d": 0,
    })

    return joined


def apply_universe_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Tradeable: mkt cap >= floor, has options market."""
    f = df[
        df["has_bbg_data"] &
        (df["market_cap"] >= MKT_CAP_FLOOR) &
        (df["total_oi"] >= OI_FLOOR)
    ].copy()
    log.info("After liquidity/options filters: %d tickers", len(f))
    return f


def apply_whitespace_filter(df: pd.DataFrame) -> pd.DataFrame:
    """Whitespace = NO live product anywhere AND no REX filing AND
    no competitor 485APOS in the last 180 days.

    Uses competitor_counts.parquet which has fund-name regex fallback —
    catches new products like TRADR's AXTI (master_data has no map_li_underlier
    yet) and REX filings via fund_extractions regex (rex_extra_*).

    The 180-day 485APOS gate (Wave A1, 2026-05-11): the audit found that
    n_competitor_485apos_180d was being COMPUTED and joined onto the
    universe but never USED as a filter. The report subtitle claims
    "no competitor 485APOS in last 180d" — this enforces it.
    """
    cc_path = _ROOT / "data" / "analysis" / "competitor_counts.parquet"
    if cc_path.exists():
        cc = pd.read_parquet(cc_path)
        # Total live products (any issuer) on each underlier
        live_long = cc.get("rex_active_long", 0) + cc.get("competitor_active_long", 0)
        live_short = cc.get("rex_active_short", 0) + cc.get("competitor_active_short", 0)
        live_total = live_long + live_short
        # REX has filed (any source: master_data is_rex OR fund_extractions regex)
        rex_filed = (cc.get("rex_active_long", 0) + cc.get("rex_active_short", 0)
                     + cc.get("rex_filed_long", 0) + cc.get("rex_filed_short", 0)
                     + cc.get("rex_extra_long", 0) + cc.get("rex_extra_short", 0))

        # Tickers with any live OR any REX filing
        excluded = set(live_total[live_total > 0].index) | set(rex_filed[rex_filed > 0].index)

        before = len(df)
        ws = df[~df.index.isin(excluded)].copy()
        log.info("Whitespace filter (cc-based): %d → %d (excluded %d)",
                 before, len(ws), before - len(ws))
    else:
        # Fallback to master-data-only filter
        ws = df[
            (df["n_comp_products"] == 0) &
            (df["n_rex_products"] == 0) &
            (df["n_rex_filed_any"] == 0)
        ].copy()
        log.info("After master-only whitespace filter: %d", len(ws))

    # 180-day competitor 485APOS gate — make the report subtitle honest.
    if "n_competitor_485apos_180d" in ws.columns:
        before = len(ws)
        ws = ws[ws["n_competitor_485apos_180d"].fillna(0) == 0].copy()
        log.info("After 180d competitor 485APOS gate: %d → %d (excluded %d)",
                 before, len(ws), before - len(ws))
    else:
        log.warning("n_competitor_485apos_180d column missing — 180d gate skipped")
    return ws


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    uni = build_universe()
    eligible = apply_universe_filters(uni)
    whitespace = apply_whitespace_filter(eligible)

    # Score
    mentions = load_apewisdom_map(set(whitespace.index))
    themes = load_themes()
    scored = compute_score_v3(whitespace, themes, mentions)
    scored = scored.sort_values("composite_score", ascending=False)

    scored.to_parquet(OUT, compression="snappy")

    # Report
    print("=" * 100)
    print("WHITESPACE v4 — NASDAQ universe, $500M floor, hardened filter")
    print("=" * 100)
    print(f"NASDAQ universe:            {len(uni):,}")
    print(f"  with bbg signal data:     {uni['has_bbg_data'].sum():,}")
    print(f"  missing bbg data (stooq): {(~uni['has_bbg_data']).sum():,}")
    print(f"Eligible (mkt cap + options): {len(eligible):,}")
    print(f"True whitespace:             {len(whitespace):,}")
    print()

    print("Top 20 by composite score:")
    for i, ticker in enumerate(scored.head(20).index, 1):
        r = scored.loc[ticker]
        sector = (r.get("sector") or "—")[:22]
        mcap = r["market_cap"]
        mcap_str = f"${mcap/1000:.1f}B" if mcap >= 1000 else f"${mcap:,.0f}M"
        rvol = r.get("rvol_90d", 0) or 0
        ret1m = r.get("ret_1m", 0) or 0
        ret1y = r.get("ret_1y", 0) or 0
        mentions = int(r.get("mentions_24h", 0) or 0)
        themes_str = r.get("themes", "") or "—"
        score = r["composite_score"]

        gate = "theme" if r.get("is_thematic") else ("mentions" if mentions else "signal")
        print(f"{i:>2}. {ticker:<6} {sector:<22} {mcap_str:<8}  "
              f"vol={rvol:>3.0f}  1m={ret1m:+4.0f}%  1y={ret1y:+5.0f}%  "
              f"mentions={mentions:>3}  [{gate}]  score={score:+.2f}")
        if themes_str != "—":
            print(f"    theme: {themes_str}")


if __name__ == "__main__":
    main()

"""Rebuild the forward-flow target correctly.

Previous version used the latest observation per underlier as 'as-of', which
leaves no observable forward window. This version picks an as-of date N days
in the past so we can actually measure the next 30 business days of flow.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

from webapp.services.ticker_normalize import normalize_underlier as _clean

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TS = _ROOT / "data" / "analysis" / "bbg_timeseries_panel.parquet"
DB = _ROOT / "data" / "etp_tracker.db"


def build_lookback_target(as_of_days_ago: int = 60, window: int = 30) -> pd.Series:
    """Pick an as-of date ~60 business days in the past. For each underlier,
    sum the flows across [as_of, as_of+window] business days. This is an
    OBSERVED forward window (not empty)."""
    ts = pd.read_parquet(TS)
    flow = ts[ts["metric"] == "daily_flow"].copy()
    flow["date"] = pd.to_datetime(flow["date"])

    # Map product ticker → underlier
    conn = sqlite3.connect(str(DB))
    try:
        m = pd.read_sql_query(
            "SELECT ticker, map_li_underlier FROM mkt_master_data "
            "WHERE primary_category='LI' AND map_li_underlier IS NOT NULL",
            conn,
        )
    finally:
        conn.close()
    m["prod"] = m["ticker"].astype(str).map(_clean)
    m["underlier"] = m["map_li_underlier"].astype(str).map(_clean)
    mapping = dict(zip(m["prod"], m["underlier"]))

    flow["underlier"] = flow["ticker"].map(mapping)
    flow = flow.dropna(subset=["underlier"])

    # Pick as-of date: `as_of_days_ago` business days before the latest
    latest = flow["date"].max()
    as_of = pd.Timestamp(latest) - pd.tseries.offsets.BDay(as_of_days_ago)
    window_end = as_of + pd.tseries.offsets.BDay(window)

    log.info("As-of: %s, window end: %s", as_of.date(), window_end.date())

    in_window = flow[(flow["date"] >= as_of) & (flow["date"] <= window_end)]
    agg = in_window.groupby("underlier")["value"].sum().rename(f"forward_flow_{window}d")
    log.info("Target: %d underliers with observed forward window", len(agg))
    return agg


def build_signal_snapshot_at(as_of_days_ago: int = 60) -> pd.DataFrame:
    """Build a snapshot of signals AT as-of (not current) — avoids look-ahead.
    For v1 we approximate: snapshot AUM and flow-trailing values at as_of."""
    ts = pd.read_parquet(TS)
    ts["date"] = pd.to_datetime(ts["date"])
    latest = ts["date"].max()
    as_of = pd.Timestamp(latest) - pd.tseries.offsets.BDay(as_of_days_ago)

    # AUM at as_of (closest prior date per ticker)
    aum = ts[(ts["metric"] == "aum") & (ts["date"] <= as_of)].copy()
    aum_snap = aum.sort_values("date").groupby("ticker").tail(1).set_index("ticker")["value"]
    aum_snap = aum_snap.rename("aum_as_of")

    # Trailing 90-day flow ending at as_of
    flow = ts[(ts["metric"] == "daily_flow") & (ts["date"] <= as_of)].copy()
    start = as_of - pd.tseries.offsets.BDay(90)
    trailing = flow[flow["date"] >= start].groupby("ticker")["value"].sum().rename("flow_90d_prior")

    snap = pd.concat([aum_snap, trailing], axis=1)

    # Map to underlier
    conn = sqlite3.connect(str(DB))
    try:
        m = pd.read_sql_query(
            "SELECT ticker, map_li_underlier FROM mkt_master_data "
            "WHERE primary_category='LI' AND map_li_underlier IS NOT NULL",
            conn,
        )
    finally:
        conn.close()
    m["prod"] = m["ticker"].astype(str).map(_clean)
    m["underlier"] = m["map_li_underlier"].astype(str).map(_clean)
    mapping = dict(zip(m["prod"], m["underlier"]))

    snap["underlier"] = snap.index.map(mapping)
    snap = snap.dropna(subset=["underlier"])

    agg = snap.groupby("underlier").agg({
        "aum_as_of": "sum",
        "flow_90d_prior": "sum",
    })
    return agg


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    target = build_lookback_target(as_of_days_ago=60, window=30)
    snap = build_signal_snapshot_at(as_of_days_ago=60)
    print(f"target samples: {len(target)}, snapshot samples: {len(snap)}")
    print("\nTarget summary:")
    print(target.describe())
    print("\nTarget + snapshot join preview:")
    joined = target.to_frame().join(snap, how="inner")
    print(joined.head(10).round(3))
    print(f"\nJoined rows (underliers with target and as-of snapshot): {len(joined)}")
    print(f"Non-zero target: {(target != 0).sum()}")

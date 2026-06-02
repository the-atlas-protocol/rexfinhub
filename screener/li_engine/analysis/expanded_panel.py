"""Expanded signal panel builder.

Combines:
    - bbg stock metrics (market_cap, adv, turnover, total_oi, put_call_skew,
      realized_vol_30d, realized_vol_90d, short_interest)
    - w5 momentum signals (5d, 1m, 3m, 6m, ytd, 1y returns per ticker)
    - ApeWisdom sentiment (if reachable)
    - Forward 30-day flow target (from time-series parquet) joined to latest
      underlier snapshot

Returns a tidy panel for multi-angle analysis: one row per underlier with all
signals + all targets.
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

from webapp.services.ticker_normalize import normalize_underlier as _clean

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
BBG = _ROOT / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm"
TS_PARQUET = _ROOT / "data" / "analysis" / "bbg_timeseries_panel.parquet"
OUT = _ROOT / "data" / "analysis" / "expanded_signal_panel.parquet"


def _coerce(v):
    if v in (None, "#ERROR", "#N/A", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_bbg_stock_signals(db: Path = DB) -> pd.DataFrame:
    conn = sqlite3.connect(str(db))
    try:
        run_id = conn.execute(
            "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
            "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    recs = []
    for ticker, blob in rows:
        if not blob:
            continue
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        d = parsed[0] if isinstance(parsed, list) else parsed
        mkt_cap = _coerce(d.get("Mkt Cap"))
        adv = _coerce(d.get("Avg Volume 30D"))
        last_price = _coerce(d.get("Last Price"))
        turnover = adv * last_price if (adv and last_price) else None
        call_oi = _coerce(d.get("Total Call OI")) or 0
        put_oi = _coerce(d.get("Total Put OI")) or 0
        total_oi = _coerce(d.get("Total OI")) or (call_oi + put_oi if (call_oi or put_oi) else None)
        skew = (call_oi - put_oi) / (call_oi + put_oi) if (call_oi + put_oi) > 0 else None
        recs.append({
            "ticker": _clean(ticker),
            "market_cap": mkt_cap,
            "adv_30d": adv,
            "turnover_30d": turnover,
            "total_oi": total_oi,
            "put_call_skew": skew,
            "realized_vol_30d": _coerce(d.get("Volatility 30D")),
            "realized_vol_90d": _coerce(d.get("Volatility 90D")),
            "short_interest": _coerce(d.get("Short Interest")),
        })
    df = pd.DataFrame.from_records(recs)
    df = df[df["ticker"] != ""].drop_duplicates("ticker").set_index("ticker")
    log.info("bbg stock: %d tickers", len(df))
    return df


def load_w5_momentum() -> pd.DataFrame:
    """Cross-sectional return signals per ticker."""
    df = pd.read_excel(BBG, sheet_name="w5")
    df["ticker"] = df["Ticker"].astype(str).map(_clean)
    df = df[df["ticker"] != ""]

    rename = {
        "1d%": "ret_1d", "5d%": "ret_5d", "1m%": "ret_1m",
        "3m%": "ret_3m", "6m%": "ret_6m", "ytd%": "ret_ytd", "1y%": "ret_1y",
    }
    keep = list(rename.keys())
    df = df[["ticker"] + keep]
    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.rename(columns=rename).drop_duplicates("ticker").set_index("ticker")
    log.info("w5 momentum: %d tickers (non-null ret_1m=%d)",
             len(df), df["ret_1m"].notna().sum())
    return df


def load_aum_trend(db: Path = DB) -> pd.DataFrame:
    """Per-underlier 12m AUM growth (from aum_history_json on products that
    map to that underlier). Aggregated across all REX + competitor products."""
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT map_li_underlier, aum, aum_history_json FROM mkt_master_data "
            "WHERE primary_category='LI' AND map_li_underlier IS NOT NULL "
            "AND map_li_underlier != '' AND aum IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    by_underlier: dict[str, list[tuple[float, float]]] = {}
    for underlier, aum_now, hist_json in rows:
        tk = _clean(underlier)
        if not tk or aum_now is None:
            continue
        aum_12m_ago = None
        if hist_json:
            try:
                h = json.loads(hist_json)
                aum_12m_ago = h.get("aum_12") or h.get("aum_11") or h.get("aum_13")
                aum_12m_ago = float(aum_12m_ago) if aum_12m_ago is not None else None
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        by_underlier.setdefault(tk, []).append((float(aum_now), aum_12m_ago))

    recs = []
    for tk, pairs in by_underlier.items():
        aum_now = sum(p[0] for p in pairs)
        aum_then = sum((p[1] or 0.0) for p in pairs if p[1] is not None)
        n_with_hist = sum(1 for p in pairs if p[1] is not None)
        if n_with_hist == 0 or aum_then == 0:
            continue
        growth = (aum_now - aum_then) / aum_then
        recs.append({"ticker": tk, "underlier_aum_12m_growth": growth,
                     "underlier_total_aum_now": aum_now})
    df = pd.DataFrame(recs).set_index("ticker") if recs else pd.DataFrame()
    log.info("AUM trend: %d underliers", len(df))
    return df


def load_apewisdom(max_pages: int = 5, timeout: float = 10.0) -> pd.DataFrame:
    """Current-snapshot sentiment from ApeWisdom (keyless)."""
    url = "https://apewisdom.io/api/v1.0/filter/{f}/page/{p}"
    recs: dict[str, dict] = {}
    for filt in ("all-stocks", "wallstreetbets"):
        for page in range(1, max_pages + 1):
            try:
                r = requests.get(url.format(f=filt, p=page), timeout=timeout)
                if r.status_code != 200:
                    break
                items = r.json().get("results", [])
                if not items:
                    break
                for it in items:
                    tk = _clean(it.get("ticker", ""))
                    if not tk:
                        continue
                    mentions = int(it.get("mentions", 0) or 0)
                    prev = int(it.get("mentions_24h_ago", 0) or 0)
                    delta = mentions - prev
                    if tk not in recs or mentions > recs[tk]["mentions_24h"]:
                        recs[tk] = {
                            "mentions_24h": mentions,
                            "mentions_delta_24h": delta,
                        }
                time.sleep(0.2)
            except Exception as e:
                log.warning("apewisdom %s p%d: %s", filt, page, e)
                break
    if not recs:
        return pd.DataFrame(columns=["mentions_24h", "mentions_delta_24h"])
    df = pd.DataFrame.from_dict(recs, orient="index")
    df.index.name = "ticker"
    log.info("sentiment: %d tickers", len(df))
    return df


def load_forward_flow_target(panel_path: Path = TS_PARQUET) -> pd.Series:
    """Per-underlier forward 30-day flow, aggregated across all products mapped
    to that underlier at each date, then taken at the most recent observation."""
    ts = pd.read_parquet(panel_path)
    fwd = ts[ts["metric"] == "forward_30d_flow"].copy()
    if fwd.empty:
        return pd.Series(dtype=float, name="forward_30d_flow")

    # bbg time-series ticker is a PRODUCT ticker (e.g. TSLT). Map to underlier.
    conn = sqlite3.connect(str(DB))
    try:
        m = pd.read_sql_query(
            "SELECT ticker, map_li_underlier FROM mkt_master_data "
            "WHERE map_li_underlier IS NOT NULL AND map_li_underlier != ''",
            conn,
        )
    finally:
        conn.close()
    m["ticker"] = m["ticker"].astype(str).map(_clean)
    m["underlier"] = m["map_li_underlier"].astype(str).map(_clean)
    mapping = dict(zip(m["ticker"], m["underlier"]))

    fwd["underlier"] = fwd["ticker"].map(mapping)
    fwd = fwd.dropna(subset=["underlier"])

    # latest date per underlier → sum across products on that date
    latest_per_u = fwd.groupby("underlier")["date"].max().reset_index().rename(columns={"date": "latest_date"})
    fwd = fwd.merge(latest_per_u, on="underlier")
    fwd = fwd[fwd["date"] == fwd["latest_date"]]
    agg = fwd.groupby("underlier")["value"].sum().rename("forward_30d_flow")
    log.info("forward_30d_flow target: %d underliers", len(agg))
    return agg


def load_existing_flow_panel(db: Path = DB) -> pd.DataFrame:
    """Fund-level flow aggregation per underlier (contemporaneous — what we
    had in v1). Kept for contrast with forward target."""
    conn = sqlite3.connect(str(db))
    try:
        df = pd.read_sql_query(
            """
            SELECT map_li_underlier AS underlier,
                   SUM(aum) AS total_aum,
                   SUM(fund_flow_3month) AS flow_3m,
                   AVG(total_return_3month) AS prod_return_3m,
                   COUNT(*) AS n_products
            FROM mkt_master_data
            WHERE primary_category='LI'
              AND map_li_underlier IS NOT NULL
              AND map_li_underlier != ''
              AND aum IS NOT NULL AND aum > 0
            GROUP BY map_li_underlier
            """,
            conn,
        )
    finally:
        conn.close()
    df["ticker"] = df["underlier"].astype(str).map(_clean)
    return df.set_index("ticker")[["total_aum", "flow_3m", "prod_return_3m", "n_products"]]


def build_panel(skip_sentiment: bool = False) -> pd.DataFrame:
    bbg = load_bbg_stock_signals()
    w5 = load_w5_momentum()
    aum_trend = load_aum_trend()
    flow = load_existing_flow_panel()
    fwd = load_forward_flow_target().to_frame()
    sentiment = pd.DataFrame() if skip_sentiment else load_apewisdom()

    panel = bbg.join(w5, how="outer")
    if not aum_trend.empty:
        panel = panel.join(aum_trend, how="left")
    panel = panel.join(flow, how="left")
    panel = panel.join(fwd, how="left")
    if not sentiment.empty:
        panel = panel.join(sentiment, how="left")

    panel.index.name = "ticker"
    return panel


def save(panel: pd.DataFrame, path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(path, compression="snappy")
    log.info("saved %d rows to %s (%.1f KB)", len(panel), path, path.stat().st_size / 1024)
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    panel = build_panel()
    save(panel)

    cov_lines = [f"{c}: {panel[c].notna().sum()}/{len(panel)} ({panel[c].notna().mean():.0%})"
                 for c in panel.columns]
    print(f"Panel: {panel.shape[0]} rows x {panel.shape[1]} cols")
    print("\nColumn coverage:")
    for line in cov_lines:
        print(f"  {line}")


if __name__ == "__main__":
    main()

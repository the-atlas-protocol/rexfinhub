"""v1.0.1 L&I Opportunity Engine — daily run.

Frozen methodology v1.0.1 (proportional 0-100, gates as flag, AI-stack theme).
See vault: li-opportunity-engine-plan.md, li-launch-framework-v1-draft.md.

What this writes every day:
  * li_engine_daily    — full universe (~6594) scored + gate flag + sector + theme tier.
  * li_etp_daily       — every L&I product's state (status, AUM, flows, underlier).
  * li_sector_daily    — sector aggregates (mentions, avg score) for rotation detection.

Run:  python -m screener.li_engine.run_v1
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from screener.li_engine.signals import (
    load_bbg_stock_signals,
    load_sentiment_signals,
    load_rex_filing_status,
    _clean_ticker,
)
from screener.li_engine import persistence

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
TAGS_CSV = _ROOT / "data" / "rules" / "ai_stack_tags.csv"

VERSION = "v1.0.1"

# Bucket weights (points). Positives sum to 100; SI is a penalty up to 8.
W = {"attention": 30, "liquidity": 22, "theme": 18, "race": 12, "momentum": 10, "vol": 8}
SI_PENALTY_MAX = 8.0
MCAP_FLOOR = 500.0
ATT_FLOOR = 5
SAT_MAX_COMPETITORS = 2


_ETP_SCHEMA = """
CREATE TABLE IF NOT EXISTS li_etp_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    run_timestamp TEXT NOT NULL,
    product_ticker TEXT NOT NULL,
    fund_name TEXT,
    issuer TEXT,
    market_status TEXT,
    aum REAL,
    fund_flow_1week REAL,
    fund_flow_1month REAL,
    inception_date TEXT,
    underlier TEXT,
    is_rex INTEGER,
    direction TEXT,
    leverage TEXT,
    UNIQUE(run_date, product_ticker)
);
CREATE INDEX IF NOT EXISTS idx_etp_daily_date ON li_etp_daily(run_date);
CREATE INDEX IF NOT EXISTS idx_etp_daily_status ON li_etp_daily(run_date, market_status);

CREATE TABLE IF NOT EXISTS li_sector_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    run_timestamp TEXT NOT NULL,
    sector TEXT NOT NULL,
    n_stocks INTEGER,
    total_mentions INTEGER,
    avg_score REAL,
    top_ticker TEXT,
    top_score REAL,
    UNIQUE(run_date, sector)
);
CREATE INDEX IF NOT EXISTS idx_sector_daily_date ON li_sector_daily(run_date);
"""


def _ensure_extended_schema(db_path: Path = DB) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_ETP_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _pct(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").rank(pct=True)


# ---------------------------------------------------------------------------
# Signal loaders for v1.0.1-only inputs (returns, SI, theme tags, race, sector)
# ---------------------------------------------------------------------------

def load_returns_si_sector(run_id=None) -> pd.DataFrame:
    """Momentum (1Y total return) + short interest ratio + GICS sector."""
    conn = sqlite3.connect(DB)
    try:
        if run_id is None:
            run_id = conn.execute(
                "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
                "AND stock_rows_written>0 ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()[0]
        rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?", (run_id,)
        ).fetchall()
    finally:
        conn.close()

    def f(d, k):
        v = d.get(k)
        try:
            return float(v) if v not in (None, "", "NaN", "#N/A", "#ERROR") else None
        except (TypeError, ValueError):
            return None

    recs = []
    for t, b in rows:
        if not b:
            continue
        try:
            d = json.loads(b)
            d = d[0] if isinstance(d, list) else d
        except json.JSONDecodeError:
            continue
        recs.append({
            "ticker": _clean_ticker(t),
            "ret_1y": f(d, "1Y Total Return"),
            "si_ratio": f(d, "Short Interest Ratio"),
            "sector": d.get("GICS Sector"),
        })
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    return df[df.ticker != ""].drop_duplicates("ticker", keep="last").set_index("ticker")


def load_theme_tags() -> pd.Series:
    if not TAGS_CSV.exists():
        log.warning("ai_stack_tags.csv not found at %s", TAGS_CSV)
        return pd.Series(dtype=object, name="theme_tier")
    df = pd.read_csv(TAGS_CSV)
    df["ticker"] = df["ticker"].astype(str).map(_clean_ticker)
    return df.drop_duplicates("ticker").set_index("ticker")["tier"].rename("theme_tier")


def load_competitor_signals() -> pd.DataFrame:
    """Per underlier: n_long_competitors (gate) + race_count (485APOS 180d + PEND)."""
    conn = sqlite3.connect(DB)
    try:
        prods = pd.read_sql_query(
            "SELECT map_li_underlier u, is_rex, market_status, map_li_direction dir "
            "FROM mkt_master_data WHERE primary_category='LI' "
            "AND map_li_underlier IS NOT NULL AND map_li_underlier!=''", conn)
        fil = pd.read_sql_query(
            "SELECT mmd.map_li_underlier u, mmd.is_rex "
            "FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id "
            "JOIN mkt_master_data mmd ON mmd.ticker = fe.class_symbol || ' US' "
            "WHERE f.form='485APOS' AND f.filing_date>=date('now','-180 day') "
            "AND mmd.map_li_underlier IS NOT NULL", conn)
    finally:
        conn.close()
    prods["u"] = prods["u"].astype(str).map(_clean_ticker)
    prods["is_long"] = prods["dir"].astype(str).str.lower().str.contains("long", na=False)
    act = prods[prods.market_status == "ACTV"]
    n_long_comp = act[(act.is_long) & (act.is_rex == 0)].groupby("u").size().rename("n_long_competitors")
    pend = prods[(prods.market_status == "PEND") & (prods.is_rex == 0)].groupby("u").size().rename("pend_ct")
    if not fil.empty:
        fil["u"] = fil["u"].astype(str).map(_clean_ticker)
        comp485 = fil[fil.is_rex == 0].groupby("u").size().rename("comp_485_180d")
    else:
        comp485 = pd.Series(dtype=int, name="comp_485_180d")
    out = pd.concat([n_long_comp, pend, comp485], axis=1).fillna(0)
    out["race_count"] = out.get("pend_ct", 0) + out.get("comp_485_180d", 0)
    return out


# ---------------------------------------------------------------------------
# ETP daily snapshot (population 2)
# ---------------------------------------------------------------------------

def snapshot_etp_daily(run_date: str, now: str) -> int:
    """Snapshot every L&I product's current state to li_etp_daily."""
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT ticker AS product_ticker, fund_name, "
            "COALESCE(issuer_nickname,issuer) AS issuer, market_status, aum, "
            "fund_flow_1week, fund_flow_1month, inception_date, "
            "map_li_underlier AS underlier, is_rex, "
            "map_li_direction AS direction, map_li_leverage_amount AS leverage "
            "FROM mkt_master_data WHERE primary_category='LI'", conn)
        df["run_date"] = run_date
        df["run_timestamp"] = now
        rows = df[["run_date", "run_timestamp", "product_ticker", "fund_name", "issuer",
                   "market_status", "aum", "fund_flow_1week", "fund_flow_1month",
                   "inception_date", "underlier", "is_rex", "direction", "leverage"]].values.tolist()
        cur = conn.cursor()
        cur.executemany(
            "INSERT OR REPLACE INTO li_etp_daily "
            "(run_date,run_timestamp,product_ticker,fund_name,issuer,market_status,aum,"
            "fund_flow_1week,fund_flow_1month,inception_date,underlier,is_rex,direction,leverage) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        log.info("li_etp_daily: %d products snapshotted", len(rows))
        return len(rows)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sector aggregates (rotation detection)
# ---------------------------------------------------------------------------

def aggregate_sectors(scored: pd.DataFrame, run_date: str, now: str) -> int:
    """Aggregate scored universe by GICS sector → li_sector_daily for rotation."""
    if "sector" not in scored.columns:
        return 0
    df = scored.dropna(subset=["sector"]).copy()
    if df.empty:
        return 0
    agg = df.groupby("sector").agg(
        n_stocks=("final_score", "size"),
        total_mentions=("mentions_24h", "sum"),
        avg_score=("final_score", "mean"),
    ).reset_index()
    # Top ticker per sector
    top = df.sort_values("final_score", ascending=False).groupby("sector").head(1)
    top = top.reset_index()[["sector", "ticker", "final_score"]].rename(
        columns={"ticker": "top_ticker", "final_score": "top_score"})
    agg = agg.merge(top, on="sector", how="left")
    agg["run_date"] = run_date
    agg["run_timestamp"] = now
    rows = agg[["run_date", "run_timestamp", "sector", "n_stocks", "total_mentions",
                "avg_score", "top_ticker", "top_score"]].values.tolist()
    conn = sqlite3.connect(DB)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO li_sector_daily "
            "(run_date,run_timestamp,sector,n_stocks,total_mentions,avg_score,top_ticker,top_score) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    log.info("li_sector_daily: %d sectors aggregated", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# v1.0.1 scorer — full universe, gate as flag
# ---------------------------------------------------------------------------

def main(skip_sentiment: bool = False) -> pd.DataFrame:
    bbg = load_bbg_stock_signals()
    rsi = load_returns_si_sector()
    theme = load_theme_tags()
    comp = load_competitor_signals()
    sent = pd.DataFrame() if skip_sentiment else load_sentiment_signals()
    rex = load_rex_filing_status()

    df = bbg.join(rsi, how="left")
    if not sent.empty and "mentions_24h" in sent.columns:
        df = df.join(sent[["mentions_24h"]], how="left")
    else:
        df["mentions_24h"] = 0
    df["mentions_24h"] = df["mentions_24h"].fillna(0)
    df["theme_tier"] = df.index.map(lambda t: theme.get(t)) if len(theme) else None
    df = df.join(comp, how="left")
    for c in ["n_long_competitors", "race_count"]:
        df[c] = df[c].fillna(0) if c in df.columns else 0
    df = df.join(rex, how="left")
    for c in ["has_rex_filing", "has_rex_launch"]:
        df[c] = df[c].fillna(False) if c in df.columns else False

    # ---- BUCKET POINTS (proportional, across the FULL UNIVERSE) ----
    df["mcap_num"] = pd.to_numeric(df["market_cap"], errors="coerce")
    att_mask = df["mentions_24h"] >= ATT_FLOOR
    df["attention_score"] = 0.0
    if att_mask.any():
        df.loc[att_mask, "attention_score"] = df.loc[att_mask, "mentions_24h"].rank(pct=True) * W["attention"]
    df["liquidity_score"] = _pct(df["turnover_30d"]).fillna(0) * W["liquidity"]
    df["theme_score"] = df["theme_tier"].map({"hot": W["theme"], "enabler": W["theme"] / 2.0}).fillna(0.0)
    df["race_score"] = (pd.to_numeric(df["race_count"], errors="coerce").clip(upper=3) / 3.0).fillna(0) * W["race"]
    df["momentum_score"] = _pct(df["ret_1y"]).fillna(0) * W["momentum"]
    df["vol_score"] = _pct(df["realized_vol_90d"]).fillna(0) * W["vol"]
    si_pct = _pct(df["si_ratio"])
    df["si_penalty"] = (si_pct.fillna(0.5) - 0.5).clip(lower=0) * 2 * SI_PENALTY_MAX

    df["final_score"] = (df["attention_score"] + df["liquidity_score"] + df["theme_score"]
                        + df["race_score"] + df["momentum_score"] + df["vol_score"]
                        - df["si_penalty"]).clip(0, 100)

    # ---- GATE FLAG (not a cut) ----
    has_theme = df["theme_tier"].isin(["hot", "enabler"])
    has_att = df["mentions_24h"] >= ATT_FLOOR
    df["passes_gate"] = ((df["mcap_num"] >= MCAP_FLOOR)
                        & (df["n_long_competitors"] <= SAT_MAX_COMPETITORS)
                        & (has_att | has_theme)).astype(int)

    df["n_signals"] = df[["turnover_30d", "realized_vol_90d", "ret_1y", "mentions_24h"]].notna().sum(axis=1)
    df = df.sort_values("final_score", ascending=False)
    df.index.name = "ticker"

    log.info("scored full universe: %d stocks (%d gate-pass)", len(df), int(df["passes_gate"].sum()))

    _ensure_extended_schema()
    run_date = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")

    # 1) score history (full universe, via existing persistence — pillar_scores_json + signal_values_json + final_score)
    persistence.write_run(df, weights_version=VERSION, notes="v1.0.1 full-universe")

    # 2) ETP daily snapshot
    snapshot_etp_daily(run_date, now)

    # 3) Sector aggregates
    aggregate_sectors(df.reset_index(), run_date, now)

    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    scored = main()
    print(f"\nfull-universe scored: {len(scored)} | gate-pass: {int(scored['passes_gate'].sum())}\n")
    cols = ["final_score", "passes_gate", "theme_tier", "sector",
            "attention_score", "liquidity_score", "theme_score",
            "race_score", "momentum_score", "vol_score", "si_penalty"]
    print(scored[cols].head(20).round(1).to_string())

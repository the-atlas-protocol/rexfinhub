"""Signal extractors for the L&I engine.

Each extractor returns a DataFrame indexed by ticker (clean, no exchange suffix)
with one column per raw signal. Missing values are encoded as NaN. Downstream
z-scoring and clipping is the scorer's job, not the extractor's.

Time-decay layer (Wave A2, 2026-05-11)
--------------------------------------
Stock Recs has historically treated all signals as equally fresh. They are not.
ApeWisdom mentions are a 24h window — if our last fetch was a week ago, those
mentions are no longer "current" retail interest. A REX filing from Sept 2024
with no follow-up amendment is functionally a dead lead — the desk moved on.

`apply_mention_decay()` and the half-life constants below are the canonical
decay primitives; both `whitespace_v3.py` and `launch_candidates.py` import
them so tuning happens in exactly one place.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

log = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "etp_tracker.db"


# ---------------------------------------------------------------------------
# DECAY CONFIG — single source of truth for all signal-staleness math
# ---------------------------------------------------------------------------
# Tune these constants to adjust decay aggressiveness. Imported by
# whitespace_v3 + launch_candidates so changes propagate automatically.

# Retail mentions: ApeWisdom returns a 24h window. Within FRESH window, no
# decay. Beyond it, weight halves every HALFLIFE days (exponential).
MENTION_FRESH_DAYS = 14
MENTION_HALFLIFE_DAYS = 7.0

# REX filings: an underlier we filed for ages ago without a follow-up
# amendment / launch is a dead lead. Step penalty (cliff) is intentional —
# matches how a portfolio manager mentally writes off a stale idea.
REX_FILING_STALE_DAYS = 90    # >90d, no follow-up: 50% penalty
REX_FILING_DEAD_DAYS = 180    # >180d, no follow-up: 80% penalty
REX_FILING_STALE_FACTOR = 0.50
REX_FILING_DEAD_FACTOR = 0.20

# Competitor filings: weight by recency over a 180-day audit window.
# Linear decay from 1.0 at t=0 to 0.0 at t=AUDIT_DAYS. Filings older than
# the window contribute nothing to "recent competitive pressure".
COMPETITOR_AUDIT_DAYS = 180


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def apply_mention_decay(mentions: float, age_days: float) -> float:
    """Exponential half-life decay for ApeWisdom mention counts.

    Within MENTION_FRESH_DAYS: no decay (returns mentions unchanged).
    Beyond it: mentions * 0.5 ** ((age_days - FRESH) / HALFLIFE_DAYS).

    Examples (HALFLIFE=7d, FRESH=14d):
        age=0d   -> 1.00x
        age=14d  -> 1.00x   (still fresh)
        age=21d  -> 0.50x   (one half-life past fresh)
        age=28d  -> 0.25x
        age=35d  -> 0.125x
    """
    if mentions is None or (isinstance(mentions, float) and math.isnan(mentions)):
        return 0.0
    if age_days is None or age_days <= MENTION_FRESH_DAYS:
        return float(mentions)
    excess = age_days - MENTION_FRESH_DAYS
    return float(mentions) * (0.5 ** (excess / MENTION_HALFLIFE_DAYS))


def rex_filing_decay_factor(days_since_filing: float | None) -> float:
    """Step decay for stale REX filings without follow-up.

    Returns the multiplier to apply to a candidate's composite score
    based on how long ago we last filed for this underlier.
    """
    if days_since_filing is None:
        return 1.0  # no filing context -> no penalty
    if days_since_filing >= REX_FILING_DEAD_DAYS:
        return REX_FILING_DEAD_FACTOR
    if days_since_filing >= REX_FILING_STALE_DAYS:
        return REX_FILING_STALE_FACTOR
    return 1.0


def competitor_filing_recency_weight(days_since_filing: float | None) -> float:
    """Linear-decay weight for a single competitor filing within the audit
    window. Older filings contribute less to "current competitive pressure"
    than fresh ones. Outside AUDIT_DAYS contributes 0.
    """
    if days_since_filing is None or days_since_filing < 0:
        return 0.0
    if days_since_filing >= COMPETITOR_AUDIT_DAYS:
        return 0.0
    return 1.0 - (days_since_filing / COMPETITOR_AUDIT_DAYS)


def days_between(later: datetime | str | pd.Timestamp,
                 earlier: datetime | str | pd.Timestamp) -> float | None:
    """Days between two date-like values; returns None on parse failure."""
    try:
        l = pd.to_datetime(later)
        e = pd.to_datetime(earlier)
        if pd.isna(l) or pd.isna(e):
            return None
        delta = (l - e).total_seconds() / 86400.0
        return float(delta)
    except Exception:
        return None


def _clean_ticker(t: str) -> str:
    """Strip Bloomberg exchange suffix ('AAPL US' -> 'AAPL')."""
    if not isinstance(t, str):
        return ""
    return t.split()[0].upper().strip()


# ---------------------------------------------------------------------------
# Bloomberg stock data (Pillars 1-3: liquidity, options, volatility)
# ---------------------------------------------------------------------------

@dataclass
class BbgStockSignals:
    turnover_30d: pd.Series
    adv_30d: pd.Series
    market_cap: pd.Series
    total_oi: pd.Series
    put_call_skew: pd.Series
    realized_vol_30d: pd.Series
    realized_vol_90d: pd.Series


def _coerce_float(v):
    if v is None or v == "#ERROR" or v == "#N/A" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_bbg_stock_signals(pipeline_run_id: int | None = None, db_path: Path = _DB_PATH) -> pd.DataFrame:
    """Load bbg stock metrics from mkt_stock_data JSON blobs.

    If pipeline_run_id is None, uses the most recent completed run.
    """
    conn = sqlite3.connect(db_path)
    try:
        if pipeline_run_id is None:
            row = conn.execute(
                "SELECT id FROM mkt_pipeline_runs "
                "WHERE status='completed' AND stock_rows_written > 0 "
                "ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("No completed pipeline runs with stock data found")
            pipeline_run_id = row[0]

        rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id = ?",
            (pipeline_run_id,),
        ).fetchall()
    finally:
        conn.close()

    records = []
    for ticker, data_json in rows:
        if not data_json:
            continue
        try:
            parsed = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        blob = parsed[0] if isinstance(parsed, list) else parsed

        mkt_cap = _coerce_float(blob.get("Mkt Cap"))
        adv = _coerce_float(blob.get("Avg Volume 30D"))
        last_price = _coerce_float(blob.get("Last Price"))
        turnover = None
        if adv is not None and last_price is not None:
            # shares outstanding approx = mkt_cap / price; turnover = adv / shares_out
            # but simpler proxy: $ turnover = adv * last_price
            turnover = adv * last_price

        call_oi = _coerce_float(blob.get("Total Call OI")) or 0.0
        put_oi = _coerce_float(blob.get("Total Put OI")) or 0.0
        total_oi = _coerce_float(blob.get("Total OI"))
        if total_oi is None:
            total_oi = (call_oi + put_oi) if (call_oi or put_oi) else None
        skew = None
        if call_oi + put_oi > 0:
            skew = (call_oi - put_oi) / (call_oi + put_oi)

        records.append({
            "ticker": _clean_ticker(ticker),
            "market_cap": mkt_cap,
            "adv_30d": adv,
            "turnover_30d": turnover,
            "total_oi": total_oi,
            "put_call_skew": skew,
            "realized_vol_30d": _coerce_float(blob.get("Volatility 30D")),
            "realized_vol_90d": _coerce_float(blob.get("Volatility 90D")),
            "last_price": last_price,
        })

    df = pd.DataFrame.from_records(records)
    if df.empty:
        log.warning("load_bbg_stock_signals: no rows for pipeline_run_id=%s", pipeline_run_id)
        return df
    df = df[df["ticker"] != ""]
    df = df.drop_duplicates(subset="ticker", keep="last").set_index("ticker")
    log.info("Loaded bbg stock signals for %d tickers (run_id=%s)", len(df), pipeline_run_id)
    return df


# ---------------------------------------------------------------------------
# Competitive whitespace (Pillar 4)
# ---------------------------------------------------------------------------

def load_competitive_whitespace(db_path: Path = _DB_PATH) -> pd.Series:
    """1 - (density / max_density) per underlier.

    Density = count of existing 2x/3x/4x leveraged products per underlier.
    Returns a Series indexed by underlier ticker (cleaned), in [0, 1].
    """
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT map_li_underlier AS underlier,
                   COUNT(*) AS n_products
            FROM mkt_master_data
            WHERE map_li_underlier IS NOT NULL
              AND map_li_underlier != ''
              AND primary_category = 'LI'
              AND (map_li_leverage_amount IN ('2.0', '3.0', '4.0', '2x', '3x', '4x')
                   OR CAST(map_li_leverage_amount AS REAL) >= 2.0)
            GROUP BY map_li_underlier
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        log.warning("load_competitive_whitespace: no LI products found")
        return pd.Series(dtype=float, name="density_score")

    df["ticker"] = df["underlier"].astype(str).map(_clean_ticker)
    df = df[df["ticker"] != ""]
    # Aggregate across duplicates (e.g. 'AMD' and 'AMD US' both clean to 'AMD')
    agg = df.groupby("ticker", as_index=True)["n_products"].sum()
    max_n = agg.max()
    s = (1.0 - (agg / max_n)).rename("density_score")
    log.info("Loaded competitive whitespace for %d distinct underliers (max_products=%d)",
             len(s), max_n)
    return s


# ---------------------------------------------------------------------------
# OC Equity / Blue Ocean ATS (Pillar 5)
# ---------------------------------------------------------------------------

def load_oc_equity_signals(xlsm_path: Path | None = None) -> pd.DataFrame:
    """Load Korean overnight demand signals from the OC sheet.

    Returns DataFrame indexed by ticker with columns:
        oc_volume_1w, oc_wow_delta, oc_1w_3m_ratio
    Only tickers with non-null 1W traded value are included; absence is
    treated as 'no signal', not 'bad signal' (per methodology §5).
    """
    if xlsm_path is None:
        # Prefer the authoritative resolver, but fall back to the local cache if
        # Graph API is transiently unavailable — the OC sheet is a backtest/
        # reporting input, not a real-time source.
        try:
            from screener.config import DATA_FILE
            xlsm_path = DATA_FILE
        except Exception as e:
            log.warning("load_oc_equity_signals: falling back to local cache (%s)", e)
            xlsm_path = Path(__file__).resolve().parent.parent.parent / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm"

    try:
        df = pd.read_excel(xlsm_path, sheet_name="OC", engine="openpyxl")
    except Exception as e:
        log.warning("load_oc_equity_signals: could not read OC sheet: %s", e)
        return pd.DataFrame(columns=["oc_volume_1w", "oc_wow_delta", "oc_1w_3m_ratio"])

    right = df[["Ticker.1", "1W Traded Value.1", "1M Traded Value.1", "3M Traded Value.1"]].copy()
    right.columns = ["ticker_raw", "vol_1w", "vol_1m", "vol_3m"]
    right = right.dropna(subset=["ticker_raw"])
    right["ticker"] = right["ticker_raw"].astype(str).map(_clean_ticker)
    right = right[right["ticker"] != ""]
    right = right.dropna(subset=["vol_1w"])

    right["oc_volume_1w"] = right["vol_1w"]
    right["oc_wow_delta"] = right["vol_1w"] - (right["vol_1m"] / 4.0)
    right["oc_1w_3m_ratio"] = right["vol_1w"] / (right["vol_3m"] / 12.0).replace(0, pd.NA)

    out = right.set_index("ticker")[["oc_volume_1w", "oc_wow_delta", "oc_1w_3m_ratio"]]
    out = out[~out.index.duplicated(keep="first")]
    log.info("Loaded OC equity signals for %d tickers", len(out))
    return out


# ---------------------------------------------------------------------------
# Social sentiment — ApeWisdom (Pillar 6)
# ---------------------------------------------------------------------------

_APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/{filter}/page/{page}"
_APEWISDOM_FILTERS = ("all-stocks", "wallstreetbets")


def load_sentiment_signals(max_pages: int = 10, timeout: float = 15.0) -> pd.DataFrame:
    """Fetch retail attention from ApeWisdom.

    Returns DataFrame indexed by ticker with:
        mentions_24h: raw mention count in trailing 24h
        mentions_delta_24h: mentions - mentions_24h_ago (raw change)
        mentions_delta_pct: pct change
        rank_improvement: rank_24h_ago - rank (positive = rising)
    Tickers not in ApeWisdom's trending tail are absent (no signal, not zero).
    """
    records: dict[str, dict] = {}
    for filt in _APEWISDOM_FILTERS:
        for page in range(1, max_pages + 1):
            url = _APEWISDOM_URL.format(filter=filt, page=page)
            try:
                resp = requests.get(url, timeout=timeout)
                if resp.status_code != 200:
                    log.warning("ApeWisdom %s p%d: HTTP %d", filt, page, resp.status_code)
                    break
                payload = resp.json()
            except Exception as e:
                log.warning("ApeWisdom %s p%d: %s", filt, page, e)
                break

            items = payload.get("results", [])
            if not items:
                break

            for item in items:
                ticker = _clean_ticker(item.get("ticker", ""))
                if not ticker:
                    continue
                mentions = int(item.get("mentions", 0) or 0)
                mentions_prev = int(item.get("mentions_24h_ago", 0) or 0)
                rank = int(item.get("rank", 0) or 0)
                rank_prev = int(item.get("rank_24h_ago", 0) or 0)

                delta = mentions - mentions_prev
                delta_pct = (delta / mentions_prev) if mentions_prev > 0 else None
                rank_improve = (rank_prev - rank) if (rank and rank_prev) else None

                existing = records.get(ticker)
                if existing is None or mentions > existing["mentions_24h"]:
                    records[ticker] = {
                        "mentions_24h": mentions,
                        "mentions_delta_24h": delta,
                        "mentions_delta_pct": delta_pct,
                        "rank_improvement": rank_improve,
                    }
            time.sleep(0.2)

            if len(items) < 50:
                break

    if not records:
        log.warning("load_sentiment_signals: ApeWisdom returned no usable data")
        return pd.DataFrame(columns=["mentions_24h", "mentions_delta_24h", "mentions_delta_pct", "rank_improvement", "mentions_fetched_at"])

    df = pd.DataFrame.from_dict(records, orient="index")
    df.index.name = "ticker"
    # Stamp every row with fetch timestamp so downstream decay logic can age
    # mentions correctly when the parquet is consumed days/weeks later.
    df["mentions_fetched_at"] = _now_utc().isoformat()
    log.info("Loaded sentiment signals for %d tickers", len(df))
    return df


# ---------------------------------------------------------------------------
# Filing status join (for downstream filtering, not a scored signal)
# ---------------------------------------------------------------------------

def load_etf_ticker_set(db_path: Path = _DB_PATH) -> set[str]:
    """Return cleaned tickers of every product in mkt_master_data (ETFs/ETPs).

    Used to filter ETFs out of the recommender output when we only want
    underlier-level candidates for filing.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM mkt_master_data WHERE ticker IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {_clean_ticker(r[0]) for r in rows if r[0]}


def load_rex_filing_status(db_path: Path = _DB_PATH) -> pd.DataFrame:
    """For each underlier, aggregate REX filing status.

    Returns DataFrame indexed by underlier ticker with:
        has_rex_filing (bool), has_rex_launch (bool), rex_filing_count (int)
    """
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT map_li_underlier AS underlier,
                   COUNT(*) AS total,
                   SUM(CASE WHEN market_status = 'EFFECTIVE' THEN 1 ELSE 0 END) AS effective_ct,
                   SUM(CASE WHEN market_status = 'PENDING' THEN 1 ELSE 0 END) AS pending_ct
            FROM mkt_master_data
            WHERE map_li_underlier IS NOT NULL
              AND is_rex = 1
              AND primary_category = 'LI'
            GROUP BY map_li_underlier
            """,
            conn,
        )
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=["has_rex_filing", "has_rex_launch", "rex_filing_count"])
    df["ticker"] = df["underlier"].astype(str).map(_clean_ticker)
    df["has_rex_filing"] = (df["total"] > 0)
    df["has_rex_launch"] = (df["effective_ct"] > 0)
    df["rex_filing_count"] = df["total"].astype(int)
    return df.set_index("ticker")[["has_rex_filing", "has_rex_launch", "rex_filing_count"]]

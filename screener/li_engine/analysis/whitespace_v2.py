"""Whitespace scorer v2 — retail-demand-priority weights.

Motivation: v1 used weights derived from IC vs log(AUM). Those weights
promoted structural liquidity (OI, market cap, turnover) which are NOT
retail-demand drivers — they are selection criteria that issuers already
apply when choosing which stocks to launch products on.

v2 uses weights derived from IC vs the BINARY SUCCESS target (AUM >= $50M in
18mo) on the post-launch panel. Against the binary outcome, realized vol
dominates (+0.26), not liquidity. Short interest ratio and institutional
ownership are the strongest NEGATIVE predictors.

Liquidity signals become FILTERS, not scoring inputs.

Universe: US equities, mkt cap >= $1B, options active.
Filter: zero active competitor 2x products, zero REX filings ever.
Scoring: vol-dominated, with retail attention + thematic bonus.
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
import yaml

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
THEMES_YAML = _ROOT / "screener" / "li_engine" / "themes.yaml"
OUT = _ROOT / "data" / "analysis" / "whitespace_v2.parquet"


def _clean(t):
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


def _coerce(v):
    if v in (None, "", "#ERROR", "#N/A", "N/A"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_universe() -> pd.DataFrame:
    """Large-cap US equities with options activity — raw universe before
    whitespace filter applied."""
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
        # LIQUIDITY FLOOR (filter, not score)
        if mkt_cap is None or mkt_cap < 1000:  # $1B
            continue
        if total_oi is None or total_oi < 5000:  # meaningful options activity
            continue

        insider = _coerce(d.get("% Insider Shares Outstanding"))
        if insider is not None and insider > 100:
            insider = None  # COIN-style data error

        recs.append({
            "ticker": _clean(ticker),
            # Filters / context (not scored)
            "market_cap": mkt_cap,
            "total_oi": total_oi,
            "turnover": _coerce(d.get("Turnover / Traded Value")),
            "adv_30d": _coerce(d.get("Avg Volume 30D")),
            "last_price": _coerce(d.get("Last Price")),
            "sector": d.get("GICS Sector"),
            "high_52w": _coerce(d.get("52W High")),
            # Scoring signals
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
    log.info("Universe (post-filter): %d tickers", len(df))
    return df


def annotate_product_coverage(universe: pd.DataFrame) -> pd.DataFrame:
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
    finally:
        conn.close()
    prods["underlier"] = prods["underlier"].astype(str).map(_clean)
    prods = prods[prods["underlier"] != ""]

    active = prods[prods["market_status"].isin(["ACTV", "ACTIVE"])]
    summary = active.groupby("underlier").agg(
        n_rex_products=("is_rex", lambda x: int(x.sum())),
        n_comp_products=("is_rex", lambda x: int((1 - x).sum())),
    )
    rex_any = prods[prods["is_rex"] == 1].groupby("underlier").size().rename("n_rex_filed_any")
    summary = summary.join(rex_any, how="left").fillna({"n_rex_filed_any": 0})

    out = universe.join(summary, how="left").fillna({
        "n_rex_products": 0, "n_comp_products": 0, "n_rex_filed_any": 0,
    })
    return out


def load_apewisdom_map(tickers: set[str]) -> dict[str, int]:
    """Legacy mentions-only map. Kept for backward compat with v2/v3 scorers."""
    full = load_apewisdom_full_map(tickers)
    return {t: blob["mentions_24h"] for t, blob in full.items()}


def load_apewisdom_full_map(tickers: set[str], max_pages: int = 6) -> dict[str, dict]:
    """Richer ApeWisdom fetch — returns per-ticker rank, mentions, and inflection.

    Used by the A3 tiered signal_strength module (needs rank + delta to decide
    URGENT vs STRONG). Same network cost as the legacy single-int variant.

    Returned blob keys:
        mentions_24h, apewisdom_rank, mentions_delta_24h,
        mentions_delta_pct, rank_improvement
    """
    url = "https://apewisdom.io/api/v1.0/filter/{f}/page/{p}"
    recs: dict[str, dict] = {}
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
                    if t not in tickers:
                        continue
                    mentions = int(it.get("mentions", 0) or 0)
                    mentions_prev = int(it.get("mentions_24h_ago", 0) or 0)
                    rank = int(it.get("rank", 0) or 0) or None
                    rank_prev = int(it.get("rank_24h_ago", 0) or 0) or None
                    delta = mentions - mentions_prev
                    delta_pct = (delta / mentions_prev) if mentions_prev > 0 else None
                    rank_improve = (rank_prev - rank) if (rank and rank_prev) else None
                    blob = {
                        "mentions_24h": mentions,
                        "apewisdom_rank": rank,
                        "mentions_delta_24h": delta,
                        "mentions_delta_pct": delta_pct,
                        "rank_improvement": rank_improve,
                    }
                    # Keep the highest-mentions observation when a ticker
                    # appears on multiple pages / filters.
                    existing = recs.get(t)
                    if existing is None or mentions > existing["mentions_24h"]:
                        recs[t] = blob
                time.sleep(0.15)
            except Exception as e:
                log.warning("apewisdom: %s", e)
                break
    return recs


def load_themes() -> dict[str, list[str]]:
    """Return {theme_name: [tickers in that theme]} from themes.yaml."""
    if not THEMES_YAML.exists():
        return {}
    with THEMES_YAML.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("themes", {})


# ---------------------------------------------------------------------------
# Scoring — retail-demand priority
# ---------------------------------------------------------------------------

WEIGHTS = {
    # Positive
    "rvol_30d":      0.20,   # IC vs success = +0.257 (TOP)
    "rvol_90d":      0.15,   # IC vs success = +0.254 — vol cluster
    "mentions_z":    0.18,   # retail demand live signal (prior; no backtest)
    "theme_bonus":   0.12,   # thematic relevance from themes.yaml
    "ret_1y":        0.10,   # sustained trend
    "insider_pct":   0.08,   # IC vs success = +0.161
    "ret_1m":        0.02,   # weak positive in post-launch
    # Negative
    "si_ratio":      -0.08,  # IC vs success = -0.208 (strongest neg)
    "inst_own_pct":  -0.07,  # IC vs success = -0.154 (retail avoids heavy inst)
}


def _zscore(s: pd.Series, log_transform: bool = False) -> pd.Series:
    if s.empty:
        return s
    x = s.copy()
    if log_transform:
        x = np.log1p(x.clip(lower=0))
    mu, sd = x.mean(skipna=True), x.std(skipna=True)
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return ((x - mu) / sd).clip(-3, 3)


def compute_score(df: pd.DataFrame, themes: dict[str, list[str]],
                  mentions_map: dict[str, int]) -> pd.DataFrame:
    out = df.copy()

    # Retail mentions signal
    out["mentions_24h"] = out.index.map(lambda t: mentions_map.get(t, 0))
    # Log-transform because mention counts are heavy-tailed
    out["mentions_z"] = _zscore(out["mentions_24h"], log_transform=True)

    # Thematic bonus: stocks in curated themes get a boost
    theme_tickers = set()
    ticker_theme_map: dict[str, list[str]] = {}
    for theme, tks in themes.items():
        for t in tks:
            tc = _clean(t)
            theme_tickers.add(tc)
            ticker_theme_map.setdefault(tc, []).append(theme)
    out["is_thematic"] = out.index.isin(theme_tickers).astype(float)
    out["themes"] = out.index.map(lambda t: ", ".join(ticker_theme_map.get(t, [])))
    out["theme_bonus"] = out["is_thematic"] * 2.0  # +2 std equivalent

    # Raw signals → z-scores
    for col in ("rvol_30d", "rvol_90d", "ret_1m", "ret_1y",
                "si_ratio", "insider_pct", "inst_own_pct"):
        if col not in out.columns:
            out[f"{col}_z"] = 0
            continue
        out[f"{col}_z"] = _zscore(out[col])

    # Composite
    score = pd.Series(0.0, index=out.index)
    score += WEIGHTS["rvol_30d"] * out["rvol_30d_z"].fillna(0)
    score += WEIGHTS["rvol_90d"] * out["rvol_90d_z"].fillna(0)
    score += WEIGHTS["mentions_z"] * out["mentions_z"].fillna(0)
    score += WEIGHTS["theme_bonus"] * out["theme_bonus"].fillna(0)
    score += WEIGHTS["ret_1y"] * out["ret_1y_z"].fillna(0)
    score += WEIGHTS["insider_pct"] * out["insider_pct_z"].fillna(0)
    score += WEIGHTS["ret_1m"] * out["ret_1m_z"].fillna(0)
    score += WEIGHTS["si_ratio"] * out["si_ratio_z"].fillna(0)
    score += WEIGHTS["inst_own_pct"] * out["inst_own_pct_z"].fillna(0)

    out["composite_score"] = score
    out["score_pct"] = out["composite_score"].rank(pct=True) * 100
    return out


def top_drivers(row: pd.Series) -> list[tuple[str, float, str]]:
    """Return ordered list of (signal, contribution, human-readable) for the top
    contributors to this ticker's score."""
    driver_components = {
        "rvol_30d_z":    ("30-day realized volatility (retail-leverage magnet)",
                          WEIGHTS["rvol_30d"]),
        "rvol_90d_z":    ("90-day realized volatility (sustained high vol)",
                          WEIGHTS["rvol_90d"]),
        "mentions_z":    ("retail mention volume on ApeWisdom",
                          WEIGHTS["mentions_z"]),
        "theme_bonus":   ("thematic relevance (theme-driven retail buying)",
                          WEIGHTS["theme_bonus"]),
        "ret_1y_z":      ("1-year price trend",
                          WEIGHTS["ret_1y"]),
        "insider_pct_z": ("insider ownership (alignment signal)",
                          WEIGHTS["insider_pct"]),
    }
    contribs = []
    for col, (label, w) in driver_components.items():
        v = row.get(col, 0) or 0
        contrib = v * w
        if contrib > 0:
            contribs.append((col, contrib, label))
    contribs.sort(key=lambda x: -x[1])
    return contribs[:3]


def negative_flags(row: pd.Series) -> list[str]:
    """Any negative signals worth flagging to the reader."""
    flags = []
    si_z = row.get("si_ratio_z", 0) or 0
    if si_z > 1.5:
        flags.append(f"elevated short-interest ratio (SI {row.get('si_ratio', 0):.1f}×)")
    inst = row.get("inst_own_pct", 0) or 0
    if inst > 80:
        flags.append(f"heavy institutional ownership ({inst:.0f}%)")
    ret_3m = row.get("ret_3m", 0) or 0
    if ret_3m > 50:
        flags.append(f"ran up {ret_3m:+.0f}% in 3m (mean-reversion risk)")
    return flags


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    universe = load_universe()
    universe = annotate_product_coverage(universe)

    # Apply whitespace filter BEFORE mentions lookup (cheaper API)
    whitespace = universe[
        (universe["n_comp_products"] == 0) &
        (universe["n_rex_products"] == 0) &
        (universe["n_rex_filed_any"] == 0)
    ].copy()
    log.info("Whitespace: %d of %d tickers (zero competitors, no REX filing)",
             len(whitespace), len(universe))

    # Fetch mentions for whitespace universe only
    mentions = load_apewisdom_map(set(whitespace.index))
    log.info("Mentions lookup: %d tickers with non-zero mentions", len(mentions))

    themes = load_themes()
    log.info("Themes loaded: %d themes, %d tagged tickers",
             len(themes), sum(len(v) for v in themes.values()))

    scored = compute_score(whitespace, themes, mentions)
    scored = scored.sort_values("composite_score", ascending=False)

    scored.to_parquet(OUT, compression="snappy")
    log.info("Wrote %s (%d rows)", OUT, len(scored))

    # Print the top 25 with drivers
    print("=" * 100)
    print("TOP 25 WHITESPACE CANDIDATES v2 (retail-demand priority)")
    print("=" * 100)
    for i, ticker in enumerate(scored.head(25).index, 1):
        row = scored.loc[ticker]
        sector = (row.get("sector") or "—")[:22]
        mcap = row["market_cap"] / 1000
        rvol = row.get("rvol_90d", 0) or 0
        ret1y = row.get("ret_1y", 0) or 0
        mentions = int(row.get("mentions_24h", 0) or 0)
        themes_str = row.get("themes", "") or "—"
        score = row["composite_score"]

        print(f"\n{i:>2}. {ticker:<7} {sector:<22}  ${mcap:>5,.1f}B  "
              f"vol={rvol:>4.0f}  ret1y={ret1y:+5.0f}%  mentions={mentions:>4}  "
              f"score={score:+.2f}")
        print(f"    themes: {themes_str}")
        drivers = top_drivers(row)
        for _, _, label in drivers:
            print(f"    + {label}")
        for flag in negative_flags(row):
            print(f"    ! {flag}")


if __name__ == "__main__":
    main()

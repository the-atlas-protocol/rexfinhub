"""Launch candidates — stocks REX has FILED for but not yet launched.

Per Ryu's spec: "stocks that we have filed for which we identify as good and
deserving of a launch. As long as there isn't a live product already out
there. If there is a filing from a competitor of that stock it should be
indicated by the # Filed Competitors."

Pipeline:
    1. Find all underliers REX has filed for (master_data is_rex=1 + fund_extractions regex)
    2. Exclude underliers where we already have an active product
    3. Exclude underliers where ANY competitor has an active product
    4. Exclude paused filings (BBUP/FIGO/SPOU per Ryu — bbg leaves them as PEND but they won't launch)
    5. Score remaining underliers using whitespace_v4 composite (with hot-theme boost)
    6. Annotate # filed competitors per underlier
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT = _ROOT / "data" / "analysis" / "launch_candidates.parquet"

# Manual exclusion list — REX filings Ryu flagged as paused / not actually pursuing
PAUSED_TICKERS = {"BBUP", "FIGO", "SPOU"}


def _clean(t):
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


def _coerce(v):
    if v in (None, "", "#ERROR", "#N/A"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_rex_filed_underliers() -> dict[str, dict]:
    """Return {underlier: {direction, source, fund_name, status}}.
    Combines mkt_master_data is_rex=1 + fund-name regex on REX-registrant filings."""
    conn = sqlite3.connect(str(DB))
    try:
        master = pd.read_sql_query(
            """SELECT ticker, fund_name, market_status, map_li_underlier,
                      map_li_direction, map_li_leverage_amount, inception_date
               FROM mkt_master_data
               WHERE is_rex = 1 AND primary_category = 'LI'
                 AND map_li_underlier IS NOT NULL AND map_li_underlier != ''""",
            conn,
        )

        # All REX filings via registrant
        fe_rex = pd.read_sql_query(
            """
            SELECT fe.series_name, fe.class_contract_name, f.filing_date, f.form, f.registrant
            FROM fund_extractions fe
            JOIN filings f ON f.id = fe.filing_id
            WHERE f.registrant LIKE '%REX%' OR f.registrant LIKE '%ETF Opportunities%'
            """,
            conn,
        )
    finally:
        conn.close()

    out: dict[str, dict] = {}

    # From master_data
    master["underlier"] = master["map_li_underlier"].astype(str).map(_clean)
    master = master[master["underlier"] != ""]
    for _, r in master.iterrows():
        u = r["underlier"]
        if u in PAUSED_TICKERS:
            continue
        if u not in out:
            out[u] = {
                "underlier": u,
                "direction": (r.get("map_li_direction") or "").strip() or "Long",
                "leverage": r.get("map_li_leverage_amount") or "2.0",
                "rex_fund_name": r.get("fund_name"),
                "rex_ticker": r.get("ticker"),
                "rex_market_status": r.get("market_status"),
                "rex_inception": r.get("inception_date"),
                "source": "master_data",
            }

    # From regex on fund names
    from screener.li_engine.analysis.filed_underliers import extract_underlier
    fe_rex["underlier"] = fe_rex["series_name"].apply(extract_underlier)
    fb = fe_rex["underlier"].isna()
    fe_rex.loc[fb, "underlier"] = fe_rex.loc[fb, "class_contract_name"].apply(extract_underlier)
    fe_rex = fe_rex.dropna(subset=["underlier"])
    fe_rex["underlier"] = fe_rex["underlier"].str.upper()

    # Detect direction from name
    import re
    for _, r in fe_rex.iterrows():
        u = r["underlier"]
        if u in PAUSED_TICKERS or u in out:
            continue
        name = r.get("series_name") or r.get("class_contract_name") or ""
        direction = "Long"
        if re.search(r"\b(?:Inverse|Short|Bear)\b", name, re.IGNORECASE):
            direction = "Short"
        out[u] = {
            "underlier": u,
            "direction": direction,
            "leverage": "2.0",
            "rex_fund_name": name,
            "rex_ticker": None,
            "rex_market_status": "FILED",
            "rex_inception": None,
            "source": "fund_extractions_regex",
        }

    return out


def load_competitor_status() -> pd.DataFrame:
    """Return per underlier: n_active_competitor, n_filed_competitor (filed not active)."""
    cc_path = _ROOT / "data" / "analysis" / "competitor_counts.parquet"
    if not cc_path.exists():
        log.warning("competitor_counts.parquet missing")
        return pd.DataFrame()
    return pd.read_parquet(cc_path)


def load_signal_data() -> pd.DataFrame:
    """Pull stock metrics for whichever tickers we need to score."""
    conn = sqlite3.connect(str(DB))
    try:
        run_id = conn.execute(
            "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
            "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?", (run_id,)
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
            "rvol_30d": _coerce(d.get("Volatility 30D")),
            "rvol_90d": _coerce(d.get("Volatility 90D")),
            "ret_1m": _coerce(d.get("1M Total Return")),
            "ret_3m": _coerce(d.get("3M Total Return")),
            "ret_1y": _coerce(d.get("1Y Total Return")),
            "si_ratio": _coerce(d.get("Short Interest Ratio")),
            "insider_pct": insider,
            "inst_own_pct": _coerce(d.get("Institutional Owner % Shares Outstanding")),
            "sector": d.get("GICS Sector"),
        })
    df = pd.DataFrame(recs)
    df = df[df["ticker"] != ""].drop_duplicates("ticker").set_index("ticker")
    return df


def build() -> pd.DataFrame:
    rex_filed = get_rex_filed_underliers()
    competitor = load_competitor_status()
    signals = load_signal_data()

    log.info("REX-filed underliers (raw): %d", len(rex_filed))
    log.info("Competitor counts available: %d", len(competitor))

    rows = []
    for u, info in rex_filed.items():
        # Skip if REX has an active product
        rex_active_long = competitor.loc[u, "rex_active_long"] if u in competitor.index else 0
        rex_active_short = competitor.loc[u, "rex_active_short"] if u in competitor.index else 0
        if rex_active_long > 0 or rex_active_short > 0:
            continue  # already launched

        # Skip if any competitor has an active product
        comp_active_long = competitor.loc[u, "competitor_active_long"] if u in competitor.index else 0
        comp_active_short = competitor.loc[u, "competitor_active_short"] if u in competitor.index else 0
        if comp_active_long > 0 or comp_active_short > 0:
            continue  # market is taken

        # Build row
        row = dict(info)
        if u in competitor.index:
            row["competitor_filed_long"] = int(competitor.loc[u, "competitor_filed_long"])
            row["competitor_filed_short"] = int(competitor.loc[u, "competitor_filed_short"])
            row["competitor_filed_total"] = (
                int(competitor.loc[u].get("competitor_filed_long", 0))
                + int(competitor.loc[u].get("competitor_filed_short", 0))
                + int(competitor.loc[u].get("competitor_extra_long", 0))
                + int(competitor.loc[u].get("competitor_extra_short", 0))
            )
        else:
            row["competitor_filed_long"] = 0
            row["competitor_filed_short"] = 0
            row["competitor_filed_total"] = 0

        # Add signal data
        if u in signals.index:
            sig = signals.loc[u]
            for col in ("market_cap", "total_oi", "rvol_30d", "rvol_90d",
                        "ret_1m", "ret_3m", "ret_1y", "si_ratio", "insider_pct",
                        "inst_own_pct", "sector"):
                row[col] = sig.get(col)
            row["has_signals"] = True
        else:
            row["has_signals"] = False

        rows.append(row)

    df = pd.DataFrame(rows).set_index("underlier")

    # Score using same methodology as v3
    if not df.empty:
        from screener.li_engine.analysis.whitespace_v3 import compute_score_v3
        from screener.li_engine.analysis.whitespace_v2 import load_themes, load_apewisdom_map
        themes = load_themes()
        mentions = load_apewisdom_map(set(df.index))
        df = compute_score_v3(df, themes, mentions)
        df = df.sort_values("composite_score", ascending=False)

    return df


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    df = build()
    df.to_parquet(OUT, compression="snappy")
    log.info("Wrote %s (%d rows)", OUT, len(df))

    print(f"\nLaunch candidates (REX filed, no live products anywhere): {len(df)}")
    if not df.empty:
        cols = ["sector", "rex_fund_name", "competitor_filed_total", "rvol_90d",
                "ret_1m", "ret_1y", "mentions_24h", "is_hot_theme", "composite_score"]
        cols = [c for c in cols if c in df.columns]
        print(df[cols].head(15).to_string())

    # Wave D2: also build the parallel foreign-underlier candidates table.
    # Keyed on foreign tickers (e.g. 000660.KS, ASML.AS) and consumed by
    # the B-renderer's "International" section. Failure here must not
    # block the US pipeline.
    try:
        from screener.li_engine.analysis import foreign_filings
        foreign_filings.main()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("foreign_filings build skipped: %s", exc)


if __name__ == "__main__":
    main()

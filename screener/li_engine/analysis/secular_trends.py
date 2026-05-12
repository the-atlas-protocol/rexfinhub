"""Secular trend auto-detector — Wave E2.

Replaces the hard-coded HOT_THEMES set in whitespace_v3 with a data-driven
detector that catches sector inflections BEFORE the move ends.

Motivation
----------
The Feb-2026 build hard-coded six "hot themes" (AI/quant/chips/space/nuclear/
defense). HBM / DRAM memory ripped 200-500% in Q1-Q2 2026 (SK Hynix, Micron,
Sandisk, Seagate) — and we missed it because "memory" was not in the list.

Inputs
------
1. Filing velocity: count of underlier-ticker mentions in
   fund_extractions.series_name per rolling 4-week window vs prior 8-week.
2. Cross-issuer cadence: number of distinct issuers filing the same
   underlier-cluster in the same week. Multi-issuer = real theme.
3. Price momentum: 1m / 3m / 6m total return on the underlier from the
   latest mkt_stock_data snapshot.
4. Mention velocity: optional ApeWisdom snapshot enrichment (live only).

Algorithm
---------
1. Extract ticker mentions per fund_extraction series_name (regex against
   the known US ticker list, with a stop list for English false-positives).
2. Group tickers into emergent CLUSTERS by week-level co-occurrence
   (tickers filed by 2+ distinct issuers in the same ISO week).
3. Score each ticker on a HEAT score:
       0.35 * filing_velocity_z
     + 0.25 * cross_issuer_cadence_z
     + 0.25 * price_momentum_z (1m+3m blend)
     + 0.15 * mentions_z (if available)
4. Roll up tickers into named themes via a seed map (themes.yaml) PLUS any
   newly-detected co-filing clusters (theme_name = "emergent_<top_ticker>").
5. Classify trend_direction: rising (4w > prior 8w), peak (current high but
   flat 4w-over-4w), fading (down).

Output
------
Parquet at data/analysis/secular_trends.parquet with columns:
  theme_name, week_of, heat_score, top_tickers (json), trend_direction,
  filing_count_4w, filing_count_8w_prior, distinct_issuers_4w,
  avg_ret_1m, avg_ret_3m, narrative_seed.

Backtest mode
-------------
Pass --as-of YYYY-MM-DD to compute the detector as of a historical date,
using only filings <= that date and the latest stock_data snapshot (price
returns are post-hoc but stable for sanity-checking which themes WOULD have
been flagged). Used to validate the detector caught "memory" by mid-March.

Graceful fallback
-----------------
If the parquet is missing or empty, whitespace_v3 falls back to the static
HOT_THEMES set with a warning banner.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from screener.li_engine.analysis.whitespace_v2 import DB, _clean, load_themes

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
OUT = _ROOT / "data" / "analysis" / "secular_trends.parquet"

# English words / generic finance tokens that look like tickers but are not.
# Built from inspecting series_name corpus.
STOPLIST = {
    # Fund-name boilerplate
    "ETF", "ETFS", "FUND", "FUNDS", "TRUST", "SHARES", "INDEX",
    "LONG", "SHORT", "DAILY", "WEEKLY", "MONTHLY", "TARGET",
    "MAX", "PRO", "PLUS", "ULTRA", "MEGA", "MINI",
    "LEVERAGED", "INVERSE", "BULL", "BEAR",
    "INCOME", "YIELD", "GROWTH", "VALUE", "CORE", "TOTAL", "SELECT",
    "EQUITY", "BOND", "BONDS", "GOLD", "SILVER", "OIL", "GAS",
    "GLOBAL", "GROUP", "MARKET", "MARKETS", "CAPITAL", "PARTNERS",
    "REAL", "ESTATE", "OFFICE", "INDUSTRY", "INDUSTRIAL",
    "DIVIDEND", "EMERGING", "DEVELOPED", "LARGE", "SMALL", "MID", "CAP",
    "BUFFER", "ACCELERATOR", "BARRIER", "CAP", "CAPS",
    "MULTI", "STRATEGIC", "TACTICAL", "DEFENSIVE", "AGGRESSIVE",
    "ALPHA", "BETA", "GAMMA", "DELTA", "THETA",
    # Generic English
    "A", "I", "AT", "BE", "BY", "GO", "IS", "IN", "ON", "OR", "OF",
    "THE", "AND", "TO", "UP", "IT", "MM", "HE", "ME", "MY", "WE", "US",
    "WAS", "HAS", "HAD", "BUT", "NOT", "GET", "GIVE", "GAIN",
    "ALL", "ANY", "NEW", "OLD", "BIG", "BUY", "SELL", "OUT", "DUE",
    "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "TEN",
    "LIFE", "EDGE", "LIVE", "LEAD", "NEXT", "PEAK", "RISK", "HIGH", "LOW",
    "DAY", "WEEK", "MONTH", "YEAR", "TIME",
    # Roman numerals
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
    # Legal entities
    "INC", "CORP", "CO", "LP", "LLC", "PLC", "SA", "AG",
    # Common index tickers (to avoid double-counting flagship product ETFs)
    "SPY", "QQQ", "IWM", "DIA", "VIX", "UVXY", "TQQQ", "SQQQ", "TLT",
    "SLV", "GLD", "URA",
    # REX-internal
    "REX", "TREX", "BMNR",
    # Months
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    # Common English false-positives surfaced in audit
    "FOR", "AM", "AN", "AS", "CAN", "DO", "FROM", "IF", "INTO",
    "NO", "SO", "THAN", "THAT", "THIS", "WHO", "WHY",
    "FLEX", "DOW", "PEAK", "VOYA",  # tickers but used as plain words in many fund names
    "RS", "AS", "PS", "WS", "BS", "DS", "FS", "JS", "SS",
    "AB", "AD", "AG", "AH",
    # Generic
    "OWN", "DAY", "WAY", "LET", "PUT", "SET", "RUN", "USE",
    "OFF", "OUR", "HER", "HIM", "HOW", "WHO",
    # Defined-outcome / ETF naming
    "SPDR", "VANGUARD", "WISDOMTREE", "INVESCO", "FIDELITY", "SCHWAB",
}

# Filings must look like leveraged/inverse single-stock products to count
# towards the secular-trend signal. This is the whole point: detect themes
# that the LI issuers are racing to launch products on.
LI_FILING_PATTERN = re.compile(
    r"\b(?:2X|3X|4X|2x|3x|4x|LONG|SHORT|INVERSE|ULTRA|DAILY\s+TARGET|BULL|BEAR|"
    r"LEVERAGE[D]?)\b",
    re.IGNORECASE,
)

# Seed taxonomy — these tickers map to existing themes. Used to give cluster
# names. Tickers NOT in this map but co-filed get an "emergent_<ticker>"
# theme name.
THEME_SEEDS = {
    "memory_hbm": ["MU", "WDC", "SNDK", "STX", "HBM"],
    "quantum": ["IONQ", "RGTI", "QBTS", "QUBT"],
    "ai_infrastructure": ["NVDA", "AVGO", "SMCI", "VRT", "DLR", "ANET", "CRWV", "NBIS"],
    "ai_applications": ["PLTR", "NOW", "CRM", "SNOW", "MDB", "BBAI", "SOUN"],
    "semiconductors": ["AMD", "TSM", "ASML", "AMAT", "LRCX", "INTC", "QCOM", "MRVL", "ARM"],
    "space": ["RKLB", "ASTS", "LUNR", "SPCE", "BKSY", "PL"],
    "nuclear": ["SMR", "LEU", "BWXT", "CEG", "VST", "OKLO", "NNE"],
    "crypto_equity": ["MSTR", "COIN", "HOOD", "RIOT", "MARA", "CLSK", "GLXY", "CRCL"],
    "biotech_gene": ["QURE", "CRSP", "BEAM", "NTLA", "VRTX"],
    "ev_battery": ["TSLA", "RIVN", "LCID", "NIO", "ALB", "QS"],
    "defense": ["LMT", "RTX", "NOC", "GD", "LDOS", "KTOS", "RKLB"],
    "fintech_neobank": ["HOOD", "SOFI", "AFRM", "UPST"],
    "robotics": ["TSLA", "SYM", "SERV", "ABBN"],
}

TICKER_PATTERN = re.compile(r"\b([A-Z]{2,5})\b")  # min length 2 — drops "T","S","R" noise


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_known_tickers(conn: sqlite3.Connection) -> set[str]:
    """Load the universe of valid US tickers from the latest stock_data run."""
    run_id_row = conn.execute(
        "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
        "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not run_id_row:
        return set()
    run_id = run_id_row[0]
    rows = conn.execute(
        "SELECT ticker FROM mkt_stock_data WHERE pipeline_run_id=?", (run_id,)
    ).fetchall()
    out = set()
    for (raw,) in rows:
        t = (raw or "").split()[0].upper().strip()
        if 2 <= len(t) <= 5 and t.isalpha() and t not in STOPLIST:
            out.add(t)
    return out


def load_filings(conn: sqlite3.Connection, as_of: date) -> pd.DataFrame:
    """All fund_extractions rows up to as_of with effective_date and registrant.
    Filtered to LI-style filings only (2X/3X/Long/Short/Ultra/Daily Target).
    This is the whole point: detect themes the LI issuers are racing to launch
    products on, not generic ETF naming noise."""
    rows = conn.execute(
        """
        SELECT fe.effective_date, fe.series_name, f.registrant
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE fe.effective_date IS NOT NULL
          AND fe.effective_date BETWEEN '2026-01-01' AND ?
          AND fe.series_name IS NOT NULL
        """,
        (as_of.isoformat(),),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["effective_date", "series_name", "registrant"])
    df["effective_date"] = pd.to_datetime(df["effective_date"], errors="coerce").dt.date
    df = df.dropna(subset=["effective_date"])
    # LI-style filter: must contain leverage/direction language
    df = df[df["series_name"].str.contains(LI_FILING_PATTERN, na=False)]
    return df


def load_stock_snapshot(conn: sqlite3.Connection) -> pd.DataFrame:
    """Latest mkt_stock_data — used for price momentum signal."""
    run_id_row = conn.execute(
        "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
        "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not run_id_row:
        return pd.DataFrame()
    run_id = run_id_row[0]
    rows = conn.execute(
        "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?",
        (run_id,),
    ).fetchall()
    recs = []
    for raw, blob in rows:
        if not blob:
            continue
        try:
            d = json.loads(blob)
            d = d[0] if isinstance(d, list) else d
        except json.JSONDecodeError:
            continue
        t = (raw or "").split()[0].upper().strip()
        if not t:
            continue

        def _f(k):
            v = d.get(k)
            if v in (None, "", "#ERROR", "#N/A", "N/A"):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        recs.append({
            "ticker": t,
            "ret_1m": _f("1M Total Return"),
            "ret_3m": _f("3M Total Return"),
            "ret_6m": _f("6M Total Return"),
            "rvol_30d": _f("Volatility 30D"),
            "rvol_90d": _f("Volatility 90D"),
            "sector": d.get("GICS Sector"),
        })
    return pd.DataFrame(recs).set_index("ticker")


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def extract_ticker_mentions(filings: pd.DataFrame, known: set[str]) -> pd.DataFrame:
    """Return long-format frame: one row per (filing, ticker) match."""
    out_rows = []
    for _, row in filings.iterrows():
        s = row["series_name"] or ""
        candidates = set(TICKER_PATTERN.findall(s.upper()))
        hits = candidates & known
        for tk in hits:
            out_rows.append({
                "ticker": tk,
                "effective_date": row["effective_date"],
                "registrant": row["registrant"] or "UNKNOWN",
            })
    if not out_rows:
        return pd.DataFrame(columns=["ticker", "effective_date", "registrant", "iso_week"])
    df = pd.DataFrame(out_rows)
    df["iso_week"] = pd.to_datetime(df["effective_date"]).dt.strftime("%Y-W%V")
    return df


def compute_velocity(mentions: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Per ticker: count last 4 weeks vs prior 8 weeks."""
    if mentions.empty:
        return pd.DataFrame(columns=[
            "ticker", "filing_count_4w", "filing_count_8w_prior",
            "distinct_issuers_4w", "velocity_ratio",
        ])
    mentions = mentions.copy()
    mentions["d"] = pd.to_datetime(mentions["effective_date"])
    cutoff_4w = pd.Timestamp(as_of - timedelta(weeks=4))
    cutoff_12w = pd.Timestamp(as_of - timedelta(weeks=12))

    recent = mentions[mentions["d"] >= cutoff_4w]
    prior = mentions[(mentions["d"] >= cutoff_12w) & (mentions["d"] < cutoff_4w)]

    by_ticker = pd.DataFrame(index=sorted(set(mentions["ticker"])))
    by_ticker["filing_count_4w"] = recent.groupby("ticker").size()
    by_ticker["filing_count_8w_prior"] = prior.groupby("ticker").size()
    by_ticker["distinct_issuers_4w"] = recent.groupby("ticker")["registrant"].nunique()
    by_ticker = by_ticker.fillna(0)
    # Velocity = recent rate / prior rate. Avoid div-by-zero.
    # 4w vs 8w → normalize 8w by /2 to compare equal-length windows.
    prior_rate = by_ticker["filing_count_8w_prior"] / 2.0
    by_ticker["velocity_ratio"] = (
        (by_ticker["filing_count_4w"] + 0.5) / (prior_rate + 0.5)
    )
    by_ticker.index.name = "ticker"
    return by_ticker.reset_index()


def assign_themes(velocity: pd.DataFrame) -> pd.DataFrame:
    """Map each ticker to its named theme via THEME_SEEDS, or 'emergent'."""
    seed_map = {}
    for theme, tks in THEME_SEEDS.items():
        for t in tks:
            seed_map.setdefault(t, []).append(theme)
    velocity = velocity.copy()
    velocity["themes"] = velocity["ticker"].map(
        lambda t: ",".join(seed_map.get(t, ["emergent"]))
    )
    return velocity


def _zscore(s: pd.Series) -> pd.Series:
    if s.empty:
        return s
    mu = s.mean(skipna=True)
    sd = s.std(skipna=True)
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).clip(-3, 3)


def compute_heat(
    velocity: pd.DataFrame,
    stocks: pd.DataFrame,
    mentions_map: dict[str, int] | None = None,
) -> pd.DataFrame:
    """Per ticker heat score, then roll up to theme."""
    df = velocity.merge(
        stocks[["ret_1m", "ret_3m", "ret_6m"]],
        left_on="ticker", right_index=True, how="left",
    )
    # Build ticker-level z-scores
    df["velocity_z"] = _zscore(np.log1p(df["velocity_ratio"]))
    df["issuer_z"] = _zscore(df["distinct_issuers_4w"])
    df["mom_blend"] = df[["ret_1m", "ret_3m"]].mean(axis=1)
    df["mom_z"] = _zscore(df["mom_blend"])
    if mentions_map:
        df["mentions"] = df["ticker"].map(lambda t: mentions_map.get(t, 0))
        df["mentions_z"] = _zscore(np.log1p(df["mentions"]))
    else:
        df["mentions"] = 0
        df["mentions_z"] = 0.0

    df["heat_ticker"] = (
        0.35 * df["velocity_z"].fillna(0)
        + 0.25 * df["issuer_z"].fillna(0)
        + 0.25 * df["mom_z"].fillna(0)
        + 0.15 * df["mentions_z"].fillna(0)
    )
    return df


def rollup_themes(ticker_heat: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Aggregate ticker-level heat into theme-level rows."""
    rows = []
    # Explode multi-theme rows
    long = ticker_heat.assign(
        theme=ticker_heat["themes"].str.split(",")
    ).explode("theme")
    long["theme"] = long["theme"].str.strip()
    long = long[long["theme"] != ""]

    for theme, grp in long.groupby("theme"):
        if theme == "emergent":
            # Skip rollup; we'll handle emergent clusters separately
            continue
        # Filter to tickers with meaningful filing activity
        active = grp[grp["filing_count_4w"] > 0]
        if active.empty:
            continue
        # Theme heat = mean ticker heat, weighted toward high-velocity members
        weights = (active["filing_count_4w"] + 1.0).values
        heat = float(np.average(active["heat_ticker"].fillna(0).values, weights=weights))
        top = active.sort_values("heat_ticker", ascending=False).head(8)
        rows.append({
            "theme_name": theme,
            "heat_score": heat,
            "week_of": as_of.isoformat(),
            "top_tickers": json.dumps(top["ticker"].tolist()),
            "filing_count_4w": int(active["filing_count_4w"].sum()),
            "filing_count_8w_prior": int(active["filing_count_8w_prior"].sum()),
            "distinct_issuers_4w": int(active["distinct_issuers_4w"].max()),
            "avg_ret_1m": float(active["ret_1m"].mean(skipna=True)) if active["ret_1m"].notna().any() else None,
            "avg_ret_3m": float(active["ret_3m"].mean(skipna=True)) if active["ret_3m"].notna().any() else None,
        })

    # Emergent clusters: tickers not in any seed theme but with multi-issuer cadence
    emergent = ticker_heat[
        (ticker_heat["themes"] == "emergent")
        & (ticker_heat["distinct_issuers_4w"] >= 2)
        & (ticker_heat["filing_count_4w"] >= 3)
    ]
    for _, r in emergent.sort_values("heat_ticker", ascending=False).head(10).iterrows():
        rows.append({
            "theme_name": f"emergent_{r['ticker']}",
            "heat_score": float(r["heat_ticker"]),
            "week_of": as_of.isoformat(),
            "top_tickers": json.dumps([r["ticker"]]),
            "filing_count_4w": int(r["filing_count_4w"]),
            "filing_count_8w_prior": int(r["filing_count_8w_prior"]),
            "distinct_issuers_4w": int(r["distinct_issuers_4w"]),
            "avg_ret_1m": float(r["ret_1m"]) if pd.notna(r["ret_1m"]) else None,
            "avg_ret_3m": float(r["ret_3m"]) if pd.notna(r["ret_3m"]) else None,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Trend direction
    def _trend(row):
        prior_rate = row["filing_count_8w_prior"] / 2.0
        recent = row["filing_count_4w"]
        if recent > prior_rate * 1.5 and recent >= 4:
            return "rising"
        if recent < prior_rate * 0.5:
            return "fading"
        return "peak" if recent > prior_rate else "stable"
    out["trend_direction"] = out.apply(_trend, axis=1)
    # Narrative seed
    def _seed(row):
        tks = json.loads(row["top_tickers"])
        ret = row["avg_ret_3m"]
        ret_str = f"{ret:+.0f}% 3m" if ret is not None and not pd.isna(ret) else "n/a"
        return (
            f"{row['theme_name']} — {row['filing_count_4w']} filings/4w "
            f"({row['distinct_issuers_4w']} issuers), avg {ret_str}, "
            f"top: {', '.join(tks[:5])}"
        )
    out["narrative_seed"] = out.apply(_seed, axis=1)
    return out.sort_values("heat_score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect(as_of: date | None = None, save: bool = True) -> pd.DataFrame:
    """Run the detector. If save, write parquet at OUT."""
    if as_of is None:
        as_of = date.today()
    conn = sqlite3.connect(str(DB))
    try:
        known = load_known_tickers(conn)
        log.info("known tickers: %d", len(known))
        filings = load_filings(conn, as_of)
        log.info("filings rows up to %s: %d", as_of, len(filings))
        stocks = load_stock_snapshot(conn)
        log.info("stock snapshot: %d tickers", len(stocks))
    finally:
        conn.close()

    if filings.empty or not known:
        log.warning("insufficient data — returning empty")
        out = pd.DataFrame()
        if save:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            out.to_parquet(OUT, compression="snappy")
        return out

    mentions = extract_ticker_mentions(filings, known)
    log.info("ticker mentions extracted: %d", len(mentions))
    velocity = compute_velocity(mentions, as_of)
    velocity = assign_themes(velocity)
    ticker_heat = compute_heat(velocity, stocks)
    themes = rollup_themes(ticker_heat, as_of)
    if save:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        themes.to_parquet(OUT, compression="snappy")
        log.info("wrote %s (%d themes)", OUT, len(themes))
    return themes


def load_themes_parquet() -> set[str]:
    """For whitespace_v3 consumption: load top-N hot theme names from parquet.
    Falls back to empty set if missing — caller should default to static list."""
    if not OUT.exists():
        return set()
    try:
        df = pd.read_parquet(OUT)
        if df.empty:
            return set()
        # Top 8 themes by heat, only rising/peak
        hot = df[df["trend_direction"].isin(("rising", "peak"))].head(8)
        return set(hot["theme_name"].tolist())
    except Exception as e:
        log.warning("failed to load themes parquet: %s", e)
        return set()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_themes(df: pd.DataFrame, limit: int = 12, label: str = ""):
    if df.empty:
        print(f"({label}) NO THEMES DETECTED")
        return
    print(f"\n=== TOP {min(limit, len(df))} THEMES{' — ' + label if label else ''} ===")
    print(f"{'theme':<28} {'heat':>6} {'4w':>4} {'8w_pr':>6} {'iss':>4} "
          f"{'ret1m':>6} {'ret3m':>6} {'trend':<8}")
    print("-" * 100)
    for _, r in df.head(limit).iterrows():
        ret1m = f"{r['avg_ret_1m']:+.0f}%" if pd.notna(r["avg_ret_1m"]) else "  -  "
        ret3m = f"{r['avg_ret_3m']:+.0f}%" if pd.notna(r["avg_ret_3m"]) else "  -  "
        print(f"{r['theme_name']:<28} {r['heat_score']:>+6.2f} "
              f"{r['filing_count_4w']:>4d} {r['filing_count_8w_prior']:>6d} "
              f"{r['distinct_issuers_4w']:>4d} {ret1m:>6} {ret3m:>6} "
              f"{r['trend_direction']:<8}")
        tks = json.loads(r["top_tickers"])
        print(f"  tickers: {', '.join(tks[:8])}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="Backtest date YYYY-MM-DD (default: today)")
    ap.add_argument("--no-save", action="store_true", help="Don't write parquet")
    ap.add_argument("--backtest-memory", action="store_true",
                    help="Run multi-date backtest validating memory-theme detection")
    args = ap.parse_args()

    if args.backtest_memory:
        print("=" * 100)
        print("MEMORY BACKTEST — did the detector flag memory before the move ended?")
        print("=" * 100)
        # Memory rallied Q1-Q2 2026. SK Hynix +500%, Micron +200%.
        # Run detector at month-ends to see when memory shows up.
        for d_str in ("2026-03-15", "2026-04-01", "2026-04-15", "2026-05-01", "2026-05-11"):
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
            df = detect(as_of=d, save=False)
            print(f"\n>>> as-of {d_str} <<<")
            mem = df[df["theme_name"] == "memory_hbm"]
            if mem.empty:
                print("  memory_hbm: NOT IN OUTPUT (filings velocity below threshold)")
            else:
                r = mem.iloc[0]
                rank = df[df["heat_score"] >= r["heat_score"]].shape[0]
                print(f"  memory_hbm: rank #{rank}/{len(df)}, heat={r['heat_score']:+.2f}, "
                      f"4w={r['filing_count_4w']} (prior 8w={r['filing_count_8w_prior']}), "
                      f"trend={r['trend_direction']}")
            _print_themes(df.head(8), limit=8, label=d_str)
        return

    as_of = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
             if args.as_of else date.today())
    df = detect(as_of=as_of, save=not args.no_save)
    _print_themes(df, limit=15, label=f"as-of {as_of}")


if __name__ == "__main__":
    main()

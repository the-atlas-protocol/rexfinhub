"""Foreign-underlier launch candidates — REX/competitor filings on foreign-listed stocks.

`launch_candidates.parquet` is keyed on US tickers and so misses fund filings
referencing foreign-listed underliers (e.g. "T-REX 2X LONG SK HYNIX DAILY
TARGET ETF" -> 000660.KS, "Direxion Daily ASML Bull 2X ETF" -> ASML.AS or
ASML US ADR — but the fund-name regex in filed_underliers.py is built for
US tickers and skips these).

Pipeline:
    1. Scan fund_extractions for REX/competitor filings whose fund name
       references a known foreign-listed underlier (SK Hynix, ASML, Sony,
       TSMC, Samsung, etc.).
    2. Join against the foreign universe (data/foreign/universe.parquet from
       Wave D1) for market/sector/market-cap. Falls back to a built-in
       seed universe if D1 hasn't landed yet.
    3. Roll up per foreign underlier: rex_status (filed/pending/none),
       competitor_2x_status (filed/active count), most-recent filing date.
    4. Rank by (rex_status, market_cap, sector_strength) — no Reddit/options
       signals available for foreign tickers tonight.
    5. Write data/analysis/foreign_launch_candidates.parquet for the
       B-renderer's "International" section.

Empty-parquet output is acceptable when no REX foreign filings exist.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
FOREIGN_UNIVERSE = _ROOT / "data" / "foreign" / "universe.parquet"
OUT = _ROOT / "data" / "analysis" / "foreign_launch_candidates.parquet"

# Tonight's static sector-strength bucket — replaced by E2 secular-trend
# scoring once it lands. Higher = hotter theme.
STATIC_SECTOR_STRENGTH = {
    "Semiconductors": 5.0,
    "Information Technology": 4.0,
    "AI / Compute": 5.0,
    "Memory": 5.0,
    "Robotics": 4.5,
    "Communication Services": 3.5,
    "Consumer Discretionary": 3.0,
    "Health Care": 3.0,
    "Financials": 2.5,
    "Energy": 2.5,
    "Industrials": 3.0,
    "Materials": 2.5,
    "Consumer Staples": 2.0,
    "Utilities": 1.5,
    "Real Estate": 1.5,
}

# Built-in seed universe — used as fallback if D1's universe.parquet is not
# yet available. Each entry is (foreign_ticker, name, market, sector,
# market_cap_usd_billions, name_keywords). `name_keywords` are case-
# insensitive substrings that, when found in a fund name, identify the
# underlier (e.g. "SK HYNIX", "HYNIX" both map to 000660.KS).
SEED_FOREIGN_UNIVERSE = [
    # ticker, name, market, sector, market_cap_usd_b, [keywords]
    ("000660.KS", "SK Hynix Inc.",                "KRX",  "Memory",                 130.0, ["SK HYNIX", "HYNIX"]),
    ("005930.KS", "Samsung Electronics Co Ltd",   "KRX",  "Semiconductors",         420.0, ["SAMSUNG ELECTRONICS", "SAMSUNG ELEC", "SAMSUNG"]),
    ("ASML.AS",   "ASML Holding NV",              "AMS",  "Semiconductors",         300.0, ["ASML"]),
    ("TSM",       "Taiwan Semiconductor Mfg ADR", "NYSE", "Semiconductors",         900.0, ["TAIWAN SEMI", "TSMC", " TSM "]),
    ("2330.TW",   "Taiwan Semiconductor Mfg",     "TWSE", "Semiconductors",         900.0, ["TAIWAN SEMICONDUCTOR"]),
    ("6758.T",    "Sony Group Corp",              "TSE",  "Communication Services",  120.0, ["SONY GROUP", "SONY"]),
    ("7203.T",    "Toyota Motor Corp",            "TSE",  "Consumer Discretionary",  280.0, ["TOYOTA"]),
    ("7974.T",    "Nintendo Co Ltd",              "TSE",  "Communication Services",   80.0, ["NINTENDO"]),
    ("9984.T",    "SoftBank Group Corp",          "TSE",  "Communication Services",   90.0, ["SOFTBANK"]),
    ("BABA",      "Alibaba Group ADR",            "NYSE", "Consumer Discretionary",  300.0, ["ALIBABA"]),
    ("0700.HK",   "Tencent Holdings Ltd",         "HKEX", "Communication Services",  500.0, ["TENCENT"]),
    ("1211.HK",   "BYD Co Ltd",                   "HKEX", "Consumer Discretionary",  100.0, [" BYD ", "BYD CO"]),
    ("9988.HK",   "Alibaba Group HK",             "HKEX", "Consumer Discretionary",  300.0, []),  # dedupe via BABA
    ("9618.HK",   "JD.com Inc HK",                "HKEX", "Consumer Discretionary",   60.0, ["JD.COM"]),
    ("PDD",       "PDD Holdings ADR",             "NASDAQ","Consumer Discretionary",  140.0, ["PDD HOLDINGS", "PINDUODUO"]),
    ("BIDU",      "Baidu Inc ADR",                "NASDAQ","Communication Services",   30.0, ["BAIDU"]),
    ("NIO",       "NIO Inc ADR",                  "NYSE", "Consumer Discretionary",   10.0, [" NIO ", "NIO INC"]),
    ("XPEV",      "XPeng Inc ADR",                "NYSE", "Consumer Discretionary",   15.0, ["XPENG", " XPEV "]),
    ("LI",        "Li Auto Inc ADR",              "NASDAQ","Consumer Discretionary",   25.0, ["LI AUTO"]),
    ("NVO",       "Novo Nordisk ADR",             "NYSE", "Health Care",             340.0, ["NOVO NORDISK", " NVO "]),
    ("SAP",       "SAP SE",                       "NYSE", "Information Technology",  280.0, [" SAP "]),
    ("MELI",      "MercadoLibre Inc",             "NASDAQ","Consumer Discretionary",  100.0, ["MERCADOLIBRE", " MELI "]),
    ("SHOP",      "Shopify Inc",                  "NYSE", "Information Technology",  130.0, ["SHOPIFY"]),
    ("SE",        "Sea Limited ADR",              "NYSE", "Communication Services",   60.0, ["SEA LIMITED", "SEA LTD"]),
    ("RACE",      "Ferrari NV",                   "NYSE", "Consumer Discretionary",   80.0, ["FERRARI"]),
    ("AZN",       "AstraZeneca ADR",              "NASDAQ","Health Care",             220.0, ["ASTRAZENECA"]),
    ("RIO",       "Rio Tinto ADR",                "NYSE", "Materials",               110.0, ["RIO TINTO"]),
    ("BHP",       "BHP Group ADR",                "NYSE", "Materials",               140.0, ["BHP GROUP", "BHP BILL"]),
    ("VALE",      "Vale SA ADR",                  "NYSE", "Materials",                55.0, [" VALE ", "VALE SA"]),
    ("PBR",       "Petroleo Brasileiro ADR",      "NYSE", "Energy",                   90.0, ["PETROBRAS", " PBR "]),
    ("INFY",      "Infosys Ltd ADR",              "NYSE", "Information Technology",   70.0, ["INFOSYS"]),
]

# REX-affiliated registrant matches (REX itself + the white-label trust
# administrator REX uses for T-REX products). Word boundary required —
# without it, the substring "REX" matches "DiREXion" (a major competitor).
REX_REGISTRANT_PAT = re.compile(r"\b(?:T-?REX|REX|ETF Opportunities)\b", re.IGNORECASE)


def load_foreign_universe() -> pd.DataFrame:
    """Prefer D1's universe.parquet; fall back to seed if missing.

    Expected schema (D1):
        foreign_ticker, name, market, sector, market_cap_usd, name_keywords (list)
    """
    if FOREIGN_UNIVERSE.exists():
        try:
            df = pd.read_parquet(FOREIGN_UNIVERSE)
            if "name_keywords" not in df.columns:
                # D1 may store keywords differently — synthesise from name
                df["name_keywords"] = df["name"].str.upper().apply(lambda n: [n.split(",")[0].strip()])
            log.info("Loaded foreign universe from D1: %d rows", len(df))
            return df
        except Exception as e:
            log.warning("Could not read D1 universe.parquet (%s); using seed", e)

    rows = [
        {
            "foreign_ticker": t,
            "name": n,
            "market": m,
            "sector": s,
            "market_cap_usd": cap * 1e9,
            "name_keywords": kws,
        }
        for (t, n, m, s, cap, kws) in SEED_FOREIGN_UNIVERSE
        if kws  # skip dedupe placeholders with no keywords
    ]
    df = pd.DataFrame(rows)
    log.info("Using seed foreign universe: %d rows (D1 not yet available)", len(df))
    return df


def scan_foreign_filings(universe: pd.DataFrame) -> pd.DataFrame:
    """Pull every fund_extractions row whose series_name (or class_contract_name)
    references a foreign underlier in the universe.

    Returns one row per (foreign_ticker, registrant, series_name, filing_date).
    """
    conn = sqlite3.connect(str(DB))
    try:
        # Pull every fund_extractions joined with filings, then keyword-match
        # in pandas — keyword matching in SQL would require a complex OR chain.
        df = pd.read_sql_query(
            """
            SELECT fe.series_name, fe.class_contract_name, fe.class_symbol,
                   f.registrant, f.form, f.filing_date, f.cik, f.accession_number
            FROM fund_extractions fe
            JOIN filings f ON f.id = fe.filing_id
            WHERE fe.series_name IS NOT NULL
            """,
            conn,
        )
        # Also pull fund_status for active/effective products
        fs = pd.read_sql_query(
            """
            SELECT fs.fund_name, fs.ticker, fs.status, fs.effective_date,
                   fs.latest_form, fs.latest_filing_date,
                   t.name AS trust_name, t.cik
            FROM fund_status fs
            JOIN trusts t ON t.id = fs.trust_id
            WHERE fs.fund_name IS NOT NULL
            """,
            conn,
        )
    finally:
        conn.close()

    # Build keyword -> foreign_ticker lookup
    kw_index: list[tuple[str, str]] = []
    for _, r in universe.iterrows():
        for kw in r["name_keywords"] or []:
            kw_index.append((kw.upper(), r["foreign_ticker"]))

    def match_underlier(name: str) -> str | None:
        if not isinstance(name, str):
            return None
        n_up = " " + name.upper() + " "
        for kw, ticker in kw_index:
            # Pad keyword with spaces if it doesn't already have word
            # boundaries — avoids "SAP" matching "SAPPHIRE" but allows
            # "ASML" inside "Direxion Daily ASML Bull 2X ETF".
            kw_padded = kw if (kw.startswith(" ") or kw.endswith(" ")) else f" {kw} "
            if kw_padded in n_up:
                return ticker
            # Also allow keyword-as-word at start/end and bracketed variants
            if kw in n_up.strip():
                # word-boundary check
                if re.search(rf"\b{re.escape(kw.strip())}\b", n_up):
                    return ticker
        return None

    # Match fund_extractions
    df["foreign_ticker"] = df["series_name"].apply(match_underlier)
    fb = df["foreign_ticker"].isna()
    df.loc[fb, "foreign_ticker"] = df.loc[fb, "class_contract_name"].apply(match_underlier)
    df = df.dropna(subset=["foreign_ticker"]).copy()

    # Match fund_status
    fs["foreign_ticker"] = fs["fund_name"].apply(match_underlier)
    fs = fs.dropna(subset=["foreign_ticker"]).copy()

    log.info("fund_extractions foreign matches: %d rows", len(df))
    log.info("fund_status foreign matches: %d rows", len(fs))

    return _rollup(df, fs)


def _is_rex(registrant: str) -> bool:
    return bool(registrant) and bool(REX_REGISTRANT_PAT.search(registrant))


def _rollup(extractions: pd.DataFrame, statuses: pd.DataFrame) -> pd.DataFrame:
    """Collapse multi-row filings into one row per foreign_ticker."""
    if extractions.empty and statuses.empty:
        return pd.DataFrame()

    rows = []
    all_tickers = set(extractions["foreign_ticker"]) | set(statuses["foreign_ticker"])

    for ticker in all_tickers:
        sub_e = extractions[extractions["foreign_ticker"] == ticker]
        sub_s = statuses[statuses["foreign_ticker"] == ticker]

        rex_extr = sub_e[sub_e["registrant"].apply(_is_rex)]
        comp_extr = sub_e[~sub_e["registrant"].apply(_is_rex)]
        rex_stat = sub_s[sub_s["trust_name"].apply(_is_rex)] if not sub_s.empty else sub_s
        comp_stat = sub_s[~sub_s["trust_name"].apply(_is_rex)] if not sub_s.empty else sub_s

        # Determine REX status
        rex_status = "none"
        rex_fund_name = None
        rex_ticker = None
        rex_latest_filing = None
        if not rex_stat.empty:
            # Active/effective products win
            eff = rex_stat[rex_stat["status"].str.upper().isin(["EFFECTIVE", "ACTIVE", "LIVE"])]
            if not eff.empty:
                rex_status = "active"
                row = eff.iloc[0]
                rex_fund_name = row["fund_name"]
                rex_ticker = row["ticker"]
            else:
                pend = rex_stat[rex_stat["status"].str.upper().isin(["PENDING", "PEND", "DELAYED"])]
                if not pend.empty:
                    rex_status = "pending"
                    rex_fund_name = pend.iloc[0]["fund_name"]
                    rex_ticker = pend.iloc[0]["ticker"]
                else:
                    rex_status = "filed"
                    rex_fund_name = rex_stat.iloc[0]["fund_name"]
            rex_latest_filing = pd.to_datetime(rex_stat["latest_filing_date"]).max()

        if rex_status == "none" and not rex_extr.empty:
            rex_status = "filed"
            latest = rex_extr.sort_values("filing_date").iloc[-1]
            rex_fund_name = latest["series_name"]
            rex_latest_filing = pd.to_datetime(latest["filing_date"])

        # Competitor activity
        comp_filings = len(comp_extr)
        comp_distinct_funds = comp_extr["series_name"].nunique() if not comp_extr.empty else 0
        comp_distinct_issuers = comp_extr["registrant"].nunique() if not comp_extr.empty else 0
        comp_active = 0
        comp_2x_status = "none"
        if not comp_stat.empty:
            comp_active = (comp_stat["status"].str.upper().isin(["EFFECTIVE", "ACTIVE", "LIVE"])).sum()
            if comp_active > 0:
                comp_2x_status = "active"
            else:
                comp_2x_status = "filed"
        elif comp_filings > 0:
            comp_2x_status = "filed"

        rows.append({
            "foreign_ticker": ticker,
            "rex_status": rex_status,
            "rex_fund_name": rex_fund_name,
            "rex_ticker": rex_ticker,
            "rex_latest_filing": rex_latest_filing,
            "competitor_filings_total": comp_filings,
            "competitor_distinct_funds": comp_distinct_funds,
            "competitor_distinct_issuers": comp_distinct_issuers,
            "competitor_2x_status": comp_2x_status,
            "competitor_active_count": int(comp_active),
        })

    return pd.DataFrame(rows)


def _rex_status_rank(s: str) -> int:
    return {"active": 4, "filed": 3, "pending": 2, "none": 1}.get(s, 0)


def rank(df: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Rank without signals: rex_status > market_cap > sector_strength.

    Annotates universe metadata (name, market, sector, market_cap_usd) and a
    composite_score for sortability.
    """
    if df.empty:
        return df

    meta = universe[["foreign_ticker", "name", "market", "sector", "market_cap_usd"]].drop_duplicates("foreign_ticker")
    df = df.merge(meta, on="foreign_ticker", how="left")

    # Suppress active REX products (already-launched markets) from the
    # candidate list — same convention as US launch_candidates.
    df = df[df["rex_status"] != "active"].copy()

    df["sector_strength"] = df["sector"].map(STATIC_SECTOR_STRENGTH).fillna(2.0)
    df["rex_status_rank"] = df["rex_status"].map(_rex_status_rank)

    # Normalise market_cap (log10 to handle 8B vs 900B range)
    import numpy as np
    cap = df["market_cap_usd"].fillna(1e9).clip(lower=1e8)
    df["market_cap_score"] = np.log10(cap) - 8.0  # ~0..3 range
    df["market_cap_score"] = df["market_cap_score"].clip(lower=0.0, upper=3.5)

    # Weighted composite: rex_status dominates, then cap, then sector
    df["composite_score"] = (
        df["rex_status_rank"] * 10.0
        + df["market_cap_score"] * 2.0
        + df["sector_strength"] * 1.0
    )
    df = df.sort_values(
        ["rex_status_rank", "composite_score"],
        ascending=[False, False],
    ).reset_index(drop=True)
    return df


def build() -> pd.DataFrame:
    universe = load_foreign_universe()
    raw = scan_foreign_filings(universe)
    log.info("Raw foreign filings rolled-up: %d underliers", len(raw))
    if raw.empty:
        return raw
    return rank(raw, universe)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    df = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        # Write an empty parquet with the expected schema so downstream
        # readers don't crash.
        empty = pd.DataFrame(columns=[
            "foreign_ticker", "name", "market", "sector", "market_cap_usd",
            "rex_status", "rex_fund_name", "rex_ticker", "rex_latest_filing",
            "competitor_filings_total", "competitor_distinct_funds",
            "competitor_distinct_issuers", "competitor_2x_status",
            "competitor_active_count", "sector_strength", "rex_status_rank",
            "market_cap_score", "composite_score",
        ])
        empty.to_parquet(OUT, compression="snappy")
        log.info("Wrote empty %s (no foreign REX filings)", OUT)
        print(f"\nForeign launch candidates: 0 (empty parquet written)")
        return

    df.to_parquet(OUT, compression="snappy")
    log.info("Wrote %s (%d rows)", OUT, len(df))

    print(f"\nForeign launch candidates: {len(df)}")
    cols = [
        "foreign_ticker", "name", "market", "sector",
        "rex_status", "rex_fund_name", "rex_latest_filing",
        "competitor_filings_total", "competitor_distinct_issuers",
        "competitor_2x_status", "competitor_active_count",
        "market_cap_usd", "composite_score",
    ]
    cols = [c for c in cols if c in df.columns]
    print(df[cols].head(10).to_string())


if __name__ == "__main__":
    main()

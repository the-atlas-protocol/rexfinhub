"""Weekly L&I Recommender — final structure per Ryu's spec (2026-04-24).

Five sections:
    1. Key Highlights (TL;DR bullets)
    2. Top Launch Recommendations — REX has filed, no live competitor product
    3. Top Filing Recommendations — true whitespace, REX hasn't filed yet
    4. Money Flow — top underliers by 4w flow with competitor counts (Long/Short)
    5. IPOs — table with valuation + 1-line description

Layout matches the daily ETP report style (email-table HTML).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

from screener.li_engine.analysis.pre_ipo_filer_race import (
    load_pre_ipo_filer_race,
    render_filers_pills,
)

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
WS = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"
LC = _ROOT / "data" / "analysis" / "launch_candidates.parquet"
TS = _ROOT / "data" / "analysis" / "bbg_timeseries_panel.parquet"
COMP = _ROOT / "data" / "analysis" / "competitor_counts.parquet"
OUT = _ROOT / "reports" / f"li_weekly_v2_{date.today().isoformat()}.html"


# ---------------------------------------------------------------------------
# Per-ticker descriptions for the LAUNCH and FILING sections.
# 1-line each. Hand-curated based on company knowledge + signals.
# ---------------------------------------------------------------------------
# Common underlier ticker → company name, for fund-name parse fallback.
# Covers the ~50 most frequent single-stock L&I underliers.
_TICKER_COMPANY_NAMES: dict[str, str] = {
    "NVDA": "NVIDIA Corporation",
    "TSLA": "Tesla Inc.",
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "AMZN": "Amazon.com Inc.",
    "META": "Meta Platforms Inc.",
    "GOOGL": "Alphabet Inc.",
    "GOOG": "Alphabet Inc.",
    "AMD": "Advanced Micro Devices Inc.",
    "INTC": "Intel Corporation",
    "AVGO": "Broadcom Inc.",
    "ARM": "Arm Holdings",
    "SMCI": "Super Micro Computer Inc.",
    "PLTR": "Palantir Technologies Inc.",
    "MSTR": "MicroStrategy Inc.",
    "COIN": "Coinbase Global Inc.",
    "MARA": "MARA Holdings Inc.",
    "RIOT": "Riot Platforms Inc.",
    "SOXL": "Direxion Daily Semiconductor Bull 3X ETF",
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ ETF",
    "IWM": "iShares Russell 2000 ETF",
    "TNA": "Direxion Daily Small Cap Bull 3X",
    "TQQQ": "ProShares UltraPro QQQ",
    "SQQQ": "ProShares UltraPro Short QQQ",
    "UPRO": "ProShares UltraPro S&P500",
    "NFLX": "Netflix Inc.",
    "DIS": "The Walt Disney Company",
    "BABA": "Alibaba Group Holding Ltd.",
    "NIO": "NIO Inc.",
    "XPEV": "XPeng Inc.",
    "LI": "Li Auto Inc.",
    "RIVN": "Rivian Automotive Inc.",
    "LCID": "Lucid Group Inc.",
    "GME": "GameStop Corp.",
    "AMC": "AMC Entertainment Holdings Inc.",
    "BBY": "Best Buy Co. Inc.",
    "F": "Ford Motor Company",
    "GM": "General Motors Company",
    "BA": "The Boeing Company",
    "GS": "Goldman Sachs Group Inc.",
    "JPM": "JPMorgan Chase & Co.",
    "XOM": "Exxon Mobil Corporation",
    "CVX": "Chevron Corporation",
    "AMAT": "Applied Materials Inc.",
    "LRCX": "Lam Research Corporation",
    "KLAC": "KLA Corporation",
    "ASML": "ASML Holding N.V.",
    "MU": "Micron Technology Inc.",
    "TSM": "Taiwan Semiconductor Manufacturing Co.",
    "DJT": "Trump Media & Technology Group",
    "RDDT": "Reddit Inc.",
    "APP": "Applovin Corporation",
    "HOOD": "Robinhood Markets Inc.",
    "SQ": "Block Inc.",
    "PYPL": "PayPal Holdings Inc.",
    "UBER": "Uber Technologies Inc.",
    "LYFT": "Lyft Inc.",
    "SNAP": "Snap Inc.",
    "TWTR": "Twitter / X Corp.",
    "ABNB": "Airbnb Inc.",
    "RBLX": "Roblox Corporation",
    "U": "Unity Software Inc.",
    "SOFI": "SoFi Technologies Inc.",
    "OPEN": "Opendoor Technologies Inc.",
    "WOLF": "Wolfspeed Inc.",
    "IONQ": "IonQ Inc.",
    "QBTS": "D-Wave Quantum Inc.",
    "RGTI": "Rigetti Computing Inc.",
    "ARQQ": "Arqit Quantum Inc.",
    "OKLO": "Oklo Inc.",
    "SMR": "NuScale Power Corporation",
    "LEU": "Centrus Energy Corp.",
    "HIMS": "Hims & Hers Health Inc.",
    "ACHR": "Archer Aviation Inc.",
    "JOBY": "Joby Aviation Inc.",
    "LUNR": "Intuitive Machines Inc.",
    "RDW": "Redwire Corporation",
    "RKLB": "Rocket Lab USA Inc.",
    "ASTS": "AST SpaceMobile Inc.",
    "STRL": "Sterling Infrastructure Inc.",
    "MELI": "MercadoLibre Inc.",
    "SE": "Sea Limited",
}

def _load_yaml_overrides(filename, default):
    """Merge a YAML config file into a default dict or list.

    For dict defaults: YAML keys override matching entries (new keys are added).
    For list defaults: YAML replaces the list wholesale when the file is present
    and parses cleanly; otherwise the hardcoded default is returned unchanged.
    Falls back to *default* silently on any error (missing file, bad YAML, etc.)
    so a corrupt config never prevents report generation.
    """
    import yaml
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent.parent.parent / "config" / filename
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        if isinstance(default, dict) and isinstance(overrides, dict):
            merged = dict(default)
            merged.update(overrides)
            return merged
        if isinstance(default, list):
            # ipo_watchlist.yaml uses {high_profile_pre_ipo: [...], recently_priced: [...]}
            if isinstance(overrides, dict):
                entries = []
                for section in overrides.values():
                    if isinstance(section, list):
                        entries.extend(section)
                if entries:
                    # Normalise: strip extra keys (e.g. last_reviewed) not in IPO_DATA
                    keep = {"ticker", "company", "date", "valuation", "desc"}
                    return [{k: v for k, v in e.items() if k in keep} for e in entries]
            return default
    except Exception as e:
        print(f"Warning: failed to load {filename}: {e}")
    return default


COMPANY_LINES = {
    # Launch candidates (REX filed, want to launch)
    "AXTI": "Compound-semiconductor substrates (InP/GaAs) for AI optical interconnects — the +3,941% 1y rally is a real photonics narrative.",
    "DOCN": "DigitalOcean — cloud infrastructure mid-cap, AI-data-center beneficiary; +53% 1m on real fundamental growth.",
    "UI": "Ubiquiti — networking equipment for ISP/enterprise; quietly compounding business with 14 retail mentions today.",
    "DNA": "Ginkgo Bioworks — synthetic biology platform; biotech vol with a 'biofoundry' narrative.",
    "AMPX": "Amprius Technologies — silicon-anode batteries for EV and aviation; +57% 1m and active retail momentum.",
    "TSEM": "Tower Semiconductor — Israeli specialty foundry (analog, RF); Intel acquisition history gives strategic-value floor.",
    "FSLY": "Fastly — edge-compute / CDN; AI-serving infrastructure tailwind has revived a beaten-down name.",
    "FNMA": "Fannie Mae — GSE recapitalization narrative continues to drive speculative flows.",
    "FMCC": "Freddie Mac — paired with FNMA; same recap thesis, smaller free float.",
    "CIEN": "Ciena — optical networking / 800G coherent; AI-data-center photonics angle.",
    "VIAV": "Viavi Solutions — optical test equipment; AI-data-center capex beneficiary.",
    "VSAT": "Viasat — satellite broadband + defense; space + connectivity.",
    "AKAM": "Akamai — CDN + cybersecurity; defensive infrastructure name.",
    # Filing candidates (whitespace)
    "LWLG": "Lightwave Logic — development-stage electro-optic polymer; +586% 1y but rally has cooled (zero retail mentions today).",
    "SLS": "SELLAS Life Sciences — clinical-stage oncology biotech; binary trial readouts drive 114% volatility.",
    "BW": "Babcock & Wilcox — small-modular reactor exposure (nuclear renaissance theme); +2,085% 1y.",
    "KOD": "Kodiak Sciences — clinical-stage retinal biotech with Phase 3 readouts driving the +1,259% 1y move.",
    "NEXT": "NextDecade — Rio Grande LNG export developer; financing milestones drove +42% 1m.",
    "CC": "Chemours — fluoropolymers + Opteon refrigerant; HFC phase-out story with 32 retail mentions.",
    "ATAI": "atai Life Sciences — psychedelic medicine biotech (psilocybin/ketamine analogues).",
    "SOC": "Sable Offshore — Santa Ynez offshore-oil restart play; institutional-driven (94% inst), not a retail leverage profile.",
    "VICR": "Vicor — power-conversion ICs for AI data centers; specialty-semis whitespace.",
    "YOU": "Clear Secure — biometric airport security (CLEAR lanes); travel + biometric narrative.",
    "NTLA": "Intellia Therapeutics — CRISPR gene editing; biotech-gene thematic.",
    "VG": "Venture Global LNG — recently IPO'd LNG exporter; same theme as NEXT but $38.7B size.",
    "WOLF": "Wolfspeed — silicon-carbide power semis; bruised by EV-cycle pullback but strategic.",
    "RUN": "Sunrun — residential solar leasing; policy-dependent solar narrative.",
    "PUMP": "ProPetro — oilfield services / pressure pumping; cyclical energy services.",
    "BWXT": "BWX Technologies — naval nuclear reactor builder + SMR; **hot-theme** play (nuclear).",
}

COMPANY_LINES = _load_yaml_overrides("company_descriptions.yaml", COMPANY_LINES)


# ---------------------------------------------------------------------------
# Company line resolver — never returns "Description pending."
# Priority: COMPANY_LINES dict → sector from parquet → fund-name parse → safe fallback
# ---------------------------------------------------------------------------

def _resolve_company_line(ticker: str, sector: str | None = None,
                          fund_name: str | None = None) -> str:
    """Return a 1-line description for *ticker*.

    Resolution order:
    1. Hand-curated COMPANY_LINES dict
    2. sector string if provided (e.g. "Technology")
    3. Fund-name parse via _TICKER_COMPANY_NAMES — strip leverage prefixes and
       attempt to infer the underlier company name from the ticker embedded in
       the fund name (e.g. "T-REX 2X LONG DJT" → DJT → Trump Media...)
    4. Safe fallback — never "Description pending."
    """
    # 1. Hand-curated
    if ticker in COMPANY_LINES:
        return COMPANY_LINES[ticker]

    # 2. Known company name dict (covers common underlier tickers)
    if ticker in _TICKER_COMPANY_NAMES:
        cname = _TICKER_COMPANY_NAMES[ticker]
        return f"{cname} — referenced via leveraged ETF"

    # 3. Fund-name parse: try to extract embedded underlier ticker
    if fund_name:
        import re
        # Strip common multiplier tokens to isolate the core ticker word(s)
        cleaned = re.sub(
            r"\b(T-REX|DEFIANCE|DIREXION|PROSHARES|ROUNDHILL|LEVERAGE\s+SHARES?|"
            r"\dX|2X|3X|LONG|SHORT|DAILY|TARGET|BULL|BEAR|ULTRA|FUND|ETF|DAILY\s+TARGET)\b",
            " ", fund_name, flags=re.IGNORECASE,
        ).strip()
        # Pick uppercase-only words (likely tickers)
        candidates = [w for w in cleaned.split() if w.isupper() and 2 <= len(w) <= 6]
        for cand in candidates:
            if cand in _TICKER_COMPANY_NAMES:
                cname = _TICKER_COMPANY_NAMES[cand]
                return f"{cname} — referenced via leveraged ETF"

    # 4. Sector hint
    if sector and sector != "—":
        return f"{ticker} — {sector} sector underlier"

    # 5. Hard fallback — informative, never "Description pending."
    return f"{ticker} — see SEC filing details"


# ---------------------------------------------------------------------------
# IPO data with handwritten descriptions
# ---------------------------------------------------------------------------
IPO_DATA = [
    # ==== HIGH-PROFILE PRE-IPO (private, expected to debut) ====
    # Valuations are last-known private rounds / tenders. Market may have moved.
    {"ticker": "SpaceX", "company": "SpaceX", "date": "TBD (Starlink spinout watch)",
     "valuation": "Last private round ~$400B", "desc": "Starlink spin-out is the most-discussed vehicle. Space-economy + satellite-broadband exposure."},
    {"ticker": "OpenAI", "company": "OpenAI", "date": "TBD",
     "valuation": "Last tender at $500B (2025)", "desc": "Foundation-model leader. PBC restructure enables eventual public exit; no S-1 yet."},
    {"ticker": "Anthropic", "company": "Anthropic", "date": "TBD",
     "valuation": "Last round ~$183B (2026)", "desc": "Claude / safety-first foundation models. Strategic backing from Amazon + Google."},
    {"ticker": "Anduril", "company": "Anduril Industries", "date": "TBD",
     "valuation": "Last round ~$30B+", "desc": "Defense-tech / autonomous systems. Lattice OS + AI-augmented hardware."},
    {"ticker": "Scale AI", "company": "Scale AI", "date": "TBD",
     "valuation": "~$25B+ (Meta investment)", "desc": "Data labeling + evaluation infrastructure. Critical-path supplier to foundation-model labs."},
    {"ticker": "Stripe", "company": "Stripe", "date": "Speculative",
     "valuation": "Last tender ~$91B (2024)", "desc": "Payments infrastructure. Long-running IPO speculation; tender prices set the floor."},
    {"ticker": "Databricks", "company": "Databricks", "date": "Speculative",
     "valuation": "Last round ~$62B", "desc": "Lakehouse + AI platform. F-1 watch ongoing; Snowflake is the trading comp."},
    {"ticker": "xAI", "company": "xAI", "date": "Speculative",
     "valuation": "Reportedly ~$200B in latest discussions", "desc": "Grok models, Musk-led. IPO path tied to Tesla/X integration narrative."},
    {"ticker": "Cerebras", "company": "Cerebras Systems", "date": "Has filed S-1",
     "valuation": "Targeted ~$8B at filing", "desc": "Wafer-scale AI accelerator chips. AI-infra exposure outside the Nvidia trade."},
    {"ticker": "Klarna", "company": "Klarna", "date": "Filed for IPO",
     "valuation": "Targeted ~$15B", "desc": "BNPL leader. Re-filed after multiple postponements; testing 2026 window."},

    # ==== RECENTLY PRICED ====
    {"ticker": "ALMR", "company": "Alamar Biosciences", "date": "Apr 17, 2026",
     "valuation": "Recently priced", "desc": "Proteomics platform — high-multiplex single-cell protein detection."},
    {"ticker": "AVEX", "company": "AEVEX Corp", "date": "Apr 17, 2026",
     "valuation": "Recently priced", "desc": "Defense / unmanned aerial systems — military drone & ISR services."},
    {"ticker": "KLRA", "company": "Kailera Therapeutics", "date": "Apr 17, 2026",
     "valuation": "Recently priced", "desc": "Obesity + GLP-1 biotech — chasing the Wegovy/Zepbound market."},
    {"ticker": "NHP", "company": "National Healthcare Properties", "date": "Apr 22, 2026",
     "valuation": "Recently priced", "desc": "Healthcare REIT — senior housing + medical office portfolio."},
    {"ticker": "YSWY", "company": "Yesway", "date": "Apr 22, 2026",
     "valuation": "Recently priced", "desc": "Convenience store chain (~440 locations across 9 states)."},
]

IPO_DATA = _load_yaml_overrides("ipo_watchlist.yaml", IPO_DATA)


def _clean(t):
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_launch_candidates(n: int = 10, min_strength: str = "MODERATE") -> pd.DataFrame:
    """Top launch candidates filtered by tiered signal strength.

    A3 upgrade: replaces the old ``has_signals == True`` filter (which only
    proved Bloomberg returned a row) with a meaningful tier gate. Defaults
    to MODERATE so weak-only candidates can't dilute the top of the report.

    Falls back to the legacy ``has_signals`` filter if the new column is
    absent (e.g. running against an older parquet).
    """
    if not LC.exists():
        return pd.DataFrame()
    df = pd.read_parquet(LC)

    if "signal_strength" in df.columns:
        from screener.li_engine.analysis.signal_strength import SignalStrength
        threshold = SignalStrength.from_name(min_strength)
        ranks = df["signal_strength"].map(
            lambda s: int(SignalStrength.from_name(s)) if isinstance(s, str) else 0
        )
        df = df[ranks >= int(threshold)]
    elif "has_signals" in df.columns:
        log.warning("load_launch_candidates: signal_strength absent; falling back to has_signals")
        df = df[df["has_signals"] == True]  # noqa: E712

    return df.sort_values("composite_score", ascending=False).head(n)


def load_whitespace_top(n: int = 10) -> pd.DataFrame:
    df = pd.read_parquet(WS)
    return df.sort_values("composite_score", ascending=False).head(n)


def load_top_mentions() -> tuple[str, int]:
    """Return (ticker, mention_count) of the highest-mention name in the universe."""
    ws = pd.read_parquet(WS)
    if "mentions_24h" not in ws.columns:
        return "—", 0
    top = ws.sort_values("mentions_24h", ascending=False).iloc[0]
    return top.name, int(top["mentions_24h"] or 0)


def load_filings_count_this_week() -> dict:
    conn = sqlite3.connect(str(DB))
    try:
        latest = conn.execute("SELECT MAX(filing_date) FROM filings").fetchone()[0]
        if not latest:
            return {"total": 0, "li_485apos": 0, "rex_li_filings": 0, "new_stocks_filed": 0}

        latest_dt = pd.to_datetime(latest)
        week_start = (latest_dt - pd.Timedelta(days=7)).date().isoformat()
        week_end = latest_dt.date().isoformat()

        # All L&I 485APOS this week
        rows = pd.read_sql_query(f"""
            SELECT f.registrant, fe.series_name
            FROM filings f
            JOIN fund_extractions fe ON fe.filing_id = f.id
            WHERE f.form='485APOS'
              AND f.filing_date BETWEEN '{week_start}' AND '{week_end}'
              AND (fe.series_name LIKE '%2X%' OR fe.series_name LIKE '%3X%'
                   OR fe.series_name LIKE '%Inverse%' OR fe.series_name LIKE '%Bull%' OR fe.series_name LIKE '%Bear%'
                   OR fe.series_name LIKE '%Long %' OR fe.series_name LIKE '%Short %')
        """, conn)
    finally:
        conn.close()

    from screener.li_engine.analysis.filed_underliers import extract_underlier
    rows["underlier"] = rows["series_name"].apply(extract_underlier)
    rex_mask = rows["registrant"].astype(str).str.contains(r"REX|ETF Opportunities", case=False, regex=True, na=False)

    return {
        "week_start": week_start,
        "week_end": week_end,
        "li_485apos": len(rows),
        "rex_li_filings": int(rex_mask.sum()),
        "new_stocks_filed": int(rows["underlier"].dropna().nunique()),
        "rex_underliers": list(rows[rex_mask]["underlier"].dropna().unique()),
    }


def load_money_flow(n: int = 12, days: int = 7) -> pd.DataFrame:
    """Top underliers by N-day flow, with competitor count + REX direction."""
    ts = pd.read_parquet(TS)
    flow = ts[ts["metric"] == "daily_flow"].copy()
    flow["date"] = pd.to_datetime(flow["date"])

    conn = sqlite3.connect(str(DB))
    try:
        m = pd.read_sql_query(
            "SELECT ticker, map_li_underlier, map_li_direction, is_rex, market_status FROM mkt_master_data "
            "WHERE primary_category='LI' AND map_li_underlier IS NOT NULL",
            conn,
        )
    finally:
        conn.close()
    m["prod"] = m["ticker"].str.split().str[0]
    m["underlier"] = m["map_li_underlier"].str.split().str[0]
    p2u = dict(zip(m["prod"], m["underlier"]))

    flow["prod"] = flow["ticker"].str.split().str[0]
    flow["underlier"] = flow["prod"].map(p2u)
    flow = flow.dropna(subset=["underlier"])
    cutoff = flow["date"].max() - pd.Timedelta(days=days)
    recent = flow[flow["date"] >= cutoff]
    agg = recent.groupby("underlier").agg(
        flow_4w=("value", "sum"),
        flow_4w_abs=("value", lambda x: x.abs().sum()),
    )

    # REX exposure with direction
    active_rex = m[(m["is_rex"] == 1) & (m["market_status"].isin(["ACTV", "ACTIVE"]))].copy()
    rex_summary = active_rex.groupby(["underlier", "map_li_direction"]).size().unstack(fill_value=0)
    if "Long" not in rex_summary.columns:
        rex_summary["Long"] = 0
    if "Short" not in rex_summary.columns:
        rex_summary["Short"] = 0
    rex_summary = rex_summary[["Long", "Short"]].rename(
        columns={"Long": "rex_long", "Short": "rex_short"}
    )
    agg = agg.join(rex_summary, how="left").fillna({"rex_long": 0, "rex_short": 0}).astype({"rex_long": int, "rex_short": int})

    # Competitor counts from competitor_counts.parquet
    if COMP.exists():
        cc = pd.read_parquet(COMP)
        agg = agg.join(cc[["competitor_active_long", "competitor_active_short"]], how="left").fillna(0)
        agg["competitor_active_long"] = agg["competitor_active_long"].astype(int)
        agg["competitor_active_short"] = agg["competitor_active_short"].astype(int)
    else:
        agg["competitor_active_long"] = 0
        agg["competitor_active_short"] = 0

    return agg.sort_values("flow_4w", ascending=False, key=lambda s: s.abs()).head(n)


def load_earliest_competitor_filing_dates() -> dict[str, dict]:
    """For each underlier ticker, return earliest competitor filing date,
    that issuer, AND the closest projected effective date if any.
    Uses fund_extractions regex extraction so we catch new filings."""
    conn = sqlite3.connect(str(DB))
    try:
        df = pd.read_sql_query(
            """
            SELECT fe.series_name, fe.class_contract_name, fe.filing_id,
                   f.filing_date, f.form, f.registrant
            FROM fund_extractions fe
            JOIN filings f ON f.id = fe.filing_id
            WHERE f.form IN ('485APOS', '485BPOS', 'N-1A', 'S-1')
            """,
            conn,
        )
        # Effective dates per fund_extraction id from fund_status
        fs = pd.read_sql_query(
            """
            SELECT fund_name, status, effective_date
            FROM fund_status
            WHERE effective_date IS NOT NULL
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return {}

    from screener.li_engine.analysis.filed_underliers import extract_underlier
    df["underlier"] = df["series_name"].apply(extract_underlier)
    fb = df["underlier"].isna()
    df.loc[fb, "underlier"] = df.loc[fb, "class_contract_name"].apply(extract_underlier)
    df = df.dropna(subset=["underlier"])
    df["underlier"] = df["underlier"].str.upper()
    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df = df.dropna(subset=["filing_date"])

    df["is_rex"] = df["registrant"].astype(str).str.contains(
        r"REX|ETF Opportunities", case=False, na=False, regex=True
    )
    comp = df[~df["is_rex"]].copy()

    # Effective dates by fund_name (best join we have)
    fs["effective_date"] = pd.to_datetime(fs["effective_date"], errors="coerce")
    fs = fs.dropna(subset=["effective_date"])
    eff_by_fund = dict(zip(fs["fund_name"].fillna(""), fs["effective_date"]))

    def _eff_for_row(row):
        for col in ("series_name", "class_contract_name"):
            name = row.get(col) or ""
            if name in eff_by_fund:
                return eff_by_fund[name]
        return pd.NaT

    comp["effective_date"] = comp.apply(_eff_for_row, axis=1)

    out: dict[str, dict] = {}
    for u, grp in comp.groupby("underlier"):
        earliest_idx = grp["filing_date"].idxmin()
        earliest = grp.loc[earliest_idx]
        # Closest (earliest) effective date — real or projected
        eff_dates = grp["effective_date"].dropna()
        closest_eff = eff_dates.min().date() if not eff_dates.empty else None
        projected_eff = None
        proj_basis = None
        if closest_eff is None:
            apos = grp[grp["form"] == "485APOS"]
            if not apos.empty:
                base = apos["filing_date"].min()
                projected_eff = (base + pd.Timedelta(days=75)).date()
                proj_basis = "485APOS+75d"
            else:
                base = grp["filing_date"].min()
                projected_eff = (base + pd.Timedelta(days=75)).date()
                proj_basis = str(grp.loc[grp["filing_date"].idxmin(), "form"]) + "+75d"
        out[u] = {
            "earliest_filing_date": earliest["filing_date"].date(),
            "earliest_issuer": (earliest["registrant"] or "")[:30],
            "n_competitors": int(grp["registrant"].nunique()),
            "n_filings": int(len(grp)),
            "closest_effective_date": closest_eff,
            "projected_effective_date": projected_eff,
            "projected_basis": proj_basis,
        }
    return out


def load_launches_this_week() -> pd.DataFrame:
    """New L&I fund launches with inception_date in last 7 days. Filter
    to L&I products only (either primary_category=LI or fund_name matches
    leverage/inverse patterns)."""
    conn = sqlite3.connect(str(DB))
    try:
        df = pd.read_sql_query(
            """
            SELECT
                m.ticker,
                m.fund_name,
                COALESCE(
                    m.issuer_display,
                    (
                        SELECT f.registrant
                        FROM fund_extractions fe
                        JOIN filings f ON f.id = fe.filing_id
                        WHERE (
                            fe.series_name = m.fund_name
                            OR fe.class_contract_name = m.fund_name
                        )
                        ORDER BY f.filing_date DESC
                        LIMIT 1
                    )
                ) AS issuer_display,
                m.is_rex,
                m.market_status,
                m.aum,
                m.inception_date,
                m.primary_category,
                m.map_li_underlier,
                m.map_li_direction,
                m.map_li_leverage_amount
            FROM mkt_master_data m
            WHERE m.inception_date IS NOT NULL
              AND m.inception_date >= date('now', '-9 days')
              AND m.inception_date <= date('now')
              AND m.market_status IN ('ACTV', 'ACTIVE')
              AND m.fund_name IS NOT NULL
              AND (
                m.primary_category = 'LI'
                OR m.fund_name LIKE '%2X%'
                OR m.fund_name LIKE '%3X%'
                OR m.fund_name LIKE '%2x%'
                OR m.fund_name LIKE '%3x%'
                OR m.fund_name LIKE '%Inverse%'
                OR m.fund_name LIKE '%Bull%'
                OR m.fund_name LIKE '%Bear%'
                OR m.fund_name LIKE '%Ultra%'
                OR m.fund_name LIKE '%Leveraged%'
              )
            """,
            conn,
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["inception_date"] = pd.to_datetime(df["inception_date"], errors="coerce")
    df = df.dropna(subset=["inception_date"])
    df = df.sort_values("inception_date", ascending=False)
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_mcap(v):
    if pd.isna(v) or v is None:
        return "—"
    if v >= 100_000:
        return f"${v/1000:.0f}B"
    if v >= 1000:
        return f"${v/1000:.1f}B"
    return f"${v:,.0f}M"


def _fmt_pct(v):
    if pd.isna(v) or v is None:
        return "—"
    return f"{v:+.0f}%"


def _fmt_oi(v):
    if pd.isna(v) or v is None:
        return "—"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1000:
        return f"{v/1000:.0f}K"
    return f"{v:.0f}"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

THEME_LABELS = {
    "ai_infrastructure": "AI Infrastructure",
    "ai_applications": "AI Applications",
    "semiconductors": "Semiconductors",
    "quantum": "Quantum",
    "space": "Space",
    "nuclear": "Nuclear",
    "memory": "Memory",
    "biotech_gene": "Biotech / Gene Editing",
    "crypto_equity": "Crypto Equity",
    "ev_battery": "EV / Battery",
    "korea_proxies": "Korea Proxies",
}


def _pretty_themes(raw: str) -> str:
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return ", ".join(THEME_LABELS.get(p, p.replace("_", " ").title()) for p in parts)


def _section_card(ticker: str, row: pd.Series, comp_filed_total: int = 0,
                  is_launch: bool = True, earliest_comp: dict | None = None) -> str:
    sector = row.get("sector") or "—"
    mcap = _fmt_mcap(row.get("market_cap"))
    rvol = row.get("rvol_90d", 0) or 0
    ret_1m = row.get("ret_1m", 0) or 0
    ret_1y = row.get("ret_1y", 0) or 0
    oi = _fmt_oi(row.get("total_oi"))
    si = row.get("si_ratio", 0) or 0
    mentions = int(row.get("mentions_24h", 0) or 0)
    themes_raw = (row.get("themes") or "").strip()
    themes_pretty = _pretty_themes(themes_raw)
    is_hot = bool(row.get("is_hot_theme", 0))

    company_line = _resolve_company_line(
        ticker,
        sector=sector if sector != "—" else None,
        fund_name=row.get("rex_fund_name") or row.get("fund_name") or None,
    )

    hot_badge = ('<span style="background:#e74c3c;color:white;padding:1px 6px;border-radius:8px;'
                 'font-size:9px;font-weight:700;margin-left:4px;">HOT THEME</span>') if is_hot else ""
    theme_badge = (f'<span style="background:#27ae60;color:white;padding:1px 6px;border-radius:8px;'
                   f'font-size:9px;font-weight:600;margin-left:4px;">{escape(themes_pretty)}</span>') if themes_pretty else ""

    # Competitor / effective date lines
    if earliest_comp and comp_filed_total > 0:
        ed = earliest_comp.get("earliest_filing_date")
        ei = earliest_comp.get("earliest_issuer", "")
        eff = earliest_comp.get("closest_effective_date")
        comp_line = f"<strong>{comp_filed_total}</strong> filed by {escape(str(ei))} (earliest filing {ed})"
        if eff:
            eff_line = f"Closest effective date: <strong>{eff}</strong>"
        else:
            proj = earliest_comp.get("projected_effective_date")
            basis = earliest_comp.get("projected_basis") or "projected"
            if proj:
                eff_line = f'Projected effective: <strong>{proj}</strong> <span style="color:#94a3b8;font-size:10px;">({basis})</span>'
            else:
                eff_line = "Closest effective date: <strong>not yet effective</strong>"
    else:
        comp_line = "<strong>0</strong> — clean greenfield"
        eff_line = "Closest effective date: <strong>n/a</strong>"

    return f'''
    <tr><td style="padding:14px 14px 6px;background:#fcfcfd;border-top:1px solid #e8eaed;">
      <span style="font-family:'Courier New',monospace;font-size:16px;font-weight:700;color:#0984e3;">{escape(ticker)}</span>
      <span style="color:#7f8c8d;font-size:11px;margin-left:8px;">{escape(sector)}</span>
      {hot_badge}{theme_badge}
    </td></tr>
    <tr><td style="padding:0 14px 6px;background:#fcfcfd;">
      <div style="font-size:12.5px;color:#2c3e50;line-height:1.55;margin:6px 0;">{escape(company_line)}</div>
    </td></tr>
    <tr><td style="padding:0 14px 12px;background:#fcfcfd;border-bottom:1px solid #ecf0f1;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#f1f3f5;border-radius:4px;">
        <tr><td style="padding:7px 10px;font-family:'Courier New',monospace;font-size:11px;color:#566573;">
          Mkt Cap: <strong>{mcap}</strong> ·
          Vol90: <strong>{rvol:.0f}%</strong> ·
          1m: <strong>{ret_1m:+.0f}%</strong> ·
          1y: <strong>{ret_1y:+.0f}%</strong> ·
          OI: <strong>{oi}</strong> ·
          SI: <strong>{si:.1f}</strong> ·
          Mentions: <strong>{mentions}</strong>
        </td></tr>
        <tr><td style="padding:6px 10px;font-size:11px;color:#566573;border-top:1px dashed #d4d8dd;">
          Filers: {comp_line}
        </td></tr>
        <tr><td style="padding:6px 10px;font-size:11px;color:#566573;border-top:1px dashed #d4d8dd;">
          {eff_line}
        </td></tr>
      </table>
    </td></tr>
    '''


def render(launch: pd.DataFrame, whitespace: pd.DataFrame, money_flow: pd.DataFrame,
           filings_summary: dict, top_mentions: tuple[str, int],
           hot_take: str, earliest_comp: dict | None = None,
           launches_week: pd.DataFrame | None = None,
           ipo_filers: dict | None = None) -> str:
    ipo_filers = ipo_filers or {}
    earliest_comp = earliest_comp or {}
    today_str = date.today().strftime("%B %d, %Y")
    week_window = f"{filings_summary.get('week_start', '')} → {filings_summary.get('week_end', '')}"

    # Build top-5 lists for highlights
    top5_file = ", ".join(whitespace.head(5).index.tolist())
    top5_launch = ", ".join(launch.head(5).index.tolist())

    # === Launch cards (show all loaded — main() loads 10) ===
    launch_cards = ""
    if not launch.empty:
        for ticker in launch.index:
            r = launch.loc[ticker]
            comp_total = int(r.get("competitor_filed_total", 0) or 0)
            launch_cards += _section_card(ticker, r, comp_total, is_launch=True,
                                          earliest_comp=earliest_comp.get(ticker))
    else:
        launch_cards = '<tr><td style="padding:14px;color:#7f8c8d;font-style:italic;">No clean launch candidates this week.</td></tr>'

    # === Whitespace cards ===
    ws_cards = ""
    for ticker in whitespace.index:
        r = whitespace.loc[ticker]
        # For whitespace: competitor_filed total = whatever the regex caught
        comp_path = _ROOT / "data" / "analysis" / "filed_underliers.parquet"
        comp_total = 0
        if comp_path.exists():
            fu = pd.read_parquet(comp_path)
            if ticker in fu.index:
                comp_total = int(fu.loc[ticker, "n_filings_total"])
        ws_cards += _section_card(ticker, r, comp_total, is_launch=False,
                                  earliest_comp=earliest_comp.get(ticker))

    # === Money flow rows ===
    flow_rows = []
    for i, (underlier, r) in enumerate(money_flow.iterrows(), 1):
        f = r["flow_4w"]
        f_color = "#27ae60" if f > 0 else "#e74c3c"
        rex_long = int(r.get("rex_long", 0) or 0)
        rex_short = int(r.get("rex_short", 0) or 0)
        if rex_long and rex_short:
            rex_str = f'<span style="color:#27ae60;font-weight:700;">L:{rex_long}</span> / <span style="color:#e74c3c;font-weight:700;">S:{rex_short}</span>'
        elif rex_long:
            rex_str = f'<span style="color:#27ae60;font-weight:700;">L:{rex_long}</span>'
        elif rex_short:
            rex_str = f'<span style="color:#e74c3c;font-weight:700;">S:{rex_short}</span>'
        else:
            rex_str = '<span style="color:#7f8c8d;">none</span>'

        comp_long = int(r.get("competitor_active_long", 0) or 0)
        comp_short = int(r.get("competitor_active_short", 0) or 0)
        comp_str = f"L:{comp_long} / S:{comp_short}"
        f_abs = float(r.get("flow_4w_abs", abs(f)) or abs(f))

        flow_rows.append(f'''
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#7f8c8d;">{i}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11.5px;font-weight:700;font-family:'Courier New',monospace;color:#0984e3;">{escape(underlier)}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;color:{f_color};font-weight:700;">${f:+,.1f}M</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;color:#566573;">${f_abs:,.1f}M</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:center;">{rex_str}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:center;color:#7f8c8d;">{comp_str}</td>
            </tr>
        ''')
    flow_rows_html = "".join(flow_rows)

    # === Launches of the Week rows ===
    launches_html_rows = []
    if launches_week is not None and not launches_week.empty:
        for _, r in launches_week.iterrows():
            t = r.get("ticker") or ""
            t_clean = (t.split()[0] if isinstance(t, str) else "")
            inception = r.get("inception_date")
            inc_str = inception.date().isoformat() if pd.notna(inception) else "—"
            _fn = r.get("fund_name")
            fund_name = ("" if (_fn is None or (isinstance(_fn, float) and pd.isna(_fn))) else str(_fn))[:60]
            _is = r.get("issuer_display")
            issuer = ("" if (_is is None or (isinstance(_is, float) and pd.isna(_is))) else str(_is))[:25]
            aum = r.get("aum") or 0
            aum_str = f"${aum:.1f}M" if aum else "$0M"
            rex_pill = ('<span style="background:#0984e3;color:white;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:700;margin-left:4px;">REX</span>' if r.get("is_rex") else '')
            launches_html_rows.append(f'''
              <tr>
                <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11.5px;font-weight:700;font-family:'Courier New',monospace;color:#0984e3;">{escape(t_clean)}{rex_pill}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;">{escape(fund_name)}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#7f8c8d;">{escape(issuer)}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;">{aum_str}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:center;color:#7f8c8d;">{inc_str}</td>
              </tr>
            ''')
    launches_html = "".join(launches_html_rows)

    # === IPO rows ===
    ipo_rows = []
    for ipo in IPO_DATA:
        race = ipo_filers.get(ipo['ticker']) or ipo_filers.get(ipo['company']) or {}
        filers_html = render_filers_pills(race.get('filers', []))
        ipo_rows.append(f'''
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11.5px;font-weight:700;font-family:'Courier New',monospace;color:#0984e3;">{escape(ipo['ticker'])}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;">{escape(ipo['company'])}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:center;">{escape(ipo['valuation'])}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:center;color:#7f8c8d;">{escape(ipo['date'])}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;">{filers_html}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#566573;font-style:italic;">{escape(ipo['desc'])}</td>
            </tr>
        ''')
    ipo_rows_html = "".join(ipo_rows)

    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Recommendations of the Week — {today_str}</title>
</head>
<body style="margin:0;padding:0;background:#f8f9fa;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:#1a1a2e;line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="720" cellpadding="0" cellspacing="0" border="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:720px;table-layout:fixed;">

<!-- HEADER -->
<tr><td style="background:#1a1a2e;padding:24px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">Stock Recommendations of the Week | {today_str}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:6px;">Top Filings + Launches + Money Flow + IPOs</div>
</td></tr>

<!-- KEY HIGHLIGHTS -->
<tr><td style="padding:15px 30px 10px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f4f5f6;border-left:4px solid #1a1a2e;border-radius:0 8px 8px 0;">
    <tr><td style="padding:14px 18px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="padding:0 0 8px;font-size:13px;font-weight:700;color:#1a1a2e;text-transform:uppercase;letter-spacing:1px;">
          Key Highlights
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Top 5 to File:</strong> <span style="font-family:'Courier New',monospace;color:#0984e3;font-weight:700;">{escape(top5_file)}</span>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Top 5 to Launch (from REX filings):</strong> <span style="font-family:'Courier New',monospace;color:#27ae60;font-weight:700;">{escape(top5_launch)}</span>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>{filings_summary.get('li_485apos', 0)} L&amp;I Filings</strong> last week ({week_window}) · REX: {filings_summary.get('rex_li_filings', 0)} · <strong>{filings_summary.get('new_stocks_filed', 0)} new underliers</strong>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Highest Retail Buzz:</strong> <span style="font-family:'Courier New',monospace;color:#0984e3;font-weight:700;">{escape(top_mentions[0])}</span> with <strong>{top_mentions[1]}</strong> mentions on ApeWisdom
        </td></tr>
        <tr><td style="padding:6px 0 0;font-size:13px;color:#566573;line-height:1.5;font-style:italic;border-top:1px dashed #d4d8dd;margin-top:6px;">
          <span style="color:#0984e3;font-weight:700;margin-right:6px;">&#9656;</span>
          {escape(hot_take)}
        </td></tr>
      </table>
    </td></tr>
  </table>
</td></tr>

<!-- TOP LAUNCH RECOMMENDATIONS -->
<tr><td style="padding:18px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
              padding-bottom:6px;border-bottom:2px solid #27ae60;">
    Top Launch Recommendations
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:6px;font-style:italic;">
    Stocks where REX has filed but not launched, AND no competitor has an active 2x product. # Filed Competitors = other issuers in the queue on the same underlier.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="border:1px solid #e8eaed;border-radius:6px;overflow:hidden;">
    {launch_cards}
  </table>
</td></tr>

<!-- TOP FILING RECOMMENDATIONS -->
<tr><td style="padding:18px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
              padding-bottom:6px;border-bottom:2px solid #0984e3;">
    Top Filing Recommendations
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:6px;font-style:italic;">
    True whitespace: zero active competitor 2x products + zero REX filings ever + no competitor 485APOS in last 180d. Hot themes (AI, Quant, Chips, Space, Nuclear) get a score boost.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="border:1px solid #e8eaed;border-radius:6px;overflow:hidden;">
    {ws_cards}
  </table>
</td></tr>

<!-- MONEY FLOW -->
<tr><td style="padding:18px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
              padding-bottom:6px;border-bottom:2px solid #1a1a2e;">
    Money Flow — Where Retail Is Buying (Last 7 Days)
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:6px;font-style:italic;">
    Top underliers by absolute 4w flow magnitude (signed value shown). Higher rank = bigger churn regardless of direction. REX exposure shown as L:N / S:N. Competitor counts (active products) shown the same way.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:#1a1a2e;">
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">#</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Underlier</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">4w Net Flow</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Gross Churn</th>
      <th style="padding:7px 8px;text-align:center;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">REX (Long/Short)</th>
      <th style="padding:7px 8px;text-align:center;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Competitors (L/S)</th>
    </tr>
    {flow_rows_html}
  </table>
</td></tr>

<!-- LAUNCHES OF THE WEEK -->
<tr><td style="padding:18px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
              padding-bottom:6px;border-bottom:2px solid #27ae60;">
    Fund Launches of the Week
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:6px;font-style:italic;">
    New ETPs that started trading in the last 7 days. REX-tagged where applicable.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:#1a1a2e;">
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Ticker</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Fund Name</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Issuer</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">AUM</th>
      <th style="padding:7px 8px;text-align:center;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Launched</th>
    </tr>
    {launches_html if launches_html else '<tr><td colspan="5" style="padding:12px;color:#7f8c8d;font-style:italic;text-align:center;">No new launches in the last 7 days.</td></tr>'}
  </table>
</td></tr>

<!-- IPOS -->
<tr><td style="padding:18px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
              padding-bottom:6px;border-bottom:2px solid #e67e22;">
    IPO Watchlist — Pre-IPO &amp; Recently Priced
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:6px;font-style:italic;">
    High-profile pre-IPO names to track plus recently-priced IPOs. The first list is "be ready when these debut" — Day-1 trading + active options + retail momentum unlock immediate filing windows.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:#1a1a2e;">
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Ticker</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Company</th>
      <th style="padding:7px 8px;text-align:center;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Valuation</th>
      <th style="padding:7px 8px;text-align:center;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Date</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Filed By</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Description</th>
    </tr>
    {ipo_rows_html}
  </table>
</td></tr>

<!-- METHODOLOGY FOOTER -->
<tr><td style="padding:20px 30px 25px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f4f5f6;border-radius:6px;">
    <tr><td style="padding:14px 18px;font-size:11px;color:#566573;line-height:1.65;">
      <strong style="color:#1a1a2e;">About this report.</strong>
      Stocks are scored on retail demand signals (volatility, options activity, momentum, attention, ownership patterns) and ranked across the full US equity universe. Recommendations are filtered so we never surface a name where an active leveraged product already exists.
      <br><br>
      <strong style="color:#1a1a2e;">Internal use only.</strong> REX Financial. Not investment advice.
    </td></tr>
  </table>
</td></tr>

</table>
</td></tr>
</table>
</body></html>
'''


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    launch = load_launch_candidates(12)
    whitespace = load_whitespace_top(12)
    money_flow = load_money_flow(12, days=7)
    filings_summary = load_filings_count_this_week()
    top_mentions = load_top_mentions()
    earliest_comp = load_earliest_competitor_filing_dates()
    launches_week = load_launches_this_week()

    # Market observation for the week — derived from the actual data
    # (Volatility regime, theme rotation, cleanest opportunity, etc.)
    high_vol_count = int((whitespace["rvol_90d"] >= 100).sum()) if "rvol_90d" in whitespace.columns else 0
    cleanest_launch = None
    if not launch.empty and "competitor_filed_total" in launch.columns:
        clean = launch[launch["competitor_filed_total"] == 0]
        if not clean.empty:
            cleanest_launch = clean.index[0]

    parts = []
    if high_vol_count >= 3:
        parts.append(f"High-vol names dominate the file list — {high_vol_count} of the top 12 file candidates show >100% realized 90-day volatility")
    if cleanest_launch:
        parts.append(f"{cleanest_launch} stands out as the cleanest launch shot (zero competitor filings on the underlier)")
    if top_mentions[1] > 30:
        parts.append(f"retail attention concentrated on {top_mentions[0]} ({top_mentions[1]} mentions)")

    hot_take = ". ".join(parts) + "." if parts else \
        "Volatility-rich names dominate this week's whitespace; the cleanest launch shots are mid-cap names with limited competitor congestion."

    ipo_filers = load_pre_ipo_filer_race()

    html = render(launch, whitespace, money_flow, filings_summary, top_mentions, hot_take,
                  earliest_comp=earliest_comp, launches_week=launches_week,
                  ipo_filers=ipo_filers)
    # Hard-block: fail fast rather than ship placeholder text.
    if "Description pending" in html:
        raise RuntimeError(
            "BLOCKING: 'Description pending' found in report output. "
            "Add the ticker to COMPANY_LINES or extend _TICKER_COMPANY_NAMES."
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB)", OUT, OUT.stat().st_size / 1024)
    print(f"Report: {OUT}")


if __name__ == "__main__":
    main()

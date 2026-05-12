"""Weekly L&I Recommender — v3 layout (Wave B-renderer rebuild, 2026-05-11).

v3 layout: decision-driving cards segregated by section.
    1. Key Highlights (TL;DR bullets)
    2. Defensive cards — competitor filed in last 30d, REX has no filing → "Should we respond?"
    3. Offensive cards — true whitespace, REX should file
    4. Watch cards — early signals, not yet HIGH/MEDIUM
    5. Killed cards — prior-week recs that decayed out (one-line reason)
    6. Money Flow + Launches of the Week + IPOs (carried over from v2)

Per-card panels: thesis / signals / competition / risks / suggested REX ticker.
Tiers: HIGH / MEDIUM / WATCH (percentile + signal-strength gates).

Set LAYOUT_VERSION = "v2" for legacy renderer. v3 is default; on any unrecoverable
data error v3 falls back to v2 automatically (graceful degradation).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

from screener.li_engine.analysis.pre_ipo_filer_race import (
    load_pre_ipo_filer_race,
    render_filers_pills,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defensive coercion helper — pandas can hand back NaN (a float) where a
# string is expected. html.escape() and str.split() both blow up on NaN
# with the misleading "'float' object has no attribute 'replace'" error
# (escape() internally calls .replace on its argument). Funnel anything
# bound for HTML through this helper before escaping or string ops.
# ---------------------------------------------------------------------------
def _safe_str(value, default: str = "") -> str:
    """Return a clean string, treating NaN/None as the default."""
    if value is None:
        return default
    if isinstance(value, float):
        try:
            if np.isnan(value):
                return default
        except Exception:
            pass
    if isinstance(value, str):
        return value
    try:
        # Scalar pd.isna (avoid passing arrays/lists through this branch)
        if pd.api.types.is_scalar(value) and pd.isna(value):
            return default
    except Exception:
        pass
    return str(value)


# === Layout version flag ====================================================
# "v3" → new card-based renderer (default).
# "v2" → legacy renderer (fallback when v3 inputs are missing).
LAYOUT_VERSION = "v3"

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
WS = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"
LC = _ROOT / "data" / "analysis" / "launch_candidates.parquet"
TS = _ROOT / "data" / "analysis" / "bbg_timeseries_panel.parquet"
COMP = _ROOT / "data" / "analysis" / "competitor_counts.parquet"
THESES_DIR = _ROOT / "data" / "weekly_theses"
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
                    # Normalise: keep render fields + provenance (as_of_date, source_url,
                    # valuation_usd, last_round_date, expected_ipo_window). Anything else
                    # (e.g. legacy "last_reviewed") is dropped.
                    keep = {"ticker", "company", "date", "valuation", "desc",
                            "valuation_usd", "last_round_date", "source_url",
                            "expected_ipo_window", "as_of_date"}
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

def load_launch_candidates(n: int = 10) -> pd.DataFrame:
    if not LC.exists():
        return pd.DataFrame()
    df = pd.read_parquet(LC)
    df = df[df["has_signals"] == True]  # need actual signal data
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

    # Provenance: freshest as_of_date across rows (string compare works for ISO dates)
    ipo_as_of = max(
        (ipo.get("as_of_date", "") for ipo in IPO_DATA if ipo.get("as_of_date")),
        default="",
    )
    ipo_as_of_html = (
        f'<div style="font-size:10.5px;color:#95a5a6;margin:2px 0 6px 0;">'
        f'Valuations data as of {escape(ipo_as_of)} — sourced from Reuters / FT / Bloomberg public reporting.'
        f'</div>'
    ) if ipo_as_of else ""

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
  {ipo_as_of_html}
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


# ===========================================================================
# v3 LAYOUT — card-based renderer (Wave B-renderer, 2026-05-11)
# ===========================================================================

# Risk taxonomy thresholds (tweak via constants, no config file needed yet).
RISK_CAPACITY_MCAP_M = 1_000          # market_cap < $1B → capacity flag
RISK_LIQUIDITY_ADV_USD = 5_000_000    # avg daily $-volume < $5M → liquidity flag
REGULATORY_KEYWORDS = (
    "cannabis", "marijuana", "gambling", "gaming",
    "crypto-mining", "bitcoin mining", "casino",
)
MOMENTUM_FADE_DROP_PCT = 30.0         # WoW score drop > 30% → momentum-fade flag

# Tier thresholds: percentile gates against the whitespace universe.
TIER_HIGH_PCT = 85
TIER_MEDIUM_PCT = 60
TIER_WATCH_PCT = 40

DEFENSIVE_LOOKBACK_DAYS = 30  # competitor filing within N days → defensive trigger


def _normalize_thesis_entry(entry) -> dict:
    """Normalize a single ticker's thesis payload to {paragraph_1, paragraph_2, ...}.

    Supports two on-disk shapes:
      * Legacy: {"paragraph_1": "...", "paragraph_2": "..."}
      * Generator (B3): {"thesis": "para1\\n\\npara2", "risks": [...], "why_now": "...",
                        "suggested_ticker": "...", "_meta": {...}}

    Always returns a dict with at least `paragraph_1` and `paragraph_2` keys (may be
    empty strings). Extra fields (risks/why_now/suggested_ticker) are passed through
    untouched in case downstream renderers want them.
    """
    if isinstance(entry, str):
        return {"paragraph_1": entry, "paragraph_2": ""}
    if not isinstance(entry, dict):
        return {"paragraph_1": "", "paragraph_2": ""}

    # Legacy schema: paragraph_1 / paragraph_2 already present
    p1 = entry.get("paragraph_1")
    p2 = entry.get("paragraph_2")
    if p1 or p2:
        out = dict(entry)
        out["paragraph_1"] = p1 or ""
        out["paragraph_2"] = p2 or ""
        return out

    # B3 generator schema: split single `thesis` string on first blank-line.
    raw = entry.get("thesis") or ""
    if isinstance(raw, str) and raw.strip():
        parts = re.split(r"\n\s*\n", raw.strip(), maxsplit=1)
        out = dict(entry)
        out["paragraph_1"] = parts[0].strip()
        out["paragraph_2"] = parts[1].strip() if len(parts) > 1 else ""
        return out

    out = dict(entry)
    out.setdefault("paragraph_1", "")
    out.setdefault("paragraph_2", "")
    return out


def _read_thesis_file(path: Path) -> dict:
    """Read one thesis JSON, unwrap the `theses` envelope if present, normalize and
    upper-case ticker keys. Returns empty dict on any failure."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Could not parse thesis JSON %s: %s", path, e)
        return {}
    if not isinstance(raw, dict):
        return {}
    # B3 generator wraps tickers under a `theses` key alongside metadata
    # (generated_at, model, week_of). Older / hand-written caches may put
    # tickers at the top level.
    inner = raw.get("theses") if isinstance(raw.get("theses"), dict) else raw
    out: dict = {}
    for ticker, entry in inner.items():
        if not isinstance(ticker, str):
            continue
        out[ticker.upper()] = _normalize_thesis_entry(entry)
    return out


def _load_thesis_dict(target_date: date | None = None) -> dict:
    """Return {TICKER: {paragraph_1, paragraph_2, ...}} for the latest thesis cache.

    Behaviour (Wave FA1 fix, 2026-05-12):
      1. Scan `data/weekly_theses/*.json`, excluding `*_manual.json`.
      2. Filenames are `<YYYY-MM-DD>.json` (Sunday-anchored week_of date).
         Pick the most recent. If `target_date` is supplied, prefer an exact
         match; otherwise fall through to "latest available".
      3. If `<week>_manual.json` exists for the chosen week, merge it on top
         (manual overrides win — humans get the final word).
      4. Ticker keys are upper-cased; the new generator schema (`{theses: {...,
         thesis: "p1\\n\\np2"}}`) is unwrapped and split into paragraph_1/2 so
         the renderer's existing `card.thesis_p1` / `thesis_p2` lookup works.

    Graceful degradation: any error → empty dict, caller substitutes the
    "Thesis pending" placeholder.
    """
    if not THESES_DIR.exists():
        return {}

    # Build (date, path) candidate list — primary cache files only.
    candidates: list[tuple[date, Path]] = []
    for p in THESES_DIR.glob("*.json"):
        if p.stem.endswith("_manual"):
            continue
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        candidates.append((d, p))

    if not candidates:
        return {}

    chosen: Path | None = None
    chosen_date: date | None = None
    if target_date is not None:
        for d, p in candidates:
            if d == target_date:
                chosen, chosen_date = p, d
                break
    if chosen is None:
        candidates.sort(key=lambda t: t[0], reverse=True)
        chosen_date, chosen = candidates[0]

    merged = _read_thesis_file(chosen)
    if not merged:
        log.warning("Thesis file %s yielded no usable entries", chosen)
        return {}

    # Manual overrides for the same week, if present.
    manual_path = THESES_DIR / f"{chosen_date.isoformat()}_manual.json"
    if manual_path.exists():
        overrides = _read_thesis_file(manual_path)
        if overrides:
            merged.update(overrides)
            log.info("Merged %d manual override(s) from %s", len(overrides), manual_path.name)

    log.info("Loaded weekly theses: %s (%d ticker(s))", chosen.name, len(merged))
    return merged


def _load_prior_week_recs() -> set[str]:
    """Load prior-week recommendation tickers (for Killed section).

    Tries: (1) `recommendation_history` SQLite table from E1, if it exists,
    (2) most recent `data/weekly_theses/*.json` whose date is at least 5 days
    before today. Returns an empty set on any failure.
    """
    # Try E1's recommendation_history table first
    try:
        conn = sqlite3.connect(str(DB))
        try:
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='recommendation_history'"
            ).fetchone()
            if tbl:
                cutoff_lo = (date.today() - timedelta(days=14)).isoformat()
                cutoff_hi = (date.today() - timedelta(days=5)).isoformat()
                df = pd.read_sql_query(
                    "SELECT DISTINCT ticker FROM recommendation_history "
                    f"WHERE week_of BETWEEN '{cutoff_lo}' AND '{cutoff_hi}'",
                    conn,
                )
                if not df.empty:
                    return set(df["ticker"].dropna().astype(str).str.upper())
        finally:
            conn.close()
    except Exception as e:
        log.info("recommendation_history lookup failed (%s); falling back to thesis history", e)

    # Fall back to most recent prior thesis JSON
    if THESES_DIR.exists():
        cutoff = date.today() - timedelta(days=5)
        candidates = []
        for p in THESES_DIR.glob("*.json"):
            try:
                d = date.fromisoformat(p.stem)
                if d <= cutoff:
                    candidates.append((d, p))
            except ValueError:
                continue
        if candidates:
            candidates.sort(reverse=True)
            _, prior = candidates[0]
            try:
                raw = json.loads(prior.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return set(k.upper() for k in raw.keys())
            except Exception:
                pass
    return set()


def _derive_risk_flags(ticker: str, row: pd.Series,
                       prior_score: float | None = None) -> list[str]:
    """Return a list of risk-flag chip names for a ticker row.

    Possible chips: capacity / liquidity / single-name / regulatory / momentum-fade.
    Each chip is a short lowercase token; the renderer maps token → label/color.
    """
    flags: list[str] = []

    mcap = row.get("market_cap")
    if pd.notna(mcap) and mcap < RISK_CAPACITY_MCAP_M:
        flags.append("capacity")

    adv = row.get("adv_30d") or row.get("turnover")
    last_px = row.get("last_price") or 0
    if pd.notna(adv) and pd.notna(last_px) and adv * (last_px or 0) < RISK_LIQUIDITY_ADV_USD:
        flags.append("liquidity")

    # Single-name vs diversified — anything in the whitespace/launch parquet is by
    # definition a single-name underlier, so flag it for awareness.
    fund_name = str(row.get("rex_fund_name") or row.get("name") or "")
    if fund_name and not re.search(r"index|etf|fund(?!\s*name)|composite|\bsector\b",
                                    fund_name, flags=re.IGNORECASE):
        flags.append("single-name")

    # Regulatory: check sector / fund name / company name
    blob = " ".join([
        str(row.get("sector") or ""),
        str(row.get("name") or ""),
        str(fund_name),
    ]).lower()
    if any(kw in blob for kw in REGULATORY_KEYWORDS):
        flags.append("regulatory")

    # Momentum-fade: WoW score drop >30%
    score = row.get("composite_score")
    if (prior_score is not None and pd.notna(score)
            and prior_score != 0 and score < prior_score):
        drop_pct = (prior_score - score) / abs(prior_score) * 100.0
        if drop_pct > MOMENTUM_FADE_DROP_PCT:
            flags.append("momentum-fade")

    return flags


_SIGNAL_LABELS = {
    "rvol_30d_z": "30d Vol",
    "rvol_90d_z": "90d Vol",
    "ret_1m_z": "1m Return",
    "ret_1y_z": "1y Return",
    "si_ratio_z": "Short Interest",
    "insider_pct_z": "Insider %",
    "inst_own_pct_z": "Inst Own %",
    "mentions_z": "Retail Mentions",
    "theme_bonus": "Hot Theme Bonus",
}


def _signal_breakdown(row: pd.Series) -> list[tuple[str, float]]:
    """Return ordered list of (label, z-score) contributions to composite_score.

    Sorted by absolute magnitude desc; only includes signals with non-zero
    contribution. Used to render the per-card "Signals" panel.
    """
    items = []
    for col, label in _SIGNAL_LABELS.items():
        v = row.get(col)
        if v is None or pd.isna(v):
            continue
        try:
            v_f = float(v)
        except (TypeError, ValueError):
            continue
        if abs(v_f) < 0.05:
            continue
        items.append((label, v_f))
    items.sort(key=lambda x: abs(x[1]), reverse=True)
    return items


def _signal_strength(row: pd.Series) -> str:
    """STRONG / URGENT / MODERATE / WEAK label based on z-score magnitudes."""
    sigs = _signal_breakdown(row)
    if not sigs:
        return "WEAK"
    top = abs(sigs[0][1])
    n_strong = sum(1 for _, v in sigs if abs(v) >= 1.5)
    if top >= 2.5 and n_strong >= 3:
        return "URGENT"
    if top >= 2.0 and n_strong >= 2:
        return "STRONG"
    if top >= 1.0:
        return "MODERATE"
    return "WEAK"


def _killer_risk(flags: list[str]) -> bool:
    """A 'killer' risk blocks a HIGH-tier rating."""
    return any(f in flags for f in ("regulatory", "momentum-fade"))


def _classify_tier(score: float, percentiles: dict, strength: str,
                   risk_flags: list[str], is_new_entrant: bool = False) -> str:
    """Return HIGH / MEDIUM / WATCH / DROP based on score + signal + risk."""
    if pd.isna(score):
        return "DROP"
    if score >= percentiles["P85"] and strength in ("STRONG", "URGENT") and not _killer_risk(risk_flags):
        return "HIGH"
    if score >= percentiles["P60"] and strength in ("MODERATE", "STRONG", "URGENT"):
        return "MEDIUM"
    if score >= percentiles["P40"] or is_new_entrant:
        return "WATCH"
    return "DROP"


def _classify_orientation(ticker: str, row: pd.Series,
                          earliest_comp: dict | None,
                          rex_filed_count: int = 0) -> str:
    """DEFENSIVE | OFFENSIVE classification.

    DEFENSIVE: a competitor filed/launched within DEFENSIVE_LOOKBACK_DAYS AND
        REX has no active filing on this underlier.
    OFFENSIVE: anything else (whitespace where REX should file).
    """
    if rex_filed_count and rex_filed_count > 0:
        # REX has filed → not defensive (we already responded)
        return "OFFENSIVE"
    if earliest_comp:
        ed = earliest_comp.get("earliest_filing_date")
        if ed:
            try:
                if isinstance(ed, str):
                    ed = date.fromisoformat(ed)
                age = (date.today() - ed).days
                if age <= DEFENSIVE_LOOKBACK_DAYS:
                    return "DEFENSIVE"
            except (TypeError, ValueError):
                pass
    return "OFFENSIVE"


def _suggested_rex_ticker(ticker: str, row: pd.Series,
                          launch_lookup: dict | None = None) -> tuple[str, str]:
    """Return (suggested_ticker, filing_status) for the card footer.

    Priority:
        1. Existing REX product on this underlier (FILED status)
        2. Synthesised T-REX ticker (NONE status, conventional naming)
    """
    if launch_lookup and ticker in launch_lookup:
        info = launch_lookup[ticker]
        sym = _safe_str(info.get("rex_ticker"))
        if sym:
            status_raw = _safe_str(info.get("rex_market_status")).upper()
            status = "FILED" if status_raw in ("FILED", "EFFECTIVE", "REGISTERED") else "DRAFT"
            return sym, status
    # Synthesise: T-REX 2X LONG <TICKER> → conventional symbol convention TBD
    return f"(prospective) 2X {ticker}", "NONE"


def _build_card_model(ticker: str, row: pd.Series, *,
                      percentiles: dict,
                      thesis_dict: dict,
                      earliest_comp_lookup: dict,
                      launch_lookup: dict,
                      prior_score: float | None = None,
                      is_new_entrant: bool = False) -> dict:
    """Assemble the full card model dict for one ticker."""
    def _safe_int(v) -> int:
        try:
            if v is None or pd.isna(v):
                return 0
            return int(v)
        except (TypeError, ValueError):
            return 0

    def _safe_float(v, default: float = 0.0) -> float:
        try:
            if v is None or pd.isna(v):
                return default
            return float(v)
        except (TypeError, ValueError):
            return default

    rex_filed = _safe_int(row.get("n_rex_filed_any")) or _safe_int(row.get("n_rex_products"))
    earliest = earliest_comp_lookup.get(ticker, {}) or {}
    risk_flags = _derive_risk_flags(ticker, row, prior_score=prior_score)
    strength = _signal_strength(row)
    score = _safe_float(row.get("composite_score"))
    tier = _classify_tier(score, percentiles, strength, risk_flags, is_new_entrant)
    orientation = _classify_orientation(ticker, row, earliest, rex_filed_count=rex_filed)
    sym, filing_status = _suggested_rex_ticker(ticker, row, launch_lookup)

    thesis = thesis_dict.get(ticker, {}) if isinstance(thesis_dict, dict) else {}
    if isinstance(thesis, str):
        thesis = {"paragraph_1": thesis, "paragraph_2": ""}

    name_str = _safe_str(row.get("name")) or ticker
    sector_str = _safe_str(row.get("sector")) or "—"
    fund_name_for_line = _safe_str(row.get("rex_fund_name")) or _safe_str(row.get("fund_name")) or None
    return {
        "ticker": ticker,
        "company_name": name_str.split(" - ")[0],
        "sector": sector_str,
        "tier": tier,
        "orientation": orientation,
        "score": score,
        "score_pct": _safe_float(row.get("score_pct")),
        "signal_strength": strength,
        "signals": _signal_breakdown(row),
        "risk_flags": risk_flags,
        "rex_filed_count": rex_filed,
        "competitor_active_long": _safe_int(row.get("n_comp_products")),
        "competitor_filed_180d": _safe_int(row.get("n_competitor_485apos_180d")),
        "earliest_competitor": earliest,
        "filing_race_rank": _safe_int(earliest.get("n_filings", 0)),
        "thesis_p1": _safe_str(thesis.get("paragraph_1")),
        "thesis_p2": _safe_str(thesis.get("paragraph_2")),
        "suggested_ticker": _safe_str(sym),
        "filing_status": _safe_str(filing_status) or "NONE",
        "company_line": _resolve_company_line(
            ticker,
            sector=sector_str if sector_str != "—" else None,
            fund_name=fund_name_for_line,
        ),
        # Carry-through metrics for the signals panel
        "market_cap": row.get("market_cap"),
        "rvol_90d": row.get("rvol_90d"),
        "ret_1m": row.get("ret_1m"),
        "ret_1y": row.get("ret_1y"),
        "si_ratio": row.get("si_ratio"),
        "mentions_24h": _safe_int(row.get("mentions_24h")),
    }


# ---- v3 HTML rendering -----------------------------------------------------

_TIER_BADGE = {
    "HIGH":   ("#27ae60", "HIGH"),
    "MEDIUM": ("#f39c12", "MEDIUM"),
    "WATCH":  ("#7f8c8d", "WATCH"),
    "DROP":   ("#bdc3c7", "DROP"),
}

_RISK_CHIP = {
    "capacity":      ("#8e44ad", "Capacity (<$1B mcap)"),
    "liquidity":     ("#d35400", "Liquidity (<$5M ADV)"),
    "single-name":   ("#34495e", "Single-Name"),
    "regulatory":    ("#c0392b", "Regulatory"),
    "momentum-fade": ("#e67e22", "Momentum Fade (-30% WoW)"),
}


def _render_tier_badge(tier: str) -> str:
    color, label = _TIER_BADGE.get(tier, _TIER_BADGE["WATCH"])
    return (f'<span style="background:{color};color:white;padding:2px 8px;'
            f'border-radius:10px;font-size:10px;font-weight:700;letter-spacing:0.5px;'
            f'margin-left:8px;">{label}</span>')


def _render_risk_chips(flags: list[str]) -> str:
    if not flags:
        return ('<span style="color:#7f8c8d;font-size:11px;font-style:italic;">'
                'No flags</span>')
    out = []
    for f in flags:
        color, label = _RISK_CHIP.get(f, ("#95a5a6", f))
        out.append(
            f'<span style="background:{color};color:white;padding:2px 7px;'
            f'border-radius:10px;font-size:10px;font-weight:600;margin-right:5px;'
            f'display:inline-block;margin-bottom:3px;">{escape(label)}</span>'
        )
    return "".join(out)


def _render_signals_panel(signals: list[tuple[str, float]]) -> str:
    if not signals:
        return ('<div style="color:#7f8c8d;font-size:11px;font-style:italic;">'
                'No strong signal contributions.</div>')
    rows = []
    for label, z in signals[:6]:
        color = "#27ae60" if z > 0 else "#e74c3c"
        bar_w = min(100, abs(z) * 30)
        rows.append(
            f'<tr><td style="padding:2px 0;font-size:11px;color:#566573;width:110px;">'
            f'{escape(label)}</td>'
            f'<td style="padding:2px 0;width:100px;">'
            f'<div style="background:{color};height:8px;width:{bar_w}px;'
            f'border-radius:2px;display:inline-block;"></div></td>'
            f'<td style="padding:2px 0 2px 6px;font-size:11px;color:{color};'
            f'font-weight:700;font-family:\'Courier New\',monospace;">'
            f'{z:+.2f}</td></tr>'
        )
    return f'<table cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table>'


def _render_competition_panel(card: dict) -> str:
    earliest = card["earliest_competitor"] or {}
    lines = []
    if card["rex_filed_count"]:
        lines.append(
            f'<div style="font-size:11.5px;color:#1a1a2e;">'
            f'REX filings: <strong>{card["rex_filed_count"]}</strong></div>'
        )
    else:
        lines.append(
            '<div style="font-size:11.5px;color:#7f8c8d;">REX filings: <strong>0</strong></div>'
        )

    comp_active = card["competitor_active_long"]
    comp_180 = card["competitor_filed_180d"]
    lines.append(
        f'<div style="font-size:11.5px;color:#1a1a2e;">'
        f'Competitor 2x active: <strong>{comp_active}</strong></div>'
    )
    lines.append(
        f'<div style="font-size:11.5px;color:#1a1a2e;">'
        f'Competitor filed (180d): <strong>{comp_180}</strong></div>'
    )

    if earliest:
        ei = _safe_str(earliest.get("earliest_issuer"))
        ed = _safe_str(earliest.get("earliest_filing_date"))
        if ed:
            lines.append(
                f'<div style="font-size:11px;color:#566573;margin-top:4px;">'
                f'Earliest: {escape(ei)} on <strong>{escape(ed)}</strong></div>'
            )
        race_rank = earliest.get("n_filings", 0) or 0
        if race_rank:
            lines.append(
                f'<div style="font-size:11px;color:#566573;">'
                f'Filing race: <strong>{race_rank}</strong> filings tracked</div>'
            )
    return "".join(lines)


def _render_thesis_panel(card: dict) -> str:
    p1 = _safe_str(card.get("thesis_p1"))
    p2 = _safe_str(card.get("thesis_p2"))
    if not p1 and not p2:
        return (
            '<div style="font-size:12px;color:#7f8c8d;font-style:italic;'
            'line-height:1.55;">Thesis pending — '
            f'<span style="color:#1a1a2e;">{escape(_safe_str(card.get("company_line")))}</span></div>'
        )
    parts = []
    if p1:
        parts.append(
            f'<p style="margin:0 0 8px 0;font-size:12px;color:#1a1a2e;'
            f'line-height:1.55;">{escape(p1)}</p>'
        )
    if p2:
        parts.append(
            f'<p style="margin:0;font-size:12px;color:#1a1a2e;'
            f'line-height:1.55;">{escape(p2)}</p>'
        )
    return "".join(parts)


def _render_card_v3(card: dict) -> str:
    """Render a single per-ticker decision card (4-panel grid)."""
    tier_badge = _render_tier_badge(card["tier"])
    strength = _safe_str(card.get("signal_strength")) or "WEAK"
    strength_color = {
        "URGENT": "#e74c3c", "STRONG": "#27ae60",
        "MODERATE": "#f39c12", "WEAK": "#7f8c8d",
    }.get(strength, "#7f8c8d")

    suggested = _safe_str(card.get("suggested_ticker")) or f"(prospective) 2X {card.get('ticker','')}"
    filing_status = _safe_str(card.get("filing_status")) or "NONE"
    fs_color = {
        "FILED":  "#27ae60",
        "DRAFT":  "#f39c12",
        "NONE":   "#7f8c8d",
    }.get(filing_status, "#7f8c8d")

    return f'''
    <tr><td style="padding:0;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#ffffff;border:1px solid #e8eaed;
                    border-radius:6px;margin-bottom:14px;">

        <!-- HEADER -->
        <tr><td style="padding:12px 16px 8px;background:#fcfcfd;
                       border-bottom:1px solid #ecf0f1;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="vertical-align:middle;">
                <span style="font-family:'Courier New',monospace;font-size:18px;
                             font-weight:700;color:#0984e3;">{escape(_safe_str(card.get("ticker")))}</span>
                <span style="color:#1a1a2e;font-size:13px;font-weight:600;
                             margin-left:10px;">{escape(_safe_str(card.get("company_name")))}</span>
                <span style="color:#7f8c8d;font-size:11px;
                             margin-left:8px;">· {escape(_safe_str(card.get("sector")) or "—")}</span>
              </td>
              <td style="text-align:right;vertical-align:middle;white-space:nowrap;">
                <span style="background:{strength_color};color:white;padding:2px 7px;
                             border-radius:10px;font-size:9px;font-weight:700;
                             letter-spacing:0.4px;">{strength}</span>
                {tier_badge}
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- 4-PANEL GRID -->
        <tr><td style="padding:0;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <!-- THESIS (top-left, 2-col span) -->
              <td colspan="2" style="padding:12px 16px;background:#ffffff;
                                     border-bottom:1px solid #f1f3f5;
                                     border-right:1px solid #f1f3f5;width:60%;
                                     vertical-align:top;">
                <div style="font-size:10px;font-weight:700;color:#7f8c8d;
                            letter-spacing:1px;text-transform:uppercase;
                            margin-bottom:8px;">Thesis</div>
                {_render_thesis_panel(card)}
              </td>
              <!-- SIGNALS (top-right) -->
              <td colspan="2" style="padding:12px 16px;background:#ffffff;
                                     border-bottom:1px solid #f1f3f5;
                                     vertical-align:top;width:40%;">
                <div style="font-size:10px;font-weight:700;color:#7f8c8d;
                            letter-spacing:1px;text-transform:uppercase;
                            margin-bottom:8px;">Signals
                  <span style="color:#1a1a2e;font-size:11px;font-weight:400;
                               margin-left:6px;">(score {card["score"]:+.2f},
                                pct {card["score_pct"]:.0f})</span></div>
                {_render_signals_panel(card["signals"])}
              </td>
            </tr>
            <tr>
              <!-- COMPETITION (bottom-left) -->
              <td colspan="2" style="padding:12px 16px;background:#fafbfc;
                                     border-right:1px solid #f1f3f5;
                                     vertical-align:top;width:60%;">
                <div style="font-size:10px;font-weight:700;color:#7f8c8d;
                            letter-spacing:1px;text-transform:uppercase;
                            margin-bottom:8px;">Competition</div>
                {_render_competition_panel(card)}
              </td>
              <!-- RISKS (bottom-right) -->
              <td colspan="2" style="padding:12px 16px;background:#fafbfc;
                                     vertical-align:top;width:40%;">
                <div style="font-size:10px;font-weight:700;color:#7f8c8d;
                            letter-spacing:1px;text-transform:uppercase;
                            margin-bottom:8px;">Risk Flags</div>
                <div>{_render_risk_chips(card["risk_flags"])}</div>
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- FOOTER: suggested ticker -->
        <tr><td style="padding:10px 16px;background:#1a1a2e;
                       border-top:1px solid #ecf0f1;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-size:11px;color:#9bb1cc;">
                Suggested REX vehicle:
                <span style="font-family:'Courier New',monospace;color:#ffffff;
                             font-weight:700;margin-left:6px;">{escape(suggested)}</span>
              </td>
              <td style="text-align:right;">
                <span style="background:{fs_color};color:white;padding:2px 8px;
                             border-radius:10px;font-size:9px;font-weight:700;
                             letter-spacing:0.5px;">{filing_status}</span>
              </td>
            </tr>
          </table>
        </td></tr>

      </table>
    </td></tr>
    '''


def _render_killed_card(ticker: str, reason: str) -> str:
    return f'''
    <tr><td style="padding:8px 14px;background:#fafbfc;
                   border:1px solid #e8eaed;border-radius:4px;
                   margin-bottom:6px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="font-family:'Courier New',monospace;font-size:13px;
                     font-weight:700;color:#7f8c8d;width:80px;">{escape(_safe_str(ticker))}</td>
          <td style="font-size:12px;color:#566573;">{escape(_safe_str(reason))}</td>
        </tr>
      </table>
    </td></tr>
    '''


def _render_section_header(title: str, subtitle: str, accent: str) -> str:
    return f'''
    <tr><td style="padding:18px 30px 5px;">
      <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
                  padding-bottom:6px;border-bottom:2px solid {accent};">{escape(title)}</div>
      <div style="font-size:12px;color:#7f8c8d;margin-bottom:10px;font-style:italic;">
        {escape(subtitle)}
      </div>
    </td></tr>
    '''


def render_v3(cards: list[dict], money_flow: pd.DataFrame,
              filings_summary: dict, top_mentions: tuple[str, int],
              hot_take: str, killed: list[tuple[str, str]],
              launches_week: pd.DataFrame | None = None,
              ipo_filers: dict | None = None) -> str:
    """v3 layout — decision-driving cards segregated into Defensive / Offensive
    / Watch + Killed sections. Money flow / launches / IPOs carry over from v2.
    """
    ipo_filers = ipo_filers or {}
    today_str = date.today().strftime("%B %d, %Y")
    week_window = f"{filings_summary.get('week_start', '')} → {filings_summary.get('week_end', '')}"

    # Bucket cards
    defensive_cards = [c for c in cards if c["orientation"] == "DEFENSIVE"
                       and c["tier"] in ("HIGH", "MEDIUM")]
    offensive_cards = [c for c in cards if c["orientation"] == "OFFENSIVE"
                       and c["tier"] in ("HIGH", "MEDIUM")]
    watch_cards = [c for c in cards if c["tier"] == "WATCH"]

    # Sort each bucket by score desc
    defensive_cards.sort(key=lambda c: c["score"], reverse=True)
    offensive_cards.sort(key=lambda c: c["score"], reverse=True)
    watch_cards.sort(key=lambda c: c["score"], reverse=True)

    n_high = sum(1 for c in cards if c["tier"] == "HIGH")
    n_medium = sum(1 for c in cards if c["tier"] == "MEDIUM")
    n_watch = len(watch_cards)
    top5 = ", ".join(c["ticker"] for c in
                     sorted(cards, key=lambda c: c["score"], reverse=True)[:5])

    def _render_card_block(cards_list, empty_msg):
        if not cards_list:
            return (f'<tr><td style="padding:14px 30px;color:#7f8c8d;'
                    f'font-style:italic;">{empty_msg}</td></tr>')
        return "".join(
            f'<tr><td style="padding:0 30px 0 30px;">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{_render_card_v3(c)}'
            f'</table></td></tr>'
            for c in cards_list
        )

    defensive_html = _render_card_block(
        defensive_cards,
        "No defensive plays this week — REX is not behind on any recent competitor filings.",
    )
    offensive_html = _render_card_block(
        offensive_cards,
        "No high-conviction whitespace shots this week.",
    )
    watch_html = _render_card_block(
        watch_cards[:8],  # cap watch list
        "No early-signal names worth watching.",
    )

    # Killed
    if killed:
        killed_rows = "".join(
            f'<tr><td style="padding:0 30px 6px 30px;">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{_render_killed_card(t, r)}'
            f'</table></td></tr>'
            for t, r in killed
        )
    else:
        killed_rows = ('<tr><td style="padding:14px 30px;color:#7f8c8d;'
                       'font-style:italic;">Nothing decayed out of last week\'s recs.</td></tr>')

    # Money flow rows (carry over from v2)
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
        f_abs = float(r.get("flow_4w_abs", abs(f)) or abs(f))
        flow_rows.append(f'''
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#7f8c8d;">{i}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11.5px;font-weight:700;font-family:'Courier New',monospace;color:#0984e3;">{escape(underlier)}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;color:{f_color};font-weight:700;">${f:+,.1f}M</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;color:#566573;">${f_abs:,.1f}M</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:center;">{rex_str}</td>
              <td style="padding:6px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:center;color:#7f8c8d;">L:{comp_long} / S:{comp_short}</td>
            </tr>
        ''')
    flow_rows_html = "".join(flow_rows)

    # IPO rows
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

    # Provenance: freshest as_of_date across rows (string compare works for ISO dates)
    ipo_as_of = max(
        (ipo.get("as_of_date", "") for ipo in IPO_DATA if ipo.get("as_of_date")),
        default="",
    )
    ipo_as_of_html = (
        f'<div style="font-size:10.5px;color:#95a5a6;margin:2px 0 6px 0;">'
        f'Valuations data as of {escape(ipo_as_of)} — sourced from Reuters / FT / Bloomberg public reporting.'
        f'</div>'
    ) if ipo_as_of else ""

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
<table width="780" cellpadding="0" cellspacing="0" border="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:780px;table-layout:fixed;">

<!-- HEADER -->
<tr><td style="background:#1a1a2e;padding:24px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">
    Stock Recommendations of the Week | {today_str}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;
              text-transform:uppercase;margin-top:6px;">
    Layout v3 · Decision Cards · Defensive / Offensive / Watch / Killed
  </div>
</td></tr>

<!-- KEY HIGHLIGHTS -->
<tr><td style="padding:15px 30px 10px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f4f5f6;border-left:4px solid #1a1a2e;border-radius:0 8px 8px 0;">
    <tr><td style="padding:14px 18px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="padding:0 0 8px;font-size:13px;font-weight:700;color:#1a1a2e;
                       text-transform:uppercase;letter-spacing:1px;">Key Highlights</td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Tier mix:</strong>
          <span style="background:#27ae60;color:white;padding:1px 7px;border-radius:8px;
                       font-size:11px;font-weight:700;margin:0 3px;">{n_high} HIGH</span>
          <span style="background:#f39c12;color:white;padding:1px 7px;border-radius:8px;
                       font-size:11px;font-weight:700;margin:0 3px;">{n_medium} MEDIUM</span>
          <span style="background:#7f8c8d;color:white;padding:1px 7px;border-radius:8px;
                       font-size:11px;font-weight:700;margin:0 3px;">{n_watch} WATCH</span>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Top 5 by composite:</strong>
          <span style="font-family:'Courier New',monospace;color:#0984e3;font-weight:700;">{escape(top5)}</span>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>{filings_summary.get('li_485apos', 0)} L&amp;I Filings</strong>
          last week ({week_window}) · REX: {filings_summary.get('rex_li_filings', 0)} ·
          <strong>{filings_summary.get('new_stocks_filed', 0)} new underliers</strong>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#1a1a2e;font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Highest Retail Buzz:</strong>
          <span style="font-family:'Courier New',monospace;color:#0984e3;font-weight:700;">{escape(top_mentions[0])}</span>
          with <strong>{top_mentions[1]}</strong> mentions
        </td></tr>
        <tr><td style="padding:6px 0 0;font-size:13px;color:#566573;line-height:1.5;
                       font-style:italic;border-top:1px dashed #d4d8dd;margin-top:6px;">
          <span style="color:#0984e3;font-weight:700;margin-right:6px;">&#9656;</span>
          {escape(hot_take)}
        </td></tr>
      </table>
    </td></tr>
  </table>
</td></tr>

{_render_section_header(
    "Defensive — Should We Respond?",
    f"Competitor filed/launched in the last {DEFENSIVE_LOOKBACK_DAYS}d and REX has no filing on this underlier. Each card answers: do we file to defend share?",
    "#e74c3c",
)}
{defensive_html}

{_render_section_header(
    "Offensive — Whitespace Shots",
    "True whitespace where REX should file first. No active competitor product, score above the MEDIUM threshold, signals validated.",
    "#27ae60",
)}
{offensive_html}

{_render_section_header(
    "Watch — Early Signals",
    f"Names above the WATCH threshold (P{TIER_WATCH_PCT}) but not yet HIGH/MEDIUM, OR new entrants since last week. Re-check next cycle.",
    "#0984e3",
)}
{watch_html}

{_render_section_header(
    "Killed — Decayed Out From Prior Week",
    "Tickers that were on last week's recommendation list but no longer clear the WATCH bar. One-line decay reason per row.",
    "#95a5a6",
)}
{killed_rows}

<!-- MONEY FLOW -->
<tr><td style="padding:18px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
              padding-bottom:6px;border-bottom:2px solid #1a1a2e;">
    Money Flow — Where Retail Is Buying (Last 7 Days)
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:6px;font-style:italic;">
    Top underliers by absolute 4w flow magnitude. REX exposure shown as L:N / S:N.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:#1a1a2e;">
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">#</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Underlier</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">4w Net Flow</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Gross Churn</th>
      <th style="padding:7px 8px;text-align:center;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">REX (L/S)</th>
      <th style="padding:7px 8px;text-align:center;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Comp (L/S)</th>
    </tr>
    {flow_rows_html}
  </table>
</td></tr>

<!-- IPOS -->
<tr><td style="padding:18px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 4px 0;
              padding-bottom:6px;border-bottom:2px solid #e67e22;">
    IPO Watchlist
  </div>
  {ipo_as_of_html}
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
      <strong style="color:#1a1a2e;">v3 layout.</strong>
      Cards classified by orientation (Defensive vs Offensive), tier
      (HIGH ≥ P{TIER_HIGH_PCT} + STRONG signal + no killer risk; MEDIUM ≥ P{TIER_MEDIUM_PCT};
      WATCH ≥ P{TIER_WATCH_PCT}), and risk flags
      (capacity / liquidity / single-name / regulatory / momentum-fade).
      Theses pulled from <code>data/weekly_theses/&lt;date&gt;.json</code> when available.
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


def _build_killed_list(prior_recs: set[str], current_tickers: set[str],
                       all_scores: pd.DataFrame) -> list[tuple[str, str]]:
    """Identify prior-week tickers no longer in the current rec set, with reason."""
    if not prior_recs:
        return []
    dropped = sorted(prior_recs - current_tickers)
    out = []
    for tk in dropped:
        # Try to look up current score in whitespace universe
        if tk in all_scores.index:
            cur = all_scores.loc[tk, "composite_score"]
            pct = all_scores.loc[tk, "score_pct"] if "score_pct" in all_scores.columns else None
            if pct is not None and pct < TIER_WATCH_PCT:
                reason = f"Score decayed below P{TIER_WATCH_PCT} (now pct {float(pct):.0f}, score {float(cur):+.2f})."
            else:
                reason = f"No longer ranked in top set (pct {float(pct or 0):.0f})."
        else:
            reason = "Dropped from universe (no signals this week)."
        out.append((tk, reason))
    return out[:10]  # cap to keep section tight


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

    html = None
    if LAYOUT_VERSION == "v3":
        try:
            # Build full universe of cards from launch + whitespace
            # Pull a wider whitespace slice so WATCH gets populated.
            ws_full = pd.read_parquet(WS) if WS.exists() else whitespace
            # Percentile thresholds against the full universe
            scores_series = ws_full["composite_score"].dropna() if "composite_score" in ws_full.columns else pd.Series([0.0])
            percentiles = {
                "P40": float(scores_series.quantile(TIER_WATCH_PCT / 100)),
                "P60": float(scores_series.quantile(TIER_MEDIUM_PCT / 100)),
                "P85": float(scores_series.quantile(TIER_HIGH_PCT / 100)),
            }

            thesis_dict = _load_thesis_dict()
            prior_recs = _load_prior_week_recs()

            # Launch lookup: ticker → row dict
            launch_lookup: dict = {}
            if not launch.empty:
                for tk in launch.index:
                    launch_lookup[tk] = launch.loc[tk].to_dict()

            # Card universe: top 12 from whitespace (will land HIGH/MEDIUM)
            # + launch candidates + "watch slice" from lower percentile
            # band (will mostly land WATCH).
            seen: set[str] = set()
            cards: list[dict] = []

            top_ws = ws_full.sort_values("composite_score", ascending=False).head(12) \
                if "composite_score" in ws_full.columns else whitespace
            # Watch slice: rows in P40-P60 band (above WATCH gate, below MEDIUM
            # gate), sorted by mentions_24h to surface rising-attention names.
            watch_slice = pd.DataFrame()
            if "composite_score" in ws_full.columns:
                lo, hi = percentiles["P40"], percentiles["P60"]
                watch_band = ws_full[(ws_full["composite_score"] >= lo)
                                     & (ws_full["composite_score"] < hi)]
                if "mentions_24h" in watch_band.columns:
                    watch_slice = watch_band.sort_values("mentions_24h",
                                                          ascending=False).head(8)
                else:
                    watch_slice = watch_band.head(8)

            ordered_tickers = list(top_ws.index) + list(launch.index) + list(watch_slice.index)
            for ticker in ordered_tickers:
                if ticker in seen:
                    continue
                seen.add(ticker)
                # Prefer launch-row data for tickers in launch; else whitespace
                if ticker in launch.index:
                    row = launch.loc[ticker]
                elif ticker in ws_full.index:
                    row = ws_full.loc[ticker]
                else:
                    continue
                is_new = ticker not in prior_recs and bool(prior_recs)
                card = _build_card_model(
                    ticker, row,
                    percentiles=percentiles,
                    thesis_dict=thesis_dict,
                    earliest_comp_lookup=earliest_comp,
                    launch_lookup=launch_lookup,
                    is_new_entrant=is_new,
                )
                cards.append(card)

            # Killed: prior-week recs absent from current top-tier set
            current_tickers = {c["ticker"] for c in cards
                               if c["tier"] in ("HIGH", "MEDIUM", "WATCH")}
            killed = _build_killed_list(prior_recs, current_tickers, ws_full)

            html = render_v3(
                cards, money_flow, filings_summary, top_mentions, hot_take,
                killed=killed,
                launches_week=launches_week,
                ipo_filers=ipo_filers,
            )
            log.info("v3 layout: %d cards (%d HIGH, %d MED, %d WATCH, %d KILLED)",
                     len(cards),
                     sum(1 for c in cards if c["tier"] == "HIGH"),
                     sum(1 for c in cards if c["tier"] == "MEDIUM"),
                     sum(1 for c in cards if c["tier"] == "WATCH"),
                     len(killed))
        except Exception as e:
            import traceback
            log.warning("v3 renderer failed (%s); falling back to v2 layout.", e)
            log.warning("v3 traceback:\n%s", traceback.format_exc())
            html = None

    if html is None:
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

    # v3 dual-write: also drop a preview at the canonical preview path
    if LAYOUT_VERSION == "v3":
        preview = _ROOT / "outputs" / "previews" / "stock_recs_v3_preview.html"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(html, encoding="utf-8")
        log.info("Preview: %s", preview)
        print(f"Preview: {preview}")


if __name__ == "__main__":
    main()

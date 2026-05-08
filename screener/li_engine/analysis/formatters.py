"""Shared formatters and rendering helpers for L&I reports.

Pure (non-DB, non-HTML) helpers extracted from
``screener.li_engine.analysis.weekly_v2_report`` so the website's
``/tools/li/candidates`` page and the weekly email can render the same
visual content from the same parquets.

Extracted (lives here):
    * ``fmt_mcap``       — market cap formatting ($1.2B / $234M)
    * ``fmt_pct``        — signed percent
    * ``fmt_oi``         — open interest with K/M suffixes
    * ``THEME_LABELS``   — pretty labels for theme tags
    * ``pretty_themes``  — comma-separated tags -> pretty list
    * ``COMPANY_LINES``  — hand-curated 1-line company descriptions
    * ``_TICKER_COMPANY_NAMES`` — common-underlier company-name dict
    * ``resolve_company_line`` — 1-line description with multi-step fallback
    * ``load_yaml_overrides``  — generic YAML config merger

Left in place (DB-touching, in weekly_v2_report.py):
    * ``load_filings_count_this_week``         — DB query (Hero KPI)
    * ``load_earliest_competitor_filing_dates`` — DB query (effective dates)
    * ``load_top_mentions``                     — parquet read (KPI)
    * ``render`` and ``_section_card``         — HTML (email-specific)

The original names in ``weekly_v2_report`` are leading-underscore (private).
This module re-exports without the underscore so the public API for callers
(website router + email script) is clean. ``weekly_v2_report`` keeps its
private aliases so it continues to work unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# YAML override loader (generic)
# ---------------------------------------------------------------------------

def load_yaml_overrides(filename: str, default: Any) -> Any:
    """Merge a YAML config file into a default dict or list.

    Mirrors ``weekly_v2_report._load_yaml_overrides``. Falls back silently
    to *default* on any error so a corrupt config never blocks rendering.
    """
    import yaml
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
            if isinstance(overrides, dict):
                entries: list[dict] = []
                for section in overrides.values():
                    if isinstance(section, list):
                        entries.extend(section)
                if entries:
                    keep = {"ticker", "company", "date", "valuation", "desc"}
                    return [{k: v for k, v in e.items() if k in keep} for e in entries]
            return default
    except Exception as e:  # pragma: no cover - defensive
        print(f"Warning: failed to load {filename}: {e}")
    return default


# ---------------------------------------------------------------------------
# Common underlier ticker -> company name (fund-name-parse fallback dict)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Hand-curated 1-line company descriptions (overridable via YAML)
# ---------------------------------------------------------------------------
_DEFAULT_COMPANY_LINES: dict[str, str] = {
    # Launch candidates
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
    # Filing candidates
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

COMPANY_LINES: dict[str, str] = load_yaml_overrides(
    "company_descriptions.yaml", _DEFAULT_COMPANY_LINES
)


# ---------------------------------------------------------------------------
# Theme labels
# ---------------------------------------------------------------------------
THEME_LABELS: dict[str, str] = {
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


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_mcap(v) -> str:
    """Format market cap (input is in $M)."""
    if v is None or pd.isna(v):
        return "—"
    if v >= 100_000:
        return f"${v/1000:.0f}B"
    if v >= 1000:
        return f"${v/1000:.1f}B"
    return f"${v:,.0f}M"


def fmt_pct(v) -> str:
    """Signed percent with no decimals."""
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.0f}%"


def fmt_oi(v) -> str:
    """Open interest with K/M suffix."""
    if v is None or pd.isna(v):
        return "—"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1000:
        return f"{v/1000:.0f}K"
    return f"{v:.0f}"


def pretty_themes(raw) -> str:
    """Convert a comma-separated theme tag string into pretty labels."""
    if not raw or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return ", ".join(THEME_LABELS.get(p, p.replace("_", " ").title()) for p in parts)


def resolve_company_line(ticker: str, sector: str | None = None,
                         fund_name: str | None = None) -> str:
    """Return a 1-line company description for *ticker*.

    Priority:
    1. Hand-curated COMPANY_LINES dict (YAML-overridable)
    2. Common-underlier company-name dict
    3. Fund-name parse for embedded ticker
    4. Sector hint
    5. Safe fallback (never "Description pending.")
    """
    # 1. Hand-curated
    if ticker in COMPANY_LINES:
        return COMPANY_LINES[ticker]

    # 2. Known company name
    if ticker in _TICKER_COMPANY_NAMES:
        cname = _TICKER_COMPANY_NAMES[ticker]
        return f"{cname} — referenced via leveraged ETF"

    # 3. Fund-name parse
    if fund_name:
        import re
        cleaned = re.sub(
            r"\b(T-REX|DEFIANCE|DIREXION|PROSHARES|ROUNDHILL|LEVERAGE\s+SHARES?|"
            r"\dX|2X|3X|LONG|SHORT|DAILY|TARGET|BULL|BEAR|ULTRA|FUND|ETF|DAILY\s+TARGET)\b",
            " ", fund_name, flags=re.IGNORECASE,
        ).strip()
        candidates = [w for w in cleaned.split() if w.isupper() and 2 <= len(w) <= 6]
        for cand in candidates:
            if cand in _TICKER_COMPANY_NAMES:
                cname = _TICKER_COMPANY_NAMES[cand]
                return f"{cname} — referenced via leveraged ETF"

    # 4. Sector hint
    if sector and sector != "—":
        return f"{ticker} — {sector} sector underlier"

    # 5. Hard fallback
    return f"{ticker} — see SEC filing details"

"""Filing Landscape service - competitive L&I matrix from SEC filings.

Ported from scripts/generate_competitive_filing_report.py for webapp use.
Queries fund_status + trusts tables, builds issuer matrices, fund-level rows,
and KPIs.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.models import FundStatus, Trust

# ---------------------------------------------------------------------------
# Issuer mapping (trust name -> display name)
# ---------------------------------------------------------------------------
ISSUER_MAP = {
    "ETF Opportunities Trust": "T-REX",
    "World Funds Trust": "T-REX",
    "Direxion Shares ETF Trust": "Direxion",
    "Direxion Funds": "Direxion",
    "ProShares Trust": "ProShares",
    "GraniteShares ETF Trust": "GraniteShares",
    "ETF Series Solutions": "Defiance",
    "Volatility Shares Trust": "Vol Shares",
    "Tidal Trust II": "LevMax",
    "Roundhill ETF Trust": "Roundhill",
    "Investment Managers Series Trust II": "Tradr",
    "Themes ETF Trust": "Lev Shares",
    "NEOS ETF Trust": "Kurv",
    "REX ETF Trust": "REX",
}

ISSUER_ORDER = [
    "T-REX", "REX", "ProShares", "Direxion", "GraniteShares", "Defiance",
    "Vol Shares", "Tradr", "Lev Shares", "LevMax", "Roundhill", "Kurv",
]

REX_ISSUERS = {"T-REX", "REX"}

# Leverage levels in sort order (fractional first, then whole)
LEVERAGE_SORT_ORDER = ["0.5x", "0.75x", "1.25x", "1.5x", "2x", "3x", "4x", "5x"]
_LEV_RANK = {lev: i for i, lev in enumerate(LEVERAGE_SORT_ORDER)}

# Whole-number leverage levels used for matrices / scorecard
_MATRIX_LEVS = ("2x", "3x", "4x", "5x")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def get_leverage(name: str) -> str | None:
    n = name.upper()
    # Fractional leverage (must check BEFORE whole numbers)
    if re.search(r"1\.5\s*[Xx]", n):
        return "1.5x"
    if re.search(r"1\.25\s*[Xx]", n):
        return "1.25x"
    if re.search(r"0\.75\s*[Xx]", n):
        return "0.75x"
    if re.search(r"0\.5\s*[Xx]", n):
        return "0.5x"
    # Whole-number leverage
    if re.search(r"\b5[Xx]\b", n):
        return "5x"
    if re.search(r"\b4[Xx]\b|DAILY TARGET 4X", n):
        return "4x"
    if re.search(r"\b3[Xx]\b|DAILY TARGET 3X|3X LONG|3X SHORT|3X INVERSE|\[.*3[Xx]1.*\]", n, re.IGNORECASE):
        return "3x"
    if re.search(r"\b2[Xx]\b|DAILY TARGET 2X", n, re.IGNORECASE):
        return "2x"
    # ProShares naming convention (no explicit NX in name)
    if "ULTRAPRO SHORT" in n or "ULTRAPRO" in n:
        return "3x"
    if "ULTRASHORT" in n or "ULTRA SHORT" in n:
        return "2x"
    if "ULTRA " in n or n.endswith("ULTRA"):
        return "2x"
    return None


def extract_underlier(name: str) -> str | None:
    n = name.strip()
    patterns = [
        # T-REX: "T-REX 2X Long NVDA Daily Target ETF"
        r"T-REX\s+\d+\.?\d*[Xx]\s+(?:LONG|INVERSE|SHORT)\s+(.+?)\s+DAILY",
        # Direxion: "Direxion Daily NVDA Bull 2X" / "Direxion Monthly NASDAQ Bear 1.25X"
        r"Direxion\s+(?:Daily|Monthly|Weekly)\s+(.+?)\s+(?:Bull|Bear)\s+\d",
        # ProShares target: "ProShares Daily Target 2X ..."
        r"ProShares Daily Target \d[Xx]\s+(.+?)$",
        # GraniteShares: "GraniteShares 2x Long NVDA Daily ETF" / "GraniteShares 1.25x Long TSLA"
        r"GraniteShares\s+\d+\.?\d*[xX]\s+(?:Long|Short|Inverse)\s+(.+?)\s+(?:Daily|ETF)",
        # Defiance: "Defiance Daily Target 2X Short MSTR ETF"
        r"Defiance Daily Target \d+\.?\d*[Xx]\s+(?:Long|Short|Inverse)\s+(.+?)\s+(?:Daily|ETF)",
        # AXS: "AXS 1.25X NVDA Bull Daily ETF"
        r"AXS\s+\d+\.?\d*[Xx]\s+(.+?)\s+(?:Bull|Bear)\s+(?:Daily|Weekly)",
        # Tradr: "Tradr 2X Long NVDA Daily ETF"
        r"[Tt][Rr]adr\s+\d+\.?\d*[Xx]\s+(?:Long|Short)\s+(.+?)\s+(?:Daily|Weekly|Monthly|Quarterly)",
        # Leverage Shares: "Leverage Shares 2x Long AAPL Daily ETP"
        r"Leverage Shares\s+\d+\.?\d*[xX]\s+(?:Long|Short)\s+(.+?)\s+(?:Daily|ETP|ETF)",
        # LevMax: "LevMax 2X NVDA [Monthly]"
        r"LevMax\S*\s+(.+?)\s+\[",
        # Roundhill: "Roundhill 2X NVDA ETF"
        r"Roundhill\s+\d+\.?\d*[Xx]\s+(.+?)\s+ETF",
        # Generic NX pattern: "2X AAPL ETF" / "3X Long NVDA"
        r"^\d+\.?\d*[Xx]\s+(?:Long|Short|Inverse|Bull|Bear)?\s*(.+?)\s+(?:Daily|ETF|ETP|Shares)",
        # ProShares Ultra/UltraPro: "ProShares Ultra S&P500" / "ProShares UltraPro Short QQQ"
        r"ProShares\s+Ultra(?:Pro\s+(?:Short\s+)?|Short\s+)?(.+?)(?:\s+ETF)?$",
        # YieldMax / REX income with leverage
        r"YieldMax\s+\d+\.?\d*[Xx]\s+(.+?)\s+Option",
        # Catch-all: explicit ticker between Long/Short and Daily
        r"(?:Long|Short|Inverse|Bull|Bear)\s+([A-Z]{1,5})\s+(?:Daily|Weekly|Monthly|ETF)",
        # Corgi/simple: "AAPL 2x Daily ETF" / "NVDA 2x Daily ETF"
        r"^([A-Z]{1,5})\s+\d+\.?\d*[xX]\s+(?:Daily|Weekly|Monthly)",
        # GraniteShares YieldBOOST: "GraniteShares YieldBOOST AAPL 2x Income"
        r"YieldBOOST\s+([A-Z]{1,5})\s+\d+\.?\d*[xX]",
        # Rex Daily Target: "Rex Daily Target 1.5X MSTR ETF"
        r"Rex Daily Target \d+\.?\d*[Xx]\s+(.+?)\s+ETF",
        # 21Shares: "21Shares 2x Long Dogecoin ETF"
        r"21Shares\s+\d+\.?\d*[xX]\s+(?:Long|Short)\s+(.+?)\s+ETF",
        # Teucrium: "Teucrium -2x Daily Corn ETF"
        r"Teucrium\s+-?\d+\.?\d*[xX]\s+Daily\s+(.+?)\s+ETF",
        # Innovator: "Innovator 2x Bitcoin Daily ETF"
        r"Innovator\s+\d+\.?\d*[xX]\s+(.+?)\s+(?:Daily|Monthly|ETF)",
        # Defiance standalone: "Defiance 2X Daily Long Pure Drone..."
        r"Defiance\s+\d+\.?\d*[Xx]\s+Daily\s+(?:Long|Short)\s+(?:Pure\s+)?(.+?)\s+(?:ETF|Automation)",
        # Leverage Shares Capped: "Leverage Shares 2x Capped Accelerated APP Monthly"
        r"Leverage Shares\s+\d+\.?\d*[xX]\s+(?:Capped\s+)?(?:Accelerated\s+)?([A-Z]{1,5})\s+(?:Monthly|Daily|Weekly)",
    ]
    for pat in patterns:
        m = re.search(pat, n, re.IGNORECASE)
        if m:
            result = m.group(1).strip()
            # Clean trailing noise words
            result = re.sub(r'\s+(?:Daily|Target|Shares|Fund|Trust|Strategy|Index)\s*$',
                            '', result, flags=re.IGNORECASE).strip()
            if result:
                return result
    return None


def normalize_underlier(u: str) -> str:
    u = u.upper()
    u = u.replace("ALPHABET", "GOOGL")
    if u == "GOOG":
        u = "GOOGL"
    u = u.replace("BITCOIN", "BTC").replace("ETHEREUM", "ETH").replace("ETHER", "ETH")
    u = u.replace("BRK-B", "BRKB").replace("BRK.B", "BRKB")
    return u


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_filing_landscape(db: Session) -> dict:
    """Query DB and build filing landscape data for the webapp.

    Returns dict with:
      matrices: {2x: {underlier: {issuer: True}}, 3x: ..., 4x: ..., 5x: ...}
      kpis: {count_2x, count_3x, count_4x, count_5x, leverage_counts, rex_exclusive, total_names}
      issuer_scorecard: [{issuer, c2, c3, c4, c5, total, exclusive}]
      active_issuers: {2x: [...], 3x: [...], 4x: [...], 5x: [...]}
      all_active_issuers: [...]
      fund_rows: [{fund_name, series_id, ticker, issuer, trust, leverage, underlier, status, ...}]
      top_underliers: [{underlier, count, leverages}, ...]
      leverage_counts: {leverage: count}
      generated_at: str
    """
    rows = db.execute(
        select(
            FundStatus.fund_name,
            FundStatus.status,
            FundStatus.ticker,
            FundStatus.series_id,
            FundStatus.prospectus_link,
            FundStatus.effective_date,
            FundStatus.latest_filing_date,
            FundStatus.latest_form,
            Trust.name,
        )
        .join(Trust, FundStatus.trust_id == Trust.id)
        .order_by(Trust.name, FundStatus.fund_name)
    ).all()

    # matrix[leverage][underlier][issuer] = True  (whole-number only)
    matrices: dict[str, dict[str, dict[str, bool]]] = {
        "2x": defaultdict(dict),
        "3x": defaultdict(dict),
        "4x": defaultdict(dict),
        "5x": defaultdict(dict),
    }

    # Fund-level rows for the flat table
    fund_rows: list[dict] = []

    for (fund_name, status, ticker, series_id, prospectus_link,
         effective_date, latest_filing_date, latest_form, trust_name) in rows:
        lev = get_leverage(fund_name)
        if not lev:
            continue
        issuer = ISSUER_MAP.get(trust_name, trust_name)
        raw_underlier = extract_underlier(fund_name)
        underlier = normalize_underlier(raw_underlier) if raw_underlier else ""

        # Populate matrix (whole-number leverage only, needs underlier)
        if lev in matrices and underlier:
            matrices[lev][underlier][issuer] = True

        fund_rows.append({
            "fund_name": fund_name,
            "series_id": series_id,
            "ticker": ticker,
            "issuer": issuer,
            "trust": trust_name,
            "leverage": lev,
            "underlier": underlier,
            "status": status,
            "prospectus_link": prospectus_link,
            "effective_date": effective_date,
            "latest_filing_date": latest_filing_date,
            "latest_form": latest_form,
        })

    # Sort fund_rows: leverage order, then underlier (blanks last), issuer, fund_name
    def _sort_key(row):
        lev_rank = _LEV_RANK.get(row["leverage"], 99)
        ul = row["underlier"]
        # Blanks sort last
        ul_key = (1, "") if not ul else (0, ul)
        return (lev_rank, ul_key, row["issuer"], row["fund_name"])

    fund_rows.sort(key=_sort_key)

    # Convert defaultdicts
    matrices = {k: dict(v) for k, v in matrices.items()}

    # Active issuers per leverage (ordered)
    active_issuers = {}
    for lev in _MATRIX_LEVS:
        seen = set()
        for issuers_map in matrices[lev].values():
            seen.update(issuers_map.keys())
        active_issuers[lev] = [i for i in ISSUER_ORDER if i in seen]

    # All active issuers across all leverage levels
    all_seen = set()
    for m in matrices.values():
        for issuers_map in m.values():
            all_seen.update(issuers_map.keys())
    all_active = [i for i in ISSUER_ORDER if i in all_seen]

    # KPIs
    rex_exclusive = 0
    for lev in _MATRIX_LEVS:
        for u, issuers_map in matrices[lev].items():
            if len(issuers_map) == 1 and any(i in REX_ISSUERS for i in issuers_map):
                rex_exclusive += 1

    total_names = len(set().union(
        *(set(matrices[lev].keys()) for lev in _MATRIX_LEVS)
    ))

    # Leverage counts from fund_rows (all levels including fractional)
    lev_counter = Counter(r["leverage"] for r in fund_rows)
    leverage_counts = {lev: lev_counter.get(lev, 0) for lev in LEVERAGE_SORT_ORDER}

    kpis = {
        # Backward-compat keys (matrix-level = unique underlier count)
        "count_2x": len(matrices["2x"]),
        "count_3x": len(matrices["3x"]),
        "count_4x": len(matrices["4x"]),
        "count_5x": len(matrices["5x"]),
        "rex_exclusive": rex_exclusive,
        "total_names": total_names,
        # New: fund-level counts per leverage
        "leverage_counts": leverage_counts,
    }

    # Top 20 underliers by fund count across all leverage levels
    underlier_data: dict[str, dict] = {}  # underlier -> {count, leverages set}
    for r in fund_rows:
        ul = r["underlier"]
        if not ul:
            continue
        if ul not in underlier_data:
            underlier_data[ul] = {"count": 0, "leverages": set()}
        underlier_data[ul]["count"] += 1
        underlier_data[ul]["leverages"].add(r["leverage"])

    top_underliers = sorted(
        underlier_data.items(), key=lambda x: x[1]["count"], reverse=True
    )[:20]
    top_underliers = [
        {
            "underlier": ul,
            "count": info["count"],
            "leverages": sorted(info["leverages"], key=lambda l: _LEV_RANK.get(l, 99)),
        }
        for ul, info in top_underliers
    ]

    # Issuer scorecard
    issuer_scorecard = []
    for iss in all_active:
        c2 = sum(1 for u, i in matrices["2x"].items() if iss in i)
        c3 = sum(1 for u, i in matrices["3x"].items() if iss in i)
        c4 = sum(1 for u, i in matrices["4x"].items() if iss in i)
        c5 = sum(1 for u, i in matrices["5x"].items() if iss in i)
        total = c2 + c3 + c4 + c5

        excl = 0
        for lev in _MATRIX_LEVS:
            for u, issuers_map in matrices[lev].items():
                if iss in issuers_map and len(issuers_map) == 1:
                    excl += 1

        issuer_scorecard.append({
            "issuer": iss,
            "c2": c2,
            "c3": c3,
            "c4": c4,
            "c5": c5,
            "total": total,
            "exclusive": excl,
            "is_rex": iss in REX_ISSUERS,
        })

    return {
        "matrices": matrices,
        "kpis": kpis,
        "issuer_scorecard": issuer_scorecard,
        "active_issuers": active_issuers,
        "all_active_issuers": all_active,
        "fund_rows": fund_rows,
        "top_underliers": top_underliers,
        "leverage_counts": leverage_counts,
        "generated_at": datetime.now().strftime("%b %d, %Y %H:%M"),
    }

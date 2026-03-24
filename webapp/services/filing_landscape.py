"""Filing Landscape service - competitive 2x/3x/4x/5x matrix from SEC filings.

Ported from scripts/generate_competitive_filing_report.py for webapp use.
Queries fund_status + trusts tables, builds issuer matrices and KPIs.
"""
from __future__ import annotations

import re
from collections import defaultdict
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


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def get_leverage(name: str) -> str | None:
    n = name.upper()
    if re.search(r"\b5[Xx]\b", n):
        return "5x"
    if re.search(r"\b4[Xx]\b|DAILY TARGET 4X", n):
        return "4x"
    if re.search(r"\b3[Xx]\b|DAILY TARGET 3X|3X1\b|\[MONTHLY 3X1\]|3X LONG|3X SHORT|3X INVERSE", n, re.IGNORECASE):
        return "3x"
    if re.search(r"\b2[Xx]\b|DAILY TARGET 2X", n, re.IGNORECASE):
        return "2x"
    return None


def extract_underlier(name: str) -> str | None:
    n = name.strip()
    patterns = [
        r"T-REX\s+\d[Xx]\s+(?:LONG|INVERSE|SHORT)\s+(.+?)\s+DAILY",
        r"Direxion Daily\s+(.+?)\s+(?:Bull|Bear)\s+[2345][Xx]",
        r"ProShares Daily Target \d[Xx]\s+(.+?)$",
        r"GraniteShares\s+\d[Xx]\s+(?:Long|Short|Inverse)\s+(.+?)\s+Daily",
        r"Defiance Daily Target \d[Xx]\s+(?:Long|Short|Inverse)\s+(.+?)\s+ETF",
        r"^[2345][Xx]\s+(.+?)\s+ETF",
        r"LevMax\S*\s+(.+?)\s+\[",
        r"[Tt][Rr]adr\s+\d[Xx]\s+(?:Long|Short)\s+(.+?)\s+(?:Daily|Weekly|Monthly|Quarterly)",
        r"Leverage Shares\s+\d[Xx]\s+(?:Long|Short)\s+(.+?)\s+Daily",
        r"Roundhill\s+\d[Xx]\s+(.+?)\s+ETF",
        r"ProShares\s+Ultra(?:Pro\s+(?:Short\s+)?)?(.+?)(?:\s+ETF)?$",
    ]
    for pat in patterns:
        m = re.search(pat, n, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def normalize_underlier(u: str) -> str:
    u = u.upper()
    u = u.replace("ALPHABET", "GOOGL")
    if u == "GOOG":
        u = "GOOGL"
    u = u.replace("BITCOIN", "BTC").replace("ETHER", "ETH")
    u = u.replace("BRK-B", "BRKB").replace("BRK.B", "BRKB")
    return u


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_filing_landscape(db: Session) -> dict:
    """Query DB and build filing landscape data for the webapp.

    Returns dict with:
      matrices: {2x: {underlier: {issuer: True}}, 3x: ..., 4x: ..., 5x: ...}
      kpis: {count_2x, count_3x, count_4x, count_5x, rex_exclusive, total_names}
      issuer_scorecard: [{issuer, c2, c3, c4, c5, total, exclusive}]
      active_issuers: {2x: [...], 3x: [...], 4x: [...], 5x: [...]}
      generated_at: str
    """
    rows = db.execute(
        select(FundStatus.fund_name, FundStatus.status, FundStatus.ticker, Trust.name)
        .join(Trust, FundStatus.trust_id == Trust.id)
        .order_by(Trust.name, FundStatus.fund_name)
    ).all()

    # matrix[leverage][underlier][issuer] = True
    matrices: dict[str, dict[str, dict[str, bool]]] = {
        "2x": defaultdict(dict),
        "3x": defaultdict(dict),
        "4x": defaultdict(dict),
        "5x": defaultdict(dict),
    }

    for fund_name, status, ticker, trust_name in rows:
        lev = get_leverage(fund_name)
        if not lev:
            continue
        issuer = ISSUER_MAP.get(trust_name, trust_name)
        underlier = extract_underlier(fund_name)
        if not underlier:
            continue
        underlier = normalize_underlier(underlier)
        matrices[lev][underlier][issuer] = True

    # Convert defaultdicts
    matrices = {k: dict(v) for k, v in matrices.items()}

    # Active issuers per leverage (ordered)
    active_issuers = {}
    for lev in ("2x", "3x", "4x", "5x"):
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
    for lev in ("2x", "3x", "4x", "5x"):
        for u, issuers_map in matrices[lev].items():
            if len(issuers_map) == 1 and any(i in REX_ISSUERS for i in issuers_map):
                rex_exclusive += 1

    total_names = len(set().union(
        *(set(matrices[lev].keys()) for lev in ("2x", "3x", "4x", "5x"))
    ))

    kpis = {
        "count_2x": len(matrices["2x"]),
        "count_3x": len(matrices["3x"]),
        "count_4x": len(matrices["4x"]),
        "count_5x": len(matrices["5x"]),
        "rex_exclusive": rex_exclusive,
        "total_names": total_names,
    }

    # Issuer scorecard
    issuer_scorecard = []
    for iss in all_active:
        c2 = sum(1 for u, i in matrices["2x"].items() if iss in i)
        c3 = sum(1 for u, i in matrices["3x"].items() if iss in i)
        c4 = sum(1 for u, i in matrices["4x"].items() if iss in i)
        c5 = sum(1 for u, i in matrices["5x"].items() if iss in i)
        total = c2 + c3 + c4 + c5

        excl = 0
        for lev in ("2x", "3x", "4x", "5x"):
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
        "generated_at": datetime.now().strftime("%b %d, %Y %H:%M"),
    }

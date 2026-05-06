"""Layer 2 — EDGAR sponsor lookup + extended brand matching.

Resolves tickers that Layer 1 (fund_name regex) could not classify.
Sources (applied in priority order):
  L2-A  extended fund_name regex (catches advisor names mid-word, after 'EA ')
  L2-B  current_issuer slash-pattern brand extraction (e.g. 'Two Roads/Anfield' -> 'Anfield')
  L2-C  trust-to-brand dict (series trusts -> known brand)
  L2-D  EDGAR submissions JSON entity name -> brand (direct issuers / confirmed names)

Input:
  docs/issuer_review_queue.csv        — 802 residue tickers from Layer 1
  data/etp_tracker.db                 — ticker <-> CIK via mkt_master_data + trusts
  data/edgar_sponsor_cache.json       — submissions JSON cache (CIK keyed, refreshed if >30d old)

Output:
  config/rules/issuer_brand_overrides.csv   — APPEND new rows (source=edgar-sponsor-2026-05-05)
  docs/issuer_review_queue.csv              — UPDATE: remove resolved rows, add layer_2_attempted col

Run:
  python scripts/edgar_sponsor_lookup.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OVERRIDES_CSV = PROJECT_ROOT / "config" / "rules" / "issuer_brand_overrides.csv"
REVIEW_QUEUE_CSV = PROJECT_ROOT / "docs" / "issuer_review_queue.csv"
SPONSOR_CACHE_JSON = PROJECT_ROOT / "data" / "edgar_sponsor_cache.json"

SOURCE_TAG = "edgar-sponsor-2026-05-05"
CACHE_MAX_AGE_DAYS = 30
BATCH_SIZE = 50
REQUEST_PAUSE = 0.15          # belt-and-suspenders on top of SECClient.pause
MAX_WALL_SECONDS = 28 * 60   # 28-minute safety limit (under 30-min brief)

# ---------------------------------------------------------------------------
# L2-A  Extended fund-name regex  (order matters: specific before generic)
# ---------------------------------------------------------------------------
_L2_FUND_NAME_PATTERNS: list[tuple[str, str]] = [
    # EA-prefixed sub-branded funds (EA Series Trust)
    (r"^EA\s+ASTORIA\b", "Astoria"),
    (r"^EA\s+BRIDGEWAY\b", "Bridgeway"),
    (r"^EA\s+MEKETA\b", "Meketa"),
    (r"^EA\s+SERIES\b", "EA Series"),
    # Fund name starts with advisor brand (Layer 1 missed due to word boundary / case)
    (r"^ALPHA\s+BLUE\s+CAPITAL\b", "Alpha Blue Capital"),
    (r"^ARGENT\b", "Argent"),
    (r"^ABACUS\b", "Abacus"),
    (r"^ALTRIUS\b", "Altrius"),
    (r"^ACUITAS\b", "Acuitas"),
    (r"^AIGLE\b", "Aigle"),
    (r"^ACVF\b|AMERICAN\s+CONSERVATIVE\s+VALUES", "American Conservative Values"),
    (r"^ADVENT\b", "Advent"),
    (r"^ANFIELD\b", "Anfield"),
    (r"^ANGEL\s+OAK\b", "Angel Oak"),
    (r"^ARCHITECT\b", "Architect"),
    (r"^ARLINGTON\b", "Arlington"),
    (r"^ARM\s+HOLDINGS\b", "Precidian"),  # ADRHedged products
    (r"ADRHEDGED", "Precidian"),           # All ADRHedged = Precidian
    (r"^ARS\b", "ARS"),
    (r"^ARIN\b", "Arin"),
    (r"^AVANTIS\b", "Avantis"),
    (r"^AVOS\b", "Avos"),
    (r"^AVORY\b", "Avory"),
    (r"^BAHL\s*&?\s*GAYNOR\b", "Bahl & Gaynor"),
    (r"^BANCREEK\b", "Bancreek"),
    (r"^BARON\b", "Baron"),
    (r"^BASTION\b", "Bastion"),
    (r"^BBLU\b|^BRIDGEWAY\b", "Bridgeway"),
    (r"^BRANDYWINE\b|BRANDYWINEGLOBAL\b", "BrandywineGlobal"),
    (r"^BROOKSTONE\b", "Brookstone"),
    (r"^BROWN\s+ADVISORY\b", "Brown Advisory"),
    (r"^BURNEY\b", "Burney"),
    (r"^CALAMOS\b", "Calamos"),
    (r"^CAPITAL\s+GROUP\b", "Capital Group"),
    (r"^CARILLON\b", "Carillon"),
    (r"^CASTELLAN\b", "Castellan"),
    (r"^CITY\s+DIFFERENT\b", "City Different"),
    (r"^CLOUGH\b", "Clough"),
    (r"^CONCOURSE\s+CAPITAL\b", "Concourse Capital"),
    (r"^CROSSMARK\b", "Crossmark"),
    (r"^CULTIV\b", "Cultivar"),
    (r"^DAC\b", "DAC"),
    (r"^DAKOTA\b", "Dakota"),
    (r"^DAVIS\b", "Davis"),
    (r"^DEFINED\s+DURATION\b", "Lively"),
    (r"^DIAMOND\s+HILL\b", "Diamond Hill"),
    (r"^DISTILLATE\b", "Distillate"),
    (r"^DRACO\b", "Draco"),
    (r"^EFFICIENT\s+MARKET\s+PORTFOLIO\b", "Efficient Market"),
    (r"^ELEVATE\b|^ELEVATION\b", "Elevation"),
    (r"^EVENTIDE\b", "Eventide"),
    (r"^F\/M\s+INVEST", "F/m Investments"),
    (r"^FM\s+COMPOUNDER\b|^FM\s+FOCUS\b", "FM"),
    (r"^FRAMEBRIDGE\b|^FRAMEW\b", "Framework"),
    (r"^FRONTIER\b", "Frontier"),
    (r"^FUNDX\b", "FundX"),
    (r"^GABELLI\b", "Gabelli"),
    (r"^GLOBAL\s+BETA\b", "Global Beta"),
    (r"^GOLDEN\b", "Golden"),
    (r"^GUINNESS\b", "Guinness Atkinson"),
    (r"^HARRIS\s+OAKMARK\b", "Harris Oakmark"),
    (r"^HEDGEY\b", "Hedgey"),
    (r"^HENNESSY\b", "Hennessy"),
    (r"^HILL\s+INVESTMENT\b", "Hill Investment Group"),
    (r"^HONEYTREE\b", "Honeytree"),
    (r"^HOTCHKIS\b", "Hotchkis and Wiley"),
    (r"^IDX\b", "IDX"),
    (r"^IMPAX\b", "Impax"),
    (r"^INFRA\b|INFRASTRUCTURE\s+ETF", "Infrastructure Capital"),
    (r"^IRONHORSE\b|IRONHOR\b", "IronHorse"),
    (r"^JP\s+MORGAN\b|^JPMORGAN\b", "JP Morgan"),
    (r"^KINGSBR\b|^KINGSBURY\b", "Kingsbury"),
    (r"^KINGSBRIDGE\b", "Kingsbridge"),
    (r"^KOVITZ\b", "Kovitz"),
    (r"^KRANESHARES?\b|^KRANE\b", "KraneShares"),
    (r"^KURV\b", "Kurv"),
    (r"^LAZARD\b", "Lazard"),
    (r"^LEUTHHOLD\b|^LEUTHOLD\b", "Leuthold"),
    (r"^LIBERTY\b", "Liberty"),
    (r"^LIFEX\b", "Stone Ridge"),   # LifeX = Stone Ridge brand
    (r"^LITMAN\s+GREGORY\b", "Litman Gregory"),
    (r"^LISTED\s+FUNDS\b", "Listed Funds"),
    (r"^LITTLE\s+HARBOR\b", "Little Harbor"),
    (r"^LOGAN\b", "Logan"),
    (r"^MADISON\b", "Madison"),
    (r"^MAN\s+ETF\b|^MAN\s+", "Man"),
    (r"^MANAGER\s+DIRECTED\b", "Manager Directed"),
    (r"^MARKETDESK\b", "MarketDesk"),
    (r"^MASON\b", "Mason"),
    (r"^MILLIMAN\b", "Milliman"),
    (r"^MORGAN\s+STANLEY\b", "Morgan Stanley"),
    (r"^MOTLEY\s+FOOL\b", "Motley Fool"),
    (r"^MYRIAD\b", "Myriad"),
    (r"^NATIXIS\b", "Natixis"),
    (r"^NIGHTVIEW\b", "Nightview"),
    (r"^NUVEEN\b", "Nuveen"),
    (r"^OCEAN\b", "Ocean Park"),
    (r"^OBRA\b", "Obra"),
    (r"^ONEASCENT\b|^ONE\s+ASCENT\b", "OneAscent"),
    (r"^OPTIMIZ\b|^OPTIMAL\b", "Optimal"),
    (r"^OTG\b", "OTG"),
    (r"^OVERNIGHT\b", "Overnight"),
    (r"^OVERLAY\b", "Overlay"),
    (r"^PALMER\s+SQUARE\b", "Palmer Square"),
    (r"^PARALEL\b|^PARALLEL\b", "Paralel"),
    (r"^PARNASSUS\b", "Parnassus"),
    (r"^PMV\b", "PMV"),
    (r"^POLISH\b|^POLO\b", "Polen"),
    (r"^PRAXIS\b", "Praxis"),
    (r"^PRECIDIAN\b", "Precidian"),
    (r"^PRINCIPAL\b", "Principal"),
    (r"^PROFESSIONALLY\s+MANAGED\b", "PMC"),
    (r"^Q3\s+ASSET\b", "Q3 Asset"),
    (r"^QRAFT\b", "Qraft"),
    (r"^RECKONER\b", "Reckoner"),
    (r"^REDWOOD\b", "Redwood"),
    (r"^REX\b", "REX"),
    (r"^RIDGELINE\b|^RIDGEL\b", "Ridgeline"),
    (r"^RBB\b", "RBB"),
    (r"^SAMSUNG\b", "Samsung"),
    (r"^SCHARF\b", "Scharf"),
    (r"^SERIES\s+PORTFOLIOS\b", "Series Portfolios"),
    (r"^SIREN\b", "Siren"),
    (r"^SOLAR\b", "Solar"),
    (r"^SOUND\b", "Sound Capital"),
    (r"^SOUNDWATCH\b", "Soundwatch"),
    (r"^SOVEREIGN\b", "Sovereign"),
    (r"^SP\s+FUNDS\b", "SP Funds"),
    (r"^SPARKLINE\b", "Sparkline"),
    (r"^SPINNAKER\b", "Spinnaker"),
    (r"^STONE\s+RIDGE\b", "Stone Ridge"),
    (r"^STRATEGAS\b", "Strategas"),
    (r"^SUBVERSIVE\b", "Subversive"),
    (r"^SUMMIT\s+GLOBAL\b", "Summit Global"),
    (r"^SYMMETRY\b", "Symmetry"),
    (r"^TEMPLETON\b", "Franklin Templeton"),
    (r"^TEMA\b", "Tema"),
    (r"^TEXAS\s+CAPITAL\b", "Texas Capital"),
    (r"^THOR\b", "Thor"),
    (r"^THORNBURG\b", "Thornburg"),
    (r"^THEMATIC\b", "Themes"),
    (r"^THRIVENT\b", "Thrivent"),
    (r"^TIMOTHY\s+PLAN\b|^TIMOTHY\b", "Timothy Plan"),
    (r"^TOEHOLD\b", "Toehold"),
    (r"^TOROSO\b|^TOROS\b", "Toroso"),
    (r"^TRANSAMER\b", "Transamerica"),
    (r"^TREMBLANT\b", "Tremblant"),
    (r"^TRUTH\s+SOCIAL\b", "Truth Social"),
    (r"^TUTTLE\b", "Tuttle"),
    (r"^TWIN\s+OAK\b", "Twin Oak"),
    (r"^ULTIMUS\b", "Ultimus"),
    (r"^UNITED\s+STATES\s+COMM\b", "USCF"),
    (r"^US\s+COMMODITY\b|^USCF\b", "USCF"),
    (r"^VALUE\s+ADVIS\b|^VALUED\s+ADVIS\b", "Regan Capital"),
    (r"^VIRTUS\b", "Virtus"),
    (r"^WBI\b", "WBI"),
    (r"^WEDBUSH\b", "Wedbush"),
    (r"^WESTWOOD\b", "Westwood"),
    (r"^WHITEWOLF\b", "WhiteWolf"),
    (r"^WISDOMTREE\b", "WisdomTree"),
    (r"^ZACKS\b", "Zacks"),
    # Multi-unit Luxembourg
    (r"LYXOR", "Lyxor"),
    (r"AMUNDI", "Amundi"),
    (r"BNP\s+PARIBAS", "BNP Paribas"),
    # Advisor brands recognizable from fund name
    (r"^ALGER\b", "Alger"),
    (r"^CULLEN\b", "Cullen"),
    (r"^GQG\b", "GQG"),
    (r"^RJ\s+EAGLE\b|^RAYMOND\s+JAMES\b", "Raymond James"),
    (r"^DFA\b|^DIMENSIONAL\b", "Dimensional"),
    (r"^CARILLON\b", "Carillon"),
    (r"^US\s+MICRO\s+CAP\b|^US\s+LARGE\s+CAP\b|^US\s+SMALL\b", "Dimensional"),
    (r"^WISDOM\s+SHORT\b|^SPEND\s+LIFE\b", "Spend Life Wisely"),
    (r"^X-SQUARE\b|^XSQUARE\b", "X-Square"),
    (r"^PHARMAGREEN\b", "Pharmagreen"),
    (r"^PINTEREST\b", "Pinterest"),
]

_L2_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(pat, re.IGNORECASE), brand)
    for pat, brand in _L2_FUND_NAME_PATTERNS
]


def match_l2_fund_name(fund_name: str) -> str | None:
    name_upper = (fund_name or "").upper().strip()
    for pattern, brand in _L2_COMPILED:
        if pattern.search(name_upper):
            return brand
    return None


# ---------------------------------------------------------------------------
# L2-B  Slash-pattern: extract brand from 'Trust Name/Brand'
# Bad values to discard (not real brands)
# ---------------------------------------------------------------------------
_SLASH_BRAND_DISCARD = {
    "USA", "THE", "UK", "ETF", "ETFS", "INC", "LLC", "TRUST",
    "FUND", "FUNDS", "CORP", "CO", "LTD", "LP", "PLC",
}

# Slash brands that are themselves series-trust names (not real advisor brands)
_SLASH_PASSTHROUGH_TRUSTS = {
    "F/M INVESTMENTS", "RBB FUND TRUST", "RBB FUND INC", "RBB ETFS",
    "NORTHERN LIGHTS FUND TRUST", "ETF SERIES SOLUTIONS",
    "ETF OPPORTUNITIES TRUST", "ELEVATION SERIES TRUST",
    "LISTED FUNDS TRUST", "SERIES PORTFOLIO TRUST",
    "SERIES PORTFOLIOS TRUST", "ADVISORS SERIES TRUST",
    "ADVISOR MANAGED PORTFOLIOS", "COLLABORATIVE INVESTMENT",
    "MANAGED PORTFOLIO SERIES", "TWO ROADS SHARED TRUST",
    "EXCHANGE-TRADED CONCEPTS",
}


def extract_slash_brand(current_issuer: str) -> str | None:
    """Extract the brand-after-slash from 'Trust/Brand' formatted issuers."""
    if "/" not in current_issuer:
        return None
    parts = current_issuer.split("/", 1)
    raw = parts[1].strip()
    if not raw:
        return None
    raw_upper = raw.upper()
    if raw_upper in _SLASH_BRAND_DISCARD:
        return None
    # Truncated values (end without space = likely cut off at 30 chars)
    # Only use if it's at least 4 chars and seems complete
    if len(raw) < 4:
        return None
    # Filter out passthrough trust names
    for pt in _SLASH_PASSTHROUGH_TRUSTS:
        if raw_upper.startswith(pt):
            return None
    return raw.title() if raw.isupper() else raw


# ---------------------------------------------------------------------------
# L2-C  Trust-to-brand dictionary (series trust -> default brand when no
#        slash and fund_name can't resolve)
# ---------------------------------------------------------------------------
_TRUST_TO_BRAND: dict[str, str] = {
    # Direct issuers
    "Precidian ETFs Trust": "Precidian",
    "Tema ETF Trust": "Tema",
    "Stone Ridge Trust": "Stone Ridge",
    "Gabelli ETFs Trust": "Gabelli",
    "Davis Fundamental ETF Trust": "Davis",
    "Thornburg ETF Trust": "Thornburg",
    "Thornburg Investment Trust": "Thornburg",
    "Harris Oakmark ETF Trust": "Harris Oakmark",
    "Wedbush Series Trust": "Wedbush",
    "Zacks Trust": "Zacks",
    "Corgi ETF Trust I": "Corgi",
    "Corgi ETF Trust II": "Corgi",
    "Kurv ETF Trust": "Kurv",
    "Madison Funds": "Madison",
    "Milliman Funds Trust": "Milliman",
    "Angel Oak Funds Trust": "Angel Oak",
    "Litman Gregory Funds Trust": "Litman Gregory",
    "Palmer Square Funds Trust": "Palmer Square",
    "Parnassus Income Funds": "Parnassus",
    "Praxis Mutual Funds": "Praxis",
    "Texas Capital Funds Trust": "Texas Capital",
    "Amplify Commodity Trust": "Amplify",
    "MAN ETF Series Trust": "Man",
    "SP Funds Trust": "SP Funds",
    "Symmetry Panoramic Trust": "Symmetry",
    "Siren ETF Trust": "Siren",
    "AltShares Trust": "AltShares",
    "BBH Trust": "BBH",
    "KraneShares Trust": "KraneShares",
    "Crossmark ETF Trust": "Crossmark",
    "First Eagle Etf Trust": "First Eagle",
    "Elkhorn ETF Trust": "Elkhorn",
    "Capital Force ETF Trust": "Capital Force",
    "Capital-Force ETF Trust": "Capital Force",
    "Impax Funds Series Trust I": "Impax",
    "AGF Investments Trust": "AGF",
    "Global Beta ETF Trust": "Global Beta",
    "Mason Capital Fund Trust": "Mason",
    "Miller Investment Trust": "Miller",
    "Legg Mason ETF Investment Trus": "Legg Mason",
    "Mango Growth ETF/Fund Parent": "Mango",
    "Amg ETF Trust": "AMG",
    "Crossmark ETF Trust": "Crossmark",
    "FundX Investment Trust": "FundX",
    "Arrow Investments Trust": "Arrow",
    "Arrow ETF Trust": "Arrow",
    "Archer Investment Series Trust": "Archer",
    "Exchange Place Advisors Trust": "Exchange Place",
    "Allspring Funds Management LLC": "Allspring",
    "Strategas Asset Management LLC": "Strategas",
    "Indexperts LLC": "Indexperts",
    "Convergence Investment Partner": "Convergence",
    "Three Bridges Capital LP/USA": "Three Bridges Capital",
    "Euclidean Technologies Managem": "Euclidean",
    "Soundwatch Capital LLC SMAs/US": "Soundwatch",
    "Sound Capital Solutions LLC": "Sound Capital",
    "Cotwo Advisors LLC/The": "Cotwo",
    "Hill Investment Group Partners": "Hill Investment Group",
    "Tremblant Advisors LP": "Tremblant",
    "ARS": "ARS",
    "Hennessy Funds Trust": "Hennessy",
    "Build Funds Trust": "Build Asset Management",
    "Brandes Funds/USA": "Brandes",
    "Frontier ETFs/USA": "Frontier",
    "WBI ETFs/USA": "WBI",
    "Tuttle Tactical ETFs/USA": "Tuttle",
    "Twin Oak ETF Co/USA": "Twin Oak",
    "Little Harbor Funds/USA": "Little Harbor",
    "DB ETNs/USA": "DWS",
    "Goldman Sachs ETNs/USA": "Goldman Sachs",
    "WisdomTree ETFs/USA": "WisdomTree",
    "Diamond Hill ETFs/USA": "Diamond Hill",
    "JP Morgan ETNs/USA": "JP Morgan",
    "Natixis ETFs/USA": "Natixis",
    "REX ETF Trust": "REX",
    # Series trusts where brand comes from fund name (handled by L2-A, but
    # fallback if fund name parsing fails)
    "EA Series Trust": "EA Series",
    "Tidal Trust I": "Tidal",
    "Tidal Trust II": "Tidal",
    "Tidal Trust III": "Tidal",
    "Tidal Trust IV": "Tidal",
    "Tidal Commodities Trust I": "Tidal",
    "ETF Series Solutions": "ETF Series Solutions",
    "Northern Lights Fund Trust IV": "Northern Lights",
    "Northern Lights Fund Trust III": "Northern Lights",
    "Northern Lights Fund Trust II": "Northern Lights",
    "Northern Lights Fund Trust": "Northern Lights",
    "Spinnaker ETF Trust": "Spinnaker",
    "Unified Series Trust": "Unified Series",
    "Collaborative Investment Serie": "Collaborative Investment",
    "Exchange Listed Funds Trust": "Exchange Listed Funds",
    "Exchange-Traded Concepts Trust": "Exchange-Traded Concepts",
    "FIS Trust": "FIS",
    "Starboard Investment Trust": "Starboard",
    "Trust For Professional Manager": "PMC",
    "Trust for Professional Manager": "PMC",
    "Professionally Managed Portfol": "PMC",
    "Strategy Shares Inc/Fund Paren": "Strategy Shares",
    "Founder Funds Trust": "Founder",
    "Alger ETF Trust/The": "Alger",
    "Alger ETF Trust": "Alger",
    "DFA Investment Dimensions Grou": "Dimensional",
    "Advisors Inner Circle Fund II/": "Advisors Inner Circle",
    "Advisors Inner Circle Fund III": "Advisors Inner Circle",
    "Carillon Series Trust": "Carillon",
    "Spend Life Wisely Funds Invest": "Spend Life Wisely",
    "X-Square Series Trust": "X-Square",
    "Capital Series Trust": "Capital Series",
    "Advisor Managed Portfolios": "Advisor Managed Portfolios",
    "Manager Directed Portfolios": "Manager Directed Portfolios",
    "Managed Portfolio Series": "Managed Portfolio",
    "THOR Financial Technologies Tr": "Thor",
    "NPF Core Equity ETF/Fund Paren": "NPF",
    "Kensington Asset Management ET": "Kensington",
    "Multi Units Luxembourg Sicav": "Amundi",
    "Pacific Asset Management/Paren": "Pacific Asset Management",
    "Valued Advisers Trust/Kovitz I": "Kovitz",
    "Value Advisers Trust/Regan": "Regan Capital",
    "ALPS Series Trust/Fundsmith In": "Fundsmith",
    "TOROSO Newfound Funds/USA": "Newfound",
    "Matrix/Unit Investment Trust/U": "Matrix",
    "2023 ETF Series Trust/The": "2023 ETF Series",
    "Capitol Series Trust": "Capitol Series",
    "Indexperts LLC": "Indexperts",
    "Strategic Trust": "Strategic",
    "Abacus FCF ETF Trust": "Abacus",
    "Abacus FCF ETFs/USA": "Abacus",
    "Guinness Atkinson Funds": "Guinness Atkinson",
    "United States Commodities ETFs": "USCF",
    "FundVantage Trust": "FundVantage",
    "Elevation Series Trust": "Elevation",
    "Corgi ETF Trust": "Corgi",
    "Samsung Asset Management Co Lt": "Samsung",
    "Truth Social Funds": "Truth Social",
    "Investment Managers Series Tru": "Investment Managers Series",
    "RBB ETFs/F/m Investments": "F/m Investments",
    "Capital Group/ETFs": "Capital Group",
    "Capital Group Fixed Income ETF": "Capital Group",
    "Capital Group Equity Etf Trust": "Capital Group",
    "Advisors' Inner Circle Fund II": "Advisors Inner Circle",
    "Listed Funds Trust": "Listed Funds Trust",
    "Franklin Templeton ETF Trust": "Franklin Templeton",
    "Principal Exchange-Traded Fund": "Principal",
    "Morgan Stanley Etf Trust": "Morgan Stanley",
    "ALPS ETF Trust": "ALPS",
    "First Trust Exchange-Traded Fu": "First Trust",
    "Two Roads Shared Trust": "Two Roads",
    "RBB Fund Trust/The": "RBB",
    "RBB Fund Inc/Summit Global Inv": "Summit Global",
    "RBB Fund Inc/F/m Investment LL": "F/m Investments",
}


def match_trust_brand(current_issuer: str) -> str | None:
    """Direct trust-name -> brand lookup (L2-C)."""
    # Exact match
    if current_issuer in _TRUST_TO_BRAND:
        return _TRUST_TO_BRAND[current_issuer]
    # Prefix match (handles truncated values)
    ci_upper = current_issuer.upper()
    for trust, brand in _TRUST_TO_BRAND.items():
        if ci_upper.startswith(trust.upper()[:20]) and len(trust) >= 15:
            return brand
    return None


# ---------------------------------------------------------------------------
# L2-D  EDGAR entity-name brand extraction
# ---------------------------------------------------------------------------

# Series trust keywords — when the entity name matches, use sub-advisor lookup
_SERIES_TRUST_KEYWORDS = [
    "SERIES TRUST", "FUND TRUST", "ETF TRUST", "TIDAL TRUST",
    "INVESTMENT TRUST", "ADVISORS TRUST", "COLLABORATIVE INVESTMENT",
    "LITMAN GREGORY", "NORTHERN LIGHTS",
]

_ENTITY_NAME_BRAND_MAP: dict[str, str] = {
    # Direct issuer entity names that EDGAR would return
    "TEMA ETF TRUST": "Tema",
    "STONE RIDGE TRUST": "Stone Ridge",
    "GABELLI ETFS TRUST": "Gabelli",
    "DAVIS FUNDAMENTAL ETF TRUST": "Davis",
    "THORNBURG ETF TRUST": "Thornburg",
    "HARRIS OAKMARK ETF TRUST": "Harris Oakmark",
    "PRECIDIAN ETFS TRUST": "Precidian",
    "GUINNESS ATKINSON FUNDS": "Guinness Atkinson",
    "FRANKLIN TEMPLETON ETF TRUST": "Franklin Templeton",
    "PRINCIPAL EXCHANGE-TRADED FUND": "Principal",
    "MORGAN STANLEY ETF TRUST": "Morgan Stanley",
    "ALLSPRING FUNDS MANAGEMENT": "Allspring",
    "ALPS ETF TRUST": "ALPS",
    "ARK ETF TRUST": "ARK",
    "SAMSUNG ASSET MANAGEMENT": "Samsung",
    "MADISON FUNDS": "Madison",
    "MILLIMAN FUNDS TRUST": "Milliman",
    "ANGEL OAK FUNDS TRUST": "Angel Oak",
    "PALMER SQUARE FUNDS TRUST": "Palmer Square",
    "PARNASSUS INCOME FUNDS": "Parnassus",
    "TEXAS CAPITAL FUNDS TRUST": "Texas Capital",
    "SP FUNDS TRUST": "SP Funds",
    "CROSSMARK ETF TRUST": "Crossmark",
    "FIRST EAGLE ETF TRUST": "First Eagle",
    "IMPAX FUNDS SERIES TRUST": "Impax",
    "AGF INVESTMENTS TRUST": "AGF",
    "GLOBAL BETA ETF TRUST": "Global Beta",
    "HENNESSY FUNDS TRUST": "Hennessy",
    "KURV ETF TRUST": "Kurv",
    "WEDBUSH SERIES TRUST": "Wedbush",
    "ZACKS TRUST": "Zacks",
    "LITTMAN GREGORY FUNDS TRUST": "Litman Gregory",
    "LITMAN GREGORY FUNDS TRUST": "Litman Gregory",
    "AMPLIFY COMMODITY TRUST": "Amplify",
    "SYMMETRY PANORAMIC TRUST": "Symmetry",
    "SIREN ETF TRUST": "Siren",
    "BARBWATER SHARED TRUST": "Two Roads",
}


def brand_from_entity_name(entity_name: str) -> str | None:
    """Map EDGAR entity name to a clean brand (L2-D)."""
    en_upper = (entity_name or "").upper().strip()
    # Exact match in our map
    if en_upper in _ENTITY_NAME_BRAND_MAP:
        return _ENTITY_NAME_BRAND_MAP[en_upper]
    # Prefix match
    for key, brand in _ENTITY_NAME_BRAND_MAP.items():
        if en_upper.startswith(key) or key.startswith(en_upper[:20]):
            return brand
    # If entity name is NOT a series trust → the entity name itself is the brand
    for kw in _SERIES_TRUST_KEYWORDS:
        if kw in en_upper:
            return None   # it's a series trust; can't derive brand from entity name
    # Entity name is the direct issuer — clean it up
    # Remove common suffixes, title-case
    cleaned = re.sub(
        r"\s*(ETF TRUST|FUND TRUST|FUNDS TRUST|INVESTMENT TRUST|TRUST|FUNDS|FUND|"
        r"LLC|INC|CORP|CO|LTD|LP|PLC|L\.P\.)\s*$",
        "", entity_name.strip(), flags=re.IGNORECASE,
    ).strip()
    if cleaned and len(cleaned) >= 3:
        return cleaned.title()
    return None


# ---------------------------------------------------------------------------
# EDGAR submissions cache
# ---------------------------------------------------------------------------

def load_sponsor_cache() -> dict:
    if SPONSOR_CACHE_JSON.exists():
        try:
            return json.loads(SPONSOR_CACHE_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_sponsor_cache(cache: dict) -> None:
    try:
        SPONSOR_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
        tmp = SPONSOR_CACHE_JSON.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        tmp.replace(SPONSOR_CACHE_JSON)
    except Exception as exc:
        print(f"  WARN: could not save sponsor cache: {exc}")


def cache_is_fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at", "")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age < CACHE_MAX_AGE_DAYS * 86400
    except Exception:
        return False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def load_ticker_cik_map(db_path: Path) -> dict[str, str]:
    """Return {ticker: cik} for tickers in the review queue via mkt_master_data -> trusts join."""
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute("""
        SELECT m.ticker, t.cik
        FROM mkt_master_data m
        JOIN trusts t ON t.name = m.issuer
    """)
    rows = cur.fetchall()
    con.close()
    return {ticker: cik for ticker, cik in rows if ticker and cik}


def load_review_queue(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_existing_overrides(path: Path) -> set[str]:
    """Return set of tickers already in issuer_brand_overrides.csv."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["ticker"] for r in csv.DictReader(fh) if r.get("ticker")}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Layer 2 EDGAR sponsor lookup.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print results without writing files.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N tickers (0 = all).")
    args = ap.parse_args()

    wall_start = time.monotonic()

    print(f"Layer 2 EDGAR sponsor lookup — {datetime.now().isoformat()}")
    print(f"DB:           {DB_PATH}")
    print(f"Review queue: {REVIEW_QUEUE_CSV}")
    print(f"Overrides:    {OVERRIDES_CSV}")

    # Load inputs
    queue = load_review_queue(REVIEW_QUEUE_CSV)
    existing_tickers = load_existing_overrides(OVERRIDES_CSV)
    ticker_cik_map = load_ticker_cik_map(DB_PATH)
    cache = load_sponsor_cache()

    # Filter out already-resolved tickers
    to_process = [r for r in queue if r["ticker"] not in existing_tickers]
    print(f"\nReview queue:     {len(queue):,} total")
    print(f"Already resolved: {len(existing_tickers):,}")
    print(f"To process:       {len(to_process):,}")

    if args.limit > 0:
        to_process = to_process[:args.limit]
        print(f"  (limited to {args.limit})")

    # Import SECClient (deferred so script can be imported without network)
    sys.path.insert(0, str(PROJECT_ROOT))
    from etp_tracker.sec_client import SECClient
    client = SECClient(
        user_agent="REX-ETP-Tracker/2.0 relasmar@rexfin.com",
        pause=0.35,
    )

    new_rows: list[dict] = []
    unresolved: list[dict] = []

    resolved_count = 0
    edgar_fetch_count = 0
    rate_limit_hits = 0

    total = len(to_process)

    for idx, row in enumerate(to_process):
        # Wall clock guard
        elapsed = time.monotonic() - wall_start
        if elapsed > MAX_WALL_SECONDS:
            print(f"\nWall clock limit reached ({MAX_WALL_SECONDS}s). Committing partial progress.")
            break

        ticker = row["ticker"]
        fund_name = row.get("fund_name", "") or ""
        current_issuer = row.get("current_issuer", "") or ""
        etp_category = row.get("etp_category", "") or ""

        brand: str | None = None
        source_detail: str = ""

        # --- L2-A: Extended fund-name regex ---
        brand = match_l2_fund_name(fund_name)
        if brand:
            source_detail = f"L2-A fund_name: {fund_name[:60]}"

        # --- L2-B: Slash-pattern brand extraction ---
        if not brand:
            brand = extract_slash_brand(current_issuer)
            if brand:
                source_detail = f"L2-B slash: {current_issuer}"

        # --- L2-C: Trust-to-brand dict ---
        if not brand:
            brand = match_trust_brand(current_issuer)
            if brand:
                source_detail = f"L2-C trust_dict: {current_issuer}"

        # --- L2-D: EDGAR submissions JSON ---
        if not brand:
            cik = ticker_cik_map.get(ticker)
            if cik:
                # Check / populate cache
                cache_entry = cache.get(str(cik), {})
                entity_name = cache_entry.get("entity_name")
                if not cache_is_fresh(cache_entry):
                    # Fetch from EDGAR
                    elapsed_now = time.monotonic() - wall_start
                    if elapsed_now > MAX_WALL_SECONDS - 60:
                        # Too close to time limit — skip EDGAR fetch
                        pass
                    else:
                        try:
                            time.sleep(REQUEST_PAUSE)
                            data = client.load_submissions_json(
                                cik,
                                refresh_submissions=True,
                                refresh_max_age_hours=720,
                            )
                            entity_name = data.get("name", "")
                            cache[str(cik)] = {
                                "entity_name": entity_name,
                                "fetched_at": datetime.now(timezone.utc).isoformat(),
                            }
                            edgar_fetch_count += 1
                        except Exception as exc:
                            err_str = str(exc)
                            if "429" in err_str or "503" in err_str:
                                rate_limit_hits += 1
                                print(f"  RATE-LIMIT hit for CIK {cik}, backing off 30s...")
                                time.sleep(30)
                                try:
                                    data = client.load_submissions_json(
                                        cik,
                                        refresh_submissions=True,
                                        refresh_max_age_hours=720,
                                    )
                                    entity_name = data.get("name", "")
                                    cache[str(cik)] = {
                                        "entity_name": entity_name,
                                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                                    }
                                    edgar_fetch_count += 1
                                except Exception:
                                    entity_name = None
                            else:
                                entity_name = None

                if entity_name:
                    brand = brand_from_entity_name(entity_name)
                    if brand:
                        source_detail = f"L2-D EDGAR entity: {entity_name}"

        # --- Record result ---
        if brand:
            new_rows.append({
                "ticker": ticker,
                "issuer_display": brand,
                "source": SOURCE_TAG,
                "notes": source_detail,
            })
            resolved_count += 1
        else:
            row_copy = dict(row)
            row_copy["layer_2_attempted"] = "true"
            unresolved.append(row_copy)

        # Progress log every BATCH_SIZE
        if (idx + 1) % BATCH_SIZE == 0:
            elapsed_s = int(time.monotonic() - wall_start)
            residue = total - resolved_count - len([r for r in unresolved if r.get("layer_2_attempted")])
            print(
                f"  Processed {idx+1}/{total}, "
                f"resolved {resolved_count}, "
                f"unresolved {len(unresolved)}, "
                f"EDGAR fetches {edgar_fetch_count}, "
                f"elapsed {elapsed_s}s"
            )
            # Save cache checkpoint
            if not args.dry_run:
                save_sponsor_cache(cache)

    # Also add not-processed entries (hit time limit) with layer_2_attempted=false
    processed_tickers = {r["ticker"] for r in new_rows} | {r["ticker"] for r in unresolved}
    for row in to_process:
        if row["ticker"] not in processed_tickers:
            row_copy = dict(row)
            row_copy["layer_2_attempted"] = "false"
            unresolved.append(row_copy)

    # Tickers that were already resolved (not in to_process) also go into residue
    # but without layer_2_attempted column change
    already_done_tickers = {r["ticker"] for r in queue if r["ticker"] in existing_tickers}

    print(f"\n--- Layer 2 Complete ---")
    print(f"Total processed:     {len(to_process):,}")
    print(f"Resolved:            {resolved_count:,}")
    print(f"Unresolved residue:  {len(unresolved):,}")
    print(f"EDGAR fetches:       {edgar_fetch_count:,}")
    print(f"Rate limit hits:     {rate_limit_hits:,}")
    print(f"Wall time:           {int(time.monotonic() - wall_start)}s")

    if args.dry_run:
        print("\n[DRY-RUN] First 20 resolved:")
        for r in new_rows[:20]:
            print(f"  {r['ticker']:15s} -> {r['issuer_display']:30s}  ({r['notes'][:60]})")
        print("\nFirst 10 unresolved:")
        for r in unresolved[:10]:
            print(f"  {r['ticker']:15s}  issuer={r.get('current_issuer', '')}")
        return 0

    # --- Append to issuer_brand_overrides.csv ---
    if new_rows:
        with OVERRIDES_CSV.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["ticker", "issuer_display", "source", "notes"]
            )
            writer.writerows(new_rows)
        print(f"\nAppended {len(new_rows):,} rows -> {OVERRIDES_CSV}")

    # --- Update issuer_review_queue.csv ---
    # Keep rows that are still unresolved; add layer_2_attempted column
    # Also preserve original rows that were already in existing_overrides (not re-processed)
    new_queue_rows = []
    resolved_tickers = {r["ticker"] for r in new_rows}

    # Rows that were NOT in to_process (already resolved or filtered) — drop them
    # Unresolved rows get layer_2_attempted annotation
    final_fieldnames = ["ticker", "fund_name", "etp_category",
                        "current_issuer", "suggested_brand", "layer_2_attempted"]
    for r in unresolved:
        new_queue_rows.append({
            "ticker": r["ticker"],
            "fund_name": r.get("fund_name", ""),
            "etp_category": r.get("etp_category", ""),
            "current_issuer": r.get("current_issuer", ""),
            "suggested_brand": r.get("suggested_brand", ""),
            "layer_2_attempted": r.get("layer_2_attempted", "false"),
        })

    with REVIEW_QUEUE_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=final_fieldnames)
        writer.writeheader()
        writer.writerows(new_queue_rows)
    print(f"Updated review queue: {len(new_queue_rows):,} remaining -> {REVIEW_QUEUE_CSV}")

    # Save final cache
    save_sponsor_cache(cache)

    # Print source breakdown
    from collections import Counter
    source_breakdown = Counter()
    for r in new_rows:
        notes = r.get("notes", "")
        if notes.startswith("L2-A"):
            source_breakdown["L2-A fund_name"] += 1
        elif notes.startswith("L2-B"):
            source_breakdown["L2-B slash"] += 1
        elif notes.startswith("L2-C"):
            source_breakdown["L2-C trust_dict"] += 1
        elif notes.startswith("L2-D"):
            source_breakdown["L2-D EDGAR"] += 1
        else:
            source_breakdown["other"] += 1

    print("\nResolved by source:")
    for src, cnt in source_breakdown.most_common():
        print(f"  {src}: {cnt}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

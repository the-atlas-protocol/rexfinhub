"""Audit Defined Outcome and Thematic category mappings in mkt_master_data.

Defined Outcome (etp_category='Defined'): audits map_defined_category.
    Expected values derived from fund name keywords:
        Buffer, Dual Buffer, Ladder, Floor, Accelerator,
        Barrier, Outcome, Hedged Equity, Defined Volatility, Defined Risk

Thematic (etp_category='Thematic'): audits map_thematic_category.
    Expected values derived from well-known theme keywords in the fund name
    (AI/DEFENSE/SPACE/GENOMICS/CLEAN ENERGY/EV/CYBER, etc.)

Output: docs/defined_thematic_underlier_audit_2026-05-05.csv

Columns:
    category      -- DB etp_category ('Defined' or 'Thematic')
    ticker        -- Bloomberg ticker
    fund_name     -- Full fund name
    current_map   -- Current value of map_defined_category or map_thematic_category
    expected      -- Category string extracted from fund name
    status        -- OK | MISMATCH | UNCLEAR
    confidence    -- HIGH | MEDIUM | LOW

This script is READ-ONLY -- it never modifies the database.
Run apply_underlier_overrides.py to persist high-confidence fixes.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OUTPUT_CSV = PROJECT_ROOT / "docs" / "defined_thematic_underlier_audit_2026-05-05.csv"


# ===========================================================================
# DEFINED OUTCOME
# ===========================================================================

# Rules for map_defined_category inference from fund name.
# Evaluated in order; first match wins.
# Each entry: (compiled_pattern, expected_value, confidence)
_DEFINED_RULES: list[tuple[re.Pattern, str, str]] = [
    # Dual Directional Buffer (check before plain Buffer)
    (re.compile(r"\bDUAL\s+DIRECTIONAL\b", re.IGNORECASE), "Dual Buffer", "HIGH"),
    (re.compile(r"\bDUAL\s+BUFFER\b",       re.IGNORECASE), "Dual Buffer", "HIGH"),

    # Laddered funds (check before Buffer because some say "LADDERED BUFFER")
    (re.compile(r"\bLADDERED\b", re.IGNORECASE), "Ladder", "HIGH"),

    # Floor funds (check before Buffer because some may say "FLOOR BUFFER")
    (re.compile(r"\bFLOOR\b",    re.IGNORECASE), "Floor",  "HIGH"),

    # Barrier funds
    (re.compile(r"\bBARRIER\b",  re.IGNORECASE), "Barrier", "HIGH"),

    # Buffer (including BUFFER100, BUFFER10, BUFFER20, etc.)
    (re.compile(r"\bBUFFER(?:\d+)?\b|\bBUFF(?:ER)?\b", re.IGNORECASE), "Buffer", "HIGH"),

    # Accelerator / Accelerated
    (re.compile(r"\bACCELERAT(?:ED|OR)\b", re.IGNORECASE), "Accelerator", "HIGH"),

    # Hedged Equity (explicit hedge strategy, not buffer)
    (re.compile(r"\bHEDGED\s+EQUITY\b|\bHEDGE\s+ETF\b", re.IGNORECASE), "Hedged Equity", "HIGH"),

    # Defined Volatility (WEBS suite)
    (re.compile(r"\bDEFINED\s+VOLATILITY\b", re.IGNORECASE), "Defined Volatility", "HIGH"),

    # Defined Risk
    (re.compile(r"\bDEFINED\s+RISK\b", re.IGNORECASE), "Defined Risk", "HIGH"),

    # Outcome / Structured Outcome
    (re.compile(r"\bSTRUCTURED\s+OUTCOME\b|\bDEFINED\s+OUTCOME\b|\bOUTCOME\b",
                re.IGNORECASE), "Outcome", "MEDIUM"),

    # Defined Protection (Innovator's 2-year product) - classify as Buffer
    (re.compile(r"\bDEFINED\s+PROTECTION\b", re.IGNORECASE), "Buffer", "HIGH"),

    # Wealth Shield, Defined Wealth - classify as Buffer (capital protection focus)
    (re.compile(r"\bDEFINED\s+WEALTH\b|\bWEALTH\s+SHIELD\b", re.IGNORECASE), "Buffer", "MEDIUM"),
]

# Known overrides for Defined funds where the name is ambiguous
_DEFINED_OVERRIDES: dict[str, tuple[str, str]] = {
    # BSTP - "BUFFER STEP-UP STRATEGY ETF" - Buffer by design
    "BSTP US": ("Buffer", "HIGH"),
    # AIOO - "ALLIANZIM EQUITY BUFFER100 PROTECTION ETF" - Buffer100 = full protection Buffer
    "AIOO US": ("Buffer", "HIGH"),
}


def _extract_defined_expected(fund_name: str) -> tuple[str | None, str]:
    """Return (expected_category, confidence) for a Defined fund."""
    fn_upper = fund_name.upper()
    for pattern, expected_val, confidence in _DEFINED_RULES:
        if pattern.search(fn_upper):
            return expected_val, confidence
    return None, ""


# ===========================================================================
# THEMATIC
# ===========================================================================

# Rules for map_thematic_category inference from fund name.
# Evaluated in order; first match wins.
# Each entry: (compiled_pattern, expected_value, confidence)
_THEMATIC_RULES: list[tuple[re.Pattern, str, str]] = [
    # Defense (check BEFORE Space and AI to avoid AEROSPACE misrouting)
    (re.compile(
        r"\bDEFENSE\b|\bDEFENSE\s+&\b|\b&\s+DEFENSE\b|"
        r"\bAEROSPACE\s+&\s+DEFENSE\b|\bDEFENSE\s+INNOVATION\b",
        re.IGNORECASE), "Defense", "HIGH"),

    # Space (explicit space / satellite themes -- NOT aerospace/defense hybrids)
    (re.compile(r"\bSPACE\b(?!\s*&\s*DEFENSE)|\bSATELLITE\b", re.IGNORECASE), "Space", "HIGH"),

    # Artificial Intelligence / Machine Learning
    (re.compile(
        r"\bARTIFICIAL\s+INTELLIGENCE\b|\bGENERATIVE\s+AI\b|"
        r"\bAI\s+(?:ETF|POWERED|INNOVATION|ENABLER|ADOPTION|ENHANCED|INFLECTION|VALUE\s+CHAIN)\b|"
        r"\b(?:BLOOMBERG|BLOOMBERG\s+)?AI\b",
        re.IGNORECASE), "Artificial Intelligence", "HIGH"),

    # Clean Energy / Renewable -- explicit clean/renewable energy labels only.
    # ELECTRIFICATION alone is ambiguous; require CLEAN ENERGY or CLEAN POWER.
    (re.compile(
        r"\bCLEAN\s+ENERGY\b|\bCLEAN\s+POWER\b|\bSOLAR\b|\bWIND\s+ENERGY\b|"
        r"\bCLIMATETECH\b|\bCLEANTECH\b|\bHYDROGEN\b|\bRENEWABLE\b",
        re.IGNORECASE), "Clean Energy", "HIGH"),

    # Electric Vehicles and Battery Technology
    (re.compile(
        r"\bELECTRIC\s+(?:CAR|VEHICLE|VEH)\b|\bBATTERY\b|"
        r"\bSMART\s+MOBILITY\b|\bROBOTAXI\b|"
        r"\bFUTURE\s+(?:VEHICLE|TRANSPORT)\b",
        re.IGNORECASE), "Electric Car & Battery", "HIGH"),

    # Cybersecurity
    (re.compile(r"\bCYBERSECURITY\b|\bCYBER\b", re.IGNORECASE), "Cybersecurity", "HIGH"),

    # Blockchain & Crypto (NOT the same as thematic Crypto products in Crypto category)
    (re.compile(
        r"\bBLOCKCHAIN\b|\bCRYPTO\s+INDUSTRY\b|\bWEB3\b|\bDEFI\b|\bONCHAIN\b|"
        r"\bDIGITAL\s+ASSET\s+ECOSYSTEM\b|\bNEXGEN\s+ECONOMY\b",
        re.IGNORECASE), "Blockchain & Crypto", "HIGH"),

    # Robotics & Automation
    (re.compile(
        r"\bROBOTICS\b|\bAUTOMATION\b|\bHUMANOID\b",
        re.IGNORECASE), "Robotics & Automation", "HIGH"),

    # Infrastructure -- explicit infrastructure / data center labels
    (re.compile(
        r"\bINFRASTRUCTURE\b|\bDATA\s+CENTER\b|\bSMART\s+GRID\b|\bMLPI\b",
        re.IGNORECASE), "Infrastructure", "HIGH"),

    # Cloud Computing -- explicit cloud / cloud computing only
    (re.compile(r"\bCLOUD\s+COMPUTING\b|\bCLOUD\b", re.IGNORECASE), "Cloud Computing", "HIGH"),

    # Genomics / Biotechnology
    (re.compile(
        r"\bGENOMIC\b|\bGENOMICS\b|\bBIOTECHNOLOGY\b|\bONCOLOGY\b|"
        r"\bGENE\b|\bGENETIC\b|\bLIFE\s+SCIENCE",
        re.IGNORECASE), "Healthcare", "HIGH"),

    # Healthcare (general)
    (re.compile(
        r"\bHEALTH(?:CARE|TECH)?\b|\bMEDICAL\b|\bMEDICINE\b|"
        r"\bPHARMA\b|\bBIOTECH\b",
        re.IGNORECASE), "Healthcare", "HIGH"),

    # FinTech
    (re.compile(r"\bFINTECH\b|\bFINANCIAL\s+TECHNOLOGY\b|\bPAYMENT\b",
                re.IGNORECASE), "FinTech", "HIGH"),

    # Cannabis and Psychedelics
    (re.compile(r"\bCANNABIS\b|\bMARIJUANA\b|\bPSYCHEDELIC\b", re.IGNORECASE),
     "Cannabis and Psychedelics", "HIGH"),

    # Sports & Esports -- explicit sports/betting/gaming/esports labels
    # Note: GAMING alone is too broad; require SPORTS or ESPORTS alongside.
    (re.compile(
        r"\bSPORTS\s+BETTING\b|\bSPORTS\b|\bESPORTS?\b|\bIGAMING\b|"
        r"\bVIDEO\s+GAM(?:E|ING)\b",
        re.IGNORECASE), "Sports & Esports", "HIGH"),

    # 5G / Connectivity -- explicit 5G label only (CONNECTIVITY alone is too broad)
    (re.compile(r"\b5G\b", re.IGNORECASE), "5G", "HIGH"),

    # Natural Resources -- explicit labels
    (re.compile(
        r"\bNATURAL\s+RESOURCES?\b|\bTIMBER\b|\bFORESTRY\b|\bAGRICULTUR(?:E|AL)\b|"
        r"\bCOMMODIT(?:Y|IES)\b",
        re.IGNORECASE), "Natural Resources", "HIGH"),

    # Water -- explicit water label (check before Environment)
    (re.compile(r"\bCLEAN\s+WATER\b|\bWATER\b", re.IGNORECASE), "Water", "HIGH"),

    # Low Carbon -- explicit low-carbon / Paris-aligned labels
    (re.compile(
        r"\bLOW\s+CARBON\b|\bCARBON\s+(?:REDUCTION|NEUTRAL|LEADER)\b|"
        r"\bPARIS\s+ALIGNED\b|\bNET\s+ZERO\b|\bNET-ZERO\b",
        re.IGNORECASE), "Low Carbon", "HIGH"),

    # Environment / ESG -- broad climate/sustainability (lower specificity)
    (re.compile(
        r"\bENVIRONMENT(?:AL)?\b|\bSUSTAIN(?:ABLE|ABILITY)\b|\bCLIMATE\b|"
        r"\bESG\b|\bCARBON\b",
        re.IGNORECASE), "Environment", "MEDIUM"),

    # Inflation
    (re.compile(r"\bINFLATION\b", re.IGNORECASE), "Inflation", "HIGH"),

    # Nuclear
    (re.compile(r"\bNUCLEAR\b|\bURANIUM\b", re.IGNORECASE), "Nuclear", "HIGH"),

    # IPO & SPAC
    (re.compile(r"\bIPO\b|\bSPAC\b|\bSPIN-OFF\b", re.IGNORECASE), "IPO & SPAC", "HIGH"),

    # E-Commerce
    (re.compile(r"\bE-COMMERCE\b|\bECOMMERCE\b|\bDISRUPTIVE\s+COMMERCE\b",
                re.IGNORECASE), "E-Commerce", "HIGH"),

    # Travel, Vacation & Leisure
    (re.compile(r"\bTRAVEL\b|\bHOTEL\b|\bRESTAURANT\b|\bLEISURE\b",
                re.IGNORECASE), "Travel, Vacation & Leisure", "HIGH"),

    # Corporate Culture / ESG-Social -- explicit corporate culture labels
    (re.compile(
        r"\bCORPORATE\s+CULTURE\b|\bHUMAN\s+CAPITAL\b|\bDEMOCRAC\b",
        re.IGNORECASE), "Corporate Culture", "HIGH"),

    # EM Tech
    (re.compile(r"\bEMERGING\s+MARKET(?:S)?\s+(?:INTERNET|TECH|DIGITAL)\b",
                re.IGNORECASE), "EM Tech", "MEDIUM"),
]

# Known overrides for Thematic funds where fund name is ambiguous or misleading.
# Key: ticker, Value: (correct_expected, confidence)
# When an override is set, we use it directly rather than running keyword rules.
_THEMATIC_OVERRIDES: dict[str, tuple[str, str]] = {
    # COPY US - "TWEEDY BROWNE INSIDER + VALUE ETF" -- INSIDER triggered Corporate
    # Culture rule but this is a quantitative value strategy with no thematic focus.
    "COPY US": ("Strategy", "HIGH"),
    # GCAD US - "GABELLI COMMERCIAL AEROSPACE AND DEFENSE ETF" -- currently Space,
    # but the fund explicitly focuses on commercial aerospace & defense contractors.
    "GCAD US": ("Defense", "HIGH"),
    # DTCR US - "DATA CENTER & DIGITAL INFRASTRUCTURE" -- currently 5G; data centers
    # are infrastructure, not 5G connectivity.
    "DTCR US": ("Infrastructure", "HIGH"),
    # JEDI US - "DEFIANCE DRONE AND MODERN WARFARE ETF" -- null; drone warfare = Defense.
    "JEDI US": ("Defense", "HIGH"),
    # NASA US - stored as 'Space & Aerospace' -- the canonical thematic label is 'Space'.
    "NASA US": ("Space", "HIGH"),
    # SPCI US - stored as 'Space & Aerospace' -- canonical label is 'Space'.
    "SPCI US": ("Space", "HIGH"),
    # ZSC US - "USCF SUSTAINABLE COMMODITY STRATEGY FUND" -- stored as Clean Energy but
    # this is a broad commodity fund (oil, metals, ag); Natural Resources is correct.
    "ZSC US": ("Natural Resources", "HIGH"),
    # LFSC US - "F/M EMERALD LIFE SCIENCES INNOVATION ETF" -- stored as General Thematic;
    # Life Sciences = biotech/pharma = Healthcare.
    "LFSC US": ("Healthcare", "HIGH"),
    # KROP US - "GLOBAL X AGTECH & FOOD INNOVATION" -- stored as 'Future of Food', which
    # is the product's own sub-taxonomy. Accept as OK (not a mismatch worth correcting).
    "KROP US": ("Future of Food", "HIGH"),
    # ELFY US - "ALPS ELECTRIFICATION INFRASTRUCTURE ETF" -- Electrification fires
    # Clean Energy rule but fund explicitly calls itself Infrastructure.
    "ELFY US": ("Infrastructure", "HIGH"),
    # DRNZ US - "REX DRONE ETF" -- 'Drones' is the correct existing category label.
    "DRNZ US": ("Drones", "HIGH"),
    # EFRA US - "ISHARES ENVIRONMENTAL INFRASTRUCTURE AND INDUSTRIALS" -- dual theme;
    # Current 'Environment' is acceptable -- do not flag as MISMATCH.
    "EFRA US": ("Environment", "HIGH"),
    # ARKF US - "ARK BLOCKCHAIN & FINTECH INNOVATION" -- dual theme. FinTech is
    # ARK's own classification and is a legitimate primary theme. Accept current.
    "ARKF US": ("FinTech", "HIGH"),
    # ARKX US - "ARK SPACE & DEFENSE INNOVATION" -- dual theme. Space is ARK's
    # classification. Accept current.
    "ARKX US": ("Space", "HIGH"),
    # BOTZ US - "GLOBAL X ROBOTICS & ARTIFICIAL INTELLIGENCE" -- dual theme.
    # Robotics & Automation is the fund's lead branding. Accept current.
    "BOTZ US": ("Robotics & Automation", "HIGH"),
    # CABZ US - "ROUNDHILL ROBOTAXI AUTONOMOUS VEHICLES" -- Robotaxi = autonomous EV,
    # but Robotics & Automation is also defensible. Accept current.
    "CABZ US": ("Robotics & Automation", "HIGH"),
    # CCSO US - "CARBON COLLECTIVE CLIMATE SOLUTIONS" -- Low Carbon is more specific
    # than Environment. Accept current Low Carbon.
    "CCSO US": ("Low Carbon", "HIGH"),
    # ETHO US - "AMPLIFY ETHO CLIMATE LEADERSHIP" -- Low Carbon is more specific.
    # Accept current.
    "ETHO US": ("Low Carbon", "HIGH"),
    # FTAG US - "FIRST TRUST GLOBAL AGRICULTURE ETF" -- Agriculture is a more specific
    # sub-category within Natural Resources. Accept current.
    "FTAG US": ("Agriculture", "HIGH"),
    # ROBT US - "NASDAQ ARTIFICIAL INTELLIGENCE AND ROBOTICS" -- dual theme.
    # Robotics & Automation is the fund's lead brand. Accept current.
    "ROBT US": ("Robotics & Automation", "HIGH"),
    # SOLR US - "GUINNESS ATKINSON SUSTAINABLE ENERGY ETF" -- fund focuses on clean /
    # sustainable energy equities. Clean Energy is correct. Accept current.
    "SOLR US": ("Clean Energy", "HIGH"),
    # VEGI US - "ISHARES MSCI AGRICULTURE PRODUCERS" -- Agriculture is a distinct
    # sub-category. Accept current.
    "VEGI US": ("Agriculture", "HIGH"),
}


def _extract_thematic_expected(fund_name: str) -> tuple[str | None, str]:
    """Return (expected_category, confidence) for a Thematic fund."""
    fn_upper = fund_name.upper()
    for pattern, expected_val, confidence in _THEMATIC_RULES:
        if pattern.search(fn_upper):
            return expected_val, confidence
    return None, ""


# ===========================================================================
# Shared helpers
# ===========================================================================

def _categories_equivalent(current: str | None, expected: str | None) -> bool:
    """Case-insensitive comparison for category label strings."""
    if not current and not expected:
        return True
    if not current or not expected:
        return False
    return current.strip().lower() == expected.strip().lower()


def _classify(
    current: str | None,
    expected: str | None,
    confidence: str,
) -> str:
    """Assign status: OK, MISMATCH, or UNCLEAR."""
    if expected is None or confidence in ("", "LOW"):
        return "UNCLEAR"
    if _categories_equivalent(current, expected):
        return "OK"
    return "MISMATCH"


# ===========================================================================
# Audit runners
# ===========================================================================

def audit_defined(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, map_defined_category
        FROM mkt_master_data
        WHERE etp_category  = 'Defined'
          AND market_status = 'ACTV'
        ORDER BY ticker
    """)
    rows = cur.fetchall()

    results: list[dict] = []
    for ticker, fund_name, current_map in rows:
        fund_name = fund_name or ""

        if ticker in _DEFINED_OVERRIDES:
            expected, confidence = _DEFINED_OVERRIDES[ticker]
            status = "OK" if _categories_equivalent(current_map, expected) else "MISMATCH"
        else:
            expected, confidence = _extract_defined_expected(fund_name)
            status = _classify(current_map, expected, confidence)

        results.append({
            "category":    "Defined",
            "ticker":      ticker,
            "fund_name":   fund_name,
            "current_map": current_map or "",
            "expected":    expected or "",
            "status":      status,
            "confidence":  confidence or "LOW",
        })
    return results


def audit_thematic(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, map_thematic_category
        FROM mkt_master_data
        WHERE etp_category  = 'Thematic'
          AND market_status = 'ACTV'
        ORDER BY ticker
    """)
    rows = cur.fetchall()

    results: list[dict] = []
    for ticker, fund_name, current_map in rows:
        fund_name = fund_name or ""

        if ticker in _THEMATIC_OVERRIDES:
            expected, confidence = _THEMATIC_OVERRIDES[ticker]
            status = "OK" if _categories_equivalent(current_map, expected) else "MISMATCH"
        else:
            expected, confidence = _extract_thematic_expected(fund_name)
            status = _classify(current_map, expected, confidence)

        results.append({
            "category":    "Thematic",
            "ticker":      ticker,
            "fund_name":   fund_name,
            "current_map": current_map or "",
            "expected":    expected or "",
            "status":      status,
            "confidence":  confidence or "LOW",
        })
    return results


def write_csv(results: list[dict]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["category", "ticker", "fund_name", "current_map", "expected", "status", "confidence"]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)


def _print_summary(label: str, results: list[dict]) -> None:
    total    = len(results)
    ok       = sum(1 for r in results if r["status"] == "OK")
    mismatch = sum(1 for r in results if r["status"] == "MISMATCH")
    unclear  = sum(1 for r in results if r["status"] == "UNCLEAR")
    mismatch_high   = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "HIGH")
    mismatch_medium = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "MEDIUM")

    print(f"Total {label} ACTV products : {total}")
    print(f"  OK       : {ok}")
    print(f"  MISMATCH : {mismatch}  (HIGH={mismatch_high}, MEDIUM={mismatch_medium})")
    print(f"  UNCLEAR  : {unclear}")
    print()
    print(f"MISMATCH details ({label}):")
    for r in results:
        if r["status"] == "MISMATCH":
            print(
                f"  [{r['confidence']:6s}] {r['ticker']:14s}  "
                f"current={r['current_map'] or '(null)':30s}  "
                f"expected={r['expected']}"
            )
    print()


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    print(f"Auditing Defined + Thematic mappings in {DB_PATH}")
    print()

    defined_results   = audit_defined(con)
    thematic_results  = audit_thematic(con)
    con.close()

    all_results = defined_results + thematic_results

    _print_summary("Defined",   defined_results)
    _print_summary("Thematic",  thematic_results)

    write_csv(all_results)
    print(f"Audit written to: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

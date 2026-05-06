"""Audit CC (Covered Call) underlier mappings in mkt_master_data.

Pulls all ACTV CC products, derives the expected underlier from the fund
name via regex + alias expansion, then compares against the current
map_cc_underlier value stored in the DB.

Output: docs/cc_underlier_audit.csv

Columns:
    ticker        - Bloomberg ticker (e.g. 'APLY US')
    fund_name     - Full fund name as loaded from Bloomberg
    current_map   - Current value of map_cc_underlier (may be None)
    expected      - Ticker extracted / resolved from the fund name
    status        - OK | MISMATCH | UNCLEAR
    confidence    - HIGH | MEDIUM | LOW

Status semantics:
    OK        - current_map matches expected after normalisation.  Normalisation
                strips the trailing ' US' suffix before comparing so that
                Bloomberg format variants ('CVNA' vs 'CVNA US') do not produce
                false positives.
    MISMATCH  - regex confidently extracted a ticker and the base ticker
                differs from current (e.g. SQ US vs expected XYZ US), OR the
                current value uses an invalid format (e.g. raw 'BTC' instead
                of a Bloomberg equity ticker, or self-referential 'TLTX UA').
    UNCLEAR   - regex could not extract a clear single-stock ticker (multi-
                underlier funds, broad index income strategies, etc.)

Confidence semantics:
    HIGH      - single stock ticker found verbatim in the fund name
    MEDIUM    - resolved via alias dict or crypto product mapping

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
OUTPUT_CSV = PROJECT_ROOT / "docs" / "cc_underlier_audit.csv"

# ---------------------------------------------------------------------------
# Alias table -- map raw word extracted from fund name -> canonical Bloomberg
# equity ticker for the underlier.  Only single-stock / index underliers that
# appear in CC fund names are included here.
# ---------------------------------------------------------------------------
CC_WORD_ALIASES: dict[str, str | None] = {
    # Single stocks
    "AAPL": "AAPL US",
    "AMD": "AMD US",
    "AMZN": "AMZN US",
    "ARM": "ARM US",
    "AVGO": "AVGO US",
    "BABA": "BABA US",
    "COIN": "COIN US",
    "CRWV": "CRWV US",
    "CVNA": "CVNA US",
    "GOOG": "GOOG US",
    "GOOGL": "GOOGL US",
    "HOOD": "HOOD US",
    "META": "META US",
    "MSTR": "MSTR US",
    "MU": "MU US",
    "NFLX": "NFLX US",
    "NVDA": "NVDA US",
    "PLTR": "PLTR US",
    "RBLX": "RBLX US",
    "SHOP": "SHOP US",
    "SMCI": "SMCI US",
    "SNOW": "SNOW US",
    "SOFI": "SOFI US",
    "SQ": "SQ US",
    "TSM": "TSM US",
    "TSLA": "TSLA US",
    "UBER": "UBER US",
    "XYZ": "XYZ US",
    "BRKB": "BRK/B US",  # Berkshire Class B -- Bloomberg stores as BRK/B US
    # Index ETFs commonly used as CC underliers
    "SPY": "SPY US",
    "QQQ": "QQQ US",
    "IWM": "IWM US",
    "TLT": "TLT US",
    "LQD": "LQD US",
    "HYG": "HYG US",
    "GLD": "GLD US",
    "SLV": "SLV US",
    "GDX": "GDX US",
    # Crypto ETF wrappers (raw coin abbreviations are NOT valid Bloomberg equity
    # tickers; the underlier for a CC strategy on Bitcoin should be the spot ETF)
    "IBIT": "IBIT US",
    "ETHA": "ETHA US",
    # Raw crypto tokens -- flag as invalid format (None means "not a valid equity ticker")
    "BTC": None,
    "ETH": None,
}

# ---------------------------------------------------------------------------
# Regex patterns for extracting the underlier ticker from the fund name.
# Evaluated in order; first match wins.
# ---------------------------------------------------------------------------

def _build_patterns() -> list[tuple[re.Pattern, callable]]:
    patterns: list[tuple[re.Pattern, callable]] = []

    # YieldMax: "YIELDMAX NVDA OPTION INCOME STRATEGY ETF"
    #           "YIELDMAX N100 SHORT OPTION INCOME STRATEGY ETF"
    yieldmax = re.compile(
        r"YIELDMAX\s+([A-Z0-9]{1,6})\s+(?:OPTION\s+INCOME|N100\b)",
        re.IGNORECASE,
    )
    patterns.append((yieldmax, lambda m: m.group(1).strip()))

    # GraniteShares YieldBoost: "GRANITESHARES YIELDBOOST SPY ETF"
    yieldboost = re.compile(
        r"GRANITESHARES\s+YIELDBOOST\s+([A-Z0-9]{1,6})\s+ETF",
        re.IGNORECASE,
    )
    patterns.append((yieldboost, lambda m: m.group(1).strip()))

    # Roundhill WeeklyPay: "ROUNDHILL NVDA WEEKLYPAY ETF"
    roundhill = re.compile(
        r"ROUNDHILL\s+([A-Z0-9]{1,6})\s+WEEKLYPAY\s+ETF",
        re.IGNORECASE,
    )
    patterns.append((roundhill, lambda m: m.group(1).strip()))

    # Kurv: "KURV YIELD PREMIUM STRATEGY AMAZON AMZN ETF/DE"
    # The ticker appears as the last word before ETF
    kurv = re.compile(
        r"KURV\s+YIELD\s+PREMIUM\s+STRATEGY\s+\w+\s+([A-Z0-9]{1,6})\s+ETF",
        re.IGNORECASE,
    )
    patterns.append((kurv, lambda m: m.group(1).strip()))

    # Amplify X% Monthly: "AMPLIFY BITCOIN 2% MONTHLY OPTION INCOME ETF"
    amplify = re.compile(
        r"AMPLIFY\s+([A-Z0-9]+)\s+\d+%\s+MONTHLY",
        re.IGNORECASE,
    )
    patterns.append((amplify, lambda m: m.group(1).strip()))

    return patterns


_PATTERNS = _build_patterns()

# ---------------------------------------------------------------------------
# Ticker normalisation helpers
# ---------------------------------------------------------------------------

def _base_ticker(value: str | None) -> str:
    """Return the base ticker without exchange/type suffix for loose comparison."""
    if not value:
        return ""
    v = value.strip()
    # Strip Bloomberg type suffix
    v = re.sub(r"\s+(Curncy|Comdty|Equity|Index)$", "", v, flags=re.IGNORECASE)
    # Strip trailing exchange code: ' US', ' UA', ' UW', etc.
    v = re.sub(r"\s+[A-Z]{2,3}$", "", v)
    return v.upper()


def _tickers_equivalent(a: str | None, b: str | None) -> bool:
    """True when both tickers refer to the same underlier (base-ticker match)."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    if a.strip().upper() == b.strip().upper():
        return True
    return _base_ticker(a) == _base_ticker(b)


def _is_invalid_format(value: str | None) -> bool:
    """True when value is a raw ticker with no exchange suffix, or self-referential."""
    if not value:
        return False
    v = value.strip()
    # Self-referential: ends in ' UA' or ' UW' (ETF's own ticker suffix, not the underlier)
    if re.search(r"\s+U[AW]$", v):
        return True
    # Raw crypto without exchange suffix: purely uppercase letters, no space, 2-10 chars
    if re.fullmatch(r"[A-Z]{2,10}", v):
        return True
    return False


# ---------------------------------------------------------------------------
# Extraction logic
# ---------------------------------------------------------------------------

def _resolve_raw(raw: str) -> tuple[str | None, str]:
    """Return (canonical_ticker, confidence) for a raw word from the fund name."""
    upper = raw.strip().upper()
    if upper in CC_WORD_ALIASES:
        val = CC_WORD_ALIASES[upper]
        if val is None:
            return (None, "MEDIUM")  # raw crypto token -- not a valid equity ticker
        return (val, "HIGH")
    # Looks like a plain uppercase ticker
    if re.fullmatch(r"[A-Z]{1,6}[0-9]?", upper):
        return (upper + " US", "HIGH")
    return (None, "")


def _resolve_amplify_crypto(raw: str) -> tuple[str | None, str]:
    """Special resolution for Amplify crypto product underliers."""
    upper = raw.strip().upper()
    if upper in ("BITCOIN", "BTC"):
        return ("IBIT US", "MEDIUM")
    if upper in ("ETHEREUM", "ETH", "ETHER"):
        return ("ETHA US", "MEDIUM")
    if upper == "XRP":
        return ("XRP US", "MEDIUM")
    if upper in ("SOLANA", "SOL"):
        return ("FSOL US", "MEDIUM")
    return _resolve_raw(raw)


def _extract_expected(fund_name: str) -> tuple[str | None, str]:
    """Return (expected_ticker, confidence) or (None, '') if no pattern matched."""
    fn_upper = fund_name.upper()
    pat_list = _PATTERNS
    for i, (pattern, extractor_fn) in enumerate(pat_list):
        m = pattern.search(fn_upper)
        if m:
            raw = extractor_fn(m)
            # Last pattern is the Amplify crypto special case
            if i == len(pat_list) - 1:
                return _resolve_amplify_crypto(raw)
            return _resolve_raw(raw)
    return None, ""


def _classify(current: str | None, expected: str | None, confidence: str) -> str:
    """Assign status: OK, MISMATCH, or UNCLEAR."""
    # Invalid format in current is always a MISMATCH when we have a confident expected
    if expected and confidence not in ("", "LOW") and _is_invalid_format(current):
        return "MISMATCH"
    if expected is None or confidence in ("", "LOW"):
        return "UNCLEAR"
    if expected is None:
        return "UNCLEAR"
    if current is None:
        return "UNCLEAR"
    if _tickers_equivalent(current, expected):
        return "OK"
    return "MISMATCH"


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

def audit(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, map_cc_underlier
        FROM mkt_master_data
        WHERE etp_category = 'CC'
          AND market_status = 'ACTV'
        ORDER BY ticker
    """)
    rows = cur.fetchall()

    results: list[dict] = []
    for ticker, fund_name, current_map in rows:
        fund_name = fund_name or ""
        expected, confidence = _extract_expected(fund_name)

        # Extra check: flag invalid-format current values even when expected is unclear
        if _is_invalid_format(current_map) and expected is None:
            # Flag as MISMATCH LOW -- clearly wrong format but expected unknown
            status = "MISMATCH"
            confidence = "LOW"
        else:
            status = _classify(current_map, expected, confidence)

        results.append({
            "ticker":       ticker,
            "fund_name":    fund_name,
            "current_map":  current_map or "",
            "expected":     expected or "",
            "status":       status,
            "confidence":   confidence or "LOW",
        })
    return results


def write_csv(results: list[dict]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["ticker", "fund_name", "current_map", "expected", "status", "confidence"]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    print(f"Auditing CC underlier mappings in {DB_PATH}")

    results = audit(con)
    con.close()

    total    = len(results)
    ok       = sum(1 for r in results if r["status"] == "OK")
    mismatch = sum(1 for r in results if r["status"] == "MISMATCH")
    unclear  = sum(1 for r in results if r["status"] == "UNCLEAR")

    mismatch_high   = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "HIGH")
    mismatch_medium = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "MEDIUM")
    mismatch_low    = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "LOW")

    write_csv(results)

    print()
    print(f"Total CC ACTV products  : {total}")
    print(f"  OK       : {ok}")
    print(f"  MISMATCH : {mismatch}  (HIGH={mismatch_high}, MEDIUM={mismatch_medium}, LOW={mismatch_low})")
    print(f"  UNCLEAR  : {unclear}")
    print()
    print("MISMATCH details:")
    for r in results:
        if r["status"] == "MISMATCH":
            print(
                f"  [{r['confidence']:6s}] {r['ticker']:14s}  "
                f"current={r['current_map'] or '(null)':28s}  "
                f"expected={r['expected'] or '(unknown)'}"
            )
    print()
    print(f"Audit written to: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

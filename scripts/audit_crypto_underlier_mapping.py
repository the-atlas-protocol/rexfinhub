"""Audit Crypto underlier mappings in mkt_master_data.

Pulls all ACTV Crypto products, derives the expected map_crypto_underlier
category string from the fund name via keyword matching, then compares
against the stored value.

The Crypto map_crypto_underlier field stores human-readable category strings,
not Bloomberg tickers:
    'Bitcoin'                 -- single-asset BTC fund
    'Ethereum'                -- single-asset ETH fund
    'Solana'                  -- single-asset SOL fund
    'XRP'                     -- single-asset XRP fund
    'Dogecoin'                -- single-asset DOGE fund
    'Chainlink'               -- single-asset LINK fund
    'Litecoin'                -- single-asset LTC fund
    'multi-token crypto'      -- diversified / index crypto basket
    'crypto + traditional asset' -- hybrid fund (crypto + bonds/equities)
    'alt-coin only (XXX)'     -- single alt-coin, non-top-5

Output: docs/crypto_underlier_audit_2026-05-05.csv

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
OUTPUT_CSV = PROJECT_ROOT / "docs" / "crypto_underlier_audit_2026-05-05.csv"

# ---------------------------------------------------------------------------
# Keyword -> expected map_crypto_underlier string.
# Evaluated in priority order (most specific first).
# Each entry: (keyword_pattern, expected_value, confidence)
# ---------------------------------------------------------------------------
_KEYWORD_RULES: list[tuple[re.Pattern, str, str]] = [
    # Crypto + traditional asset (blended with equity/bonds/S&P).
    # Checked FIRST -- takes priority over the multi-token and single-asset rules.
    # Note: "BITCOIN STRATEGY PLUS INCOME" (MAXI) is a pure BTC options fund
    # and does NOT qualify here -- only funds explicitly rotating into bonds/equities.
    (re.compile(r"\bS&P\s+500\s+AND\s+(?:BITCOIN|ETHEREUM|SOLANA|XRP)\b|"
                r"\bBITCOIN\s+AND\s+TREASURIES\b|"
                r"\bBTC.*TREASURIES\b|"
                r"\bTRENDWISE\s+(?:BITCOIN|ETHER|BTC)\b|"
                r"\bUS\s+EQUITY\s+PLUS\s+BITCOIN\b|"
                r"\bCURRENCY\s+DEBASEMENT\b|"
                r"\bALTERNATIVE\s+FIAT\b|"
                r"\bFREE\s+MARKETS\b|"
                r"\bINFLATION\s+PLUS\b",
                re.IGNORECASE), "crypto + traditional asset", "HIGH"),

    # Multi-token / diversified crypto -- check BEFORE single-asset to avoid
    # matching 'BITCOIN' in 'BITCOIN & ETHER' funds incorrectly.
    # Note: BTC/ETH with TREASURIES is already caught above (crypto+traditional).
    (re.compile(r"\bCOINDESK\s+\d+\b|\bCRYPTO\s+(?:\d+|INDEX|INDUSTRY|THEMATIC)\b|"
                r"\b10\s+CRYPTO\b|\bDIGITAL\s+ASSET\s+ECOSYSTEM\b|"
                r"\bBLOCKCHAIN\s+&\s+(?:BITCOIN|FINTECH)\b|"
                r"\bBITCOIN\s+&\s+ETHER\b|\bBTC/ETH\b|"
                r"\bCRYPTO\s+INDUSTRY\b|\bCOINDESK\s+20\b|"
                r"\bNASDAQ\s+CME\s+CRYPTO\b|\bFTSE\s+CRYPTO\b",
                re.IGNORECASE), "multi-token crypto", "HIGH"),

    # Single-asset -- Bitcoin (exclude blended BTC+ETH funds)
    (re.compile(r"\bBITCOIN\s+AND\s+ETHER\b|\bBTC\s*/\s*ETH\b", re.IGNORECASE), "multi-token crypto", "HIGH"),
    (re.compile(r"\bBITCOIN\b", re.IGNORECASE), "Bitcoin", "HIGH"),
    (re.compile(r"\bBTC\b(?!\s*/\s*ETH)", re.IGNORECASE), "Bitcoin", "HIGH"),

    # Single-asset -- Ethereum
    (re.compile(r"\bETHEREUM\b|\bETHER\b(?!\s+AND\s+TREASURIES)", re.IGNORECASE), "Ethereum", "HIGH"),
    (re.compile(r"\bETH\b(?!\s*-)", re.IGNORECASE), "Ethereum", "MEDIUM"),

    # Single-asset -- Solana
    (re.compile(r"\bSOLANA\b|\bSOL\b(?:\s+\+\s+STAKING)?", re.IGNORECASE), "Solana", "HIGH"),

    # Single-asset -- XRP
    (re.compile(r"\bXRP\b", re.IGNORECASE), "XRP", "HIGH"),

    # Single-asset -- Dogecoin
    (re.compile(r"\bDOGECOIN\b|\bDOGE\b", re.IGNORECASE), "Dogecoin", "HIGH"),

    # Single-asset -- Chainlink
    (re.compile(r"\bCHAINLINK\b", re.IGNORECASE), "Chainlink", "HIGH"),

    # Single-asset -- Litecoin
    (re.compile(r"\bLITECOIN\b", re.IGNORECASE), "Litecoin", "HIGH"),

    # Known alt-coins by name -- stored as 'alt-coin only (XXX)'
    (re.compile(r"\bAVALANCHE\b|\bAVAX\b", re.IGNORECASE), "alt-coin only (AVAX)", "HIGH"),
    (re.compile(r"\bSUI\b(?:\s+STAKING)?", re.IGNORECASE), "alt-coin only (SUI)", "HIGH"),
    (re.compile(r"\bPOLKADOT\b|\bDOT\b", re.IGNORECASE), "alt-coin only (DOT)", "HIGH"),
    (re.compile(r"\bHBAR\b", re.IGNORECASE), "alt-coin only (HBAR)", "HIGH"),
    (re.compile(r"\bHYPE\b", re.IGNORECASE), "alt-coin only (HYPE)", "MEDIUM"),
    (re.compile(r"\bBNB\b", re.IGNORECASE), "alt-coin only (BNB)", "HIGH"),
    (re.compile(r"\bCARDANO\b|\bADA\b(?!\s+ETF\b)", re.IGNORECASE), "alt-coin only (ADA)", "HIGH"),

    # Broad crypto / blockchain (multi-token)
    (re.compile(r"\bCRYPTO\b|\bDIGITAL\s+ASSET\b|\bBLOCKCHAIN\b|"
                r"\bALTCOIN\b|\bTOKENIZATION\b|\bSTABLECOIN\b|"
                r"\bWEB3\b|\bDEFI\b|\bONCHAIN\b|\bCOINDESK\b|"
                r"\bINNOVATOR\b|\bADOPTERS\b|\bMINERS?\b",
                re.IGNORECASE), "multi-token crypto", "MEDIUM"),

    # Bitcoin mining ETFs -- tracked under Bitcoin umbrella
    (re.compile(r"\bBITCOIN\s+MIN(?:ERS?|ING)\b|\bBTC\s+MIN(?:ERS?|ING)\b",
                re.IGNORECASE), "Bitcoin", "HIGH"),
]

# Known overrides where the fund name is misleading or ambiguous.
# Key: ticker, Value: (correct_expected, confidence)
_OVERRIDES: dict[str, tuple[str, str]] = {
    # DEFI US - 'HASHDEX COMMODITIES TRUST' which is a BTC spot fund despite name
    "DEFI US": ("Bitcoin", "HIGH"),
    # OBTC US - Osprey Bitcoin Trust - fund name says BITCOIN
    "OBTC US": ("Bitcoin", "HIGH"),
    # MNRS US - GRAYSCALE BITCOIN MINERS ETF - miners = Bitcoin umbrella
    "MNRS US": ("Bitcoin", "HIGH"),
    # ORO US - ARROW INVESTMENTS TRUST - VALTORO = gold+BTC hybrid -> crypto + traditional
    "ORO US": ("crypto + traditional asset", "MEDIUM"),
}


def _extract_expected(fund_name: str) -> tuple[str | None, str]:
    """Return (expected_category_string, confidence) or (None, '') if none matched."""
    fn_upper = fund_name.upper()
    for pattern, expected_val, confidence in _KEYWORD_RULES:
        if pattern.search(fn_upper):
            return expected_val, confidence
    return None, ""


def _categories_equivalent(current: str | None, expected: str | None) -> bool:
    """Case-insensitive string comparison for category labels."""
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


def audit(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, map_crypto_underlier
        FROM mkt_master_data
        WHERE etp_category  = 'Crypto'
          AND market_status = 'ACTV'
        ORDER BY ticker
    """)
    rows = cur.fetchall()

    results: list[dict] = []
    for ticker, fund_name, current_map in rows:
        fund_name = fund_name or ""

        if ticker in _OVERRIDES:
            expected, confidence = _OVERRIDES[ticker]
            status = "OK" if _categories_equivalent(current_map, expected) else "MISMATCH"
        else:
            expected, confidence = _extract_expected(fund_name)
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
    print(f"Auditing Crypto underlier mappings in {DB_PATH}")

    results = audit(con)
    con.close()

    total    = len(results)
    ok       = sum(1 for r in results if r["status"] == "OK")
    mismatch = sum(1 for r in results if r["status"] == "MISMATCH")
    unclear  = sum(1 for r in results if r["status"] == "UNCLEAR")

    mismatch_high   = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "HIGH")
    mismatch_medium = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "MEDIUM")

    write_csv(results)

    print()
    print(f"Total Crypto ACTV products : {total}")
    print(f"  OK       : {ok}")
    print(f"  MISMATCH : {mismatch}  (HIGH={mismatch_high}, MEDIUM={mismatch_medium})")
    print(f"  UNCLEAR  : {unclear}")
    print()
    print("MISMATCH details:")
    for r in results:
        if r["status"] == "MISMATCH":
            print(
                f"  [{r['confidence']:6s}] {r['ticker']:14s}  "
                f"current={r['current_map'] or '(null)':30s}  "
                f"expected={r['expected']}"
            )
    print()
    print(f"Audit written to: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

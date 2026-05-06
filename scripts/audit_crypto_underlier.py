"""Audit Crypto underlier mappings in mkt_master_data.

Pulls all ACTV Crypto products, derives the expected underlier label from the
fund name via keyword matching, then compares against the current
map_crypto_underlier value stored in the DB.

Output: docs/crypto_underlier_audit.csv

Columns:
    ticker        - Bloomberg ticker (e.g. 'IBIT US')
    fund_name     - Full fund name as loaded from Bloomberg
    current_map   - Current value of map_crypto_underlier (may be None)
    expected      - Expected label derived from the fund name
    status        - OK | MISMATCH | UNCLEAR
    confidence    - HIGH | MEDIUM | LOW

Note on map_crypto_underlier format
------------------------------------
The DB stores HUMAN-READABLE labels for crypto underliers, not Bloomberg
Curncy codes.  The canonical label set is:

    Bitcoin                    - pure BTC exposure
    Ethereum                   - pure ETH exposure
    Solana                     - pure SOL exposure
    XRP                        - pure XRP exposure
    Dogecoin                   - pure DOGE exposure
    Chainlink                  - pure LINK exposure
    Litecoin                   - pure LTC exposure
    multi-token crypto         - basket / index of multiple tokens
    crypto + traditional asset - blends crypto with equities / bonds
    alt-coin only (<COIN>)     - single non-BTC/ETH alt-coin

MISMATCH is flagged when:
  - A single-token fund has a label identifying the WRONG token
  - A hybrid fund (name mentions multiple assets or "Treasuries") is labelled
    as a single-token product
  - A fund has NULL but the name clearly identifies the underlier

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
OUTPUT_CSV = PROJECT_ROOT / "docs" / "crypto_underlier_audit.csv"

# ---------------------------------------------------------------------------
# Keywords that identify the primary token from the fund name.
# Evaluated in order; first match wins.  "AND"/"&"/multi-token signals trigger
# a multi-token label regardless of individual token matches.
# ---------------------------------------------------------------------------

# Signals that the fund holds MORE THAN ONE asset class
_HYBRID_SIGNALS = frozenset({
    "TREASURIES", "TREASURY", "T-BILL", "S&P 500", "GOLD", "INFLATION",
    "CURRENCY DEBASEMENT",
    # Equity-plus-crypto hybrid funds (e.g. "SIMPLIFY US EQUITY PLUS BITCOIN")
    "EQUITY PLUS",
})

# Multi-token crypto signals
_MULTI_TOKEN_SIGNALS = re.compile(
    r"\bAND\b|\b&\b|MULTI|INDEX|ALTCOIN|COINDESK|DIGITAL\s+ASSET|"
    r"BLOCKCHAIN|ECONOMY|CRYPTO\s+INDUSTRY|STABLECOIN|TOKENIZATION|"
    r"FTSE\s+CRYPTO|CME\s+CRYPTO|HEDGED\s+DIGITAL",
    re.IGNORECASE,
)

# Single-token patterns: (keyword_set, expected_label)
_TOKEN_RULES: list[tuple[frozenset[str], str]] = [
    (frozenset({"BITCOIN", "BTC"}),     "Bitcoin"),
    (frozenset({"ETHEREUM", "ETHER"}),  "Ethereum"),
    (frozenset({"SOLANA"}),             "Solana"),
    (frozenset({"XRP"}),                "XRP"),
    (frozenset({"DOGECOIN", "DOGE"}),   "Dogecoin"),
    (frozenset({"CHAINLINK"}),          "Chainlink"),
    (frozenset({"LITECOIN"}),           "Litecoin"),
    (frozenset({"AVALANCHE", "AVAX"}),  "alt-coin only (AVAX)"),
    (frozenset({"CARDANO", "ADA"}),     "alt-coin only (ADA)"),
    (frozenset({"BINANCE", "BNB"}),     "alt-coin only (BNB)"),
    (frozenset({"POLKADOT", "DOT"}),    "alt-coin only (DOT)"),
    (frozenset({"HEDERA", "HBAR"}),     "alt-coin only (HBAR)"),
    (frozenset({"SUI"}),                "alt-coin only (SUI)"),
    (frozenset({"HYPE", "HYPERLIQUID"}), "alt-coin only (HYPE)"),
]

# Canonical label set accepted without question
_VALID_LABELS: frozenset[str] = frozenset({
    "Bitcoin", "Ethereum", "Solana", "XRP", "Dogecoin",
    "Chainlink", "Litecoin",
    "multi-token crypto", "crypto + traditional asset",
    "alt-coin only (AVAX)", "alt-coin only (ADA)", "alt-coin only (BNB)",
    "alt-coin only (DOT)", "alt-coin only (HBAR)", "alt-coin only (SUI)",
    "alt-coin only (HYPE)",
})


def _extract_expected(fund_name: str) -> tuple[str | None, str]:
    """Return (expected_label, confidence) from the fund name."""
    fn = fund_name.upper()
    words = set(re.findall(r"[A-Z0-9]+", fn))

    # Hybrid: contains traditional asset signals alongside crypto
    if any(sig in fn for sig in _HYBRID_SIGNALS):
        return ("crypto + traditional asset", "HIGH")

    # Multi-token: explicit basket / index language
    if _MULTI_TOKEN_SIGNALS.search(fn):
        return ("multi-token crypto", "MEDIUM")

    # Single-token identification
    matched_tokens: list[str] = []
    for keywords, label in _TOKEN_RULES:
        if keywords & words:
            matched_tokens.append(label)

    if len(matched_tokens) == 1:
        return (matched_tokens[0], "HIGH")
    if len(matched_tokens) > 1:
        return ("multi-token crypto", "MEDIUM")

    return (None, "")


def _classify(current: str | None, expected: str | None, confidence: str) -> str:
    """Assign status: OK, MISMATCH, or UNCLEAR."""
    if expected is None or confidence in ("", "LOW"):
        return "UNCLEAR"

    if current is None:
        # NULL where we have a confident expected: flag MISMATCH
        return "MISMATCH"

    if current == expected:
        return "OK"

    # Check: expected is hybrid/multi but current is single-token (or vice versa)
    # Any disagreement on the label is a MISMATCH when we have HIGH/MEDIUM confidence
    return "MISMATCH"


def audit(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, map_crypto_underlier
        FROM mkt_master_data
        WHERE etp_category = 'Crypto'
          AND market_status = 'ACTV'
        ORDER BY ticker
    """)
    rows = cur.fetchall()

    results: list[dict] = []
    for ticker, fund_name, current_map in rows:
        fund_name = fund_name or ""
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
                f"current={r['current_map'] or '(null)':38s}  "
                f"expected={r['expected'] or '(unknown)'}"
            )
    print()
    print(f"Audit written to: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

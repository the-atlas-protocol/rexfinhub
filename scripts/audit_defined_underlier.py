"""Audit Defined Outcome category mappings in mkt_master_data.

Pulls all ACTV Defined products, derives the expected category label from the
fund name via keyword matching, then compares against the current
map_defined_category value stored in the DB.

Output: docs/defined_underlier_audit.csv

Columns:
    ticker        - Bloomberg ticker (e.g. 'BAPR US')
    fund_name     - Full fund name as loaded from Bloomberg
    current_map   - Current value of map_defined_category (may be None)
    expected      - Expected category label derived from the fund name
    status        - OK | MISMATCH | UNCLEAR
    confidence    - HIGH | MEDIUM | LOW

Canonical map_defined_category labels
--------------------------------------
    Buffer              - standard downside buffer
    Dual Buffer         - dual-directional buffer (INNOVATOR / FT VEST Dual Directional)
    Ladder              - laddered multi-tranche buffer
    Accelerator         - upside accelerator / growth accelerated products
    Barrier             - barrier-based protection (not a buffer)
    Floor               - hard floor protection (INNOVATOR Floor, Bitcoin Floor series)
    Outcome             - structured defined outcome (non-buffer, e.g. KraneShares KWEB)
    Hedged Equity       - overlay-based hedged equity (JPMorgan Hedged Equity Laddered)
    Defined Volatility  - volatility-targeting defined outcome
    Defined Risk        - defined risk strategy

MISMATCH is flagged when the fund name contains unambiguous structural keywords
(e.g. "DUAL DIRECTIONAL", "LADDERED", "BARRIER") that contradict the stored label.

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
OUTPUT_CSV = PROJECT_ROOT / "docs" / "defined_underlier_audit.csv"

# ---------------------------------------------------------------------------
# Category derivation rules -- evaluated in priority order.
# Each rule is (check_fn, expected_label, confidence).
# ---------------------------------------------------------------------------

def _extract_expected(fund_name: str) -> tuple[str | None, str]:
    """Return (expected_category_label, confidence) from the fund name."""
    fn = fund_name.upper()

    # Highest priority: unambiguous structural keywords

    # Dual Buffer / Dual Directional
    if "DUAL DIRECTIONAL" in fn:
        return ("Dual Buffer", "HIGH")

    # Laddered buffer: explicit "LADDERED" + "BUFFER" combination
    # NB: JPMorgan Hedged Equity Laddered Overlay is NOT a laddered buffer --
    # check for "HEDGED EQUITY" first (see below).
    if "HEDGED EQUITY" in fn and "LADDER" in fn:
        return ("Hedged Equity", "MEDIUM")

    if "LADDERED" in fn and ("BUFFER" in fn or "LADDER" in fn):
        # T-BILL laddered products are NOT buffer ETFs; they are straight ladder structures
        if "T-BILL" in fn or "TBILL" in fn:
            return ("Ladder", "HIGH")
        return ("Ladder", "HIGH")

    # Accelerator products -- only when NOT combined with BUFFER keyword.
    # "INNOVATOR US EQUITY ACCELERATED 9 BUFFER ETF" is still a Buffer product
    # with an accelerated-upside feature; the category remains Buffer.
    if "BUFFER" not in fn and any(x in fn for x in [
        "ACCELERATED PLUS", "UNCAPPED ACCELERATOR", "GROWTH ACCELERATED",
        "GROWTH-100 ACCELERATED", "US EQUITY ACCELERATED",
    ]):
        return ("Accelerator", "HIGH")

    # Barrier products (barrier-based, distinct from buffer)
    if "BARRIER" in fn and "BUFFER" not in fn:
        return ("Barrier", "HIGH")

    # Floor products
    if "FLOOR" in fn and "BUFFER" not in fn:
        return ("Floor", "HIGH")

    # Defined / Structured Outcome
    if any(x in fn for x in ["DEFINED OUTCOME", "STRUCTURED OUTCOME"]):
        return ("Outcome", "HIGH")

    # Hedged Equity (JPMorgan Hedged Equity series without "Laddered")
    if "HEDGED EQUITY" in fn:
        return ("Hedged Equity", "MEDIUM")

    # Defined Volatility / Defined Risk
    if "DEFINED VOLATILITY" in fn:
        return ("Defined Volatility", "MEDIUM")
    if "DEFINED RISK" in fn:
        return ("Defined Risk", "MEDIUM")

    # Standard Buffer: any remaining fund with BUFFER in the name
    if "BUFFER" in fn:
        return ("Buffer", "HIGH")

    # Fallback for floor/ladder without explicit keyword combos
    if "FLOOR" in fn:
        return ("Floor", "MEDIUM")
    if "LADDER" in fn:
        return ("Ladder", "MEDIUM")

    return (None, "")


def _classify(current: str | None, expected: str | None, confidence: str) -> str:
    """Assign status: OK, MISMATCH, or UNCLEAR."""
    if expected is None or confidence in ("", "LOW"):
        return "UNCLEAR"
    if current is None:
        return "MISMATCH"
    if current == expected:
        return "OK"
    return "MISMATCH"


def audit(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, map_defined_category
        FROM mkt_master_data
        WHERE etp_category = 'Defined'
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
    print(f"Auditing Defined Outcome category mappings in {DB_PATH}")

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
    print(f"Total Defined ACTV products : {total}")
    print(f"  OK       : {ok}")
    print(f"  MISMATCH : {mismatch}  (HIGH={mismatch_high}, MEDIUM={mismatch_medium})")
    print(f"  UNCLEAR  : {unclear}")
    print()
    print("MISMATCH details:")
    for r in results:
        if r["status"] == "MISMATCH":
            print(
                f"  [{r['confidence']:6s}] {r['ticker']:14s}  "
                f"current={r['current_map'] or '(null)':25s}  "
                f"expected={r['expected'] or '(unknown)'}"
            )
    print()
    print(f"Audit written to: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Nightly audit: surface duplicate tickers across rex_products rows.

Bug 2 Layer 2: even with the Phase 3 name-validation fix, recycled tickers
can still produce two rex_products rows that share a ticker but reference
different funds. This audit runs nightly, finds those, and emits a JSON
report consumed by the 20:15 pipeline_summary email.

Schema of the output file:
    {
      "generated_at": "ISO timestamp",
      "duplicate_count": N,
      "duplicates": [
        {
          "ticker": "TSII",
          "rows": [
            {"id": 123, "name": "T-REX 2X Long TSM ...", "status": "Effective"},
            {"id": 456, "name": "T-REX 2X Long TSLA ...", "status": "Listed"}
          ]
        }
      ]
    }
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OUT_PATH = PROJECT_ROOT / "data" / ".duplicate_tickers_audit.json"

log = logging.getLogger("audit_duplicate_tickers")


def find_duplicates() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT id, ticker, name, status, initial_filing_date
            FROM rex_products
            WHERE ticker IS NOT NULL
              AND TRIM(ticker) != ''
            ORDER BY UPPER(TRIM(ticker)), id
        """).fetchall()
    finally:
        conn.close()

    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        key = r["ticker"].strip().upper()
        by_ticker.setdefault(key, []).append({
            "id": r["id"],
            "name": r["name"],
            "status": r["status"],
            "initial_filing_date": str(r["initial_filing_date"]) if r["initial_filing_date"] else None,
        })

    duplicates = [
        {"ticker": t, "rows": rs}
        for t, rs in by_ticker.items()
        if len(rs) > 1
    ]
    return duplicates


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not DB_PATH.exists():
        log.error("DB not found at %s", DB_PATH)
        return 1

    duplicates = find_duplicates()

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "duplicate_count": len(duplicates),
        "duplicates": duplicates,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Duplicate-ticker audit -> {OUT_PATH}")
    print(f"  Duplicates found: {len(duplicates)}")
    for d in duplicates[:10]:
        names = " | ".join((r["name"] or "")[:40] for r in d["rows"])
        print(f"  {d['ticker']:<10} ({len(d['rows'])} rows): {names}")
    if len(duplicates) > 10:
        print(f"  ... and {len(duplicates) - 10} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())

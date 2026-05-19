"""Phase 4 (ADR 0006) — Stage 5: link rex_products to underlier_master via fund_underlier.

For each rex_products row, looks up its underlier text (preferring underlying_ticker,
falling back to underlier, then underlying_name) against underlier_master.display_symbol
and inserts a fund_underlier row.

effective_from: rex_products.initial_filing_date OR created_at
effective_to: NULL (current)
weight: NULL (single-underlier products are the common case; basket weights deferred)

Idempotent. Pre-requisite: backfill_product_master.py + backfill_underlier_master.py.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


# Normalize a candidate string the same way the heuristic classifier did so
# we can find the matching underlier_master row by display_symbol.
TICKER_RE = re.compile(r"^([A-Z]{1,5})(\s+US)?$")


def normalize_to_display_symbol(s: str) -> str:
    """Mirror the classifier's display_symbol derivation."""
    if not s:
        return ""
    s = s.strip()
    cleaned = re.sub(r"\s+", "", s).upper()
    # Crypto shortcuts
    for pattern, sym in [
        (r"^(BTC|BITCOIN|XBT)(USD)?$", "XBTUSD"),
        (r"^(ETH|ETHEREUM|XET)(USD)?$", "XETUSD"),
        (r"^(SOL|SOLANA)(USD)?$", "XSOLUSD"),
        (r"^(DOGE|DOGECOIN)(USD)?$", "XDOGEUSD"),
        (r"^(XRP|RIPPLE)(USD)?$", "XXRPUSD"),
    ]:
        if re.match(pattern, cleaned):
            return sym
    # Index "FOO Index"
    m = re.match(r"^([A-Z0-9]+)\s+Index$", s, re.I)
    if m:
        return m.group(1).upper()
    # Ticker
    m = TICKER_RE.match(s)
    if m:
        return m.group(1).upper()
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rex_rows = conn.execute("""
            SELECT id, canonical_id, underlier, underlying_ticker, underlying_name,
                   initial_filing_date, created_at
            FROM rex_products
            WHERE canonical_id IS NOT NULL
        """).fetchall()

        # underlier_master lookup
        um_rows = conn.execute("SELECT underlier_id, display_symbol FROM underlier_master").fetchall()
        um_by_symbol = {r["display_symbol"]: r["underlier_id"] for r in um_rows}

        existing_links = {
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT canonical_id, underlier_id, effective_from FROM fund_underlier"
            ).fetchall()
        }

        print(f"DB: {db_path}")
        print(f"rex_products rows: {len(rex_rows)}")
        print(f"underlier_master rows: {len(um_rows)}")
        print(f"existing fund_underlier rows: {len(existing_links)}")

        stats = {"resolved": 0, "no_underlier": 0, "no_match": 0, "already_linked": 0}
        to_insert = []
        unmatched_samples = []

        for r in rex_rows:
            # Pick preferred underlier text — most specific first
            raw = (r["underlying_ticker"] or r["underlier"] or r["underlying_name"] or "").strip()
            if not raw:
                stats["no_underlier"] += 1
                continue

            disp = normalize_to_display_symbol(raw)
            uid = um_by_symbol.get(disp)
            if not uid:
                # Try the raw string as-is (for unknowns that kept their long form)
                uid = um_by_symbol.get(raw)
            if not uid:
                stats["no_match"] += 1
                if len(unmatched_samples) < 5:
                    unmatched_samples.append(f"id={r['id']} raw={raw!r} disp={disp!r}")
                continue

            effective_from = r["initial_filing_date"] or r["created_at"] or datetime.utcnow().isoformat()
            key = (r["canonical_id"], uid, effective_from)
            if key in existing_links:
                stats["already_linked"] += 1
                continue
            stats["resolved"] += 1
            to_insert.append((r["canonical_id"], uid, None, effective_from))

        print(f"\nResolution:")
        for k, v in stats.items():
            print(f"  {k:18s} {v}")
        if unmatched_samples:
            print("\nSample unmatched:")
            for s in unmatched_samples:
                print(f"  {s}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        if to_insert:
            conn.executemany(
                "INSERT INTO fund_underlier "
                "(canonical_id, underlier_id, weight, effective_from) "
                "VALUES (?, ?, ?, ?)",
                to_insert,
            )
            conn.commit()
            print(f"\nCommitted {len(to_insert)} fund_underlier rows.")

        total = conn.execute("SELECT COUNT(*) FROM fund_underlier").fetchone()[0]
        print(f"fund_underlier total rows: {total}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

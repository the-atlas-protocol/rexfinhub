"""One-off: populate underlier_master + fund_underlier for the 8 Listed REX
products that the assertion runner flagged as missing fund_underlier links.

7 are MicroSectors ETNs (issued by Bank of Montreal, tracked by Solactive
proprietary indices, basket-based). 1 is TLDR — Laddered T-Bill ETF.

bmo_suite -> Solactive index code mapping (per BMO MicroSectors prospectuses):
  FANG+                            -> NYFANG       NYSE FANG+ Index
  FANG & Innovation                -> SOLFNGI      Solactive FANG Innovation Index
  Gold                             -> XAU          Solactive Spot Gold Index
  Gold Miners                      -> SOLGDXJ      Solactive United States Gold Miners Index
  Oil & Gas Exploration & Production -> SOLXOP    Solactive U.S. Oil & Gas Exploration & Production Index
  Energy                           -> SOLXLE       Solactive U.S. Energy Index

TLDR uses U.S. Treasury Bills 1-3M ladder — modeled as `basket` of T-Bill instruments.

Idempotent: skips rows that already have a fund_underlier link.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


# Per-bmo_suite -> (index_provider, index_code, display_symbol, description)
MICROSECTORS_INDICES = {
    "FANG+": ("NYSE", "NYFANG", "NYFANG",
              "NYSE FANG+ Index (META/AMZN/AAPL/NFLX/GOOGL/NVDA/TSLA/MSFT/AMD/BABA)"),
    "FANG & Innovation": ("Solactive", "SOLFNGI", "SOLFNGI",
                          "Solactive MicroSectors FANG & Innovation Index"),
    "Gold": ("Solactive", "XAU", "XAU",
             "Solactive Spot Gold Index"),
    "Gold Miners": ("Solactive", "SOLGDXJ", "SOLGDXJ",
                    "Solactive U.S. Gold Miners Index"),
    "Oil & Gas Exploration & Production": ("Solactive", "SOLXOP", "SOLXOP",
                                           "Solactive U.S. Oil & Gas Exploration & Production Index"),
    "Energy": ("Solactive", "SOLXLE", "SOLXLE",
               "Solactive U.S. Energy Index"),
}

TLDR_UNDERLIER = {
    "ticker": "TLDR",
    "underlier_type": "basket",
    "display_symbol": "USTB1-3M_LADDER",
    "note": "U.S. Treasury Bills 1-3 month laddered (issuance/maturity rolling)",
}


def get_or_create_index(conn, provider, code, display_symbol, note) -> str:
    """Return underlier_id; create if missing."""
    row = conn.execute(
        "SELECT underlier_id FROM underlier_master WHERE display_symbol = ?",
        (display_symbol,),
    ).fetchone()
    if row:
        return row[0]
    uid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO underlier_master
        (underlier_id, underlier_type, index_provider, index_code,
         display_symbol, created_at, resolved_via)
        VALUES (?, 'index', ?, ?, ?, ?, 'microsectors_manual_2026-05-19')
    """, (uid, provider, code, display_symbol, datetime.utcnow().isoformat()))
    return uid


def get_or_create_basket(conn, display_symbol, note) -> str:
    row = conn.execute(
        "SELECT underlier_id FROM underlier_master WHERE display_symbol = ?",
        (display_symbol,),
    ).fetchone()
    if row:
        return row[0]
    uid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO underlier_master
        (underlier_id, underlier_type, display_symbol, created_at, resolved_via)
        VALUES (?, 'basket', ?, ?, 'microsectors_manual_2026-05-19')
    """, (uid, display_symbol, datetime.utcnow().isoformat()))
    return uid


def link_fund(conn, canonical_id, underlier_id, effective_from):
    existing = conn.execute(
        "SELECT 1 FROM fund_underlier WHERE canonical_id = ? AND underlier_id = ?",
        (canonical_id, underlier_id),
    ).fetchone()
    if existing:
        return False
    conn.execute("""
        INSERT INTO fund_underlier
        (canonical_id, underlier_id, weight, effective_from)
        VALUES (?, ?, NULL, ?)
    """, (canonical_id, underlier_id, effective_from))
    return True


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
        rows = conn.execute("""
            SELECT rp.id, rp.canonical_id, rp.ticker, rp.bmo_suite, rp.name,
                   rp.initial_filing_date, rp.created_at
            FROM rex_products rp
            LEFT JOIN fund_underlier fu ON fu.canonical_id = rp.canonical_id
            WHERE rp.status = 'Listed'
              AND fu.canonical_id IS NULL
            ORDER BY rp.ticker
        """).fetchall()

        print(f"DB: {db_path}")
        print(f"Candidates: {len(rows)}")

        linked = 0
        skipped = 0
        unmatched = []

        for r in rows:
            ticker = r["ticker"]
            bmo = r["bmo_suite"]
            effective_from = r["initial_filing_date"] or r["created_at"] or datetime.utcnow().isoformat()

            if ticker == "TLDR":
                uid = get_or_create_basket(conn, TLDR_UNDERLIER["display_symbol"],
                                           TLDR_UNDERLIER["note"]) if not args.dry_run else "<would-create>"
                source_label = "TLDR -> T-Bill basket"
            elif bmo and bmo in MICROSECTORS_INDICES:
                provider, code, disp, note = MICROSECTORS_INDICES[bmo]
                uid = get_or_create_index(conn, provider, code, disp, note) if not args.dry_run else "<would-create>"
                source_label = f"{ticker} ({bmo}) -> {disp}"
            else:
                unmatched.append({"ticker": ticker, "bmo_suite": bmo, "name": (r["name"] or "")[:40]})
                continue

            if args.dry_run:
                print(f"  WOULD LINK {source_label}")
                linked += 1
            else:
                if link_fund(conn, r["canonical_id"], uid, effective_from):
                    print(f"  LINKED     {source_label}")
                    linked += 1
                else:
                    print(f"  ALREADY    {source_label}")
                    skipped += 1

        if unmatched:
            print(f"\nUNMATCHED ({len(unmatched)}):")
            for u in unmatched:
                print(f"  {u}")

        if not args.dry_run:
            conn.commit()

        print(f"\nLinked: {linked}")
        print(f"Already-linked: {skipped}")
        print(f"Unmatched: {len(unmatched)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

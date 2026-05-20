"""Phase 4b (Track 3) — finish underlier classification.

Audit 2026-05-20 found underlier_master had 15 rows typed 'unknown'. Two
distinct problems:

  1. Five MicroSectors 3X leveraged ETNs (FNGU, NRGU, NRGD, BNKU, BNKD) had a
     fund_underlier link to the JUNK underlier '0'. fix_microsectors_underliers.py
     skipped them because it only handled products with NO link at all — these
     had a (wrong) link. This script repoints them to the correct Solactive /
     NYSE index underliers.

  2. Thirteen orphan underlier_master rows (no REX fund links them; they came
     from the full mkt_master_data competitor universe) plus the '-' row used
     by ULTI were left as type 'unknown'. This script reclassifies them to
     their correct polymorphic types (commodity / crypto_pair / basket).

After this runs, the only remaining 'unknown' row is the literal junk string
'0', which no fund references.

Idempotent — re-running is a no-op (ETN repoint skips funds already on a
non-unknown underlier; reclassification matches WHERE underlier_type='unknown').

Usage:
    python scripts/fix_underlier_classification.py [--db PATH] [--dry-run]
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

RESOLVED_VIA = "phase4b_manual_2026-05-20"


# --- Part A: MicroSectors ETN -> correct index underlier -------------------
# ticker: (index_provider, index_code, display_symbol, description)
# NRGU/NRGD share the Big Oil index; BNKU/BNKD share the Big Banks index.
ETN_INDEX = {
    "FNGU": ("NYSE", "NYFANG", "NYFANG",
             "NYSE FANG+ Index"),
    "NRGU": ("Solactive", "SOLBIGOIL", "SOLBIGOIL",
             "Solactive MicroSectors U.S. Big Oil Index"),
    "NRGD": ("Solactive", "SOLBIGOIL", "SOLBIGOIL",
             "Solactive MicroSectors U.S. Big Oil Index"),
    "BNKU": ("Solactive", "SOLBIGBANKS", "SOLBIGBANKS",
             "Solactive MicroSectors U.S. Big Banks Index"),
    "BNKD": ("Solactive", "SOLBIGBANKS", "SOLBIGBANKS",
             "Solactive MicroSectors U.S. Big Banks Index"),
}


# --- Part B: orphan / junk underlier reclassification ----------------------
# original display_symbol -> fields to UPDATE on the underlier_master row.
# Matched WHERE underlier_type='unknown' so a second run is a no-op.
# XAU/XAG are precious metals -> commodity. The alt-coins -> crypto_pair.
# Multi-* and the literal 'Basket' / '-' -> basket. '0' is left as junk.
RECLASSIFY = {
    "XAG Curncy": {"underlier_type": "commodity", "ticker": "XAG",
                   "display_symbol": "XAGUSD"},
    "XAU Curncy": {"underlier_type": "commodity", "ticker": "XAU",
                   "display_symbol": "XAUUSD"},
    "XSUIUSD Curncy": {"underlier_type": "crypto_pair", "crypto_base": "SUI",
                       "crypto_quote": "USD", "display_symbol": "XSUIUSD"},
    "alt-coin only (ADA)": {"underlier_type": "crypto_pair", "crypto_base": "ADA",
                            "crypto_quote": "USD", "display_symbol": "XADAUSD"},
    "alt-coin only (AVAX)": {"underlier_type": "crypto_pair", "crypto_base": "AVAX",
                             "crypto_quote": "USD", "display_symbol": "XAVAXUSD"},
    "alt-coin only (BNB)": {"underlier_type": "crypto_pair", "crypto_base": "BNB",
                            "crypto_quote": "USD", "display_symbol": "XBNBUSD"},
    "alt-coin only (DOT)": {"underlier_type": "crypto_pair", "crypto_base": "DOT",
                            "crypto_quote": "USD", "display_symbol": "XDOTUSD"},
    "alt-coin only (HBAR)": {"underlier_type": "crypto_pair", "crypto_base": "HBAR",
                             "crypto_quote": "USD", "display_symbol": "XHBARUSD"},
    "alt-coin only (HYPE)": {"underlier_type": "crypto_pair", "crypto_base": "HYPE",
                             "crypto_quote": "USD", "display_symbol": "XHYPEUSD"},
    "alt-coin only (SUI)": {"underlier_type": "crypto_pair", "crypto_base": "SUI",
                            "crypto_quote": "USD", "display_symbol": "XSUIUSD"},
    "Multi-Crypto": {"underlier_type": "basket", "display_symbol": "MULTI_CRYPTO"},
    "multi-token crypto": {"underlier_type": "basket",
                           "display_symbol": "MULTI_TOKEN_CRYPTO"},
    "Basket": {"underlier_type": "basket", "display_symbol": "UNSPEC_BASKET"},
    # ULTI — REX IncomeMax Option Strategy ETF, a covered-call income fund with
    # no single traditional underlier. Modeled as a basket (its option-strategy
    # portfolio).
    "-": {"underlier_type": "basket", "display_symbol": "OPTION_INCOME_STRATEGY"},
}


def get_or_create_index(conn, provider, code, display_symbol, dry_run):
    """Return underlier_id for an index underlier; create if missing."""
    row = conn.execute(
        "SELECT underlier_id FROM underlier_master "
        "WHERE display_symbol = ? AND underlier_type = 'index'",
        (display_symbol,),
    ).fetchone()
    if row:
        return row[0]
    uid = str(uuid.uuid4())
    if not dry_run:
        conn.execute(
            "INSERT INTO underlier_master "
            "(underlier_id, underlier_type, index_provider, index_code, "
            "display_symbol, created_at, resolved_via) "
            "VALUES (?, 'index', ?, ?, ?, ?, ?)",
            (uid, provider, code, display_symbol,
             datetime.utcnow().isoformat(), RESOLVED_VIA),
        )
    return uid


def repoint_etns(conn, dry_run):
    """Part A — repoint the 5 MicroSectors ETNs off the junk underlier."""
    print("\n--- Part A: MicroSectors ETN repoint ---")
    repointed = 0
    for ticker, (provider, code, disp, desc) in ETN_INDEX.items():
        row = conn.execute(
            "SELECT canonical_id FROM rex_products WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row or not row[0]:
            print(f"  WARN  {ticker}: no rex_products row / canonical_id — skip")
            continue
        cid = row[0]
        bad = conn.execute(
            "SELECT fu.underlier_id FROM fund_underlier fu "
            "JOIN underlier_master um ON um.underlier_id = fu.underlier_id "
            "WHERE fu.canonical_id = ? AND um.underlier_type = 'unknown'",
            (cid,),
        ).fetchall()
        if not bad:
            print(f"  OK    {ticker}: already on a resolved underlier — skip")
            continue
        target = get_or_create_index(conn, provider, code, disp, dry_run)
        for (bad_uid,) in bad:
            if dry_run:
                print(f"  WOULD {ticker}: repoint {bad_uid[:8]} -> {disp} ({desc})")
            else:
                conn.execute(
                    "UPDATE fund_underlier SET underlier_id = ? "
                    "WHERE canonical_id = ? AND underlier_id = ?",
                    (target, cid, bad_uid),
                )
                print(f"  FIXED {ticker}: -> {disp} ({desc})")
            repointed += 1
    print(f"  ETN links repointed: {repointed}")
    return repointed


def reclassify_orphans(conn, dry_run):
    """Part B — reclassify orphan / '-' underlier_master rows to typed kinds."""
    print("\n--- Part B: orphan underlier reclassification ---")
    updated = 0
    for original, fields in RECLASSIFY.items():
        row = conn.execute(
            "SELECT underlier_id FROM underlier_master "
            "WHERE display_symbol = ? AND underlier_type = 'unknown'",
            (original,),
        ).fetchone()
        if not row:
            print(f"  SKIP  {original!r}: not present as unknown (already done)")
            continue
        uid = row[0]
        fields = dict(fields, resolved_via=RESOLVED_VIA)
        if dry_run:
            print(f"  WOULD {original!r} -> {fields['underlier_type']} "
                  f"/ {fields['display_symbol']}")
        else:
            set_clause = ", ".join(f"{c} = ?" for c in fields)
            conn.execute(
                f"UPDATE underlier_master SET {set_clause} WHERE underlier_id = ?",
                list(fields.values()) + [uid],
            )
            print(f"  FIXED {original!r} -> {fields['underlier_type']} "
                  f"/ {fields['display_symbol']}")
        updated += 1
    print(f"  underlier_master rows reclassified: {updated}")
    return updated


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
        before = conn.execute(
            "SELECT COUNT(*) FROM underlier_master WHERE underlier_type='unknown'"
        ).fetchone()[0]
        print(f"DB: {db_path}")
        print(f"underlier_master 'unknown' rows before: {before}")

        repoint_etns(conn, args.dry_run)
        reclassify_orphans(conn, args.dry_run)

        if args.dry_run:
            print("\nDRY-RUN — no changes committed")
            return 0

        conn.commit()

        after = conn.execute(
            "SELECT COUNT(*) FROM underlier_master WHERE underlier_type='unknown'"
        ).fetchone()[0]
        junk_linked = conn.execute(
            "SELECT COUNT(*) FROM rex_products rp "
            "JOIN fund_underlier fu ON fu.canonical_id = rp.canonical_id "
            "JOIN underlier_master um ON um.underlier_id = fu.underlier_id "
            "WHERE rp.status = 'Listed' AND um.underlier_type = 'unknown'"
        ).fetchone()[0]
        print(f"\nunderlier_master 'unknown' rows after: {after} "
              f"(expected 1 — the junk '0' orphan)")
        print(f"Listed REX funds still linked to an unknown underlier: {junk_linked} "
              f"(expected 0)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

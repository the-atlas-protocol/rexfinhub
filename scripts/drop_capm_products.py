"""Phase 3 (ADR 0007) Stage 5 / Track 4a — drop the retired capm_products table.

capm_products was merged into rex_products in Phase 3. Track 4a completed the
code cutover: import_capm.py now writes rex_products, the route layer reads
rex_products, the startup seed and the CapMProduct ORM model are gone. This
script performs the final retirement.

GATE C — equivalence proof of death. Before the DROP, every capm_products row
must be recoverable from rex_products:
  - its ticker matches a rex_products row, AND
  - for every CapM-managed column where capm_products holds a non-empty value,
    the matching rex_products row also holds a non-empty value. Equal values =
    migrated; different non-empty values = survivorship / admin override (both
    fine); rex empty where capm had data = DATA LOSS -> abort.

If the gate fails, the table is NOT dropped. Idempotent: a no-op when the
table is already gone. capm_audit_log and capm_trust_aps are retained.

Usage:
    python scripts/drop_capm_products.py --dry-run
    python scripts/drop_capm_products.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"

# CapM-managed columns merged into rex_products by migrate_capm_data_to_rex.py.
CAPM_COLS = [
    "bb_ticker", "inception_date", "issuer", "fixed_fee", "variable_fee",
    "cut_off", "custodian", "our_category", "product_type", "category",
    "sub_category", "leverage", "underlying_ticker", "underlying_name",
    "expense_ratio", "bmo_suite",
]


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


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
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='capm_products'"
        ).fetchone()
        if not exists:
            print("capm_products already dropped — nothing to do.")
            return 0

        capm_rows = conn.execute("SELECT * FROM capm_products").fetchall()
        print(f"DB: {db_path}")
        print(f"capm_products rows: {len(capm_rows)}")

        # --- GATE C: equivalence proof of death ---
        losses: list[tuple[str, str]] = []   # (ticker, column) — capm had data, rex empty
        unmatched: list[str] = []
        for capm in capm_rows:
            ticker = (capm["ticker"] or "").strip()
            if not ticker:
                continue
            rex = conn.execute(
                "SELECT * FROM rex_products WHERE upper(trim(ticker)) = upper(?)",
                (ticker,),
            ).fetchone()
            if rex is None:
                unmatched.append(ticker)
                continue
            rex_keys = set(rex.keys())
            for col in CAPM_COLS:
                cv = capm[col]
                if _empty(cv):
                    continue
                rv = rex[col] if col in rex_keys else None
                if _empty(rv):
                    losses.append((ticker, col))

        print("\nGATE C — equivalence proof:")
        print(f"  capm rows with no rex_products match: {len(unmatched)}")
        print(f"  CapM values that would be LOST (rex empty): {len(losses)}")
        for t in sorted(unmatched)[:20]:
            print(f"    unmatched: {t}")
        for t, c in losses[:20]:
            print(f"    data-loss: {t}.{c}")

        if unmatched or losses:
            print("\nGATE C FAILED — capm_products is NOT fully recoverable from "
                  "rex_products. Aborting; table NOT dropped.")
            return 1

        print("  GATE C PASSED — every capm_products value is present in rex_products.")

        if args.dry_run:
            print("\nDRY-RUN — capm_products would be DROPPED. No change made.")
            return 0

        conn.execute("DROP TABLE capm_products")
        conn.commit()
        print("\ncapm_products DROPPED. Retirement complete "
              "(capm_audit_log + capm_trust_aps retained).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

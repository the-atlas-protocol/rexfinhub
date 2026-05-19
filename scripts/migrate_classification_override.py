"""Phase 6 (ADR 0009) — Stages 1+2: classification_override schema + CSV migration.

Creates:
  - classification_override (canonical_id, field_name, value, set_by, set_at, reason)
  - assertion_run (id, run_at, assertion_name, passed, count, sample)

Migrates the rule CSVs in config/rules/ into classification_override rows.
Each CSV row becomes one or more override rows; ticker keys are resolved
to canonical_id via the identifier_xref table (Phase 4 prereq).

Idempotent: skips (canonical_id, field_name) pairs that already exist.

Pre-requisite: Phase 4 Stages 1-3 (canonical_id + identifier_xref populated).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
RULES_DIR = PROJECT_ROOT / "config" / "rules"

DDL = [
    """CREATE TABLE IF NOT EXISTS classification_override (
        canonical_id   TEXT NOT NULL,
        field_name     TEXT NOT NULL,
        value          TEXT,
        set_by         TEXT NOT NULL,
        set_at         TIMESTAMP NOT NULL,
        reason         TEXT,
        PRIMARY KEY (canonical_id, field_name),
        FOREIGN KEY (canonical_id) REFERENCES product_master(canonical_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_classification_override_field
        ON classification_override (field_name)""",
    """CREATE TABLE IF NOT EXISTS assertion_run (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at          TIMESTAMP NOT NULL,
        assertion_name  TEXT NOT NULL,
        category        TEXT NOT NULL,
        passed          INTEGER NOT NULL,
        fail_count      INTEGER NOT NULL DEFAULT 0,
        sample_json     TEXT,
        details         TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_assertion_run_lookup
        ON assertion_run (run_at, assertion_name)""",
]

# Per CSV: (filename, key_column, fields_to_extract)
# fields_to_extract: list of (csv_column, override_field_name) tuples.
CSV_MIGRATIONS = [
    ("fund_mapping.csv", "ticker", [
        ("etp_category", "etp_category"),
    ]),
    ("issuer_brand_overrides.csv", "ticker", [
        ("issuer_display", "issuer_display"),
    ]),
    ("attributes_LI.csv", "ticker", [
        ("map_li_category", "li_category"),
        ("map_li_subcategory", "li_subcategory"),
        ("map_li_direction", "li_direction"),
        ("map_li_leverage_amount", "li_leverage_amount"),
        ("map_li_underlier", "li_underlier"),
    ]),
    ("attributes_CC.csv", "ticker", [
        ("map_cc_subcategory", "cc_subcategory"),
        ("map_cc_underlier", "cc_underlier"),
    ]),
    ("attributes_Crypto.csv", "ticker", [
        ("map_crypto_underlier", "crypto_underlier"),
        ("map_crypto_strategy", "crypto_strategy"),
    ]),
    ("attributes_Defined.csv", "ticker", [
        ("map_defined_category", "defined_category"),
    ]),
    ("attributes_Thematic.csv", "ticker", [
        ("map_thematic_theme", "thematic_theme"),
    ]),
]


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
        for stmt in DDL:
            conn.execute(stmt)

        # Build ticker -> canonical_id lookup from identifier_xref.
        # Strip " US" suffix since CSV tickers are bare ("AAPL") and SEC ticker
        # values in identifier_xref are also bare.
        ticker_lookup = {}
        for row in conn.execute(
            "SELECT id_value, canonical_id FROM identifier_xref "
            "WHERE id_type='ticker' AND valid_to IS NULL"
        ):
            t = row["id_value"].strip().upper().replace(" US", "")
            ticker_lookup[t] = row["canonical_id"]
        print(f"DB: {db_path}")
        print(f"identifier_xref ticker lookups available: {len(ticker_lookup)}")

        existing_overrides = {
            (row[0], row[1]) for row in conn.execute(
                "SELECT canonical_id, field_name FROM classification_override"
            ).fetchall()
        }
        print(f"existing classification_override rows: {len(existing_overrides)}")

        now = datetime.utcnow().isoformat()
        to_insert = []
        stats_per_csv = {}
        unmatched_tickers = set()

        for csv_name, key_col, field_specs in CSV_MIGRATIONS:
            csv_path = RULES_DIR / csv_name
            if not csv_path.exists():
                continue
            try:
                with open(csv_path, encoding="utf-8", newline="") as fh:
                    reader = csv.DictReader(fh)
                    rows = list(reader)
            except UnicodeDecodeError:
                print(f"  SKIP {csv_name}: utf-8 decode error")
                continue

            csv_stats = {"rows": len(rows), "matched": 0, "unmatched": 0, "fields_added": 0}
            for r in rows:
                raw_ticker = (r.get(key_col) or "").strip().upper().replace(" US", "")
                if not raw_ticker:
                    continue
                canonical = ticker_lookup.get(raw_ticker)
                if not canonical:
                    csv_stats["unmatched"] += 1
                    unmatched_tickers.add(raw_ticker)
                    continue
                csv_stats["matched"] += 1
                for csv_col, override_field in field_specs:
                    val = (r.get(csv_col) or "").strip()
                    if not val:
                        continue
                    if (canonical, override_field) in existing_overrides:
                        continue
                    existing_overrides.add((canonical, override_field))
                    to_insert.append((
                        canonical, override_field, val,
                        f"csv_import:{csv_name}", now,
                        f"Initial migration from {csv_name} on 2026-05-19",
                    ))
                    csv_stats["fields_added"] += 1
            stats_per_csv[csv_name] = csv_stats

        print("\nPer-CSV migration plan:")
        print(f"  {'csv':30s} {'rows':>6s} {'matched':>8s} {'unmatched':>10s} {'override_rows':>14s}")
        for n, s in stats_per_csv.items():
            print(f"  {n:30s} {s['rows']:>6d} {s['matched']:>8d} {s['unmatched']:>10d} {s['fields_added']:>14d}")
        print(f"\nTotal new override rows to insert: {len(to_insert)}")
        print(f"Distinct unmatched tickers across all CSVs: {len(unmatched_tickers)}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        if to_insert:
            conn.executemany(
                "INSERT INTO classification_override "
                "(canonical_id, field_name, value, set_by, set_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                to_insert,
            )
            conn.commit()
            print(f"\nCommitted {len(to_insert)} classification_override rows.")

        total = conn.execute("SELECT COUNT(*) FROM classification_override").fetchone()[0]
        print(f"classification_override total: {total}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

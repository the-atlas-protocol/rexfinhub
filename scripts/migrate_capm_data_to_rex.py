"""Phase 3 (ADR 0007) — Stage 2: backfill CapM data into rex_products.

For each row in capm_products, finds the matching rex_products row by ticker
(case-insensitive, trimmed) and applies the per-field survivorship rules from
the ADR.

Survivorship summary:
  - direction:     CapM wins when CapM is non-NULL (Rex's NULLs get filled).
                   This is the one column where Rex's data is empirically
                   incomplete and CapM's is hand-curated.
  - name:          fill from CapM's fund_name only if Rex's name is empty.
  - product_suite: fill from CapM's suite_source only if Rex's is empty.
  - latest_prospectus_link: fill from CapM's prospectus_link only if Rex's empty.
  - competitors:   fill from CapM's competitor_products only if Rex's empty.
  - manually_edited_fields: UNION of both sides.
  - All CapM-only columns (bb_ticker, inception_date, issuer, fees,
    classification axes, leverage, underlying_*, expense_ratio, bmo_suite):
    populated unconditionally (Rex didn't have them before this migration).

Idempotent. Running twice does no extra work because the conditional fills
check for Rex's column being NULL.

Pre-requisite: scripts/migrate_capm_to_rex_schema.py must have already run
(Stage 1 — adds the 16 new columns to rex_products).

Usage:
    python scripts/migrate_capm_data_to_rex.py
    python scripts/migrate_capm_data_to_rex.py --dry-run
    python scripts/migrate_capm_data_to_rex.py --db /path/to/etp_tracker.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"

# CapM-only columns: always populated (Rex didn't have them).
CAPM_ONLY = [
    "bb_ticker", "inception_date", "issuer",
    "fixed_fee", "variable_fee", "cut_off", "custodian",
    "our_category", "product_type", "category", "sub_category",
    "leverage", "underlying_ticker", "underlying_name",
    "expense_ratio", "bmo_suite",
]

# Conditional fills: capm_column -> rex_column. Only set rex_column when it's NULL/empty.
COND_FILLS = {
    "fund_name":         "name",
    "suite_source":      "product_suite",
    "prospectus_link":   "latest_prospectus_link",
    "competitor_products": "competitors",
}


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _merge_edited_fields(rex_raw: str | None, capm_raw: str | None) -> str | None:
    """Union the two manually_edited_fields JSON lists (deduplicated, sorted)."""
    def _parse(raw):
        if not raw:
            return []
        try:
            v = json.loads(raw)
            return [str(x) for x in v] if isinstance(v, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    merged = sorted(set(_parse(rex_raw)) | set(_parse(capm_raw)))
    return json.dumps(merged) if merged else None


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
        # Verify Stage 1 ran
        rex_cols = {row[1] for row in conn.execute("PRAGMA table_info(rex_products)").fetchall()}
        missing = [c for c in CAPM_ONLY if c not in rex_cols]
        if missing:
            print(f"ERROR: Stage 1 incomplete — rex_products missing columns: {missing}", file=sys.stderr)
            print("Run: python scripts/migrate_capm_to_rex_schema.py", file=sys.stderr)
            return 1

        capm_rows = conn.execute("SELECT * FROM capm_products").fetchall()
        print(f"DB: {db_path}")
        print(f"capm_products row count: {len(capm_rows)}")

        stats = {
            "matched": 0,
            "unmatched": 0,
            "direction_filled": 0,
            "name_filled": 0,
            "suite_filled": 0,
            "prospectus_filled": 0,
            "competitors_filled": 0,
            "edit_log_merged": 0,
        }

        for capm in capm_rows:
            ticker = (capm["ticker"] or "").strip()
            if not ticker:
                stats["unmatched"] += 1
                continue

            rex = conn.execute(
                "SELECT * FROM rex_products WHERE upper(trim(ticker)) = upper(?)",
                (ticker,),
            ).fetchone()
            if rex is None:
                stats["unmatched"] += 1
                print(f"  UNMATCHED ticker={ticker} fund_name={capm['fund_name'][:60]}")
                continue

            stats["matched"] += 1
            sets = []
            params = []

            # CapM-only columns: always set (overwrite NULL or take CapM verbatim).
            # We do NOT skip when Rex already has a value because these columns
            # were just added — they're guaranteed NULL on first run.
            for col in CAPM_ONLY:
                cv = capm[col]
                if cv is None or (isinstance(cv, str) and cv.strip() == ""):
                    continue
                sets.append(f"{col} = ?")
                params.append(cv)

            # Direction: CapM wins when CapM is non-NULL (overwrite Rex's NULL).
            capm_dir = capm["direction"]
            if not _is_empty(capm_dir) and _is_empty(rex["direction"]):
                sets.append("direction = ?")
                params.append(capm_dir)
                stats["direction_filled"] += 1

            # Conditional fills: fill rex_col only when rex_col is empty.
            for cap_col, rex_col in COND_FILLS.items():
                cv = capm[cap_col]
                if _is_empty(cv):
                    continue
                if _is_empty(rex[rex_col]):
                    sets.append(f"{rex_col} = ?")
                    params.append(cv)
                    stats[f"{rex_col.replace('latest_prospectus_link', 'prospectus').replace('product_suite','suite')}_filled"] = \
                        stats.get(f"{rex_col.replace('latest_prospectus_link', 'prospectus').replace('product_suite','suite')}_filled", 0) + 1

            # manually_edited_fields: union both sides.
            merged_edited = _merge_edited_fields(rex["manually_edited_fields"], capm["manually_edited_fields"])
            if merged_edited != rex["manually_edited_fields"]:
                sets.append("manually_edited_fields = ?")
                params.append(merged_edited)
                stats["edit_log_merged"] += 1

            if not sets:
                continue

            stmt = f"UPDATE rex_products SET {', '.join(sets)} WHERE id = ?"
            params.append(rex["id"])
            if args.dry_run:
                if stats["matched"] <= 3:  # show first 3 for spot-check
                    print(f"  DRY-RUN  {stmt}  params=[{len(params)-1} fields, id={rex['id']}]")
            else:
                conn.execute(stmt, params)

        if not args.dry_run:
            conn.commit()
            print("Committed.")

        print()
        print("Stats:")
        for k, v in stats.items():
            print(f"  {k:20s} = {v}")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

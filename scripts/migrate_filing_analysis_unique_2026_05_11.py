"""Hotfix H1 — Migrate filing_analyses UNIQUE constraint.

R5 changed the SQLAlchemy model from ``UNIQUE(filing_id)`` to
``UNIQUE(filing_id, writer_model)`` so that swapping the writer model
(e.g. Sonnet -> Opus) triggers re-analysis of an already-analyzed filing.
However ``Base.metadata.create_all`` does NOT migrate constraints on
existing tables, so on any DB that pre-dates R5 the OLD single-column
UNIQUE is still in force. That silently re-binds the writer-model swap
to "duplicate key error" and the new analysis is never written.

This script:

  1. Inspects ``filing_analyses`` for its current UNIQUE constraints.
  2. If the OLD ``UNIQUE(filing_id)`` is in place (and the new composite
     UNIQUE is NOT), rebuilds the table SQLite-style:
       - rename old to ``filing_analyses_old``
       - create new with correct UNIQUE
       - copy rows over (deduping on the new key — keeps newest by id)
       - drop old table
       - all wrapped in a single transaction
  3. If the new constraint is already present, exits cleanly.
  4. ``--dry-run`` (default) just reports what it would do without
     touching the DB.

Usage:
    python scripts/migrate_filing_analysis_unique_2026_05_11.py             # dry run
    python scripts/migrate_filing_analysis_unique_2026_05_11.py --apply     # execute
    python scripts/migrate_filing_analysis_unique_2026_05_11.py --apply --db /path/to/db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
NEW_UNIQUE_NAME = "uq_filing_analyses_filing_writer"


def list_unique_indexes(con: sqlite3.Connection, table: str) -> list[dict]:
    """Return [{name, columns: [..], origin}] for each UNIQUE index on table.

    SQLite stores UniqueConstraint() as an index with origin='u' and
    auto-generated UNIQUEs (from `column UNIQUE` shorthand) with origin='pk'
    or 'u'. The table_info PRAGMA also reports 'pk' columns. We use
    index_list + index_info to get the full picture.
    """
    cur = con.cursor()
    cur.execute(f"PRAGMA index_list({table})")
    rows = cur.fetchall()
    out = []
    for row in rows:
        # PRAGMA index_list cols: seq, name, unique, origin, partial
        _, name, is_unique, origin, _ = row
        if not is_unique:
            continue
        cur.execute(f"PRAGMA index_info({name})")
        cols = [r[2] for r in cur.fetchall()]
        out.append({"name": name, "columns": cols, "origin": origin})
    return out


def diagnose(con: sqlite3.Connection) -> dict:
    """Return diagnosis dict: {has_old, has_new, indexes, row_count}."""
    indexes = list_unique_indexes(con, "filing_analyses")
    has_old = any(idx["columns"] == ["filing_id"] for idx in indexes)
    has_new = any(
        sorted(idx["columns"]) == sorted(["filing_id", "writer_model"])
        for idx in indexes
    )
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM filing_analyses")
    row_count = cur.fetchone()[0]
    return {
        "has_old": has_old,
        "has_new": has_new,
        "indexes": indexes,
        "row_count": row_count,
    }


def get_create_sql(con: sqlite3.Connection, table: str) -> str:
    cur = con.cursor()
    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    row = cur.fetchone()
    return row[0] if row else ""


def rebuild_table(con: sqlite3.Connection) -> None:
    """Rebuild filing_analyses with the new UNIQUE(filing_id, writer_model).

    We materialise the new schema by hand so that the migration does not
    depend on importing webapp.models (which pulls FastAPI etc.). The
    column list MUST stay in sync with webapp/models.py FilingAnalysis.
    """
    cur = con.cursor()
    cur.execute("BEGIN")
    try:
        # 1. Rename old table out of the way.
        cur.execute("ALTER TABLE filing_analyses RENAME TO filing_analyses_old")

        # 2. Create new table with correct constraint. Mirrors
        #    webapp/models.py::FilingAnalysis exactly.
        cur.execute(
            """
            CREATE TABLE filing_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_id INTEGER NOT NULL,
                analyzed_at DATETIME NOT NULL,
                prospectus_url VARCHAR,
                objective_excerpt TEXT,
                strategy_excerpt TEXT,
                filing_title VARCHAR,
                strategy_type VARCHAR,
                underlying VARCHAR,
                structure VARCHAR,
                portfolio_holding VARCHAR,
                distribution VARCHAR,
                narrative TEXT,
                interestingness FLOAT,
                selector_reason VARCHAR,
                selector_model VARCHAR,
                writer_model VARCHAR,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd FLOAT,
                FOREIGN KEY (filing_id) REFERENCES filings (id),
                CONSTRAINT uq_filing_analyses_filing_writer
                    UNIQUE (filing_id, writer_model)
            )
            """
        )

        # 3. Re-create the non-unique lookup index on filing_id.
        cur.execute(
            "CREATE INDEX ix_filing_analyses_filing_id "
            "ON filing_analyses (filing_id)"
        )

        # 4. Copy rows. Dedupe on (filing_id, writer_model) keeping the
        #    NEWEST row (highest id). For rows where writer_model is NULL
        #    in the legacy data, treat NULL as a single bucket per
        #    filing_id (UNIQUE in SQLite allows multiple NULLs but we want
        #    the new constraint to be meaningful — keep newest only).
        cur.execute(
            """
            INSERT INTO filing_analyses
            SELECT * FROM filing_analyses_old
            WHERE id IN (
                SELECT MAX(id) FROM filing_analyses_old
                GROUP BY filing_id, COALESCE(writer_model, '__NULL__')
            )
            """
        )

        # 5. Drop the old table.
        cur.execute("DROP TABLE filing_analyses_old")

        con.commit()
    except Exception:
        con.rollback()
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Actually run the migration (default: dry-run only).")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH),
                   help=f"Path to SQLite DB (default: {DEFAULT_DB_PATH})")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        return 1

    print(f"Inspecting {db_path}")
    con = sqlite3.connect(str(db_path))
    try:
        # Confirm the table exists at all.
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("filing_analyses",),
        )
        if not cur.fetchone():
            print("filing_analyses table does not exist — nothing to migrate.")
            return 0

        diag = diagnose(con)
        print(f"  rows: {diag['row_count']}")
        print(f"  unique indexes ({len(diag['indexes'])}):")
        for idx in diag["indexes"]:
            print(f"    - {idx['name']}: {idx['columns']} (origin={idx['origin']})")

        if diag["has_new"] and not diag["has_old"]:
            print("  Already migrated — UNIQUE(filing_id, writer_model) present.")
            return 0
        if not diag["has_old"]:
            print("  Old UNIQUE(filing_id) not found and new composite "
                  "UNIQUE missing — schema is in an unexpected state. "
                  "Refusing to migrate automatically; investigate manually.")
            return 2

        print()
        print("  Plan: rename -> create new -> copy (dedupe) -> drop old.")
        if not args.apply:
            print("  DRY RUN — pass --apply to execute.")
            return 0

        print("  Applying migration...")
        rebuild_table(con)

        diag2 = diagnose(con)
        print(f"  Done. Rows after migration: {diag2['row_count']}")
        for idx in diag2["indexes"]:
            print(f"    - {idx['name']}: {idx['columns']} (origin={idx['origin']})")
        if diag2["has_new"] and not diag2["has_old"]:
            print("  SUCCESS: new UNIQUE constraint is in place.")
            return 0
        print("  WARNING: post-migration diagnosis does not match expectation.")
        return 3
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

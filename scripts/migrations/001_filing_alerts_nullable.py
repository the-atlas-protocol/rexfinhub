"""Migration: make FilingAlert.trust_id nullable + add Tier 1/2 handoff fields.

Idempotent — safe to run multiple times. Checks the existing schema first and
only rebuilds the table if it has the old layout (NOT NULL trust_id, no cik
column). Preserves all existing rows, marking them as source='legacy' and
enrichment_status=1 (already done).

Why a rebuild: SQLite cannot ALTER a column from NOT NULL to NULL — you have
to recreate the table. New columns (cik, source, enrichment_status, etc.) are
added in the same pass.

Run:
    python -m scripts.migrations.001_filing_alerts_nullable
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"


def needs_migration(conn: sqlite3.Connection) -> bool:
    """True iff filing_alerts has the old schema (trust_id NOT NULL, no cik col)."""
    cur = conn.execute("PRAGMA table_info(filing_alerts)")
    cols = {row[1]: row for row in cur.fetchall()}
    if not cols:
        return False  # table doesn't exist yet; create_all handles it
    trust_id_col = cols.get("trust_id")
    # notnull flag is row[3] in PRAGMA table_info
    trust_id_not_null = trust_id_col is not None and trust_id_col[3] == 1
    missing_cik = "cik" not in cols
    return trust_id_not_null or missing_cik


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE filing_alerts_new (
                id INTEGER NOT NULL PRIMARY KEY,
                trust_id INTEGER,
                cik VARCHAR(20),
                accession_number VARCHAR(30) NOT NULL UNIQUE,
                form_type VARCHAR(20) NOT NULL,
                filed_date DATE,
                detected_at DATETIME NOT NULL,
                processed BOOLEAN NOT NULL,
                source VARCHAR(30),
                enrichment_status INTEGER NOT NULL DEFAULT 0,
                enrichment_error TEXT,
                primary_doc_url TEXT,
                size_bytes INTEGER,
                company_name VARCHAR(200),
                FOREIGN KEY(trust_id) REFERENCES trusts(id)
            )
        """)
        conn.execute("""
            INSERT INTO filing_alerts_new
              (id, trust_id, accession_number, form_type, filed_date,
               detected_at, processed, source, enrichment_status)
            SELECT id, trust_id, accession_number, form_type, filed_date,
                   detected_at, processed, 'legacy', 1
              FROM filing_alerts
        """)
        conn.execute("DROP TABLE filing_alerts")
        conn.execute("ALTER TABLE filing_alerts_new RENAME TO filing_alerts")
        for idx_sql in (
            "CREATE INDEX idx_filing_alerts_trust ON filing_alerts(trust_id)",
            "CREATE INDEX idx_filing_alerts_processed ON filing_alerts(processed)",
            "CREATE INDEX idx_filing_alerts_filed ON filing_alerts(filed_date)",
            "CREATE INDEX idx_filing_alerts_cik ON filing_alerts(cik)",
            "CREATE INDEX idx_filing_alerts_enrichment ON filing_alerts(enrichment_status)",
        ):
            conn.execute(idx_sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def main() -> int:
    if not DB_PATH.exists():
        print(f"No DB at {DB_PATH} — nothing to migrate")
        return 0
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not needs_migration(conn):
            print("filing_alerts already migrated — nothing to do")
            return 0
        before = conn.execute("SELECT COUNT(*) FROM filing_alerts").fetchone()[0]
        print(f"Migrating filing_alerts ({before} rows)...")
        migrate(conn)
        after = conn.execute("SELECT COUNT(*) FROM filing_alerts").fetchone()[0]
        print(f"Done. {after} rows preserved.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

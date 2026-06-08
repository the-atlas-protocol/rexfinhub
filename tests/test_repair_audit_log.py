"""Tests for NEW-01: repair_missing_canonical writes capm_audit_log entries.

Before the fix, rescued/created rows had no audit trail — making it
impossible to reconstruct who minted a canonical_id and when. Now every
rescued/created row produces a single capm_audit_log INSERT row with
source='repair_missing_canonical'.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.repair_missing_canonical import (
    find_orphans,
    repair_orphans,
)


# Mirror the test_repair_missing_canonical DDL + add capm_audit_log.
DDL = [
    """CREATE TABLE filings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trust_id INTEGER,
        accession_number TEXT,
        form TEXT,
        filing_date TEXT,
        primary_link TEXT,
        primary_document TEXT,
        cik TEXT,
        registrant TEXT,
        processed INTEGER DEFAULT 0,
        created_at TEXT
    )""",
    """CREATE TABLE fund_extractions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filing_id INTEGER NOT NULL,
        series_id TEXT,
        series_name TEXT,
        class_contract_id TEXT,
        class_contract_name TEXT,
        class_symbol TEXT,
        extracted_from TEXT,
        effective_date TEXT,
        effective_date_confidence TEXT,
        delaying_amendment INTEGER DEFAULT 0,
        prospectus_name TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE rex_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        trust TEXT,
        product_suite TEXT NOT NULL,
        status TEXT NOT NULL,
        ticker TEXT,
        direction TEXT,
        initial_filing_date TEXT,
        estimated_effective_date TEXT,
        target_listing_date TEXT,
        seed_date TEXT,
        official_listed_date TEXT,
        latest_form TEXT,
        latest_prospectus_link TEXT,
        cik TEXT,
        series_id TEXT,
        class_contract_id TEXT,
        notes TEXT,
        underlying_ticker TEXT,
        underlying_name TEXT,
        canonical_id TEXT,
        status_cached TEXT,
        created_at TEXT,
        updated_at TEXT,
        bb_ticker TEXT
    )""",
    """CREATE TABLE product_master (
        canonical_id TEXT PRIMARY KEY,
        created_at TIMESTAMP NOT NULL,
        fund_name TEXT,
        status_current TEXT,
        is_rex INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE identifier_xref (
        canonical_id TEXT NOT NULL,
        id_type TEXT NOT NULL,
        id_value TEXT NOT NULL,
        valid_from TIMESTAMP NOT NULL,
        valid_to TIMESTAMP,
        source TEXT NOT NULL,
        PRIMARY KEY (canonical_id, id_type, id_value, valid_from)
    )""",
    """CREATE TABLE underlier_master (
        underlier_id TEXT PRIMARY KEY,
        underlier_type TEXT NOT NULL,
        primary_figi TEXT,
        ticker TEXT,
        index_provider TEXT,
        index_code TEXT,
        crypto_base TEXT,
        crypto_quote TEXT,
        display_symbol TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL,
        resolved_via TEXT
    )""",
    """CREATE TABLE fund_underlier (
        canonical_id TEXT NOT NULL,
        underlier_id TEXT NOT NULL,
        weight REAL,
        effective_from TIMESTAMP NOT NULL,
        effective_to TIMESTAMP,
        PRIMARY KEY (canonical_id, underlier_id, effective_from)
    )""",
    """CREATE TABLE capm_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        table_name TEXT NOT NULL,
        row_id INTEGER,
        field_name TEXT,
        old_value TEXT,
        new_value TEXT,
        row_label TEXT,
        changed_by TEXT,
        changed_at TIMESTAMP NOT NULL
    )""",
]


def _build_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for stmt in DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


@pytest.fixture()
def db(tmp_path):
    conn = _build_db(tmp_path / "repair_audit.db")
    yield conn
    conn.close()


def _insert_filing(conn, **kw):
    cur = conn.execute(
        "INSERT INTO filings (form, filing_date, cik, registrant, primary_link) "
        "VALUES (?, ?, ?, ?, ?)",
        (kw["form"], kw["filing_date"], kw["cik"], kw["registrant"],
         kw.get("primary_link", "https://sec.gov/x")),
    )
    return cur.lastrowid


def _insert_extraction(conn, **kw):
    cur = conn.execute(
        "INSERT INTO fund_extractions (filing_id, series_id, series_name, "
        "class_contract_id) VALUES (?, ?, ?, ?)",
        (kw["filing_id"], kw["series_id"], kw["series_name"],
         kw.get("class_contract_id")),
    )
    return cur.lastrowid


def test_apply_writes_audit_log_row_per_created_row(db):
    """Every newly-created rex_products row gets one capm_audit_log entry
    with source='repair_missing_canonical' and action='INSERT'."""
    # Two distinct orphan series — expect 2 audit rows.
    f1 = _insert_filing(db, form="485APOS", filing_date="2026-05-20",
                        cik="0001771146", registrant="ETF Opportunities Trust")
    _insert_extraction(db, filing_id=f1, series_id="S000AAA",
                       series_name="T-REX 2X Long ABC")
    f2 = _insert_filing(db, form="485BPOS", filing_date="2026-05-21",
                        cik="0001771146", registrant="ETF Opportunities Trust")
    _insert_extraction(db, filing_id=f2, series_id="S000BBB",
                       series_name="T-REX 2X Long DEF")
    db.commit()

    orphans = find_orphans(db)
    assert len(orphans) == 2
    stats = repair_orphans(db, orphans, dry_run=False)
    assert stats["rex_products_created"] == 2

    audit_rows = db.execute(
        "SELECT * FROM capm_audit_log WHERE changed_by = 'repair_missing_canonical' "
        "ORDER BY id"
    ).fetchall()
    assert len(audit_rows) == 2
    for row in audit_rows:
        assert row["action"] == "INSERT"
        assert row["table_name"] == "rex_products"
        assert row["field_name"] == "canonical_id"
        assert row["new_value"] is not None
        assert row["row_label"]


def test_dry_run_writes_no_audit_rows(db):
    """Dry-run mode must not write any capm_audit_log rows."""
    f1 = _insert_filing(db, form="485APOS", filing_date="2026-05-20",
                        cik="0001771146", registrant="ETF Opportunities Trust")
    _insert_extraction(db, filing_id=f1, series_id="S000CCC",
                       series_name="T-REX 2X Long GHI")
    db.commit()

    repair_orphans(db, find_orphans(db), dry_run=True)
    assert db.execute("SELECT COUNT(*) FROM capm_audit_log").fetchone()[0] == 0


def test_rescue_existing_rex_row_logs_with_rescue_action(db):
    """When the rex_products row already exists but lacks canonical_id, the
    repair rescues it — the audit row uses action='RESCUE'."""
    f1 = _insert_filing(db, form="485APOS", filing_date="2026-05-20",
                        cik="1771146", registrant="ETF Opportunities Trust")
    _insert_extraction(db, filing_id=f1, series_id="S000DDD",
                       series_name="T-REX 2X Long JKL")
    db.execute(
        "INSERT INTO rex_products (name, product_suite, status, cik, "
        "series_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("T-REX 2X Long JKL", "T-REX", "Filed", "1771146", "S000DDD",
         "2026-05-20T00:00:00", "2026-05-20T00:00:00"),
    )
    db.commit()

    repair_orphans(db, find_orphans(db), dry_run=False)

    audit = db.execute(
        "SELECT * FROM capm_audit_log WHERE changed_by = 'repair_missing_canonical'"
    ).fetchall()
    assert len(audit) == 1
    assert audit[0]["action"] == "RESCUE"

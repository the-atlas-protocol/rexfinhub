"""Tests for scripts.repair_missing_canonical.

Builds a tiny on-disk SQLite fixture that mirrors the real schema for the
five tables the script touches (filings, fund_extractions, rex_products,
product_master, identifier_xref, underlier_master, fund_underlier).

Each test creates its own fixture DB under tmp_path so they never collide
with the real data/etp_tracker.db.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from scripts.repair_missing_canonical import (
    find_orphans,
    repair_orphans,
    snapshot_counts,
)


# ---------------------------------------------------------------------------
# Fixture DB builder
# ---------------------------------------------------------------------------

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
        lmm TEXT,
        exchange TEXT,
        mgt_fee REAL,
        tracking_index TEXT,
        fund_admin TEXT,
        cu_size INTEGER,
        starting_nav REAL,
        notes TEXT,
        competitors TEXT,
        manually_edited_fields TEXT,
        bb_ticker TEXT,
        inception_date TEXT,
        issuer TEXT,
        fixed_fee TEXT,
        variable_fee TEXT,
        cut_off TEXT,
        custodian TEXT,
        our_category TEXT,
        product_type TEXT,
        category TEXT,
        sub_category TEXT,
        leverage TEXT,
        underlying_ticker TEXT,
        underlying_name TEXT,
        expense_ratio REAL,
        bmo_suite TEXT,
        canonical_id TEXT,
        status_cached TEXT,
        created_at TEXT,
        updated_at TEXT
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
]


def _build_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for stmt in DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def _insert_filing(conn: sqlite3.Connection, *, form: str, filing_date: str,
                   cik: str, registrant: str, primary_link: str = "https://sec.gov/x") -> int:
    cur = conn.execute(
        "INSERT INTO filings (form, filing_date, cik, registrant, primary_link) "
        "VALUES (?, ?, ?, ?, ?)",
        (form, filing_date, cik, registrant, primary_link),
    )
    return cur.lastrowid


def _insert_extraction(conn: sqlite3.Connection, *, filing_id: int, series_id: str,
                       series_name: str, class_contract_id: str | None = None,
                       class_symbol: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO fund_extractions (filing_id, series_id, series_name, "
        "class_contract_id, class_symbol) VALUES (?, ?, ?, ?, ?)",
        (filing_id, series_id, series_name, class_contract_id, class_symbol),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_conn(tmp_path):
    path = tmp_path / "repair_test.db"
    conn = _build_db(path)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFindOrphans:
    def test_finds_rex_named_extraction_without_xref(self, db_conn):
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="0001771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000999111",
                           series_name="T-REX 2X Long NVDA Daily Target ETF")
        db_conn.commit()

        orphans = find_orphans(db_conn)
        assert len(orphans) == 1
        assert orphans[0]["series_id"] == "S000999111"

    def test_skips_extraction_with_existing_xref(self, db_conn):
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="0001771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000999222",
                           series_name="T-REX 2X Long XYZ Daily Target ETF")
        # Pre-existing xref entry — this row is NOT an orphan.
        db_conn.execute(
            "INSERT INTO identifier_xref "
            "(canonical_id, id_type, id_value, valid_from, source) "
            "VALUES (?, ?, ?, ?, ?)",
            ("existing-uuid", "series_id", "S000999222", "2026-05-20", "test"),
        )
        db_conn.commit()

        assert find_orphans(db_conn) == []

    def test_skips_non_rex_named(self, db_conn):
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="9999999", registrant="Direxion Funds")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000888888",
                           series_name="Direxion Daily NVDA Bull 2X Shares")
        db_conn.commit()

        assert find_orphans(db_conn) == []

    def test_dedups_multiple_filings_same_series(self, db_conn):
        # Same series_id, two filings; expect a single orphan row.
        f1 = _insert_filing(db_conn, form="485APOS", filing_date="2026-04-10",
                            cik="0001771146", registrant="ETF Opportunities Trust")
        f2 = _insert_filing(db_conn, form="485BPOS", filing_date="2026-05-20",
                            cik="0001771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=f1, series_id="S000111111",
                           series_name="T-REX 2X Long FOO")
        _insert_extraction(db_conn, filing_id=f2, series_id="S000111111",
                           series_name="T-REX 2X Long FOO")
        db_conn.commit()

        orphans = find_orphans(db_conn)
        assert len(orphans) == 1
        # Newest filing wins -> 485BPOS.
        assert orphans[0]["form"] == "485BPOS"

    def test_since_filter(self, db_conn):
        f_old = _insert_filing(db_conn, form="485APOS", filing_date="2026-01-05",
                               cik="0001771146", registrant="ETF Opportunities Trust")
        f_new = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-25",
                               cik="0001771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=f_old, series_id="S000AAA",
                           series_name="T-REX 2X Long A")
        _insert_extraction(db_conn, filing_id=f_new, series_id="S000BBB",
                           series_name="T-REX 2X Long B")
        db_conn.commit()

        orphans = find_orphans(db_conn, since=date(2026, 5, 1))
        assert {o["series_id"] for o in orphans} == {"S000BBB"}


class TestRepairOrphans:
    def test_creates_full_chain_for_orphan(self, db_conn):
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="0001771146", registrant="ETF Opportunities Trust",
                             primary_link="https://sec.gov/Archives/trex.htm")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000777777",
                           series_name="T-REX 2X Long ABC Daily Target ETF",
                           class_contract_id="C000666666")
        db_conn.commit()

        before = snapshot_counts(db_conn)
        assert before["rex_products"] == 0
        assert before["product_master"] == 0
        assert before["identifier_xref_series"] == 0

        orphans = find_orphans(db_conn)
        stats = repair_orphans(db_conn, orphans, dry_run=False)

        assert stats["rex_products_created"] == 1
        assert stats["rex_products_reused"] == 0
        assert stats["identifier_xref_inserted"] >= 2  # at least cik + series_id

        # Confirm rex_products row landed with the right fields.
        rex = db_conn.execute("SELECT * FROM rex_products").fetchone()
        assert rex["series_id"] == "S000777777"
        assert rex["cik"] == "1771146"   # leading zeros stripped
        assert rex["latest_form"] == "485APOS"
        assert rex["status"] == "Filed"  # 485APOS -> Filed
        assert rex["canonical_id"] is not None

        # product_master row exists and matches.
        pm = db_conn.execute("SELECT * FROM product_master").fetchone()
        assert pm["canonical_id"] == rex["canonical_id"]
        assert pm["is_rex"] == 1  # T-REX is a REX suite

        # identifier_xref contains the series_id we expected.
        xref = db_conn.execute(
            "SELECT * FROM identifier_xref WHERE id_type='series_id'"
        ).fetchone()
        assert xref["id_value"] == "S000777777"
        assert xref["canonical_id"] == rex["canonical_id"]

    def test_485BPOS_lands_as_effective(self, db_conn):
        fid = _insert_filing(db_conn, form="485BPOS", filing_date="2026-05-20",
                             cik="1771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000222222",
                           series_name="T-REX 2X Long DEF Daily Target ETF")
        db_conn.commit()

        repair_orphans(db_conn, find_orphans(db_conn), dry_run=False)

        rex = db_conn.execute("SELECT * FROM rex_products").fetchone()
        assert rex["status"] == "Effective"

    def test_reuses_existing_rex_product(self, db_conn):
        """rex_products row already exists but lacks canonical_id chain."""
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="1771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000333333",
                           series_name="T-REX 2X Long GHI")
        # Pre-existing rex row, no canonical_id (the orphan case we expect in prod).
        db_conn.execute(
            "INSERT INTO rex_products (name, product_suite, status, cik, series_id, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("T-REX 2X Long GHI", "T-REX", "Filed", "1771146", "S000333333",
             "2026-05-20T00:00:00", "2026-05-20T00:00:00"),
        )
        db_conn.commit()

        stats = repair_orphans(db_conn, find_orphans(db_conn), dry_run=False)

        assert stats["rex_products_created"] == 0
        assert stats["rex_products_reused"] == 1
        assert db_conn.execute("SELECT COUNT(*) FROM rex_products").fetchone()[0] == 1
        # canonical_id was minted on the existing row.
        rex = db_conn.execute("SELECT * FROM rex_products").fetchone()
        assert rex["canonical_id"] is not None

    def test_dry_run_writes_nothing(self, db_conn):
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="1771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000444444",
                           series_name="T-REX 2X Long JKL")
        db_conn.commit()

        before = snapshot_counts(db_conn)
        repair_orphans(db_conn, find_orphans(db_conn), dry_run=True)
        after = snapshot_counts(db_conn)

        assert before == after

    def test_idempotent_rerun(self, db_conn):
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="1771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000555555",
                           series_name="T-REX 2X Long MNO")
        db_conn.commit()

        # First run repairs.
        repair_orphans(db_conn, find_orphans(db_conn), dry_run=False)
        snap1 = snapshot_counts(db_conn)

        # Second run: orphan list is empty, nothing to do.
        orphans2 = find_orphans(db_conn)
        assert orphans2 == []
        repair_orphans(db_conn, orphans2, dry_run=False)
        snap2 = snapshot_counts(db_conn)

        assert snap1 == snap2

    def test_links_fund_underlier_when_resolvable(self, db_conn):
        fid = _insert_filing(db_conn, form="485APOS", filing_date="2026-05-20",
                             cik="1771146", registrant="ETF Opportunities Trust")
        _insert_extraction(db_conn, filing_id=fid, series_id="S000666666",
                           series_name="T-REX 2X Long NVDA Daily Target ETF")
        # Pre-existing rex_products row with an underlying_ticker that matches
        # an underlier_master entry.
        db_conn.execute(
            "INSERT INTO rex_products (name, product_suite, status, cik, series_id, "
            "underlying_ticker, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("T-REX 2X Long NVDA", "T-REX", "Filed", "1771146", "S000666666",
             "NVDA", "2026-05-20T00:00:00", "2026-05-20T00:00:00"),
        )
        db_conn.execute(
            "INSERT INTO underlier_master (underlier_id, underlier_type, "
            "display_symbol, created_at) VALUES (?, ?, ?, ?)",
            ("u-nvda", "equity", "NVDA", "2026-05-20T00:00:00"),
        )
        db_conn.commit()

        stats = repair_orphans(db_conn, find_orphans(db_conn), dry_run=False)

        assert stats["fund_underlier_linked"] == 1
        link = db_conn.execute("SELECT * FROM fund_underlier").fetchone()
        assert link["underlier_id"] == "u-nvda"

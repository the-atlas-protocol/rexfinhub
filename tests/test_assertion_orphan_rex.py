"""Tests for the `no_orphan_rex_extractions` assertion in scripts/run_assertions.

The orphan-prevention assertion catches the failure mode that the
sprint1/repair-orphans script cleared (38 orphans): a REX-name series in
fund_extractions inside the watermark window with no matching
identifier_xref(id_type='series_id') row.

Each test builds a minimal on-disk SQLite fixture with the four tables the
assertion touches (filings, fund_extractions, identifier_xref, plus an empty
no-op for the watermark file).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from scripts.run_assertions import assert_no_orphan_rex_extractions


DDL = [
    """CREATE TABLE filings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        form TEXT,
        filing_date TEXT,
        cik TEXT,
        registrant TEXT,
        primary_link TEXT
    )""",
    """CREATE TABLE fund_extractions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filing_id INTEGER NOT NULL,
        series_id TEXT,
        series_name TEXT,
        class_contract_id TEXT,
        class_symbol TEXT
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
]


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    """Build a fixture DB and isolate from the real watermark file.

    The assertion reads data/.sync_rex_products_watermark to widen the window;
    we point PROJECT_ROOT at tmp_path so the watermark file is absent and the
    assertion falls back to the 30-day window.
    """
    # Isolate the watermark lookup — no data/ dir under tmp_path means
    # watermark_file.exists() returns False and the 30-day default kicks in.
    monkeypatch.setattr("scripts.run_assertions.PROJECT_ROOT", tmp_path)

    path = tmp_path / "assertion_test.db"
    conn = sqlite3.connect(str(path))
    for stmt in DDL:
        conn.execute(stmt)
    conn.commit()
    yield conn
    conn.close()


def _insert_filing(conn, *, form: str, filing_date: str,
                   cik: str = "0001771146",
                   registrant: str = "ETF Opportunities Trust") -> int:
    cur = conn.execute(
        "INSERT INTO filings (form, filing_date, cik, registrant, primary_link) "
        "VALUES (?, ?, ?, ?, ?)",
        (form, filing_date, cik, registrant, "https://sec.gov/x"),
    )
    return cur.lastrowid


def _insert_extraction(conn, *, filing_id: int, series_id: str,
                       series_name: str) -> int:
    cur = conn.execute(
        "INSERT INTO fund_extractions (filing_id, series_id, series_name) "
        "VALUES (?, ?, ?)",
        (filing_id, series_id, series_name),
    )
    return cur.lastrowid


def _recent_date() -> str:
    """Date inside the 30-day watermark window."""
    return (date.today() - timedelta(days=3)).isoformat()


def _old_date() -> str:
    """Date well outside the 30-day window."""
    return (date.today() - timedelta(days=120)).isoformat()


class TestAssertionOrphanRex:
    def test_one_orphan_fails(self, db_conn):
        """Single REX-name extraction with no xref inside the window -> FAIL."""
        fid = _insert_filing(db_conn, form="485APOS", filing_date=_recent_date())
        _insert_extraction(
            db_conn, filing_id=fid, series_id="S000999111",
            series_name="T-REX 2X Long NVDA Daily Target ETF",
        )
        db_conn.commit()

        passed, count, sample, detail = assert_no_orphan_rex_extractions(db_conn)
        assert passed is False
        assert count == 1
        assert sample[0]["series_id"] == "S000999111"
        assert "S000999111" in detail or "1 REX-name" in detail

    def test_xref_present_passes(self, db_conn):
        """REX extraction with matching identifier_xref(series_id) -> PASS."""
        fid = _insert_filing(db_conn, form="485APOS", filing_date=_recent_date())
        _insert_extraction(
            db_conn, filing_id=fid, series_id="S000111222",
            series_name="T-REX 2X Long FOO",
        )
        db_conn.execute(
            "INSERT INTO identifier_xref "
            "(canonical_id, id_type, id_value, valid_from, source) "
            "VALUES (?, ?, ?, ?, ?)",
            ("uuid-1", "series_id", "S000111222", "2026-05-20", "test"),
        )
        db_conn.commit()

        passed, count, _, _ = assert_no_orphan_rex_extractions(db_conn)
        assert passed is True
        assert count == 0

    def test_competitor_extraction_ignored(self, db_conn):
        """Direxion / ProShares / etc. are not REX — should not flag."""
        fid = _insert_filing(
            db_conn, form="485APOS", filing_date=_recent_date(),
            registrant="Direxion Funds",
        )
        _insert_extraction(
            db_conn, filing_id=fid, series_id="S000888888",
            series_name="Direxion Daily NVDA Bull 2X Shares",
        )
        db_conn.commit()

        passed, count, _, _ = assert_no_orphan_rex_extractions(db_conn)
        assert passed is True
        assert count == 0

    def test_old_orphan_outside_window_ignored(self, db_conn):
        """Orphan from 120 days ago is outside the 30-day window -> PASS."""
        fid = _insert_filing(db_conn, form="485APOS", filing_date=_old_date())
        _insert_extraction(
            db_conn, filing_id=fid, series_id="S000ANCIENT",
            series_name="T-REX 2X Long Ancient",
        )
        db_conn.commit()

        passed, count, _, _ = assert_no_orphan_rex_extractions(db_conn)
        assert passed is True
        assert count == 0

    def test_dedup_across_filings(self, db_conn):
        """Same series_id across two filings counts once."""
        f1 = _insert_filing(db_conn, form="485APOS",
                            filing_date=(date.today() - timedelta(days=10)).isoformat())
        f2 = _insert_filing(db_conn, form="485BPOS",
                            filing_date=(date.today() - timedelta(days=3)).isoformat())
        _insert_extraction(db_conn, filing_id=f1, series_id="S000DEDUP",
                           series_name="T-REX 2X Long FOO")
        _insert_extraction(db_conn, filing_id=f2, series_id="S000DEDUP",
                           series_name="T-REX 2X Long FOO")
        db_conn.commit()

        passed, count, _, _ = assert_no_orphan_rex_extractions(db_conn)
        assert passed is False
        assert count == 1

    def test_microsectors_flagged(self, db_conn):
        """MicroSectors is a REX-licensed brand — its orphans must flag."""
        fid = _insert_filing(db_conn, form="485APOS", filing_date=_recent_date())
        _insert_extraction(
            db_conn, filing_id=fid, series_id="S000MICRO",
            series_name="MicroSectors FANG+ Index 3X Leveraged ETN",
        )
        db_conn.commit()

        passed, count, _, _ = assert_no_orphan_rex_extractions(db_conn)
        assert passed is False
        assert count == 1

    def test_registrant_matches_when_name_blank(self, db_conn):
        """If extraction series_name is blank, fall back to registrant."""
        fid = _insert_filing(
            db_conn, form="485APOS", filing_date=_recent_date(),
            registrant="T-REX Trust",
        )
        _insert_extraction(db_conn, filing_id=fid, series_id="S000VIAREG",
                           series_name="")
        db_conn.commit()

        passed, count, _, _ = assert_no_orphan_rex_extractions(db_conn)
        assert passed is False
        assert count == 1

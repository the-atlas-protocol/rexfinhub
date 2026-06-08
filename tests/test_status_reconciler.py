"""Tests for webapp.services.status_reconciler.gather_evidence + derive_status.

Locks in the BUG-14/CIC-01 fix: gather_evidence must join through
rex_products.ticker (the fund's own canonical ticker), NOT through
identifier_xref.id_value (any mapped ticker — sibling fund, underlying
stock, recycled ticker, etc.). Otherwise a fund false-promotes to Listed
whenever an unrelated xref entry happens to point at a Bloomberg ACTV
ticker.

Each test builds an on-disk SQLite fixture under tmp_path that mirrors the
real schema for the four tables the reconciler touches (rex_products,
product_master, status_history, identifier_xref, mkt_master_data).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from webapp.services.status_reconciler import (
    STATUS_EFFECTIVE,
    STATUS_LISTED,
    STATUS_UNDER_CONSIDERATION,
    append_transition,
    derive_status,
    gather_evidence,
)


# ---------------------------------------------------------------------------
# Fixture DDL — minimal mirror of the production schema
# ---------------------------------------------------------------------------

DDL = [
    """CREATE TABLE rex_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        ticker TEXT,
        status TEXT,
        status_cached TEXT,
        latest_form TEXT,
        official_listed_date TEXT,
        estimated_effective_date TEXT,
        initial_filing_date TEXT,
        inception_date TEXT,
        canonical_id TEXT
    )""",
    """CREATE TABLE product_master (
        canonical_id TEXT PRIMARY KEY,
        created_at TIMESTAMP NOT NULL,
        fund_name TEXT,
        status_current TEXT,
        is_rex INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_id TEXT NOT NULL,
        status TEXT NOT NULL,
        valid_from TIMESTAMP NOT NULL,
        valid_to TIMESTAMP,
        tx_from TIMESTAMP NOT NULL,
        tx_to TIMESTAMP,
        source TEXT,
        evidence TEXT,
        set_by TEXT,
        created_at TIMESTAMP
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
    """CREATE TABLE mkt_master_data (
        ticker TEXT PRIMARY KEY,
        ticker_clean TEXT,
        market_status TEXT,
        inception_date TEXT,
        fund_name TEXT
    )""",
]


def _build_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for stmt in DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def _insert_product(conn: sqlite3.Connection, *, name: str, ticker: str | None,
                    canonical_id: str, status: str = "Filed",
                    latest_form: str = "485APOS",
                    effective_date: str | None = None) -> None:
    conn.execute(
        "INSERT INTO rex_products (name, ticker, status, latest_form, "
        "estimated_effective_date, canonical_id) VALUES (?, ?, ?, ?, ?, ?)",
        (name, ticker, status, latest_form, effective_date, canonical_id),
    )
    conn.execute(
        "INSERT INTO product_master (canonical_id, created_at, fund_name, "
        "status_current, is_rex) VALUES (?, ?, ?, ?, ?)",
        (canonical_id, datetime.utcnow().isoformat(), name, status, 1),
    )


def _insert_xref(conn: sqlite3.Connection, canonical_id: str,
                 id_type: str, id_value: str) -> None:
    conn.execute(
        "INSERT INTO identifier_xref (canonical_id, id_type, id_value, "
        "valid_from, source) VALUES (?, ?, ?, ?, ?)",
        (canonical_id, id_type, id_value, "2026-01-01T00:00:00", "test"),
    )


def _insert_mkt(conn: sqlite3.Connection, *, ticker: str, market_status: str,
                inception_date: str | None = "2026-05-01",
                fund_name: str | None = None) -> None:
    conn.execute(
        "INSERT INTO mkt_master_data (ticker, ticker_clean, market_status, "
        "inception_date, fund_name) VALUES (?, ?, ?, ?, ?)",
        (ticker, ticker, market_status, inception_date, fund_name or ticker),
    )


@pytest.fixture()
def db(tmp_path):
    conn = _build_db(tmp_path / "reconciler_test.db")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cross_product_xref_contamination_does_not_promote(db):
    """BUG-14: fund A has an identifier_xref entry for SOUN (its underlying
    stock); SOUN appears in mkt_master_data with market_status='ACTV'.

    Pre-fix: gather_evidence joined identifier_xref -> mkt_master_data on
    id_value=ticker, so it picked up SOUN's ACTV and false-promoted fund A
    to Listed.

    Post-fix: the join is through rex_products.ticker (the fund's OWN
    canonical ticker), so SOUN's ACTV is ignored when evaluating fund A.
    """
    cid = str(uuid.uuid4())
    # Fund A's own ticker is FOOX (not yet listed — not in mkt_master_data).
    _insert_product(db, name="REX FOOX Fund", ticker="FOOX", canonical_id=cid)
    # Stale/underlier xref pointing at SOUN — a totally unrelated stock.
    _insert_xref(db, cid, "ticker", "SOUN")
    # SOUN is an ACTV stock in Bloomberg.
    _insert_mkt(db, ticker="SOUN", market_status="ACTV")
    db.commit()

    ev = gather_evidence(db, cid)
    assert ev["bloomberg_actv_evidence"] is False, (
        "Fund A should NOT see SOUN's ACTV via xref contamination"
    )
    assert derive_status(ev) != STATUS_LISTED


def test_single_fund_actv_promotes_to_listed(db):
    """Positive case: a fund whose OWN ticker is ACTV promotes to Listed."""
    cid = str(uuid.uuid4())
    _insert_product(db, name="REX BAR Fund", ticker="BARX", canonical_id=cid,
                    status="Effective", latest_form="485BPOS",
                    effective_date="2026-01-15")
    _insert_xref(db, cid, "ticker", "BARX")
    _insert_mkt(db, ticker="BARX", market_status="ACTV",
                inception_date="2026-05-01")
    db.commit()

    ev = gather_evidence(db, cid)
    assert ev["bloomberg_actv_evidence"] is True
    assert derive_status(ev) == STATUS_LISTED


def test_sec_effective_evidence_promotes_to_effective(db):
    """485BPOS landed with an estimated_effective_date in the past, no
    Bloomberg ACTV yet -> Effective."""
    cid = str(uuid.uuid4())
    _insert_product(db, name="REX BAZ Fund", ticker="BAZX", canonical_id=cid,
                    status="Effective", latest_form="485BPOS",
                    effective_date="2026-01-15")
    db.commit()

    ev = gather_evidence(db, cid)
    assert ev["sec_effective_evidence"] is True
    assert ev["bloomberg_actv_evidence"] is False
    assert derive_status(ev) == STATUS_EFFECTIVE


def test_liqu_demotes_from_listed(db):
    """A fund previously Listed whose own ticker now shows market_status=LIQU
    derives to 'delisted'."""
    cid = str(uuid.uuid4())
    _insert_product(db, name="REX QUX Fund", ticker="QUXX", canonical_id=cid,
                    status="Listed", latest_form="485BPOS")
    _insert_xref(db, cid, "ticker", "QUXX")
    _insert_mkt(db, ticker="QUXX", market_status="LIQU")
    db.commit()

    ev = gather_evidence(db, cid)
    assert ev["bloomberg_liqu_evidence"] is True
    assert derive_status(ev) == "delisted"


def test_status_cached_synced_on_append_transition(db):
    """append_transition must update both rex_products.status_cached AND
    rex_products.status when it writes a new status_history row."""
    cid = str(uuid.uuid4())
    _insert_product(db, name="REX SYNC Fund", ticker="SNCX", canonical_id=cid,
                    status="Filed", latest_form="485BPOS",
                    effective_date="2026-01-15")
    _insert_xref(db, cid, "ticker", "SNCX")
    _insert_mkt(db, ticker="SNCX", market_status="ACTV")
    db.commit()

    append_transition(db, cid, old_status=None, new_status="listed",
                      evidence={"foo": "bar"})
    db.commit()

    row = db.execute(
        "SELECT status, status_cached FROM rex_products WHERE canonical_id = ?",
        (cid,),
    ).fetchone()
    assert row["status_cached"] == "listed"
    assert row["status"] == "Listed"


def test_dry_run_idempotent(db):
    """Two consecutive gather_evidence + derive_status passes produce
    identical results, no DB writes."""
    cid = str(uuid.uuid4())
    _insert_product(db, name="REX IDEM Fund", ticker="IDMX", canonical_id=cid,
                    status="Effective", latest_form="485BPOS",
                    effective_date="2026-01-15")
    _insert_xref(db, cid, "ticker", "IDMX")
    _insert_mkt(db, ticker="IDMX", market_status="ACTV")
    db.commit()

    ev1 = gather_evidence(db, cid)
    proposed1 = derive_status(ev1)
    pre_history = db.execute("SELECT COUNT(*) FROM status_history").fetchone()[0]
    pre_product = db.execute("SELECT status, status_cached FROM rex_products "
                              "WHERE canonical_id = ?", (cid,)).fetchone()

    ev2 = gather_evidence(db, cid)
    proposed2 = derive_status(ev2)
    post_history = db.execute("SELECT COUNT(*) FROM status_history").fetchone()[0]
    post_product = db.execute("SELECT status, status_cached FROM rex_products "
                               "WHERE canonical_id = ?", (cid,)).fetchone()

    assert ev1 == ev2
    assert proposed1 == proposed2
    assert pre_history == post_history == 0
    assert tuple(pre_product) == tuple(post_product)


def test_null_canonical_id_skipped(db):
    """gather_evidence on a canonical_id that doesn't exist returns {}."""
    ev = gather_evidence(db, "no-such-canonical-id")
    assert ev == {}
    # derive_status on empty evidence -> under_consideration
    assert derive_status(ev) == STATUS_UNDER_CONSIDERATION


def test_multi_series_fund_uses_own_ticker(db):
    """Series A and Series B of the same fund have distinct tickers and
    distinct canonical_ids. If only Series B's ticker is ACTV in Bloomberg,
    promoting Series A would be wrong — its own ticker is not ACTV.

    This is the per-series correctness check called out in handoff §7.
    """
    cid_a = str(uuid.uuid4())
    cid_b = str(uuid.uuid4())
    _insert_product(db, name="REX MULTI Series A", ticker="MULAX",
                    canonical_id=cid_a, status="Effective",
                    latest_form="485BPOS", effective_date="2026-01-15")
    _insert_product(db, name="REX MULTI Series B", ticker="MULBX",
                    canonical_id=cid_b, status="Effective",
                    latest_form="485BPOS", effective_date="2026-01-15")
    _insert_xref(db, cid_a, "ticker", "MULAX")
    _insert_xref(db, cid_b, "ticker", "MULBX")
    # Only Series B is on the market.
    _insert_mkt(db, ticker="MULBX", market_status="ACTV")
    db.commit()

    ev_a = gather_evidence(db, cid_a)
    ev_b = gather_evidence(db, cid_b)
    assert ev_a["bloomberg_actv_evidence"] is False
    assert ev_b["bloomberg_actv_evidence"] is True
    assert derive_status(ev_a) == STATUS_EFFECTIVE
    assert derive_status(ev_b) == STATUS_LISTED

"""Smoke tests for etp_tracker.reconciler — REX lifecycle matching + PEND/ACTV.

Covers the three new functions added 2026-05-12:
    - match_rex_products()      multi-key (cik, trust, issuer) fallback chain
    - promote_pend_to_actv()    mkt_master_data PEND -> ACTV when past inception
    - backfill_missing_cik()    rex_products.cik <- trusts.cik via trust name

These run on the in-memory SQLite engine in tests/conftest.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from etp_tracker.reconciler import (
    _normalize_issuer,
    _normalize_ticker,
    _parse_inception,
    backfill_missing_cik,
    match_rex_products,
    promote_pend_to_actv,
)
from webapp.models import (
    ClassificationAuditLog,
    Filing,
    MktMasterData,
    RexProduct,
    Trust,
)


# ---------------------------------------------------------------------------
# Normalizer unit tests
# ---------------------------------------------------------------------------

class TestNormalizers:
    def test_normalize_ticker_strips_us_suffix(self):
        assert _normalize_ticker("NVDX US") == "NVDX"
        assert _normalize_ticker("nvdx us") == "NVDX"
        assert _normalize_ticker(" BRKB ") == "BRKB"

    def test_normalize_ticker_handles_empty(self):
        assert _normalize_ticker(None) == ""
        assert _normalize_ticker("") == ""

    def test_normalize_issuer_strips_suffixes(self):
        assert "rex etf" not in _normalize_issuer("REX ETF Trust")
        # Case-insensitive
        assert _normalize_issuer("REX Series Trust") == _normalize_issuer("rex series trust")

    def test_parse_inception_iso(self):
        assert _parse_inception("2024-01-15") == date(2024, 1, 15)
        assert _parse_inception("2024-01-15T00:00:00") == date(2024, 1, 15)

    def test_parse_inception_alternates(self):
        assert _parse_inception("01/15/2024") == date(2024, 1, 15)
        assert _parse_inception("2024/01/15") == date(2024, 1, 15)

    def test_parse_inception_invalid(self):
        assert _parse_inception(None) is None
        assert _parse_inception("") is None
        assert _parse_inception("not-a-date") is None


# ---------------------------------------------------------------------------
# match_rex_products() — multi-key fallback
# ---------------------------------------------------------------------------

class TestMatchRexProducts:
    def test_cik_match_within_window(self, db_session):
        """Primary path: rex_products.cik matches a Filing.cik within window."""
        trust = Trust(cik="0001111", name="REX Test Trust", slug="rex-test", is_rex=True)
        db_session.add(trust)
        db_session.flush()
        db_session.add(Filing(
            trust_id=trust.id, accession_number="0001111-26-000001",
            form="485BPOS", filing_date=date.today() - timedelta(days=10),
            cik="0001111", registrant="REX Test Trust",
        ))
        db_session.add(RexProduct(
            name="Test 2X Bull", product_suite="T-REX", status="Filed",
            ticker="TBUL", cik="0001111", trust="REX Test Trust",
        ))
        db_session.commit()

        stats = match_rex_products(db_session)
        assert stats.total_rex_products == 1
        assert stats.matched == 1
        assert stats.matched_by_cik == 1
        assert stats.matched_by_trust == 0

    def test_trust_fallback_when_cik_null(self, db_session):
        """rex_products with NULL cik but valid trust name -> match via trust_id."""
        trust = Trust(cik="0002222", name="REX Other Trust", slug="rex-other", is_rex=True)
        db_session.add(trust)
        db_session.flush()
        db_session.add(Filing(
            trust_id=trust.id, accession_number="0002222-26-000001",
            form="485APOS", filing_date=date.today() - timedelta(days=30),
            cik="0002222", registrant="REX Other Trust",
        ))
        db_session.add(RexProduct(
            name="NoCikFund", product_suite="T-REX", status="Filed",
            ticker="NCK", cik=None, trust="REX Other Trust",
        ))
        db_session.commit()

        stats = match_rex_products(db_session)
        assert stats.matched == 1
        assert stats.matched_by_cik == 0
        assert stats.matched_by_trust == 1

    def test_window_excludes_old_filings(self, db_session):
        """Filings older than the 90-day window should not produce a match."""
        trust = Trust(cik="0003333", name="REX Stale Trust", slug="rex-stale", is_rex=True)
        db_session.add(trust)
        db_session.flush()
        db_session.add(Filing(
            trust_id=trust.id, accession_number="0003333-20-000001",
            form="485BPOS", filing_date=date.today() - timedelta(days=400),
            cik="0003333", registrant="REX Stale Trust",
        ))
        db_session.add(RexProduct(
            name="Stale Fund", product_suite="T-REX", status="Filed",
            ticker="STL", cik="0003333", trust="REX Stale Trust",
        ))
        db_session.commit()

        stats = match_rex_products(db_session, window_days=90)
        assert stats.total_rex_products == 1
        assert stats.matched == 0
        assert stats.unmatched == 1

    def test_unmatched_when_no_signal(self, db_session):
        """Product with no CIK + no matching trust -> unmatched."""
        db_session.add(RexProduct(
            name="Orphan Fund", product_suite="T-REX", status="Filed",
            ticker="ORF", cik=None, trust=None,
        ))
        db_session.commit()

        stats = match_rex_products(db_session)
        assert stats.matched == 0
        assert stats.unmatched == 1


# ---------------------------------------------------------------------------
# promote_pend_to_actv() — mkt_master_data status flip
# ---------------------------------------------------------------------------

class TestPromotePendToActv:
    def test_dry_run_does_not_mutate(self, db_session):
        row = MktMasterData(
            ticker="DGOO", market_status="PEND",
            inception_date="2022-12-01",
        )
        db_session.add(row)
        db_session.commit()

        stats = promote_pend_to_actv(db_session, dry_run=True)
        assert stats.candidates == 1
        assert stats.promoted == 0
        db_session.refresh(row)
        assert row.market_status == "PEND"

    def test_apply_promotes_past_inception(self, db_session):
        row = MktMasterData(
            ticker="ARMX", market_status="PEND",
            inception_date="2025-05-19",
        )
        db_session.add(row)
        db_session.commit()

        stats = promote_pend_to_actv(db_session, dry_run=False)
        assert stats.promoted == 1
        db_session.refresh(row)
        assert row.market_status == "ACTV"
        # Audit log written
        audit_rows = db_session.query(ClassificationAuditLog).filter_by(
            ticker="ARMX", column_name="market_status",
        ).all()
        assert len(audit_rows) == 1
        assert audit_rows[0].old_value == "PEND"
        assert audit_rows[0].new_value == "ACTV"
        assert audit_rows[0].source == "reconciler"

    def test_future_inception_not_promoted(self, db_session):
        future = (date.today() + timedelta(days=60)).isoformat()
        row = MktMasterData(
            ticker="FUTR", market_status="PEND",
            inception_date=future,
        )
        db_session.add(row)
        db_session.commit()

        stats = promote_pend_to_actv(db_session, dry_run=False)
        assert stats.candidates == 0
        assert stats.promoted == 0
        db_session.refresh(row)
        assert row.market_status == "PEND"

    def test_null_inception_not_promoted(self, db_session):
        row = MktMasterData(
            ticker="NULL", market_status="PEND",
            inception_date=None,
        )
        db_session.add(row)
        db_session.commit()

        stats = promote_pend_to_actv(db_session, dry_run=False)
        assert stats.promoted == 0


# ---------------------------------------------------------------------------
# backfill_missing_cik() — rex_products.cik <- trusts.cik via trust name
# ---------------------------------------------------------------------------

class TestBackfillMissingCik:
    def test_backfills_when_trust_name_matches(self, db_session):
        trust = Trust(cik="0009998", name="REX Backfill Trust", slug="rex-bf", is_rex=True)
        db_session.add(trust)
        db_session.flush()
        prod = RexProduct(
            name="BackfillMe", product_suite="T-REX", status="Filed",
            ticker="BF", cik=None, trust="REX Backfill Trust",
        )
        db_session.add(prod)
        db_session.commit()

        stats = backfill_missing_cik(db_session, dry_run=False)
        assert stats.candidates == 1
        assert stats.backfilled == 1
        db_session.refresh(prod)
        assert prod.cik == "0009998"

    def test_dry_run_does_not_mutate(self, db_session):
        trust = Trust(cik="0009997", name="REX DryRun Trust", slug="rex-dr", is_rex=True)
        db_session.add(trust)
        db_session.flush()
        prod = RexProduct(
            name="DryRunMe", product_suite="T-REX", status="Filed",
            ticker="DR", cik=None, trust="REX DryRun Trust",
        )
        db_session.add(prod)
        db_session.commit()

        stats = backfill_missing_cik(db_session, dry_run=True)
        assert stats.candidates == 1
        assert stats.backfilled == 1  # counted as if applied
        db_session.refresh(prod)
        assert prod.cik is None       # but DB unchanged

    def test_no_trust_match(self, db_session):
        prod = RexProduct(
            name="NoMatch", product_suite="T-REX", status="Filed",
            ticker="NM", cik=None, trust="Trust That Does Not Exist",
        )
        db_session.add(prod)
        db_session.commit()

        stats = backfill_missing_cik(db_session, dry_run=False)
        assert stats.candidates == 1
        assert stats.backfilled == 0
        assert stats.no_trust_match == 1

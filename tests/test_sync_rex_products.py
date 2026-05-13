"""Tests for scripts.sync_rex_products_from_filings.

Validates the five behaviors specified in the implementation brief:

    1. NEW fund creation from a fresh 485APOS in a curated trust.
    2. 485APOS -> 485BPOS transition flips status Filed -> Effective.
    3. manually_edited_fields is respected (no overwrite).
    4. Re-running the sync is idempotent (no duplicate creates).
    5. No duplicate creation when a rex_products row already matches a filing.

Plus Phase 3 activation from mkt_master_data.

These run against the in-memory SQLite engine in tests/conftest.py.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts.sync_rex_products_from_filings import (
    phase1_2_sync_filings,
    phase3_activate_from_market,
)
from webapp.models import (
    Filing,
    FundExtraction,
    MktMasterData,
    RexProduct,
    Trust,
)


# A CIK that lives in etp_tracker.trusts.TRUST_CIKS — "ETF Opportunities Trust"
# (Tuttle/T-REX). Tests pin to this so the curated-trust gate is satisfied
# without having to mock the registry.
CURATED_CIK = "1771146"
CURATED_TRUST_NAME = "ETF Opportunities Trust"


# A CIK that is NOT in TRUST_CIKS — used to verify the gate.
NON_CURATED_CIK = "9999999"


@pytest.fixture()
def curated_trust(db_session):
    trust = Trust(
        cik=CURATED_CIK,
        name=CURATED_TRUST_NAME,
        slug="etf-opportunities-trust",
        is_rex=False,
        is_active=True,
    )
    db_session.add(trust)
    db_session.flush()
    return trust


def _trust_ciks() -> set[str]:
    """Match the normalization the script uses (lstripped leading zeros)."""
    from etp_tracker.trusts import TRUST_CIKS
    return {str(k).lstrip("0") for k in TRUST_CIKS.keys()}


# ---------------------------------------------------------------------------
# Phase 1 — new fund creation
# ---------------------------------------------------------------------------

class TestPhase1NewFund:
    def test_creates_new_rex_product_for_485APOS_in_curated_trust(self, db_session, curated_trust):
        filing = Filing(
            trust_id=curated_trust.id,
            accession_number="0001771146-26-000999",
            form="485APOS",
            filing_date=date(2026, 5, 12),
            primary_link="https://sec.gov/Archives/test.htm",
            primary_document="trex2xlongabc.htm",
            cik=CURATED_CIK,
            registrant=CURATED_TRUST_NAME,
            processed=True,
        )
        db_session.add(filing)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=filing.id,
            series_id="S000999999",
            series_name="T-REX 2X Long ABC Daily Target ETF",
            class_contract_id="C000888888",
        ))
        db_session.commit()

        stats = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )

        assert stats.filings_scanned == 1
        assert stats.new_products_inserted == 1
        # Confirm the row exists with expected fields
        products = db_session.query(RexProduct).all()
        assert len(products) == 1
        p = products[0]
        assert p.name == "T-REX 2X Long ABC Daily Target ETF"
        assert p.product_suite == "T-REX"
        assert p.status == "Filed"   # 485APOS -> Filed
        assert p.latest_form == "485APOS"
        assert p.cik == CURATED_CIK
        assert p.series_id == "S000999999"
        assert p.initial_filing_date == date(2026, 5, 12)
        # Rule 485(a) review window
        assert p.estimated_effective_date == date(2026, 5, 12) + timedelta(days=75)

    def test_485BPOS_creates_with_effective_status(self, db_session, curated_trust):
        """A new 485BPOS row should land as 'Effective', not 'Filed'."""
        filing = Filing(
            trust_id=curated_trust.id,
            accession_number="0001771146-26-000998",
            form="485BPOS",
            filing_date=date(2026, 5, 11),
            primary_link="https://sec.gov/Archives/eff.htm",
            cik=CURATED_CIK,
            registrant=CURATED_TRUST_NAME,
        )
        db_session.add(filing)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=filing.id,
            series_id="S000777777",
            series_name="T-REX 2X Long XYZ Daily Target ETF",
        ))
        db_session.commit()

        phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )

        p = db_session.query(RexProduct).one()
        assert p.status == "Effective"
        assert p.latest_form == "485BPOS"

    def test_skips_filings_from_non_curated_non_rex_trust(self, db_session):
        """A 485APOS from a random non-curated CIK with no REX-name pattern is skipped."""
        trust = Trust(cik=NON_CURATED_CIK, name="Random Other Trust",
                      slug="random-other", is_rex=False, is_active=True)
        db_session.add(trust)
        db_session.flush()

        filing = Filing(
            trust_id=trust.id,
            accession_number="9999999-26-000001",
            form="485APOS",
            filing_date=date(2026, 5, 10),
            primary_link="https://sec.gov/Archives/random.htm",
            cik=NON_CURATED_CIK,
            registrant="Random Other Trust",
        )
        db_session.add(filing)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=filing.id,
            series_id="S000000000",
            series_name="Generic Equity Fund",
        ))
        db_session.commit()

        stats = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )

        assert stats.filings_scanned == 1
        assert stats.new_products_inserted == 0
        assert db_session.query(RexProduct).count() == 0

    def test_accepts_rex_named_fund_from_non_curated_trust(self, db_session):
        """REX-Osprey filed via a non-curated trust must still create a row."""
        trust = Trust(cik=NON_CURATED_CIK, name="Some Other Trust",
                      slug="some-other", is_rex=False, is_active=True)
        db_session.add(trust)
        db_session.flush()

        filing = Filing(
            trust_id=trust.id,
            accession_number="9999999-26-000002",
            form="485APOS",
            filing_date=date(2026, 5, 9),
            primary_link="https://sec.gov/Archives/osprey.htm",
            cik=NON_CURATED_CIK,
            registrant="Some Other Trust",
        )
        db_session.add(filing)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=filing.id,
            series_id="S000111111",
            series_name="REX-Osprey Solana Staking ETF",
        ))
        db_session.commit()

        stats = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )
        assert stats.new_products_inserted == 1


# ---------------------------------------------------------------------------
# Phase 2 — form transitions
# ---------------------------------------------------------------------------

class TestPhase2Transitions:
    def test_485APOS_to_485BPOS_flips_status_to_effective(self, db_session, curated_trust):
        """Existing 'Filed' row gets promoted to 'Effective' on 485BPOS arrival."""
        # Seed an existing rex_products row in the Filed state
        existing = RexProduct(
            name="T-REX 2X Long ABC Daily Target ETF",
            trust=CURATED_TRUST_NAME,
            product_suite="T-REX",
            status="Filed",
            cik=CURATED_CIK,
            series_id="S000999999",
            latest_form="485APOS",
            initial_filing_date=date(2026, 5, 9),
        )
        db_session.add(existing)
        db_session.flush()
        existing_id = existing.id

        # A 485BPOS for the same series arrives
        f2 = Filing(
            trust_id=curated_trust.id,
            accession_number="0001771146-26-001000",
            form="485BPOS",
            filing_date=date(2026, 7, 23),
            primary_link="https://sec.gov/Archives/eff.htm",
            cik=CURATED_CIK,
            registrant=CURATED_TRUST_NAME,
        )
        db_session.add(f2)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=f2.id,
            series_id="S000999999",
            series_name="T-REX 2X Long ABC Daily Target ETF",
        ))
        db_session.commit()

        stats = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )

        db_session.expire_all()
        p = db_session.query(RexProduct).filter_by(id=existing_id).one()
        assert p.status == "Effective"
        assert p.latest_form == "485BPOS"
        assert p.estimated_effective_date == date(2026, 7, 23)
        assert stats.form_transitions == 1
        assert stats.status_promotions == 1
        # And no new row was inserted
        assert db_session.query(RexProduct).count() == 1

    def test_manually_edited_status_is_respected(self, db_session, curated_trust):
        """If status is in manually_edited_fields, the 485BPOS transition skips it."""
        existing = RexProduct(
            name="T-REX 2X Long DEF Daily Target ETF",
            trust=CURATED_TRUST_NAME,
            product_suite="T-REX",
            status="Filed",
            cik=CURATED_CIK,
            series_id="S000444444",
            latest_form="485APOS",
            initial_filing_date=date(2026, 5, 9),
            manually_edited_fields=json.dumps(["status"]),
        )
        db_session.add(existing)
        db_session.flush()

        f2 = Filing(
            trust_id=curated_trust.id,
            accession_number="0001771146-26-001001",
            form="485BPOS",
            filing_date=date(2026, 7, 23),
            primary_link="https://sec.gov/Archives/eff2.htm",
            cik=CURATED_CIK,
            registrant=CURATED_TRUST_NAME,
        )
        db_session.add(f2)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=f2.id,
            series_id="S000444444",
            series_name="T-REX 2X Long DEF Daily Target ETF",
        ))
        db_session.commit()

        phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )

        db_session.expire_all()
        p = db_session.query(RexProduct).one()
        # Status preserved...
        assert p.status == "Filed"
        # ...but latest_form (not protected) still advances.
        assert p.latest_form == "485BPOS"


# ---------------------------------------------------------------------------
# Idempotency + duplicate suppression
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_does_not_create_duplicates(self, db_session, curated_trust):
        filing = Filing(
            trust_id=curated_trust.id,
            accession_number="0001771146-26-001002",
            form="485APOS",
            filing_date=date(2026, 5, 12),
            primary_link="https://sec.gov/Archives/dup.htm",
            cik=CURATED_CIK,
            registrant=CURATED_TRUST_NAME,
        )
        db_session.add(filing)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=filing.id,
            series_id="S000222222",
            series_name="T-REX 2X Long GHI Daily Target ETF",
        ))
        db_session.commit()

        # First run inserts
        stats1 = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )
        assert stats1.new_products_inserted == 1

        # Second run finds the existing row -> no insert
        stats2 = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )
        assert stats2.new_products_inserted == 0
        assert stats2.skipped_already_matched == 1
        assert db_session.query(RexProduct).count() == 1

    def test_matches_existing_by_cik_and_name_when_no_series(self, db_session, curated_trust):
        """A filing with no FundExtraction should still match by (cik, name)."""
        # Existing row with same name + CIK but no series_id
        db_session.add(RexProduct(
            name="T-REX 2X Long JKL Daily Target ETF",
            trust=CURATED_TRUST_NAME,
            product_suite="T-REX",
            status="Filed",
            cik=CURATED_CIK,
            latest_form="485APOS",
            initial_filing_date=date(2026, 5, 9),
        ))
        db_session.flush()

        filing = Filing(
            trust_id=curated_trust.id,
            accession_number="0001771146-26-001003",
            form="485BPOS",
            filing_date=date(2026, 7, 23),
            primary_link="https://sec.gov/Archives/jkl-eff.htm",
            cik=CURATED_CIK,
            registrant=CURATED_TRUST_NAME,
        )
        db_session.add(filing)
        db_session.flush()
        # Extraction has series_name only — no series_id
        db_session.add(FundExtraction(
            filing_id=filing.id,
            series_name="T-REX 2X Long JKL Daily Target ETF",
        ))
        db_session.commit()

        stats = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=False, trust_ciks=_trust_ciks(),
        )

        assert stats.new_products_inserted == 0
        assert stats.skipped_already_matched == 1
        # The existing row was updated to Effective
        db_session.expire_all()
        p = db_session.query(RexProduct).one()
        assert p.status == "Effective"


# ---------------------------------------------------------------------------
# Phase 3 — activation from mkt_master_data
# ---------------------------------------------------------------------------

class TestPhase3Activation:
    def test_effective_with_active_market_status_promotes_to_listed(self, db_session):
        db_session.add(RexProduct(
            name="T-REX 2X Long MNO Daily Target ETF",
            trust=CURATED_TRUST_NAME,
            product_suite="T-REX",
            status="Effective",
            ticker="MNOX",
            cik=CURATED_CIK,
        ))
        db_session.add(MktMasterData(
            ticker="MNOX US",
            market_status="ACTV",
            inception_date="2026-04-15",
        ))
        db_session.commit()

        stats = phase3_activate_from_market(db_session, dry_run=False)
        assert stats.listed_promotions == 1

        db_session.expire_all()
        p = db_session.query(RexProduct).one()
        assert p.status == "Listed"
        assert p.official_listed_date == date(2026, 4, 15)

    def test_phase3_skips_when_market_status_pend(self, db_session):
        db_session.add(RexProduct(
            name="T-REX 2X Long PQR Daily Target ETF",
            trust=CURATED_TRUST_NAME,
            product_suite="T-REX",
            status="Effective",
            ticker="PQRX",
            cik=CURATED_CIK,
        ))
        db_session.add(MktMasterData(
            ticker="PQRX",
            market_status="PEND",
            inception_date="2026-04-15",
        ))
        db_session.commit()

        stats = phase3_activate_from_market(db_session, dry_run=False)
        assert stats.listed_promotions == 0

        db_session.expire_all()
        p = db_session.query(RexProduct).one()
        assert p.status == "Effective"


# ---------------------------------------------------------------------------
# Dry-run safety
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_writes_nothing(self, db_session, curated_trust):
        filing = Filing(
            trust_id=curated_trust.id,
            accession_number="0001771146-26-001004",
            form="485APOS",
            filing_date=date(2026, 5, 12),
            primary_link="https://sec.gov/Archives/dryrun.htm",
            cik=CURATED_CIK,
            registrant=CURATED_TRUST_NAME,
        )
        db_session.add(filing)
        db_session.flush()
        db_session.add(FundExtraction(
            filing_id=filing.id,
            series_id="S000666666",
            series_name="T-REX 2X Long STU Daily Target ETF",
        ))
        db_session.commit()

        stats = phase1_2_sync_filings(
            db_session, since=date(2026, 5, 1),
            dry_run=True, trust_ciks=_trust_ciks(),
        )
        assert stats.new_products_planned == 1
        # No insertion took place
        assert db_session.query(RexProduct).count() == 0

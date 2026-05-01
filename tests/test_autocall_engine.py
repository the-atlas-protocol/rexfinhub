"""Engine fixtures derived from the Excel screenshots.

Three ground-truth cases (all with: tenor=60mo, obs=1mo, NCP=12mo,
AC=100%, coupon=60%, protection=50%, memory=OFF):

  1. BMAXUS, issue 2019-12-31  -> Called Jan 2021, 13 paid, 0 missed
  2. BMAXUS, issue 2007-12-31  -> Called Apr 2011, 17 paid, 23 missed
  3. SPX+NDX, issue 2007-12-31 -> Matured Above Dec 2012, 54 paid, 6 missed
"""
from __future__ import annotations

from datetime import date

import pytest

from webapp.database import SessionLocal
from webapp.services.autocall_engine import (
    NoteParams, Outcome, load_level_store, simulate_note,
)


EXCEL_PARAMS = NoteParams(
    tenor_months=60,
    obs_freq_months=1,
    coupon_rate_pa_pct=10.0,
    coupon_barrier_pct=60.0,
    ac_barrier_pct=100.0,
    protection_barrier_pct=50.0,
    memory=False,
    no_call_months=12,
)


@pytest.fixture(scope="module")
def store():
    db = SessionLocal()
    try:
        return load_level_store(db, tickers=["BMAXUS Index", "SPX Index", "NDX Index"])
    finally:
        db.close()


def test_bmaxus_2019_called_jan_2021(store):
    result = simulate_note(["BMAXUS Index"], date(2019, 12, 31), EXCEL_PARAMS, store)
    assert result.outcome == Outcome.AUTOCALLED, result.error
    assert result.outcome_date == date(2021, 1, 31), f"got {result.outcome_date}"
    assert result.n_coupons_paid == 13
    assert result.n_coupons_missed == 0


def test_bmaxus_2007_called_apr_2011(store):
    result = simulate_note(["BMAXUS Index"], date(2007, 12, 31), EXCEL_PARAMS, store)
    assert result.outcome == Outcome.AUTOCALLED, result.error
    assert result.outcome_date == date(2011, 4, 30), f"got {result.outcome_date}"
    assert result.n_coupons_paid == 17
    assert result.n_coupons_missed == 23


def test_spx_ndx_2007_matured_above_dec_2012(store):
    result = simulate_note(["SPX Index", "NDX Index"], date(2007, 12, 31), EXCEL_PARAMS, store)
    assert result.outcome == Outcome.MATURED_ABOVE, result.error
    assert result.outcome_date == date(2012, 12, 31), f"got {result.outcome_date}"
    assert result.n_coupons_paid == 54
    assert result.n_coupons_missed == 6
    assert result.final_principal_pct == 100.0

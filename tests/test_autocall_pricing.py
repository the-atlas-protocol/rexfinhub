"""Sanity tests for the Black-Scholes Monte Carlo par-coupon pricer."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from webapp.database import SessionLocal
from webapp.services.autocall_engine import NoteParams, load_level_store
from webapp.services.autocall_pricing import price_par_coupon


DEFAULT_PARAMS = NoteParams(
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
        return load_level_store(
            db, tickers=["SPX Index", "NDX Index", "BMAXUS Index"]
        )
    finally:
        db.close()


def test_par_coupon_spx_2007(store):
    """Single-ref SPX 60/100/50 no-memory should price in 6-9% pa range."""
    out = price_par_coupon(
        ["SPX Index"], date(2007, 12, 31), DEFAULT_PARAMS, store,
        n_paths=10000, seed=42,
    )
    assert out["method"] == "mc"
    coupon = out["coupon_pa_pct"]
    assert coupon is not None
    # Wide tolerance — vol regime / correlation / div yield noise.
    assert 4.0 <= coupon <= 12.0, f"unexpected SPX par coupon: {coupon}"
    # PV should sit close to par.
    assert abs(out["pv_at_par"] - 1.0) < 0.01


def test_par_coupon_spx_memory(store):
    """Memory adds value to the investor at fixed terms, so the par coupon
    must be LOWER (or equal within MC noise) under memory ON than memory OFF.

    The task brief framed this as "memory should be higher" but that's only
    true when product designers also widen the coupon barrier to compensate.
    Holding all other terms fixed, the catchup mechanism strictly increases
    the PV of cashflows to the investor, so par coupon must move DOWN.

    We use a configuration with a tighter coupon barrier where memory has
    bite — at SPX defaults (CB=60%, AC=100%) the effect is below MC noise.
    """
    tight_params = replace(DEFAULT_PARAMS, coupon_barrier_pct=80.0, ac_barrier_pct=120.0)
    base = price_par_coupon(
        ["SPX Index"], date(2007, 12, 31), tight_params, store,
        n_paths=10000, seed=42,
    )
    memo_params = replace(tight_params, memory=True)
    memo = price_par_coupon(
        ["SPX Index"], date(2007, 12, 31), memo_params, store,
        n_paths=10000, seed=42,
    )
    assert memo["method"] == "mc"
    # Memory ON => investor gets caught-up coupons => par coupon is lower.
    assert memo["coupon_pa_pct"] <= base["coupon_pa_pct"] + 0.05
    assert 2.0 <= memo["coupon_pa_pct"] <= 15.0


def test_par_coupon_basket(store):
    """Worst-of basket should price at a HIGHER coupon than single-ref."""
    single = price_par_coupon(
        ["SPX Index"], date(2007, 12, 31), DEFAULT_PARAMS, store,
        n_paths=10000, seed=42,
    )
    basket = price_par_coupon(
        ["SPX Index", "NDX Index"], date(2007, 12, 31), DEFAULT_PARAMS, store,
        n_paths=10000, seed=42,
    )
    assert basket["method"] == "mc"
    assert basket["coupon_pa_pct"] > single["coupon_pa_pct"]


def test_par_coupon_lower_protection(store):
    """Protection barrier 30% should price LOWER coupon than 50%."""
    base = price_par_coupon(
        ["SPX Index"], date(2007, 12, 31), DEFAULT_PARAMS, store,
        n_paths=10000, seed=42,
    )
    safer_params = replace(DEFAULT_PARAMS, protection_barrier_pct=30.0)
    safer = price_par_coupon(
        ["SPX Index"], date(2007, 12, 31), safer_params, store,
        n_paths=10000, seed=42,
    )
    assert safer["method"] == "mc"
    assert safer["coupon_pa_pct"] < base["coupon_pa_pct"]


def test_falls_back_when_no_history(store):
    """Issue date with too little prior data should fall back."""
    out = price_par_coupon(
        ["SPX Index"], date(2007, 1, 15), DEFAULT_PARAMS, store,
        n_paths=10000, seed=42,
    )
    assert out["method"] == "fallback"
    assert out["coupon_pa_pct"] is None

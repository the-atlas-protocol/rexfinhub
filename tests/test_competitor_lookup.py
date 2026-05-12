"""Smoke tests for the universal competitor map + lookup service.

Validates:
  - The CSV at config/rules/competitor_map.csv loads without errors
  - Coverage is >= 90 rows (96 REX products at time of writing)
  - Known high-confidence mappings are present (FEPI -> JEPQ, NVDX -> NVDL)
  - The service-layer helpers behave (normalization, attach_competitors)
"""
from __future__ import annotations

from webapp.services.competitor_lookup import (
    CompetitorEntry,
    attach_competitors,
    get_competitors,
    get_competitors_by_suite,
    load_competitor_map,
)


def test_csv_loads_and_has_coverage():
    """The CSV should load and have at least 90 REX product rows."""
    mapping = load_competitor_map(force_reload=True)
    assert isinstance(mapping, dict)
    assert len(mapping) >= 90, (
        f"Expected >= 90 rows in competitor_map.csv, got {len(mapping)}"
    )
    # Every value is a CompetitorEntry with a non-empty suite
    for tk, entry in mapping.items():
        assert isinstance(entry, CompetitorEntry)
        assert entry.rex_ticker == tk
        assert entry.rex_suite, f"{tk} is missing rex_suite"


def test_fepi_has_jepq_competitor():
    """FEPI US is the flagship Equity Premium Income product; JEPQ is the
    canonical Nasdaq-100 covered-call competitor referenced throughout the
    flow report logic."""
    comps = get_competitors("FEPI US")
    assert "JEPQ US" in comps, (
        f"FEPI US should list JEPQ US as a competitor; got {comps}"
    )
    # Also works without the suffix
    assert "JEPQ US" in get_competitors("FEPI")


def test_nvdx_has_nvdl_competitor():
    """NVDX US is REX's 2x long NVDA single-stock ETF; NVDL US is the
    GraniteShares analogue and the most direct competitor."""
    comps = get_competitors("NVDX US")
    assert "NVDL US" in comps, (
        f"NVDX US should list NVDL US as a competitor; got {comps}"
    )


def test_get_competitors_unknown_ticker():
    """Unknown tickers should return an empty list, not raise."""
    assert get_competitors("ZZZZ US") == []
    assert get_competitors("") == []


def test_get_competitors_by_suite():
    """Suite filter should return only entries with the matching suite."""
    trex = get_competitors_by_suite("T-REX")
    assert len(trex) >= 40, f"Expected >= 40 T-REX rows, got {len(trex)}"
    # Case-insensitive
    assert get_competitors_by_suite("t-rex") == trex
    # NVDX is in T-REX
    assert "NVDX US" in trex


def test_attach_competitors_enriches_dict():
    """attach_competitors should add competitors / logic / notes keys."""
    fund_data = {"ticker": "FEPI US", "fund_name": "REX FANG"}
    out = attach_competitors(fund_data)
    assert out is fund_data  # same object, mutated
    assert isinstance(out["competitors"], list)
    tickers = [c["ticker"] for c in out["competitors"]]
    assert "JEPQ US" in tickers
    assert isinstance(out["competitor_logic"], str)
    assert out["competitor_logic"]  # non-empty for FEPI


def test_attach_competitors_unknown_ticker_safe():
    """Unknown ticker still gets the keys populated (empty)."""
    out = attach_competitors({"ticker": "ZZZZ US"})
    assert out["competitors"] == []
    assert out["competitor_logic"] == ""
    assert out["competitor_notes"] == ""


def test_attach_competitors_handles_non_dict():
    """Non-dict input should be returned unchanged (defensive)."""
    assert attach_competitors(None) is None
    assert attach_competitors("not a dict") == "not a dict"

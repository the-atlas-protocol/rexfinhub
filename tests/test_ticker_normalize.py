"""Tests for canonical underlier normalization.

Sprint 1 (2026-06-02): canonical form is ROOT + EXCHANGE — drop only
the trailing asset-class word (Equity/Curncy/Comdty/Index/Govt/Corp/
Mtge). Exchange code (US, LN, KS, ...) is PRESERVED so cross-listed
names remain distinct (FEPI US != FEPI LN).
"""
from __future__ import annotations

import pytest

from webapp.services.ticker_normalize import (
    normalize_underlier,
    normalize_ticker,
)


class TestNormalizeUnderlier:
    """Canonical rule: drop only the asset-class suffix, preserve exchange."""

    def test_bare_ticker(self):
        assert normalize_underlier("NVDA") == "NVDA"

    def test_ticker_with_exchange_preserved(self):
        assert normalize_underlier("NVDA US") == "NVDA US"

    def test_drops_equity_keeps_exchange(self):
        assert normalize_underlier("NVDA US Equity") == "NVDA US"

    def test_london_listing_preserved(self):
        assert normalize_underlier("FEPI LN") == "FEPI LN"

    def test_us_listing_preserved(self):
        assert normalize_underlier("FEPI US") == "FEPI US"

    def test_fepi_us_distinct_from_fepi_ln(self):
        # The core invariant the new rule protects.
        assert normalize_underlier("FEPI US") != normalize_underlier("FEPI LN")

    def test_korean_listing_with_numeric_root(self):
        assert normalize_underlier("005930 KS") == "005930 KS"

    def test_curncy_dropped(self):
        assert normalize_underlier("BTC Curncy") == "BTC"

    # --- Auxiliary cases ---

    def test_none_returns_empty(self):
        assert normalize_underlier(None) == ""

    def test_empty_string(self):
        assert normalize_underlier("") == ""

    def test_whitespace_only(self):
        assert normalize_underlier("   ") == ""

    def test_lowercase_input_uppercased(self):
        assert normalize_underlier("nvda us equity") == "NVDA US"

    def test_extra_whitespace_collapsed(self):
        assert normalize_underlier("  NVDA   US   Equity  ") == "NVDA US"

    @pytest.mark.parametrize(
        "asset_class",
        ["Equity", "Curncy", "Comdty", "Index", "Govt", "Corp", "Mtge"],
    )
    def test_all_asset_class_suffixes_dropped(self, asset_class):
        assert normalize_underlier(f"FOO US {asset_class}") == "FOO US"

    def test_idempotent(self):
        for raw in ("NVDA", "NVDA US", "NVDA US Equity", "FEPI LN", "BTC Curncy"):
            once = normalize_underlier(raw)
            twice = normalize_underlier(once)
            assert once == twice, f"not idempotent for {raw!r}: {once!r} -> {twice!r}"

    def test_single_token_not_misread_as_asset_class(self):
        # Defensive: if someone passes literally "EQUITY" alone, we keep it.
        assert normalize_underlier("EQUITY") == "EQUITY"


class TestNormalizeTicker:
    """``normalize_ticker`` strips everything past the first token."""

    def test_bare(self):
        assert normalize_ticker("NVDA") == "NVDA"

    def test_strips_exchange(self):
        assert normalize_ticker("NVDA US") == "NVDA"

    def test_strips_full_bbg(self):
        assert normalize_ticker("NVDA US Equity") == "NVDA"

    def test_empty(self):
        assert normalize_ticker(None) == ""
        assert normalize_ticker("") == ""

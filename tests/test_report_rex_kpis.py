"""Tests for the rex_kpis rebuild + flow report cache-bypass fixes.

Audit (2026-06-08) found three bugs in report builders:

* BUG-01 / BUG-02 — L&I and Income highlight bullets pulled REX count/AUM
  from a category-wide dict that disagreed with the segment-aggregated
  metrics rendered in the email body. The fix is the helper
  `_rebuild_rex_kpis_from_segments`, which is the single source of truth
  for the headline REX KPI and is computed from the same per-segment
  rex_funds lists the body consumes.

* BUG-03 — Flow report's `Active ETPs` count was sourced from a stale
  `mkt_report_cache` row. The fix exposes a `use_cache` parameter on
  `get_flow_report` so email builders can force a fresh recompute.
"""
from __future__ import annotations

from unittest.mock import patch

from webapp.services.report_data import (
    _rebuild_rex_kpis_from_segments,
    get_flow_report,
)


# ---------------------------------------------------------------------------
# BUG-01 / BUG-02 — rex_kpis rebuild helper
# ---------------------------------------------------------------------------

def test_rebuild_rex_kpis_sums_ss_and_index():
    """Headline count must equal SS count + Index count (no overlap)."""
    ss = [
        {"ticker": "TSLT", "aum": 1500.0, "flow_1w": 25.0},
        {"ticker": "NVDU", "aum": 900.0, "flow_1w": -10.0},
    ]
    index = [
        {"ticker": "BITX", "aum": 1200.0, "flow_1w": 50.0},
        {"ticker": "SOXL", "aum": 500.0, "flow_1w": 5.0},
    ]
    out = _rebuild_rex_kpis_from_segments(ss, index, category_total_aum=50_000.0)
    assert out["count"] == 4
    # 1500 + 900 + 1200 + 500 = 4100 M -> $4.1B
    assert out["total_aum"] == "$4.1B"
    # 25 - 10 + 50 + 5 = 70 M -> +$70.0M
    assert out["flow_1w"] == "+$70.0M"
    assert out["flow_1w_positive"] is True
    # 4100 / 50000 = 8.2%
    assert out["share"] == "8.2%"


def test_rebuild_rex_kpis_dedups_by_ticker():
    """A defensively-duplicated ticker across segments must not double-count."""
    ss = [{"ticker": "TSLT", "aum": 1000.0, "flow_1w": 10.0}]
    index = [
        {"ticker": "TSLT", "aum": 1000.0, "flow_1w": 10.0},  # duplicate
        {"ticker": "BITX", "aum": 500.0, "flow_1w": 20.0},
    ]
    out = _rebuild_rex_kpis_from_segments(ss, index, category_total_aum=10_000.0)
    assert out["count"] == 2  # TSLT counted once
    # 1000 + 500 = 1500 M -> $1.5B
    assert out["total_aum"] == "$1.5B"


def test_rebuild_rex_kpis_handles_empty_segments():
    out = _rebuild_rex_kpis_from_segments([], [], category_total_aum=0.0)
    assert out["count"] == 0
    assert out["total_aum"] == "--"
    assert out["share"] == "--"


def test_rebuild_rex_kpis_zero_share_when_total_aum_zero():
    """No category AUM => share must be '--' (not divide-by-zero)."""
    ss = [{"ticker": "X", "aum": 100.0, "flow_1w": 0}]
    out = _rebuild_rex_kpis_from_segments(ss, [], category_total_aum=0.0)
    assert out["share"] == "--"


def test_rebuild_rex_kpis_negative_flow():
    ss = [{"ticker": "X", "aum": 100.0, "flow_1w": -50.0}]
    out = _rebuild_rex_kpis_from_segments(ss, [], category_total_aum=1000.0)
    assert out["flow_1w_positive"] is False


def test_rebuild_rex_kpis_anchor_li_2026_06_08():
    """Anchor: audit DB truth (2026-06-08) was SS=40/$2.4B + Index=24/$1.7B = 64/$4.1B.

    Rendered bug was 69 funds / $20.3B. This test locks in the correct
    aggregation behaviour against the audit's published numbers so a
    regression to the category-wide dict would be caught.
    """
    ss = [{"ticker": f"SS{i}", "aum": 60.0, "flow_1w": 0} for i in range(40)]  # 40 * 60 = 2400 M
    index = [{"ticker": f"IDX{i}", "aum": 1700.0 / 24, "flow_1w": 0} for i in range(24)]
    out = _rebuild_rex_kpis_from_segments(ss, index, category_total_aum=47_500.0)
    assert out["count"] == 64
    assert out["total_aum"] == "$4.1B"
    # 4100 / 47500 ~= 8.6%
    assert out["share"] == "8.6%"


# ---------------------------------------------------------------------------
# BUG-03 — flow report cache-bypass
# ---------------------------------------------------------------------------

def test_get_flow_report_use_cache_false_skips_cache_read():
    """When use_cache=False, _read_report_cache must NOT be consulted."""
    sentinel = {"available": False, "data_as_of": "", "data_as_of_short": ""}
    with patch("webapp.services.report_data._read_report_cache") as mock_cache, \
         patch("webapp.services.report_data._ON_RENDER", False), \
         patch("webapp.services.report_data._get_cache",
               return_value={"available": False}):
        mock_cache.return_value = {"should": "not_be_returned"}
        result = get_flow_report(db=object(), use_cache=False)
        mock_cache.assert_not_called()
        assert result == sentinel


def test_get_flow_report_use_cache_true_reads_cache():
    """Default use_cache=True must still consult the cache for Render."""
    cached_payload = {"available": True, "grand_kpis": {"count": 5368}}
    with patch("webapp.services.report_data._read_report_cache",
               return_value=cached_payload) as mock_cache:
        result = get_flow_report(db=object())  # default use_cache=True
        mock_cache.assert_called_once()
        assert result is cached_payload

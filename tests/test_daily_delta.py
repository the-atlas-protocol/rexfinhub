"""Offline tests for the launch/closure delta (scripts/analyze_daily_delta.py)."""
from scripts.analyze_daily_delta import compute_delta, format_delta


def _prev(*pairs):
    return {t: {"market_status": s} for t, s in pairs}


def test_detects_rex_launch():
    today = [{"ticker": "TSLX", "fund_name": "T-REX 2X Long Tesla", "market_status": "ACTV", "is_rex": 1}]
    prev = _prev(("TSLX", "PEND"))
    d = compute_delta(today, prev)
    assert len(d["launches"]) == 1 and d["launches"][0]["ticker"] == "TSLX"
    assert d["launches"][0]["is_rex"] is True
    assert not d["closures"]


def test_detects_closure_and_ignores_unchanged():
    today = [
        {"ticker": "DEAD", "fund_name": "Old ETN", "market_status": "DLST", "is_rex": 0},
        {"ticker": "SAME", "fund_name": "Steady", "market_status": "ACTV", "is_rex": 1},
    ]
    prev = _prev(("DEAD", "ACTV"), ("SAME", "ACTV"))
    d = compute_delta(today, prev)
    assert [e["ticker"] for e in d["closures"]] == ["DEAD"]
    assert not d["launches"]


def test_new_ticker_with_no_prior_is_not_a_launch():
    # a ticker absent from the prior snapshot has no transition -> not counted
    today = [{"ticker": "NEW", "fund_name": "Brand New", "market_status": "ACTV", "is_rex": 0}]
    d = compute_delta(today, _prev())
    assert not d["launches"] and not d["closures"]


def test_format_flags_rex_for_confirmation():
    today = [{"ticker": "TSLX", "fund_name": "T-REX 2X Long Tesla", "market_status": "ACTV", "is_rex": 1}]
    txt = format_delta({"available": True, "dates": ["d2", "d1"], **compute_delta(today, _prev(("TSLX", "PEND")))})
    assert "TSLX" in txt and "REX — confirm" in txt and "expected" in txt


def test_format_competitor_launch_not_flagged():
    today = [{"ticker": "COMP", "fund_name": "Rival 2X", "market_status": "ACTV", "is_rex": 0}]
    txt = format_delta({"available": True, "dates": ["d2", "d1"], **compute_delta(today, _prev(("COMP", "PEND")))})
    assert "COMP" in txt and "REX — confirm" not in txt


def test_empty_when_no_changes():
    assert format_delta({"available": True, "dates": ["d2", "d1"], "launches": [], "closures": []}) == ""
    assert format_delta({"available": False, "dates": [], "launches": [], "closures": []}) == ""

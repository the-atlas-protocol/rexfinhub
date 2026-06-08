"""Regression test: weekly_v2_report.main() writes recommendation_history.

Anchors the wire-up added 2026-06-08. The append step is wrapped in a
try/except (so a failure here never blocks the report send), but if the
import path or DataFrame shape ever drifts, this test catches it.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest


_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


@pytest.fixture()
def fake_db(tmp_path, monkeypatch):
    """Point recommendation_history at a temp SQLite DB."""
    db = tmp_path / "etp_tracker.db"
    db.touch()  # exists() check in append_weekly_recommendations
    from screener.li_engine.analysis import recommendation_history
    monkeypatch.setattr(recommendation_history, "_DB", db)
    return db


def _fake_launch_df() -> pd.DataFrame:
    """Mimic the renderer's `launch` DataFrame: ticker-indexed + scored."""
    return pd.DataFrame(
        {
            "composite_score": [0.91, 0.88, 0.74, 0.55],
            "rex_fund_name": ["L1", "L2", "L3", "L4"],
            "competitor_filed_total": [0, 0, 1, 2],
        },
        index=pd.Index(["AAA", "BBB", "CCC", "DDD"], name="ticker"),
    )


def _fake_whitespace_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "composite_score": [0.80, 0.65, 0.40],
            "rvol_90d": [110, 80, 50],
        },
        index=pd.Index(["EEE", "FFF", "GGG"], name="ticker"),
    )


def test_main_appends_recommendation_history(fake_db, monkeypatch, tmp_path):
    """Mock the heavy data loads + HTML write; assert rows land in the DB."""
    from screener.li_engine.analysis import weekly_v2_report as wvr

    launch = _fake_launch_df()
    whitespace = _fake_whitespace_df()

    monkeypatch.setattr(wvr, "load_launch_candidates", lambda n: launch)
    monkeypatch.setattr(wvr, "load_whitespace_top", lambda n: whitespace)
    monkeypatch.setattr(wvr, "load_money_flow", lambda n, days=7: pd.DataFrame())
    monkeypatch.setattr(wvr, "load_filings_count_this_week", lambda: {"count": 0})
    monkeypatch.setattr(wvr, "load_top_mentions", lambda: ("XYZ", 5))
    monkeypatch.setattr(wvr, "load_earliest_competitor_filing_dates", lambda: {})
    monkeypatch.setattr(wvr, "load_launches_this_week", lambda: [])
    monkeypatch.setattr(wvr, "load_pre_ipo_filer_race", lambda: [])
    monkeypatch.setattr(wvr, "render", lambda *a, **kw: "<html>ok</html>")
    monkeypatch.setattr(wvr, "LAYOUT_VERSION", "v2")

    # Redirect OUT to a temp file so we don't pollute the repo.
    monkeypatch.setattr(wvr, "OUT", tmp_path / "report.html")

    wvr.main()

    conn = sqlite3.connect(str(fake_db))
    try:
        rows = conn.execute(
            "SELECT ticker, confidence_tier, section, composite_score "
            "FROM recommendation_history ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    tickers = [r[0] for r in rows]
    sections = {r[2] for r in rows}
    assert "AAA" in tickers
    assert "EEE" in tickers
    assert "launch" in sections
    assert "filing" in sections
    # Top-3 by score should be HIGH tier.
    tiers_by_ticker = {r[0]: r[1] for r in rows}
    assert tiers_by_ticker["AAA"] == "HIGH"


def test_main_swallows_append_failure(fake_db, monkeypatch, tmp_path):
    """Even if the append raises, main() must still complete successfully."""
    from screener.li_engine.analysis import weekly_v2_report as wvr

    monkeypatch.setattr(wvr, "load_launch_candidates", lambda n: _fake_launch_df())
    monkeypatch.setattr(wvr, "load_whitespace_top", lambda n: _fake_whitespace_df())
    monkeypatch.setattr(wvr, "load_money_flow", lambda n, days=7: pd.DataFrame())
    monkeypatch.setattr(wvr, "load_filings_count_this_week", lambda: {"count": 0})
    monkeypatch.setattr(wvr, "load_top_mentions", lambda: ("XYZ", 5))
    monkeypatch.setattr(wvr, "load_earliest_competitor_filing_dates", lambda: {})
    monkeypatch.setattr(wvr, "load_launches_this_week", lambda: [])
    monkeypatch.setattr(wvr, "load_pre_ipo_filer_race", lambda: [])
    monkeypatch.setattr(wvr, "render", lambda *a, **kw: "<html>ok</html>")
    monkeypatch.setattr(wvr, "LAYOUT_VERSION", "v2")
    monkeypatch.setattr(wvr, "OUT", tmp_path / "report.html")

    # Force the append to blow up.
    with mock.patch(
        "screener.li_engine.analysis.recommendation_history.append_weekly_recommendations",
        side_effect=RuntimeError("boom"),
    ):
        wvr.main()  # Must not raise.

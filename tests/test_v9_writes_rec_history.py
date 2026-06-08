"""Regression test: trex_combined_v9.build() writes recommendation_history.

Anchors the wire-up added 2026-06-08. v9 has its own row-builder
(`build_rows_from_v9`) because the internal DataFrames are shaped
differently from the v2 renderer's. This test pins both the adapter and
the call-site.
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
    db = tmp_path / "etp_tracker.db"
    db.touch()
    from screener.li_engine.analysis import recommendation_history
    monkeypatch.setattr(recommendation_history, "_DB", db)
    return db


def test_build_rows_from_v9_shapes():
    from screener.li_engine.analysis.recommendation_history import build_rows_from_v9

    no_live = pd.DataFrame({
        "ticker": ["AAA", "BBB", "CCC", "DDD"],
        "composite_score": [0.9, 0.7, 0.6, 0.4],
    })
    pipeline = pd.DataFrame({
        "underlier_clean": ["XYZ", "QQQ"],
        "ticker": ["RXXX US", "RQQQ US"],
        "fund_name": ["T-REX 2X LONG XYZ", "T-REX 2X LONG QQQ"],
        "underlier_score": [80.0, 60.0],
    })

    rows = build_rows_from_v9(date(2026, 6, 8), no_live, pipeline)
    assert len(rows) == 6
    sections = {r.section for r in rows}
    assert sections == {"filing", "launch"}
    # Whitespace top should be HIGH.
    ws = [r for r in rows if r.section == "filing"]
    assert ws[0].ticker == "AAA"
    assert ws[0].confidence_tier == "HIGH"
    # Pipeline rex_ticker carries through.
    pl = [r for r in rows if r.section == "launch"]
    assert pl[0].ticker == "XYZ"
    assert pl[0].suggested_rex_ticker == "RXXX"


def test_build_rows_from_v9_handles_empty():
    from screener.li_engine.analysis.recommendation_history import build_rows_from_v9
    assert build_rows_from_v9(date(2026, 6, 8), None, None) == []
    assert build_rows_from_v9(date(2026, 6, 8), pd.DataFrame(), pd.DataFrame()) == []


def test_v9_build_call_site_wires_append(fake_db, monkeypatch, tmp_path):
    """Verify build() invokes append_weekly_recommendations with v9-shaped rows.

    v9.build() is a large monolithic function; rather than stub every
    loader + HTML helper, we patch the rendering bottleneck (write_text)
    and assert the rec-history append was called with the right DataFrames.
    """
    from screener.li_engine.analysis import trex_combined_v9 as v9
    from screener.li_engine.analysis import recommendation_history as rh

    no_live = pd.DataFrame({
        "ticker": ["AAA", "BBB"],
        "composite_score": [0.9, 0.6],
    })
    pipeline = pd.DataFrame()  # empty — skip the pipeline render block

    # Stub the data loaders.
    monkeypatch.setattr(v9, "OUT_DIR", tmp_path)
    monkeypatch.setattr(v9, "_single_stock_set", lambda: set())
    monkeypatch.setattr(v9, "load_rex_position", lambda: {})
    monkeypatch.setattr(v9, "load_counts", lambda: pd.DataFrame())
    monkeypatch.setattr(v9, "load_imminent_launches", lambda: pd.DataFrame())
    monkeypatch.setattr(v9, "load_recent_competitor_filings", lambda: pd.DataFrame())
    monkeypatch.setattr(v9, "load_trex_2x_long_pipeline", lambda: pipeline)
    monkeypatch.setattr(v9, "load_scored", lambda: no_live)
    monkeypatch.setattr(v9, "load_inverse_gap", lambda s: pd.DataFrame())
    monkeypatch.setattr(v9, "load_launch_anyway", lambda rp, ss: pd.DataFrame())
    monkeypatch.setattr(v9, "load_foreign", lambda: pd.DataFrame())
    monkeypatch.setattr(v9, "load_delisting_watch", lambda: pd.DataFrame())
    monkeypatch.setattr(v9, "load_flow_score_backtest", lambda s: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(v9, "load_track_record_recs", lambda: {"high_profile": [], "recently_priced": []})
    monkeypatch.setattr(v9, "load_ipo_yaml", lambda: [])
    monkeypatch.setattr(v9, "_df", lambda *a, **kw: pd.DataFrame())

    calls = {"append": None, "rows": None}
    real_append = rh.append_weekly_recommendations

    def capturing_append(rows, db_path=None):
        calls["rows"] = list(rows)
        calls["append"] = True
        return real_append(rows, db_path=db_path)

    monkeypatch.setattr(rh, "append_weekly_recommendations", capturing_append)

    # The mega-table render block reads many no_live columns we haven't
    # stubbed; let it fail and confirm the append still happens via the
    # outer try/except OR the build completes. To make it deterministic,
    # patch the iterrows call to short-circuit the HTML loops by giving
    # no_live the minimal cols. Easiest: empty no_live too, but then the
    # append-rows would be empty. Use an alternate path: short-circuit
    # the entire HTML stage by raising in Path.write_text, then assert
    # the append fired first (it's BEFORE write_text in our wire-up
    # ordering... no, it's AFTER). So we need build() to complete.
    #
    # Pragmatic approach: stub no_live with all the columns the mega
    # table reads (sector, themes, market_cap, etc.) — all empty/0.
    no_live_full = no_live.assign(
        name="X", sector="X", themes="", market_cap=0, rvol_90d=0,
        ret_1m=0, ret_1y=0, mentions_24h=0,
        has_rex_filing=False, has_comp_filing=False,
        u_clean=["AAA", "BBB"], has_live=False,
    )
    monkeypatch.setattr(v9, "load_scored", lambda: no_live_full)

    try:
        v9.build()
    except Exception:
        pass  # don't care if HTML stage breaks — the append is what we test

    # The append must have been called with rows derived from no_live.
    assert calls["append"] is True, "append_weekly_recommendations was never called"
    tickers = {r.ticker for r in calls["rows"]}
    assert "AAA" in tickers
    assert "BBB" in tickers
    sections = {r.section for r in calls["rows"]}
    assert sections == {"filing"}  # pipeline empty → only whitespace rows

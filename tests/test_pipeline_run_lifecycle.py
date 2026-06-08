"""Tests for mkt_pipeline_runs lifecycle (audit O20 leak fix).

Every started pipeline run must reach a terminal status ('completed' or
'failed'). A row must never be left as 'running' after sync_market_data
returns, even when an exception is raised mid-flight.

Run: python -m pytest tests/test_pipeline_run_lifecycle.py -v
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from webapp.database import Base
from webapp.models import MktPipelineRun


# data_engine.py runs get_bloomberg_file() at import time, which requires
# live Graph API creds. Install a stub before importing market_sync so the
# inline `from webapp.services.data_engine import build_all` succeeds in
# the test environment. Individual tests patch build_all via the stub.
_de_stub = types.ModuleType("webapp.services.data_engine")
_de_stub.build_all = lambda *a, **kw: {"master": pd.DataFrame(), "ts": pd.DataFrame()}
_de_stub.build_all_from_csvs = lambda *a, **kw: {"master": pd.DataFrame(), "ts": pd.DataFrame()}
sys.modules.setdefault("webapp.services.data_engine", _de_stub)

from webapp.services import market_sync  # noqa: E402


# ---------------------------------------------------------------------------
# Per-test in-memory DB. The shared conftest db_session wraps everything in
# an outer transaction that gets rolled back, which conflicts with the
# explicit db.commit() / db.rollback() calls in sync_market_data.
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _runs(db) -> list[MktPipelineRun]:
    return list(db.execute(select(MktPipelineRun)).scalars())


def _fake_master(n: int = 6000) -> pd.DataFrame:
    """Master DataFrame with enough rows to pass the >=5000 validation gate."""
    return pd.DataFrame({
        "ticker": [f"TST{i} US" for i in range(n)],
        "fund_name": [f"Fund {i}" for i in range(n)],
    })


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------
def test_successful_sync_marks_run_completed(db):
    fake_result = {"master": _fake_master(), "ts": pd.DataFrame()}

    with patch.object(market_sync, "_SYNC_LOCK"), \
         patch("webapp.services.data_engine.build_all", return_value=fake_result), \
         patch.object(market_sync, "_insert_master_data", return_value=6000), \
         patch.object(market_sync, "_insert_time_series", return_value=0), \
         patch.object(market_sync, "_compute_and_cache_reports", return_value=["li_report"]), \
         patch.object(market_sync, "_compute_and_cache_screener"), \
         patch.object(market_sync, "_sync_global_etp", return_value=0), \
         patch.object(market_sync, "_export_csvs"), \
         patch.object(market_sync, "_update_last_run_marker"), \
         patch.object(market_sync, "_auto_scan_classifications", return_value=0):
        result = market_sync.sync_market_data(db)

    assert result["master_rows"] == 6000

    runs = _runs(db)
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].finished_at is not None
    assert runs[0].error_message is None


# ---------------------------------------------------------------------------
# Exception path — the original leak
# ---------------------------------------------------------------------------
def test_exception_during_sync_marks_run_failed(db):
    fake_result = {"master": _fake_master(), "ts": pd.DataFrame()}
    boom = RuntimeError("simulated insert failure")

    with patch.object(market_sync, "_SYNC_LOCK"), \
         patch("webapp.services.data_engine.build_all", return_value=fake_result), \
         patch.object(market_sync, "_insert_master_data", side_effect=boom):
        with pytest.raises(RuntimeError, match="simulated insert failure"):
            market_sync.sync_market_data(db)

    runs = _runs(db)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].finished_at is not None
    assert "simulated insert failure" in (runs[0].error_message or "")


def test_exception_in_report_cache_marks_run_failed(db):
    """Failure later in the pipeline (after master rows inserted) also
    leaves the run in a terminal state."""
    fake_result = {"master": _fake_master(), "ts": pd.DataFrame()}

    with patch.object(market_sync, "_SYNC_LOCK"), \
         patch("webapp.services.data_engine.build_all", return_value=fake_result), \
         patch.object(market_sync, "_insert_master_data", return_value=6000), \
         patch.object(market_sync, "_insert_time_series", return_value=0), \
         patch.object(market_sync, "_compute_and_cache_reports",
                      side_effect=ValueError("report cache crashed")):
        with pytest.raises(ValueError, match="report cache crashed"):
            market_sync.sync_market_data(db)

    runs = _runs(db)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "report cache crashed" in (runs[0].error_message or "")


# ---------------------------------------------------------------------------
# Invariant: no row is ever left as 'running' after the function returns
# ---------------------------------------------------------------------------
def test_no_running_rows_remain_after_success(db):
    fake_result = {"master": _fake_master(), "ts": pd.DataFrame()}

    with patch.object(market_sync, "_SYNC_LOCK"), \
         patch("webapp.services.data_engine.build_all", return_value=fake_result), \
         patch.object(market_sync, "_insert_master_data", return_value=6000), \
         patch.object(market_sync, "_insert_time_series", return_value=0), \
         patch.object(market_sync, "_compute_and_cache_reports", return_value=[]), \
         patch.object(market_sync, "_compute_and_cache_screener"), \
         patch.object(market_sync, "_sync_global_etp", return_value=0), \
         patch.object(market_sync, "_export_csvs"), \
         patch.object(market_sync, "_update_last_run_marker"), \
         patch.object(market_sync, "_auto_scan_classifications", return_value=0):
        market_sync.sync_market_data(db)

    stuck = [r for r in _runs(db) if r.status == "running"]
    assert stuck == [], f"Found stuck 'running' rows: {stuck}"


def test_no_running_rows_remain_after_failure(db):
    fake_result = {"master": _fake_master(), "ts": pd.DataFrame()}

    with patch.object(market_sync, "_SYNC_LOCK"), \
         patch("webapp.services.data_engine.build_all", return_value=fake_result), \
         patch.object(market_sync, "_insert_master_data",
                      side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            market_sync.sync_market_data(db)

    stuck = [r for r in _runs(db) if r.status == "running"]
    assert stuck == [], f"Found stuck 'running' rows: {stuck}"

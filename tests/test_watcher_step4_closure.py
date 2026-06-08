"""Tests for AW-01/02/03 closure: watcher_atom trusts must be included in
the curated universe so step4 (and step5) rollup runs for them.

The fix is in etp_tracker.run_pipeline.load_ciks_from_db — the curated
universe now selects Trust.source IN ('curated', 'watcher_atom') instead
of only 'curated'. Without this, FundStatus rows created by the watcher's
lightweight rollup never escape the placeholder
status_reason='watcher_atom: lightweight rollup pending step4'.
"""
from __future__ import annotations

import pytest

from etp_tracker.run_pipeline import load_ciks_from_db
from webapp.models import Trust


@pytest.fixture()
def trusts_seeded(db_session, monkeypatch):
    """Inject a curated trust + a watcher_atom trust + a bulk_discovery trust
    and route load_ciks_from_db through the test session."""
    db_session.add(Trust(
        cik="0001111000",
        name="Curated Test Trust",
        slug="curated-test",
        is_rex=True,
        is_active=True,
        source="curated",
    ))
    db_session.add(Trust(
        cik="0002222000",
        name="Watcher Auto Trust",
        slug="watcher-auto",
        is_rex=False,
        is_active=True,
        source="watcher_atom",
    ))
    db_session.add(Trust(
        cik="0003333000",
        name="Discovered Trust",
        slug="discovered",
        is_rex=False,
        is_active=True,
        source="bulk_discovery",
    ))
    # Inactive curated trust — should be excluded.
    db_session.add(Trust(
        cik="0004444000",
        name="Inactive Curated",
        slug="inactive-curated",
        is_rex=False,
        is_active=False,
        source="curated",
    ))
    db_session.commit()

    # Patch SessionLocal so load_ciks_from_db sees our test data.
    import etp_tracker.run_pipeline as rp

    def _fake_session():
        return _SessionShim(db_session)

    monkeypatch.setattr("webapp.database.SessionLocal", _fake_session)
    yield


class _SessionShim:
    """Tiny shim so the real SessionLocal() call in load_ciks_from_db hands
    back our test session without closing it on .close()."""
    def __init__(self, real_session):
        self._real = real_session

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def close(self):
        # No-op — fixture owns the lifecycle.
        pass


def test_curated_universe_includes_watcher_atom(trusts_seeded):
    """curated universe selects Trust.source IN ('curated', 'watcher_atom').
    Asserts both the curated and watcher-auto trusts are returned, and the
    bulk_discovery + inactive trusts are excluded."""
    ciks, overrides = load_ciks_from_db(universe="curated")
    assert set(ciks) == {"1111000", "2222000"}
    assert overrides["1111000"] == "Curated Test Trust"
    assert overrides["2222000"] == "Watcher Auto Trust"


def test_discovered_universe_unchanged(trusts_seeded):
    """The 'discovered' universe filter still selects only bulk_discovery."""
    ciks, _ = load_ciks_from_db(universe="discovered")
    assert set(ciks) == {"3333000"}


def test_all_universe_includes_everything_active(trusts_seeded):
    """'all' returns every active trust regardless of source."""
    ciks, _ = load_ciks_from_db(universe="all")
    assert set(ciks) == {"1111000", "2222000", "3333000"}
    # Inactive trust excluded.
    assert "4444000" not in ciks

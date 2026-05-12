"""Smoke tests for the /strategy/* surface.

Born from the 2026-05-12 incident where the three strategy index pages
(/strategy, /strategy/whitespace, /strategy/race) returned 500 with the
21-byte FastAPI default error body. The crash was a column-access bug in
``webapp/routers/strategy.py`` that fired only when the underlying
parquet on Render's persistent disk had drifted from the version checked
in locally.

These tests assert each route returns 200 OK with a non-trivial HTML
body under three parquet-state scenarios:

1. **Real parquets** — when ``data/analysis/whitespace_v4.parquet`` and
   friends exist on disk, hit each route and assert 200.
2. **Missing parquets** — point the router at a temp dir with no
   parquets and assert each route still returns 200 (rendered via
   ``strategy/empty.html``) rather than 500.
3. **Schema-drift parquets** — write a parquet missing critical columns
   (``composite_score``, ``sector``) and assert routes still return 200.

The third scenario is the one that locks in the fix — without the
defensive ``_safe_get`` / ``in df.columns`` guards, a missing
``composite_score`` would re-raise as a KeyError on
``df.sort_values("composite_score")`` and 500 the page.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Skip cache prewarm at import — we never need the screener or market caches.
import webapp.main as _main_mod  # noqa: E402

_main_mod._prewarm_caches = lambda: None  # type: ignore[assignment]

from webapp.main import app, SITE_PASSWORD  # noqa: E402
from webapp.routers import strategy as strategy_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    """Authenticated TestClient — POSTs to /login so site_auth is set."""
    c = TestClient(app)
    resp = c.post(
        "/login",
        data={"password": SITE_PASSWORD, "next": "/"},
        follow_redirects=False,
    )
    # 303 = login accepted; 200 = login form re-rendered with error.
    assert resp.status_code == 303, (
        f"Login failed (status {resp.status_code}). SITE_PASSWORD wrong?"
    )
    return c


@pytest.fixture
def parquet_redirect(tmp_path, monkeypatch):
    """Redirect strategy router parquet paths to a temp dir.

    Returns the temp dir so individual tests can drop parquets into it
    before hitting the routes. Restores the originals on teardown via
    monkeypatch.
    """
    monkeypatch.setattr(strategy_mod, "WS_PARQUET", tmp_path / "whitespace_v4.parquet")
    monkeypatch.setattr(strategy_mod, "WS_V1_PARQUET", tmp_path / "whitespace_candidates.parquet")
    monkeypatch.setattr(strategy_mod, "RACE_PARQUET", tmp_path / "filing_race.parquet")
    monkeypatch.setattr(strategy_mod, "CADENCE_PARQUET", tmp_path / "issuer_cadence.parquet")
    return tmp_path


# ---------------------------------------------------------------------------
# Scenario 1: real on-disk parquets (skip if absent)
# ---------------------------------------------------------------------------

REAL_WS = ROOT / "data" / "analysis" / "whitespace_v4.parquet"
REAL_RACE = ROOT / "data" / "analysis" / "filing_race.parquet"


@pytest.mark.skipif(
    not REAL_WS.exists(),
    reason="whitespace_v4.parquet not present locally — skipping live-data smoke",
)
def test_strategy_home_with_real_parquet(client: TestClient) -> None:
    resp = client.get("/strategy")
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.text) > 1000, "response suspiciously short"
    assert "Whitespace" in resp.text or "Strategy" in resp.text


@pytest.mark.skipif(
    not REAL_WS.exists(),
    reason="whitespace_v4.parquet not present locally",
)
def test_strategy_whitespace_with_real_parquet(client: TestClient) -> None:
    resp = client.get("/strategy/whitespace")
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.text) > 1000


@pytest.mark.skipif(
    not REAL_RACE.exists(),
    reason="filing_race.parquet not present locally",
)
def test_strategy_race_with_real_parquet(client: TestClient) -> None:
    resp = client.get("/strategy/race")
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.text) > 500


# ---------------------------------------------------------------------------
# Scenario 2: parquets missing entirely — must render empty.html, not 500
# ---------------------------------------------------------------------------

def test_strategy_home_missing_parquet(client: TestClient, parquet_redirect) -> None:
    resp = client.get("/strategy")
    assert resp.status_code == 200, resp.text[:500]
    # empty.html message: "Data not available yet"
    assert "Data not available" in resp.text or "No whitespace" in resp.text


def test_strategy_whitespace_missing_parquet(client: TestClient, parquet_redirect) -> None:
    resp = client.get("/strategy/whitespace")
    assert resp.status_code == 200, resp.text[:500]
    assert "Data not available" in resp.text or "No whitespace" in resp.text


def test_strategy_race_missing_parquet(client: TestClient, parquet_redirect) -> None:
    # /strategy/race is allowed to render the race page with empty tables
    # even when both parquets are missing — assert 200 either way.
    resp = client.get("/strategy/race")
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.text) > 200


# ---------------------------------------------------------------------------
# Scenario 3: schema-drift parquets (the bug we just fixed)
# ---------------------------------------------------------------------------

def _write_drifted_whitespace(path: Path) -> None:
    """Whitespace parquet missing composite_score, sector, themes.

    Mirrors the failure mode where Render's persistent-disk parquet has
    a different column set than the local copy.
    """
    df = pd.DataFrame(
        {
            "market_cap": [1500.0, 2500.0, 3500.0],
            "rvol_90d": [1.2, 0.8, 1.5],
            "ret_1m": [5.0, -2.0, 10.0],
            "mentions_24h": [0, 5, 12],
            "is_thematic": [0, 1, 0],
        },
        index=pd.Index(["AAA", "BBB", "CCC"], name="ticker"),
    )
    df.to_parquet(path)


def _write_drifted_race(path: Path) -> None:
    """Race parquet missing days_until_launch — should render empty list."""
    df = pd.DataFrame(
        {
            "filing_date": pd.to_datetime(["2026-05-01"]),
            "registrant": ["Test Registrant"],
        }
    )
    df.to_parquet(path)


def test_strategy_home_with_drifted_schema(client: TestClient, parquet_redirect) -> None:
    """Missing composite_score must NOT crash the home dashboard."""
    _write_drifted_whitespace(parquet_redirect / "whitespace_v4.parquet")
    resp = client.get("/strategy")
    assert resp.status_code == 200, resp.text[:500]
    # Should render the home template, not the empty fallback
    assert len(resp.text) > 1000


def test_strategy_whitespace_with_drifted_schema(client: TestClient, parquet_redirect) -> None:
    """Missing composite_score / sector must NOT crash the whitespace table."""
    _write_drifted_whitespace(parquet_redirect / "whitespace_v4.parquet")
    resp = client.get("/strategy/whitespace")
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.text) > 1000


def test_strategy_race_with_drifted_schema(client: TestClient, parquet_redirect) -> None:
    """Race parquet missing days_until_launch must render empty table, not 500."""
    _write_drifted_race(parquet_redirect / "filing_race.parquet")
    resp = client.get("/strategy/race")
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.text) > 200

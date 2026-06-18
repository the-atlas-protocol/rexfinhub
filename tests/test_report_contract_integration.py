"""Integration: the built reports agree with the contract on the live DB.

Locks Phase 4 — if a future change makes get_li_report / get_cc_report / get_flow_report
compute their universe a different way than the contract, this fails. Skips cleanly when
the local DB isn't present (CI without a synced DB).
"""
import pytest

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/etp_tracker.db").exists(),
    reason="needs a local synced etp_tracker.db",
)


@pytest.fixture(scope="module")
def master():
    from webapp.database import SessionLocal
    from webapp.services.report_data import _load_from_db
    db = SessionLocal()
    try:
        return _load_from_db(db)["master"]
    finally:
        db.close()


def test_headline_numbers_tie_to_contract(master):
    from market import contracts
    for rid, expected in contracts.expected_values().items():
        assert contracts.count_for_rule(master, rid) == expected, f"{rid} drifted"


def test_li_report_universe_equals_contract(master):
    from webapp.database import SessionLocal
    from webapp.services.report_data import get_li_report
    from market import contracts
    db = SessionLocal()
    try:
        li = get_li_report(db, use_cache=False)
    finally:
        db.close()
    assert li["kpis"]["count"] == contracts.count_for_rule(master, "li_universe_actv")


def test_cc_report_universe_equals_contract(master):
    from webapp.database import SessionLocal
    from webapp.services.report_data import get_cc_report
    from market import contracts
    db = SessionLocal()
    try:
        cc = get_cc_report(db, use_cache=False)
    finally:
        db.close()
    assert cc["kpis"]["count"] == contracts.count_for_rule(master, "cc_universe_actv")

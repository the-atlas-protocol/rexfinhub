"""Stage A — fix-at-source ingest tests (approved plan).

Locks the pure logic behind the reconciler-as-health-probe and the status
assert-noop guard. Runs fully offline (no SEC, no DB).
"""
from __future__ import annotations

from datetime import date

import pytest

import etp_tracker.reconciler as rc
import webapp.services.status_reconciler as sr


def _row(form, accession, company="Acme Trust"):
    return rc.IndexRow(form_type=form, company_name=company, cik="111",
                       date_filed="2026-06-18",
                       filename=f"edgar/data/111/{accession}.txt",
                       accession_number=accession)


# ---------------------------------------------------------------------------
# Reconciler pure diff — the ingest gap
# ---------------------------------------------------------------------------

def test_accepted_from_index_keeps_only_fund_forms():
    rows = [_row("485APOS", "a"), _row("497", "b"), _row("8-K", "c"), _row("10-K", "d")]
    kept = {r.form_type for r in rc.accepted_from_index(rows)}
    assert kept == {"485APOS", "497"}


def test_diff_missed_is_the_gap():
    accepted = [_row("485APOS", "a"), _row("497", "b"), _row("485BPOS", "c")]
    # 'a' already in our DB -> only 'b' and 'c' are the ingest gap
    missed = rc.diff_missed(accepted, existing_accessions={"a"})
    assert {r.accession_number for r in missed} == {"b", "c"}


def test_diff_missed_empty_when_all_present():
    accepted = [_row("485APOS", "a"), _row("497", "b")]
    assert rc.diff_missed(accepted, existing_accessions={"a", "b"}) == []


def test_probe_result_missed_count():
    pr = rc.ProbeResult(target_date=date(2026, 6, 18),
                        missed=[_row("485APOS", "a"), _row("497", "b")])
    assert pr.missed_count == 2


def test_ingest_gap_alert_is_noop_when_clean(monkeypatch):
    # zero missed -> never escalates (no clutter)
    called = {"n": 0}

    def _fake_alert(*a, **k):
        called["n"] += 1
    monkeypatch.setattr("etp_tracker.email_alerts.send_critical_alert", _fake_alert, raising=False)
    rc._escalate_ingest_gap(0, [])
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# Status derivation — single source, used at ingest + by the assert guard
# ---------------------------------------------------------------------------

def test_derive_status_bloomberg_authoritative():
    assert sr.derive_status({"bloomberg_actv_evidence": True}) == sr.STATUS_LISTED
    assert sr.derive_status({"bloomberg_liqu_evidence": True}) == sr.STATUS_DELISTED


def test_derive_status_sec_lifecycle():
    assert sr.derive_status({"sec_effective_evidence": True}) == sr.STATUS_EFFECTIVE
    assert sr.derive_status({"sec_delayed_evidence": True}) == sr.STATUS_DELAYED
    assert sr.derive_status({"sec_filed_evidence": True}) == sr.STATUS_FILED
    assert sr.derive_status({}) == sr.STATUS_UNDER_CONSIDERATION


def test_capm_case_status_never_emits_forbidden():
    # every derived status maps to a canonical CapM display (no Pending/Delayed/Target List)
    forbidden = {"Pending", "Delayed", "Target List", "Suspended"}
    for disp in sr._CAPM_CASE_STATUS.values():
        assert disp not in forbidden


def test_is_promotion_ordering():
    assert sr._is_promotion(sr.STATUS_FILED, sr.STATUS_LISTED) is True
    assert sr._is_promotion(sr.STATUS_LISTED, sr.STATUS_FILED) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

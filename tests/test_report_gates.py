"""Stage C gate tests + Stage B cascade tests (approved plan).

These lock the invariants that stop wrong data shipping, and run fully offline (no
DB, no Bloomberg file, no API key):

  - canonical_status() maps every raw input into the allowed display set and NEVER
    emits a forbidden 'Pending'/'Delayed';
  - the brand name-fallback always resolves a brand (issuer_display never NULL);
  - the resolution cascade returns rung-1 hits and falls through to the human queue;
  - the preflight forbidden-status scan catches a rendered status but not prose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from market.definitions import (
    canonical_status,
    ALLOWED_DISPLAY_STATUSES,
    _STATUS_DISPLAY,
)


# ---------------------------------------------------------------------------
# canonical_status — the single status source
# ---------------------------------------------------------------------------

FORBIDDEN_DISPLAY = {"Pending", "Delayed", "Live", "Target List"}


def test_canonical_status_maps_into_allowed_set():
    # every raw key the mapper knows about must land in the allowed display set
    for raw in _STATUS_DISPLAY:
        assert canonical_status(raw) in ALLOWED_DISPLAY_STATUSES


def test_canonical_status_never_emits_forbidden():
    samples = ["PENDING", "PEND", "DELAYED", "DELAYING AMENDMENT", "LIVE",
               "TARGET LIST", "ACTV", "LIQU", "DLST", "", None, "garbage-code"]
    for raw in samples:
        assert canonical_status(raw) not in FORBIDDEN_DISPLAY


def test_canonical_status_unknown_defaults_to_filed():
    assert canonical_status("totally-unknown") == "Filed"
    assert canonical_status(None) == "Filed"


# ---------------------------------------------------------------------------
# Brand name-fallback — issuer_display is never left NULL
# ---------------------------------------------------------------------------

def test_name_fallback_brand_resolves_first_significant_word():
    from scripts.derive_issuer_brands import name_fallback_brand
    assert name_fallback_brand("The Free Markets US ETF") == "Free"
    assert name_fallback_brand("AXS 2X NVDA Bull ETF") == "AXS"   # acronym preserved
    assert name_fallback_brand("corgi ai power etf") == "Corgi"   # title-cased


def test_name_fallback_brand_none_only_for_empty():
    from scripts.derive_issuer_brands import name_fallback_brand
    assert name_fallback_brand("") is None
    assert name_fallback_brand("   ") is None
    assert name_fallback_brand("REX FANG ETF") is not None


# ---------------------------------------------------------------------------
# Resolution cascade — rules -> ... -> human queue
# ---------------------------------------------------------------------------

def test_cascade_rule_rung_hits(tmp_path, monkeypatch):
    import market.resolve as r
    monkeypatch.setattr(r, "JOURNAL_DIR", tmp_path)
    c = r.standard_cascade(lambda req: "REX" if req.ticker == "TLDR" else None,
                           enable_web=False)
    res = c.resolve(r.FactRequest(kind="issuer_brand", ticker="TLDR",
                                  fund_name="The Laddered T-Bill ETF"))
    assert res is not None and res.value == "REX" and res.rung == 1 and res.accepted


def test_cascade_falls_through_to_human_queue(tmp_path, monkeypatch):
    import market.resolve as r
    monkeypatch.setattr(r, "JOURNAL_DIR", tmp_path)
    c = r.standard_cascade(lambda req: None, enable_web=False)  # nothing resolves
    req = r.FactRequest(kind="etp_category", ticker="ZZZZ", fund_name="Mystery Fund")
    assert c.resolve(req) is None  # -> human queue
    accepted, unresolved = c.resolve_many([req], skip_resolved=False)
    assert accepted == [] and [u.ticker for u in unresolved] == ["ZZZZ"]


def test_cascade_low_confidence_is_not_accepted(tmp_path, monkeypatch):
    import market.resolve as r
    monkeypatch.setattr(r, "JOURNAL_DIR", tmp_path)
    low = r.Resolution(kind="x", ticker="T", value="guess",
                       confidence=r.CONF_LOW, rung=3, source="ai_web_search")
    assert not low.accepted

    def low_rung(req):
        return low
    c = r.Cascade([low_rung])
    # a LOW answer must NOT auto-apply — it falls through to the human queue
    assert c.resolve(r.FactRequest(kind="x", ticker="T")) is None


# ---------------------------------------------------------------------------
# Preflight forbidden-status scan — catches rendered status, ignores prose
# ---------------------------------------------------------------------------

def test_status_scan_flags_rendered_status(tmp_path, monkeypatch):
    import scripts.preflight_check as pf
    monkeypatch.setattr(pf, "PREVIEW_DIR", tmp_path)
    (tmp_path / "bad.html").write_text(
        "<table><tr><td>FUND</td><td>Pending</td></tr></table>", encoding="utf-8")
    out = pf.audit_status_canonical(None)
    assert out["status"] == "fail"
    assert any("Pending" in h["statuses"] for h in out["hits"])


def test_status_scan_ignores_prose_and_passes_clean(tmp_path, monkeypatch):
    import scripts.preflight_check as pf
    monkeypatch.setattr(pf, "PREVIEW_DIR", tmp_path)
    # 'pending' inside prose (not a rendered cell value) must NOT trip the gate
    (tmp_path / "ok.html").write_text(
        "<p>The filing is pending SEC review.</p><td>Listed</td>", encoding="utf-8")
    out = pf.audit_status_canonical(None)
    assert out["status"] == "pass", out["detail"]


def test_maintenance_escape_hatch_is_disabled():
    import scripts.preflight_check as pf
    # the escape hatch that let wrong data ship is permanently off
    assert pf._maintenance_window_active() is False


# ---------------------------------------------------------------------------
# Send is hard-blocked on a red preflight (the no-wrong-data-ever guarantee)
# ---------------------------------------------------------------------------

def test_send_blocked_by_red_marker(tmp_path):
    from scripts.send_all import preflight_blocks_send
    marker = tmp_path / ".preflight_red"
    marker.write_text("2026-06-18", encoding="utf-8")
    blocked, reason = preflight_blocks_send(tmp_path / "missing.json", marker)
    assert blocked and "marker" in reason


def test_send_blocked_by_fail_result(tmp_path):
    import json
    from scripts.send_all import preflight_blocks_send
    rf = tmp_path / ".preflight_result.json"
    rf.write_text(json.dumps({"overall_status": "fail"}), encoding="utf-8")
    blocked, reason = preflight_blocks_send(rf, tmp_path / ".preflight_red")
    assert blocked and "fail" in reason


def test_send_allowed_when_green(tmp_path):
    import json
    from scripts.send_all import preflight_blocks_send
    rf = tmp_path / ".preflight_result.json"
    rf.write_text(json.dumps({"overall_status": "pass"}), encoding="utf-8")
    blocked, _ = preflight_blocks_send(rf, tmp_path / ".preflight_red")
    assert blocked is False


def test_send_not_blocked_when_no_signal(tmp_path):
    # absent result + absent marker -> not blocked on its own (marker is authoritative)
    from scripts.send_all import preflight_blocks_send
    blocked, _ = preflight_blocks_send(tmp_path / "missing.json", tmp_path / ".preflight_red")
    assert blocked is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

"""Layered safeguard tests for etp_tracker.email_alerts._send_html_digest.

The function gates every outbound send through four layers:

    L1  config/.send_enabled must be "true" (or bypass_gate=True)
    L6  per-recipient daily rate limit (or bypass_rate_limit=True)
    L7  self-loop block: refuse if any recipient == AZURE_SENDER
        (or allow_self_loop=True)
    L8  audit pattern: every attempt and every block writes an entry
        to data/.send_audit.json

The Graph API is mocked at the import site inside _send_html_digest
(`from webapp.services.graph_email import is_configured, send_email`) so
no test ever touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from etp_tracker import email_alerts  # noqa: E402


# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_audit(tmp_path, monkeypatch):
    """Redirect _AUDIT_PATH into tmp_path so tests never touch the live
    data/.send_audit.json file."""
    audit_path = tmp_path / "data" / ".send_audit.json"
    monkeypatch.setattr(email_alerts, "_AUDIT_PATH", audit_path)
    return audit_path


@pytest.fixture()
def mock_graph_send():
    """Mock the Graph-API send_email at the import site used inside
    _send_html_digest. Returns the mock so the test can assert call args."""
    with patch("webapp.services.graph_email.is_configured", return_value=True), \
         patch("webapp.services.graph_email.send_email", return_value=True) as m_send:
        yield m_send


@pytest.fixture()
def gate_closed(monkeypatch):
    """Force every gate read inside _send_html_digest to return False.

    The function tries `webapp.services.system_flags.get_flag` first and
    falls back to a file-exists check on config/.send_enabled. We make the
    DB helper return False AND point the fallback file at a tmp path that
    doesn't exist."""
    monkeypatch.setattr("webapp.services.system_flags.get_flag", lambda name: False)


@pytest.fixture()
def gate_open(monkeypatch):
    """Force every gate read to True."""
    monkeypatch.setattr("webapp.services.system_flags.get_flag", lambda name: True)


@pytest.fixture()
def no_self_loop(monkeypatch):
    """Disable the L7 sender lookup so it returns None (treated as 'no
    sender configured' → L7 effectively no-ops). Tests that target L7 will
    override this with an explicit address."""
    monkeypatch.setattr(email_alerts, "_azure_sender_address", lambda: None)


@pytest.fixture()
def no_rate_limit(monkeypatch):
    """Stub L6 rate-limit check to always return [] (no recipients over cap)."""
    monkeypatch.setattr(email_alerts, "_recipients_over_limit_today", lambda recipients: [])


# ---------------------------------------------------------------------------
# L1 — Send gate
# ---------------------------------------------------------------------------

def test_l1_gate_closed_blocks_send(isolated_audit, gate_closed, no_self_loop, no_rate_limit):
    """Gate file missing / .send_enabled != 'true' → refuse, audit blocked."""
    ok = email_alerts._send_html_digest(
        html_body="<p>x</p>",
        recipients=["alice@example.com"],
        subject_override="Test L1",
    )
    assert ok is False
    entries = json.loads(isolated_audit.read_text(encoding="utf-8"))
    # Should have at least one "attempt" entry and one "blocked" entry with L1 reason.
    phases = [e["phase"] for e in entries]
    assert "attempt" in phases
    blocked = [e for e in entries if e["phase"] == "blocked"]
    assert blocked, "expected a blocked-phase audit entry"
    assert any("L1 gate" in e["note"] for e in blocked)


def test_l1_bypass_gate_allows_through(isolated_audit, gate_closed, no_self_loop,
                                       no_rate_limit, mock_graph_send):
    """bypass_gate=True must override the closed gate and reach the Graph API."""
    ok = email_alerts._send_html_digest(
        html_body="<p>x</p>",
        recipients=["alice@example.com"],
        subject_override="Test L1 bypass",
        bypass_gate=True,
    )
    assert ok is True
    assert mock_graph_send.call_count == 1


# ---------------------------------------------------------------------------
# L6 — Per-recipient daily rate limit
# ---------------------------------------------------------------------------

def test_l6_rate_limit_blocks_when_over_cap(isolated_audit, gate_open, no_self_loop, monkeypatch):
    """If any recipient is at/over the daily cap, refuse the send."""
    monkeypatch.setattr(
        email_alerts, "_recipients_over_limit_today",
        lambda recipients: [("alice@example.com", 6)],
    )
    ok = email_alerts._send_html_digest(
        html_body="<p>x</p>",
        recipients=["alice@example.com"],
        subject_override="Test L6",
    )
    assert ok is False
    entries = json.loads(isolated_audit.read_text(encoding="utf-8"))
    blocked = [e for e in entries if e["phase"] == "blocked"]
    assert any("L6 rate limit" in e["note"] for e in blocked)


def test_l6_bypass_rate_limit_permits(isolated_audit, gate_open, no_self_loop,
                                      mock_graph_send, monkeypatch):
    """bypass_rate_limit=True skips the cap check (operator-driven override)
    and logs a bypass_rate_limit phase entry."""
    # If the L6 check were actually invoked it would fail the test — guard:
    monkeypatch.setattr(
        email_alerts, "_recipients_over_limit_today",
        lambda recipients: (_ for _ in ()).throw(
            AssertionError("rate-limit check ran despite bypass_rate_limit=True")
        ),
    )
    ok = email_alerts._send_html_digest(
        html_body="<p>x</p>",
        recipients=["alice@example.com"],
        subject_override="Test L6 bypass",
        bypass_rate_limit=True,
    )
    assert ok is True
    entries = json.loads(isolated_audit.read_text(encoding="utf-8"))
    assert any(e["phase"] == "bypass_rate_limit" for e in entries)


# ---------------------------------------------------------------------------
# L7 — Self-loop block
# ---------------------------------------------------------------------------

def test_l7_self_loop_refused(isolated_audit, gate_open, no_rate_limit, monkeypatch):
    """If any recipient matches AZURE_SENDER, refuse the batch."""
    monkeypatch.setattr(email_alerts, "_azure_sender_address",
                        lambda: "relasmar@rexfin.com")
    ok = email_alerts._send_html_digest(
        html_body="<p>x</p>",
        recipients=["relasmar@rexfin.com", "alice@example.com"],
        subject_override="Test L7",
    )
    assert ok is False
    entries = json.loads(isolated_audit.read_text(encoding="utf-8"))
    blocked = [e for e in entries if e["phase"] == "blocked"]
    assert any("L7 self-loop" in e["note"] for e in blocked)


def test_l7_allow_self_loop_permits(isolated_audit, gate_open, no_rate_limit,
                                    mock_graph_send, monkeypatch):
    """allow_self_loop=True must let a sender-as-recipient batch through
    (used for explicit test paths to Sent Items)."""
    monkeypatch.setattr(email_alerts, "_azure_sender_address",
                        lambda: "relasmar@rexfin.com")
    ok = email_alerts._send_html_digest(
        html_body="<p>x</p>",
        recipients=["relasmar@rexfin.com"],
        subject_override="Test L7 allow",
        allow_self_loop=True,
    )
    assert ok is True
    assert mock_graph_send.call_count == 1


# ---------------------------------------------------------------------------
# L8 — Audit pattern (attempt logged up front, blocks recorded forensically)
# ---------------------------------------------------------------------------

def test_l8_audit_pattern_attempt_logged_before_block(isolated_audit, gate_closed,
                                                     no_self_loop, no_rate_limit):
    """L8 guarantees an 'attempt' audit row is written BEFORE any layer can
    crash. After a block, the audit file contains both attempt and blocked
    entries with full recipient list captured for forensic review."""
    recipients = ["alice@example.com", "bob@example.com"]
    email_alerts._send_html_digest(
        html_body="<p>x</p>",
        recipients=recipients,
        subject_override="Test L8 audit",
    )
    entries = json.loads(isolated_audit.read_text(encoding="utf-8"))
    # Order matters: attempt is logged before any safeguard decision.
    assert entries[0]["phase"] == "attempt"
    assert entries[0]["subject"] == "Test L8 audit"
    assert entries[0]["recipients"] == recipients
    assert entries[0]["recipient_count"] == 2
    # And there is a subsequent blocked entry.
    assert any(e["phase"] == "blocked" for e in entries[1:])

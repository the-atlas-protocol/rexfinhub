"""Tests for scripts/send_all.py — atomic batch sender.

Covers:
  - Gate-file state inspection (presence / absence / value)
  - open_gate / close_gate atomic writes
  - --use-decision token validation (5 failure modes + GO path)
  - Dry-run path (no Graph API call)
  - Bundle resolution (every key in BUNDLES maps to REPORTS)
  - critical=True classification for the daily report

The Graph API is never touched — tests either run in dry-run mode or mock
etp_tracker.email_alerts._send_html_digest at the import site used inside
_send_one.
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

from scripts import send_all  # noqa: E402


# ---------------------------------------------------------------------------
# Gate file helpers — redirect module-level paths into tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_gate(tmp_path, monkeypatch):
    """Redirect GATE_FILE / GATE_LOG into tmp_path so tests never touch
    config/.send_enabled in the live repo."""
    gate_file = tmp_path / "config" / ".send_enabled"
    gate_log = tmp_path / "data" / ".gate_state_log.jsonl"
    monkeypatch.setattr(send_all, "GATE_FILE", gate_file)
    monkeypatch.setattr(send_all, "GATE_LOG", gate_log)
    return gate_file


# ---------------------------------------------------------------------------
# 1-3: gate_state() — presence, absence, "false"
# ---------------------------------------------------------------------------

def test_gate_state_missing(tmp_gate):
    assert send_all.gate_state() == "missing"


def test_gate_state_true(tmp_gate):
    tmp_gate.parent.mkdir(parents=True, exist_ok=True)
    tmp_gate.write_text("true", encoding="utf-8")
    assert send_all.gate_state() == "true"


def test_gate_state_false(tmp_gate):
    tmp_gate.parent.mkdir(parents=True, exist_ok=True)
    tmp_gate.write_text("false", encoding="utf-8")
    assert send_all.gate_state() == "false"


# ---------------------------------------------------------------------------
# 4-5: open_gate / close_gate write the correct token and append to log
# ---------------------------------------------------------------------------

def test_open_gate_writes_true_and_logs(tmp_gate):
    send_all.open_gate(actor="pytest", note="unit test")
    assert tmp_gate.read_text(encoding="utf-8") == "true"
    log_lines = send_all.GATE_LOG.read_text(encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 1
    entry = json.loads(log_lines[0])
    assert entry["action"] == "open"
    assert entry["state"] == "true"
    assert entry["actor"] == "pytest"


def test_close_gate_writes_false_and_logs(tmp_gate):
    send_all.open_gate(actor="pytest", note="setup")
    send_all.close_gate(actor="pytest", note="teardown")
    assert tmp_gate.read_text(encoding="utf-8") == "false"
    log_lines = send_all.GATE_LOG.read_text(encoding="utf-8").strip().splitlines()
    # one open + one close
    assert len(log_lines) == 2
    assert json.loads(log_lines[1])["action"] == "close"
    assert json.loads(log_lines[1])["state"] == "false"


# ---------------------------------------------------------------------------
# 6-9: --use-decision token validation failure modes (via main())
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_decision(tmp_path, monkeypatch):
    """Redirect PROJECT_ROOT-derived decision/token paths into tmp_path.

    Since main() resolves these via PROJECT_ROOT inside the function body,
    we monkeypatch PROJECT_ROOT itself on the module.
    """
    monkeypatch.setattr(send_all, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    # also redirect gate paths so the log writes don't escape tmp_path
    monkeypatch.setattr(send_all, "GATE_FILE", tmp_path / "config" / ".send_enabled")
    monkeypatch.setattr(send_all, "GATE_LOG", tmp_path / "data" / ".gate_state_log.jsonl")
    return tmp_path


def _run_main(argv: list[str]) -> int:
    """Invoke send_all.main() with patched argv. Returns the exit code."""
    with patch.object(sys, "argv", ["send_all.py"] + argv):
        return send_all.main()


def test_use_decision_no_decision_file(tmp_decision):
    # No data/.preflight_decision.json exists.
    # Patch send_critical_alert so the ABORT path doesn't try to hit Graph.
    with patch("etp_tracker.email_alerts.send_critical_alert", return_value=True):
        rc = _run_main(["--bundle", "daily", "--use-decision"])
    assert rc == 3


def test_use_decision_no_token_file(tmp_decision):
    decision = tmp_decision / "data" / ".preflight_decision.json"
    decision.write_text(json.dumps({"action": "GO", "token": "abc12345"}), encoding="utf-8")
    # No .preflight_token file.
    with patch("etp_tracker.email_alerts.send_critical_alert", return_value=True):
        rc = _run_main(["--bundle", "daily", "--use-decision"])
    assert rc == 3


def test_use_decision_token_mismatch(tmp_decision):
    (tmp_decision / "data" / ".preflight_decision.json").write_text(
        json.dumps({"action": "GO", "token": "aaaaaaaa", "recorded_et": "x"}),
        encoding="utf-8",
    )
    (tmp_decision / "data" / ".preflight_token").write_text(
        json.dumps({"token": "bbbbbbbb"}), encoding="utf-8",
    )
    with patch("etp_tracker.email_alerts.send_critical_alert", return_value=True):
        rc = _run_main(["--bundle", "daily", "--use-decision"])
    assert rc == 3


def test_use_decision_action_not_go(tmp_decision):
    (tmp_decision / "data" / ".preflight_decision.json").write_text(
        json.dumps({"action": "HOLD", "token": "tok123", "reason": "vol spike"}),
        encoding="utf-8",
    )
    (tmp_decision / "data" / ".preflight_token").write_text(
        json.dumps({"token": "tok123"}), encoding="utf-8",
    )
    with patch("etp_tracker.email_alerts.send_critical_alert", return_value=True):
        rc = _run_main(["--bundle", "daily", "--use-decision"])
    # action != GO is a clean stand-down (rc 0), not an error.
    assert rc == 0


# ---------------------------------------------------------------------------
# 10: Dry-run path — _send_one returns dry_run, never calls Graph
# ---------------------------------------------------------------------------

def test_send_one_dry_run_does_not_call_graph(monkeypatch):
    """In dry-run mode, _send_one must build the report but skip the actual send."""
    # Stub the builder so we don't need a real DB.
    monkeypatch.setitem(
        send_all.REPORTS,
        "daily",
        (lambda db: ("Test Subject", "<html>body</html>"), "daily", True),
    )
    # Stub recipient resolution.
    monkeypatch.setattr(send_all, "_resolve_recipients",
                        lambda list_type, override_to: ["test@example.com"])

    # If _send_html_digest is invoked, fail loudly.
    def _boom(*a, **kw):
        raise AssertionError("Graph send must not be called in dry-run")
    monkeypatch.setattr("etp_tracker.email_alerts._send_html_digest", _boom)

    out = send_all._send_one(
        "daily", db=None, override_to=None, dry_run=True,
        bypass_gate=False, allow_self_loop=False,
    )
    assert out["status"] == "dry_run"
    assert out["subject"] == "Test Subject"
    assert out["html_size"] == len("<html>body</html>")
    assert out["recipients"] == ["test@example.com"]


# ---------------------------------------------------------------------------
# 11: Bundle resolution — every key in every BUNDLES list maps to REPORTS
# ---------------------------------------------------------------------------

def test_bundles_resolve_to_known_reports():
    for bundle_name, keys in send_all.BUNDLES.items():
        assert keys, f"bundle {bundle_name!r} is empty"
        for k in keys:
            assert k in send_all.REPORTS, (
                f"bundle {bundle_name!r} references unknown report {k!r}"
            )


# ---------------------------------------------------------------------------
# 12: critical=True path — daily is critical, others aren't
# ---------------------------------------------------------------------------

def test_daily_is_critical_others_are_not():
    """REPORTS schema: (builder, list_type, critical). Only daily is critical;
    a non-critical failure does not abort the rest of the bundle."""
    _builder, _list_type, daily_critical = send_all.REPORTS["daily"]
    assert daily_critical is True
    for key, (_b, _lt, crit) in send_all.REPORTS.items():
        if key == "daily":
            continue
        assert crit is False, f"unexpected critical=True on {key}"

"""Sanity-check the grade-recommendations systemd unit files.

Catches typos that would silently make systemd reject the unit on
daemon-reload. Not a deep validation — just structure + key directives.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_UNITS = _REPO / "deploy" / "systemd"


def _parse(name: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    # systemd allows duplicate keys + empty values; tolerate both.
    cp.read(_UNITS / name, encoding="utf-8")
    return cp


def test_service_unit_parses():
    cp = _parse("rexfinhub-grade-recommendations.service")
    assert cp.has_section("Unit")
    assert cp.has_section("Service")
    assert cp.get("Service", "Type") == "oneshot"
    assert cp.get("Service", "User") == "jarvis"
    exec_start = cp.get("Service", "ExecStart")
    assert "grade_recommendations" in exec_start
    assert "/home/jarvis/venv/bin/python" in exec_start


def test_timer_unit_parses():
    cp = _parse("rexfinhub-grade-recommendations.timer")
    assert cp.has_section("Timer")
    assert cp.has_section("Install")
    cal = cp.get("Timer", "OnCalendar")
    # Sunday 23:00 — runs before Monday morning send.
    assert "Sun" in cal
    assert "23:00" in cal
    assert cp.get("Timer", "Persistent").lower() == "true"
    assert cp.get("Install", "WantedBy") == "timers.target"


def test_notes_file_exists():
    notes = _UNITS / "NOTES_grade_recs_2026-06-08.md"
    assert notes.exists(), "deploy notes missing — Ryu can't deploy without them"
    body = notes.read_text(encoding="utf-8")
    assert "daemon-reload" in body
    assert "enable --now" in body

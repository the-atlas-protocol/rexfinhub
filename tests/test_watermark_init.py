"""Tests for ORCH-03/08: sync_rex_products watermark initialisation.

* `--init-watermark` writes today's date to the watermark file and exits.
* `read_watermark()` on a missing file creates one with today's date
  (the pre-fix default of 2026-04-01 caused fresh installs to re-process
  two months of filings).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_watermark(monkeypatch, tmp_path: Path):
    """Redirect the WATERMARK_FILE module global to a tmp path."""
    import scripts.sync_rex_products_from_filings as sync

    wm = tmp_path / ".sync_rex_products_watermark"
    monkeypatch.setattr(sync, "WATERMARK_FILE", wm)
    yield wm


def test_init_watermark_writes_today(tmp_watermark, monkeypatch):
    """`--init-watermark` writes today's date and returns 0 without scanning."""
    import scripts.sync_rex_products_from_filings as sync

    # Sanity: file must not exist before.
    assert not tmp_watermark.exists()

    rc = sync.main(["--init-watermark"])
    assert rc == 0
    assert tmp_watermark.exists()
    content = tmp_watermark.read_text(encoding="utf-8").strip()
    assert content == date.today().isoformat()


def test_init_watermark_overwrites_existing(tmp_watermark):
    """Re-running `--init-watermark` with a stale watermark resets to today."""
    import scripts.sync_rex_products_from_filings as sync

    tmp_watermark.write_text("2024-01-01\n", encoding="utf-8")

    rc = sync.main(["--init-watermark"])
    assert rc == 0
    assert tmp_watermark.read_text(encoding="utf-8").strip() == date.today().isoformat()


def test_missing_watermark_creates_today_not_2026_04_01(tmp_watermark):
    """The previous default was date(2026, 4, 1) — the bug. On a missing
    watermark, read_watermark must now return TODAY and write it."""
    import scripts.sync_rex_products_from_filings as sync

    assert not tmp_watermark.exists()

    got = sync.read_watermark()
    assert got == date.today()
    assert got != date(2026, 4, 1)
    # File now written.
    assert tmp_watermark.exists()
    assert tmp_watermark.read_text(encoding="utf-8").strip() == date.today().isoformat()


def test_unreadable_watermark_resets_to_today(tmp_watermark):
    """A corrupt watermark file resets to today (writes through)."""
    import scripts.sync_rex_products_from_filings as sync

    tmp_watermark.write_text("not-a-date\n", encoding="utf-8")
    got = sync.read_watermark()
    assert got == date.today()
    assert tmp_watermark.read_text(encoding="utf-8").strip() == date.today().isoformat()


def test_valid_watermark_preserved(tmp_watermark):
    """A valid watermark file is read as-is and NOT overwritten."""
    import scripts.sync_rex_products_from_filings as sync

    tmp_watermark.write_text("2026-05-15\n", encoding="utf-8")
    got = sync.read_watermark()
    assert got == date(2026, 5, 15)
    # Unchanged.
    assert tmp_watermark.read_text(encoding="utf-8").strip() == "2026-05-15"

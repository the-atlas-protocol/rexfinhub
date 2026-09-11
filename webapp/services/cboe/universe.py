"""Ticker universe generation + daily-tier selection.

The universe is every 1-4 letter uppercase alpha combo (475,254 total).
The daily tier is the small subset we re-check every night: all 1-3 letter
combos (18,278), a rolling 50k-ticker slice of the 4-letter space (full
4-letter coverage every ~9 nights), and every ticker that changed state in
the last 30 days (a small, high-signal recheck set). ~68k symbols total —
deliberately bounded so the nightly scan stays polite, not the full 475k.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from itertools import product
from string import ascii_uppercase
from typing import Iterator

from sqlalchemy.orm import Session

from webapp.models import CboeScanRun, CboeStateChange

ROLLING_BATCH_SIZE = 50_000
RECENT_FLIP_WINDOW_DAYS = 30

_RESUMABLE_TIERS = ("daily", "4-letter-batch", "full", "4-letter")


def combos_of_length(n: int) -> Iterator[str]:
    for combo in product(ascii_uppercase, repeat=n):
        yield "".join(combo)


def all_1_to_3_letter() -> list[str]:
    out: list[str] = []
    for n in (1, 2, 3):
        out.extend(combos_of_length(n))
    return out


def all_4_letter() -> list[str]:
    return list(combos_of_length(4))


def recently_flipped(db: Session, days: int = RECENT_FLIP_WINDOW_DAYS) -> list[str]:
    """Tickers whose available/taken state changed within the last `days` — a
    small, high-signal set worth re-checking often (something happened there)."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(CboeStateChange.ticker)
        .filter(CboeStateChange.detected_at >= cutoff)
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _last_4_letter_checkpoint(db: Session) -> str | None:
    row = (
        db.query(CboeScanRun.last_ticker_scanned)
        .filter(CboeScanRun.tier.in_(_RESUMABLE_TIERS))
        .filter(CboeScanRun.last_ticker_scanned.isnot(None))
        .order_by(CboeScanRun.started_at.desc())
        .first()
    )
    if row is None:
        return None
    candidate = row[0]
    return candidate if candidate and len(candidate) == 4 else None


def rolling_4_letter_batch(db: Session, batch_size: int = ROLLING_BATCH_SIZE) -> list[str]:
    """Slice of the 4-letter space starting after the last checkpoint; wraps."""
    space = all_4_letter()
    checkpoint = _last_4_letter_checkpoint(db)
    start = 0
    if checkpoint is not None:
        try:
            start = space.index(checkpoint) + 1
        except ValueError:
            start = 0
    if start >= len(space):
        start = 0
    end = start + batch_size
    if end <= len(space):
        return space[start:end]
    # wrap
    return space[start:] + space[: end - len(space)]


def daily_tier(db: Session, rolling_batch_size: int = ROLLING_BATCH_SIZE) -> list[str]:
    """1-3 letter + rolling 4-letter batch + recently-flipped. De-duplicated.

    Deliberately bounded (~68k). The old behaviour also folded in every
    known-available ticker, which after a full sweep meant ~450k symbols —
    no smaller than a full scan, which is what got the VPS IP WAF-blocked."""
    seen: set[str] = set()
    out: list[str] = []

    def _add(t: str) -> None:
        if t not in seen:
            seen.add(t)
            out.append(t)

    for t in all_1_to_3_letter():
        _add(t)
    for t in rolling_4_letter_batch(db, rolling_batch_size):
        _add(t)
    for t in recently_flipped(db):
        _add(t)
    return out


def tier_by_name(db: Session, tier: str) -> list[str]:
    if tier == "1-letter":
        return list(combos_of_length(1))
    if tier == "2-letter":
        return list(combos_of_length(2))
    if tier == "3-letter":
        return list(combos_of_length(3))
    if tier == "4-letter":
        return all_4_letter()
    if tier == "daily":
        return daily_tier(db)
    if tier == "full":
        return all_1_to_3_letter() + all_4_letter()
    raise ValueError(f"Unknown tier: {tier!r}")

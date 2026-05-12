"""
Universal REX competitor lookup.

Single source of truth: ``config/rules/competitor_map.csv``.
One row per REX product. Pipe-separated ``competitor_tickers``.

Public API:
    load_competitor_map() -> dict[str, CompetitorEntry]
    get_competitors(rex_ticker) -> list[str]
    get_competitors_by_suite(suite) -> dict[str, CompetitorEntry]
    attach_competitors(fund_data: dict) -> dict

All tickers are normalized to the ``TICKER US`` / ``TICKER LN`` Bloomberg style
that ``rex_funds.csv`` and ``competitor_groups.csv`` already use, so lookups
work whether the caller passes ``"NVDX"`` or ``"NVDX US"``.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
# webapp/services/competitor_lookup.py -> repo root is three levels up
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CSV_PATH = _REPO_ROOT / "config" / "rules" / "competitor_map.csv"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CompetitorEntry:
    """Single row from competitor_map.csv (one REX product)."""

    rex_ticker: str
    rex_suite: str
    rex_strategy: str
    competitor_tickers: tuple[str, ...] = field(default_factory=tuple)
    competitor_logic: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "rex_ticker": self.rex_ticker,
            "rex_suite": self.rex_suite,
            "rex_strategy": self.rex_strategy,
            "competitor_tickers": list(self.competitor_tickers),
            "competitor_logic": self.competitor_logic,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Cache (thread-safe, lazy)
# ---------------------------------------------------------------------------
_CACHE: dict[str, CompetitorEntry] | None = None
_CACHE_MTIME: float | None = None
_CACHE_LOCK = threading.Lock()


def _normalize_ticker(t: str) -> str:
    """Normalize to 'TICKER US' / 'TICKER LN' form (matches rex_funds.csv)."""
    if not t:
        return ""
    t = t.strip().upper()
    if t.endswith(" US") or t.endswith(" LN"):
        return t
    return f"{t} US"


def _parse_competitor_tickers(raw: str) -> tuple[str, ...]:
    """Parse the pipe-separated competitor_tickers cell -> normalized tuple."""
    if not raw or not isinstance(raw, str):
        return ()
    parts = [p.strip() for p in raw.split("|")]
    return tuple(_normalize_ticker(p) for p in parts if p)


def _load_from_disk(path: Path) -> dict[str, CompetitorEntry]:
    """Read competitor_map.csv from disk. Returns {normalized_rex_ticker: entry}."""
    if not path.exists():
        log.warning("competitor_map.csv not found at %s", path)
        return {}
    try:
        # Match project convention: python engine + skip bad lines (CLAUDE.md).
        df = pd.read_csv(path, engine="python", on_bad_lines="skip")
    except Exception as e:  # pragma: no cover - defensive
        log.warning("Failed to read competitor_map.csv: %s", e)
        return {}

    required = {
        "rex_ticker", "rex_suite", "rex_strategy",
        "competitor_tickers", "competitor_logic", "notes",
    }
    missing = required - set(df.columns)
    if missing:
        log.warning("competitor_map.csv missing columns: %s", sorted(missing))
        return {}

    result: dict[str, CompetitorEntry] = {}
    for _, row in df.iterrows():
        rex_t = _normalize_ticker(str(row.get("rex_ticker", "") or ""))
        if not rex_t:
            continue
        entry = CompetitorEntry(
            rex_ticker=rex_t,
            rex_suite=str(row.get("rex_suite", "") or "").strip(),
            rex_strategy=str(row.get("rex_strategy", "") or "").strip(),
            competitor_tickers=_parse_competitor_tickers(
                str(row.get("competitor_tickers", "") or "")
            ),
            competitor_logic=str(row.get("competitor_logic", "") or "").strip(),
            notes=str(row.get("notes", "") or "").strip(),
        )
        result[rex_t] = entry
    return result


def load_competitor_map(force_reload: bool = False) -> dict[str, CompetitorEntry]:
    """Return the full {rex_ticker: CompetitorEntry} mapping.

    Cached in-process; reloads automatically if the CSV mtime changes.
    """
    global _CACHE, _CACHE_MTIME
    path = _CSV_PATH
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None

    with _CACHE_LOCK:
        if (
            not force_reload
            and _CACHE is not None
            and _CACHE_MTIME == mtime
        ):
            return _CACHE
        _CACHE = _load_from_disk(path)
        _CACHE_MTIME = mtime
        return _CACHE


def get_competitors(rex_ticker: str) -> list[str]:
    """Return the list of competitor tickers for a given REX product.

    Returns ``[]`` if the ticker is unknown or has no mapped competitors.
    """
    if not rex_ticker:
        return []
    key = _normalize_ticker(rex_ticker)
    entry = load_competitor_map().get(key)
    if entry is None:
        return []
    return list(entry.competitor_tickers)


def get_competitors_by_suite(suite: str) -> dict[str, CompetitorEntry]:
    """Return all CompetitorEntry rows for a given REX suite.

    Suite match is case-insensitive on the trimmed value.
    """
    if not suite:
        return {}
    needle = suite.strip().lower()
    return {
        tk: entry
        for tk, entry in load_competitor_map().items()
        if entry.rex_suite.lower() == needle
    }


def attach_competitors(fund_data: dict) -> dict:
    """Enrich a fund-detail dict with competitor info.

    Looks up the ticker in ``fund_data`` (keys tried in order: ``ticker``,
    ``rex_ticker``) and adds:

        fund_data["competitors"] = [
            {"ticker": ..., ...},   # currently just ticker
        ]
        fund_data["competitor_logic"] = ""
        fund_data["competitor_notes"] = ""

    Returns the same dict for chaining. Always safe to call: if no mapping
    exists, the fields are still added (empty values).
    """
    if not isinstance(fund_data, dict):
        return fund_data
    ticker = fund_data.get("ticker") or fund_data.get("rex_ticker") or ""
    entry = load_competitor_map().get(_normalize_ticker(ticker)) if ticker else None
    if entry is None:
        fund_data["competitors"] = []
        fund_data["competitor_logic"] = ""
        fund_data["competitor_notes"] = ""
        return fund_data
    fund_data["competitors"] = [{"ticker": t} for t in entry.competitor_tickers]
    fund_data["competitor_logic"] = entry.competitor_logic
    fund_data["competitor_notes"] = entry.notes
    return fund_data


__all__ = [
    "CompetitorEntry",
    "load_competitor_map",
    "get_competitors",
    "get_competitors_by_suite",
    "attach_competitors",
]

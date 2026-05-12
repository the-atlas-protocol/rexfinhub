"""Tiered signal strength — replaces the binary `has_signals` flag.

Wave A3 of the stockrecs upgrade. The legacy `has_signals` column was
misleading: it was True iff Bloomberg returned ANY row for the ticker, which
in practice meant "is this a real US-listed equity" — useless for filtering
launch candidates. This module assigns a meaningful strength tier per
candidate based on rank, breadth, and freshness of the signals we actually
care about.

The five tiers
--------------
- ``URGENT``   : ApeWisdom rank top 25 + 3+ active signals + recent inflection
- ``STRONG``   : 2+ active signals + at least one ranked top 100
- ``MODERATE`` : 1+ active signal ranked top 250
- ``WEAK``     : at least one signal ranked top 500 (but nothing better)
- ``NONE``     : no usable signal data at all

Per-signal records (returned alongside the tier) are tuples of
``(signal_name, strength, raw_value, age_days)`` so downstream consumers
(e.g. composite scorer, report renderer) can apply both tier-weighting and
age decay.

The age dimension lets us distinguish a STRONG signal observed today from a
STRONG signal observed two weeks ago — the latter should decay toward
MODERATE in the composite. Bloomberg-derived signals carry the age of their
``mkt_pipeline_runs.finished_at`` row; ApeWisdom signals are fetched live
(age = 0).

Backward compatibility
----------------------
``has_signals`` is preserved as a derived boolean column equal to
``signal_strength != 'NONE'`` so the parquet schema and existing webapp /
report consumers continue to work without recoding.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Iterable

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB = _ROOT / "data" / "etp_tracker.db"


class SignalStrength(IntEnum):
    """Ordered tier of overall signal strength.

    IntEnum so callers can write ``df['signal_strength'] >= MODERATE``
    naturally. The string ``name`` is what gets persisted to parquet.
    """

    NONE = 0
    WEAK = 1
    MODERATE = 2
    STRONG = 3
    URGENT = 4

    @classmethod
    def from_name(cls, name: str) -> "SignalStrength":
        try:
            return cls[name.upper()]
        except (KeyError, AttributeError):
            return cls.NONE


# ---------------------------------------------------------------------------
# Per-signal record
# ---------------------------------------------------------------------------

@dataclass
class SignalRecord:
    """One observation of one signal for one ticker."""

    name: str           # e.g. "mentions_24h", "rvol_30d", "ret_1m"
    strength: SignalStrength
    raw_value: float | None
    age_days: float     # 0.0 == observed today
    rank: int | None = None     # rank within the scored universe (1 = best); None if not ranked

    def decay_factor(self, half_life_days: float = 14.0) -> float:
        """Multiplicative decay so that a 14-day-old signal is worth half a fresh one."""
        if self.age_days <= 0 or half_life_days <= 0:
            return 1.0
        return 0.5 ** (self.age_days / half_life_days)

    def weighted_strength(self) -> float:
        """Strength tier blended with freshness — used by composite scorer."""
        return float(self.strength) * self.decay_factor()


# ---------------------------------------------------------------------------
# Configuration — which Bloomberg columns count as "signals" and how to rank
# ---------------------------------------------------------------------------

# (column_name, ascending) — ascending=False means LARGER value is BETTER
_SCORED_BBG_SIGNALS: tuple[tuple[str, bool], ...] = (
    ("rvol_30d",  False),
    ("rvol_90d",  False),
    ("ret_1m",    False),
    ("ret_1y",    False),
    ("total_oi",  False),
)

# Tier cutoffs based on rank (1 = best)
_TOP_25  = 25
_TOP_100 = 100
_TOP_250 = 250
_TOP_500 = 500

# What counts as a "recent inflection" for URGENT classification.
INFLECTION_DELTA_PCT = 0.50          # +50% mentions vs 24h ago
INFLECTION_RANK_IMPROVEMENT = 10     # rose 10+ ranks in 24h


# ---------------------------------------------------------------------------
# Bloomberg run age — read once per build()
# ---------------------------------------------------------------------------

def get_bbg_run_age_days(db_path: Path = _DB) -> float:
    """Days since the most recent completed bbg pipeline run finished.

    Returns 0.0 if the run finished today, larger numbers as it ages. Falls
    back to 0.0 (assume fresh) if the table can't be queried — we'd rather
    avoid penalising signals than crash the launch_candidates build.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT finished_at FROM mkt_pipeline_runs "
                "WHERE status='completed' AND stock_rows_written > 0 "
                "ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return 0.0
        finished = pd.to_datetime(row[0], utc=True, errors="coerce")
        if pd.isna(finished):
            return 0.0
        now = pd.Timestamp.now(tz=timezone.utc)
        age = (now - finished).total_seconds() / 86400.0
        return max(0.0, float(age))
    except Exception as exc:
        log.warning("get_bbg_run_age_days: %s — defaulting to 0.0", exc)
        return 0.0


# ---------------------------------------------------------------------------
# Per-signal strength — rank-based
# ---------------------------------------------------------------------------

def _rank_to_strength(rank: int | None) -> SignalStrength:
    """Map a 1-indexed rank within the scored universe to a tier."""
    if rank is None:
        return SignalStrength.NONE
    if rank <= _TOP_100:
        return SignalStrength.STRONG
    if rank <= _TOP_250:
        return SignalStrength.MODERATE
    if rank <= _TOP_500:
        return SignalStrength.WEAK
    return SignalStrength.NONE


def _build_signal_ranks(df: pd.DataFrame) -> dict[str, pd.Series]:
    """For each scored bbg signal, return a Series of int ranks aligned to df.index.

    Tickers with NaN for a signal are absent from that signal's rank Series.
    Rank 1 == best.
    """
    out: dict[str, pd.Series] = {}
    for col, ascending in _SCORED_BBG_SIGNALS:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        # method='min' is friendliest for "top N" cutoffs — ties share the better rank
        out[col] = s.rank(ascending=ascending, method="min", na_option="keep").astype("Int64")
    return out


# ---------------------------------------------------------------------------
# ApeWisdom-driven signal records
# ---------------------------------------------------------------------------

def _ape_records_for_ticker(
    ticker: str,
    ape_blob: dict | None,
) -> list[SignalRecord]:
    """Convert one ApeWisdom blob (from signals.load_sentiment_signals) into records."""
    if not ape_blob:
        return []

    recs: list[SignalRecord] = []
    mentions = ape_blob.get("mentions_24h")
    rank = ape_blob.get("apewisdom_rank")
    delta_pct = ape_blob.get("mentions_delta_pct")
    rank_improve = ape_blob.get("rank_improvement")

    # Mentions tier — uses ApeWisdom's own rank when available
    if mentions is not None and mentions > 0:
        if rank is not None and rank <= _TOP_25:
            tier = SignalStrength.URGENT
        elif rank is not None and rank <= _TOP_100:
            tier = SignalStrength.STRONG
        elif rank is not None and rank <= _TOP_250:
            tier = SignalStrength.MODERATE
        elif rank is not None and rank <= _TOP_500:
            tier = SignalStrength.WEAK
        else:
            # No rank info but non-zero mentions — at least WEAK
            tier = SignalStrength.WEAK
        recs.append(SignalRecord(
            name="mentions_24h",
            strength=tier,
            raw_value=float(mentions),
            age_days=0.0,
            rank=int(rank) if rank else None,
        ))

    # Inflection signal — only if material
    if delta_pct is not None and delta_pct >= INFLECTION_DELTA_PCT:
        recs.append(SignalRecord(
            name="mentions_inflection_pct",
            strength=SignalStrength.STRONG,
            raw_value=float(delta_pct),
            age_days=0.0,
        ))
    elif rank_improve is not None and rank_improve >= INFLECTION_RANK_IMPROVEMENT:
        recs.append(SignalRecord(
            name="mentions_inflection_rank",
            strength=SignalStrength.MODERATE,
            raw_value=float(rank_improve),
            age_days=0.0,
        ))

    return recs


# ---------------------------------------------------------------------------
# Aggregation — overall ticker tier from per-signal records
# ---------------------------------------------------------------------------

def aggregate_strength(records: list[SignalRecord]) -> SignalStrength:
    """Collapse per-signal records into an overall ticker tier per the spec."""
    if not records:
        return SignalStrength.NONE

    # URGENT: ApeWisdom rank top 25 + 3+ active signals + recent inflection
    ape_top25 = any(
        r.name == "mentions_24h" and r.rank is not None and r.rank <= _TOP_25
        for r in records
    )
    has_inflection = any(r.name.startswith("mentions_inflection") for r in records)
    n_active = sum(1 for r in records if r.strength >= SignalStrength.WEAK)
    if ape_top25 and n_active >= 3 and has_inflection:
        return SignalStrength.URGENT

    # STRONG: 2+ signals + at least one in top 100
    has_top100 = any(
        r.strength >= SignalStrength.STRONG for r in records
    ) or any(
        r.name == "mentions_24h" and r.rank is not None and r.rank <= _TOP_100
        for r in records
    )
    if n_active >= 2 and has_top100:
        return SignalStrength.STRONG

    # MODERATE: 1+ signal in top 250
    has_top250 = any(r.strength >= SignalStrength.MODERATE for r in records)
    if has_top250:
        return SignalStrength.MODERATE

    # WEAK: at least 1 below top 500 (i.e. anything still tagged WEAK)
    if any(r.strength == SignalStrength.WEAK for r in records):
        return SignalStrength.WEAK

    return SignalStrength.NONE


# ---------------------------------------------------------------------------
# Public entry point — annotate a launch_candidates DataFrame
# ---------------------------------------------------------------------------

def annotate_signal_strength(
    df: pd.DataFrame,
    ape_map: dict[str, dict] | None = None,
    bbg_age_days: float | None = None,
) -> pd.DataFrame:
    """Add ``signal_strength``, ``signal_records``, and derived ``has_signals`` columns.

    Parameters
    ----------
    df : DataFrame indexed by ticker (uppercase, no exchange suffix)
    ape_map : optional {ticker: ape_blob} where ape_blob has keys
        ``mentions_24h``, ``apewisdom_rank``, ``mentions_delta_pct``,
        ``rank_improvement``. Pass ``None`` to skip ApeWisdom signals.
    bbg_age_days : age (in days) of the bbg signal data. If None, queried
        from mkt_pipeline_runs.

    Returns
    -------
    The input df with three new columns:
      - ``signal_strength``  : str (one of NONE/WEAK/MODERATE/STRONG/URGENT)
      - ``signal_records``   : list[dict] of per-signal observations
      - ``has_signals``      : bool, derived = (signal_strength != 'NONE')
    """
    if df.empty:
        df = df.copy()
        df["signal_strength"] = pd.Series(dtype="object")
        df["signal_records"] = pd.Series(dtype="object")
        df["has_signals"] = pd.Series(dtype=bool)
        return df

    if bbg_age_days is None:
        bbg_age_days = get_bbg_run_age_days()

    bbg_ranks = _build_signal_ranks(df)
    ape_map = ape_map or {}

    out = df.copy()
    strengths: list[str] = []
    records_by_ticker: list[list[dict]] = []

    for ticker in out.index:
        records: list[SignalRecord] = []

        # Bloomberg-derived signals — use cross-sectional rank within this universe
        for col, _ in _SCORED_BBG_SIGNALS:
            if col not in bbg_ranks:
                continue
            rank_val = bbg_ranks[col].get(ticker)
            if pd.isna(rank_val):
                continue
            rank_int = int(rank_val)
            tier = _rank_to_strength(rank_int)
            if tier == SignalStrength.NONE:
                continue
            records.append(SignalRecord(
                name=col,
                strength=tier,
                raw_value=float(out.at[ticker, col]) if pd.notna(out.at[ticker, col]) else None,
                age_days=bbg_age_days,
                rank=rank_int,
            ))

        # ApeWisdom signals
        records.extend(_ape_records_for_ticker(ticker, ape_map.get(ticker)))

        overall = aggregate_strength(records)
        strengths.append(overall.name)
        records_by_ticker.append([
            {
                "name": r.name,
                "strength": r.strength.name,
                "raw_value": r.raw_value,
                "age_days": round(r.age_days, 2),
                "rank": r.rank,
            }
            for r in records
        ])

    out["signal_strength"] = strengths
    out["signal_records"] = records_by_ticker
    # Backward-compat — keep `has_signals` as derived boolean.
    out["has_signals"] = [s != SignalStrength.NONE.name for s in strengths]
    return out


def signal_strength_multiplier(strength: str | SignalStrength) -> float:
    """Composite-score multiplier from overall ticker tier.

    Conservative scaling — even URGENT only gives a 1.4x boost; the
    underlying composite_score remains the primary ordering signal.
    """
    if isinstance(strength, str):
        strength = SignalStrength.from_name(strength)
    return {
        SignalStrength.NONE:     1.00,
        SignalStrength.WEAK:     1.00,
        SignalStrength.MODERATE: 1.10,
        SignalStrength.STRONG:   1.25,
        SignalStrength.URGENT:   1.40,
    }[strength]

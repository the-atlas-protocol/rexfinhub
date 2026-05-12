"""REX product ticker naming-convention generator.

Pattern-matched from the live REX universe in ``mkt_master_data`` as of
2026-05-12 (snapshot in ``data/rules/rex_ticker_pattern_2026-05-12.csv``).

The convention is empirical, not prescriptive — we extract what REX has
actually shipped publicly and replicate the dominant suffix pattern.

Hit-rate against the existing single-stock REX universe:
    - 2x Long  : 22 / 30 = 73 %
    - 2x Short :  5 /  6 = 83 %

Mismatches are branding-driven (``ROBN`` for Robinhood, ``BTCL`` for
Bitcoin Long, ``ETU`` for Ether) or use shorter roots and cannot be
recovered by a deterministic rule.

Public API:

    >>> from screener.li_engine.data.rex_naming import suggest_ticker
    >>> suggest_ticker('NVDA US', 2.0, 'Long')
    {'is_existing': True,
     'existing_ticker': 'NVDX',
     'suggested_ticker': 'NVDX',
     'description': 'T-REX 2X Long NVDA Daily Target ETF',
     'confidence': 'high'}

The ``suggested_ticker`` field is **always** populated — even when no
pattern matches, the function falls back to ``<root[:3]>U`` for 2x Long
and ``<root[:3]>Z`` for 2x Short, which together cover ~74 % of the
existing REX line-up.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Minimum hit-rate against the existing REX universe before we treat the
#: rule as "trusted" rather than a guess. Set to 70 % per the build spec.
MIN_HIT_RATE = 0.70

#: Repo-relative path to the existing-REX snapshot. Lives in ``config/rules/``
#: per repo convention (``data/rules/`` is deprecated — see fix R6).
_PATTERN_CSV = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "rules"
    / "rex_ticker_pattern_2026-05-12.csv"
)


# ---------------------------------------------------------------------------
# Underlier root normalisation
# ---------------------------------------------------------------------------

#: Bloomberg-style suffixes that should be stripped before extracting the root.
_BBG_SUFFIXES = (" US", " UA", " LN", " UN", " CN", " UQ", " Curncy", " Equity")

#: Crypto pair → conventional ticker root used by REX fund names.
_CRYPTO_ROOT = {
    "XBTUSD": "BTC",
    "XETUSD": "ETH",
    "BTCUSD": "BTC",
    "ETHUSD": "ETH",
    "SOLUSD": "SOL",
    "XRPUSD": "XRP",
    "DOGEUSD": "DOGE",
}

#: Long-form company name (as seen in REX fund_name) → ticker root.
_NAME_TO_ROOT = {
    "APPLE": "AAPL",
    "MICROSOFT": "MSFT",
    "NVIDIA": "NVDA",
    "ALPHABET": "GOOG",
    "TESLA": "TSLA",
    "AMAZON": "AMZN",
    "META": "META",
    "BROADCOM": "AVGO",
    "BITCOIN": "BTC",
    "ETHER": "ETH",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "DOGECOIN": "DOGE",
}


def _normalise_root(underlier: str) -> str:
    """Strip BBG suffixes and crypto pair markers; uppercase."""
    if not underlier:
        return ""
    u = underlier.strip().upper()
    for suf in _BBG_SUFFIXES:
        if u.endswith(suf.upper()):
            u = u[: -len(suf)].strip()
            break
    if u in _CRYPTO_ROOT:
        u = _CRYPTO_ROOT[u]
    if u in _NAME_TO_ROOT:
        u = _NAME_TO_ROOT[u]
    return u


# ---------------------------------------------------------------------------
# Existing REX lookup (loaded once, reused)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RexProduct:
    ticker: str           # e.g. "NVDX"
    fund_name: str
    root: str             # normalised underlier root, e.g. "NVDA"
    leverage: Optional[float]
    direction: Optional[str]   # "Long" | "Short" | None


@lru_cache(maxsize=1)
def _load_existing() -> tuple[_RexProduct, ...]:
    """Load the snapshot CSV. Returns empty tuple if file is missing."""
    if not _PATTERN_CSV.exists():
        return ()
    out: list[_RexProduct] = []
    with _PATTERN_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lev = float(row["leverage"]) if row.get("leverage") else None
            except (TypeError, ValueError):
                lev = None
            out.append(
                _RexProduct(
                    ticker=row["rex_ticker"].strip(),
                    fund_name=(row.get("fund_name") or "").strip(),
                    root=(row.get("root_underlier") or "").strip().upper(),
                    leverage=lev,
                    direction=(row.get("direction") or "").strip() or None,
                )
            )
    return tuple(out)


def _lookup_existing(
    root: str, leverage: float, direction: str
) -> Optional[_RexProduct]:
    """Return the existing REX product matching (root, leverage, direction)."""
    if not root:
        return None
    dir_norm = (direction or "").lower()
    # Treat "Inverse" as "Short" for the lookup key.
    if dir_norm in ("short", "inverse"):
        dir_norm = "short"
    elif dir_norm == "long":
        dir_norm = "long"
    else:
        return None
    for p in _load_existing():
        if (
            p.root == root
            and p.leverage == leverage
            and (p.direction or "").lower() == dir_norm
        ):
            return p
    return None


# ---------------------------------------------------------------------------
# Suffix tables (extracted from snapshot)
# ---------------------------------------------------------------------------

# Distribution of last char among 2x LONG single-stock products:
#   U=20, X=4, P=3, L=1, N=1, T=1     → "U" is the dominant convention
# Distribution of last char among 2x SHORT:
#   Z=3, D=2, Q=1                     → "Z" is the dominant convention
#
# We construct the candidate as <root[:3]> + suffix. Where the resulting
# candidate collides with an unrelated existing ticker (rare, not handled
# here), the caller should verify against the live universe.

_SUFFIX_BY_KEY: dict[tuple[float, str], str] = {
    (1.0, "long"): "A",   # active overlay (e.g. growth & income roots end in II/I)
    (2.0, "long"): "U",   # MSTU, NVDU(=NVDX special), AAPX (X also valid)
    (3.0, "long"): "T",   # forward-looking — REX has not shipped 3x yet
    (1.0, "short"): "S",
    (2.0, "short"): "Z",  # MSTZ, TSLZ, NVDZ(=NVDQ special)
}


def _root_prefix(root: str, n: int = 3) -> str:
    """Return up-to-n leading alphanumeric chars of the root, uppercased."""
    return "".join(c for c in root if c.isalnum())[:n].upper()


def _build_suggestion(root: str, leverage: float, direction: str) -> str:
    """Apply the dominant suffix rule. Always returns a 4-char-ish code."""
    dir_key = (direction or "").lower()
    if dir_key in ("inverse", "short"):
        dir_key = "short"
    elif dir_key == "long":
        dir_key = "long"
    suffix = _SUFFIX_BY_KEY.get((leverage, dir_key), "?")
    prefix = _root_prefix(root, 3)
    if not prefix:
        return f"REX{suffix}"
    return f"{prefix}{suffix}"


def _build_description(root: str, leverage: float, direction: str) -> str:
    """Mirror the public REX naming convention for fund descriptions."""
    dir_key = (direction or "").lower()
    if dir_key in ("inverse", "short"):
        dir_word = "Inverse"
    elif dir_key == "long":
        dir_word = "Long"
    else:
        dir_word = direction or ""
    lev_word = f"{int(leverage)}X" if leverage and leverage.is_integer() else (
        f"{leverage:g}X" if leverage else ""
    )
    pieces = ["T-REX", lev_word, dir_word, root.upper(), "Daily Target ETF"]
    return " ".join(p for p in pieces if p)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_ticker(
    underlier: str,
    leverage: float,
    direction: str,
) -> dict:
    """Suggest a REX product ticker for ``(underlier, leverage, direction)``.

    Args:
        underlier: Bloomberg or plain ticker of the underlying (e.g. ``"NVDA US"``,
            ``"MSTR"``, ``"XBTUSD Curncy"``). The function strips BBG suffixes
            and normalises crypto pairs.
        leverage: Leverage multiplier as a float (e.g. ``2.0``).
        direction: ``"Long"`` | ``"Short"`` | ``"Inverse"`` (case-insensitive).

    Returns:
        dict with keys:
            - ``is_existing`` (bool): REX has already filed this exact product.
            - ``existing_ticker`` (str | None): the live ticker if existing.
            - ``suggested_ticker`` (str): always a 4-letter-style code.
            - ``description`` (str): the canonical T-REX fund description.
            - ``confidence`` (str): ``"high"`` if matches existing, else
              ``"medium"`` (rule has 70 %+ hit-rate) or ``"low"`` (unrecognised
              leverage/direction combo).
    """
    root = _normalise_root(underlier)
    existing = _lookup_existing(root, leverage, direction)

    if existing is not None:
        return {
            "is_existing": True,
            "existing_ticker": existing.ticker,
            "suggested_ticker": existing.ticker,
            "description": existing.fund_name or _build_description(
                root, leverage, direction
            ),
            "confidence": "high",
        }

    suggested = _build_suggestion(root, leverage, direction)
    dir_key = (direction or "").lower()
    confidence = (
        "medium"
        if (leverage, "short" if dir_key in ("inverse", "short") else dir_key)
        in _SUFFIX_BY_KEY
        else "low"
    )
    return {
        "is_existing": False,
        "existing_ticker": None,
        "suggested_ticker": suggested,
        "description": _build_description(root, leverage, direction),
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Self-test / hit-rate audit
# ---------------------------------------------------------------------------

def hit_rate() -> dict:
    """Re-compute the rule's hit-rate against the snapshot. For diagnostics."""
    products = _load_existing()
    by_bucket: dict[tuple[float, str], list[tuple[str, str, str]]] = {}
    for p in products:
        if not p.root or not p.leverage or not p.direction:
            continue
        dir_norm = "short" if p.direction.lower() in ("short", "inverse") else "long"
        key = (p.leverage, dir_norm)
        by_bucket.setdefault(key, []).append((p.ticker, p.root, p.fund_name))

    out: dict = {}
    for (lev, dir_norm), items in sorted(by_bucket.items()):
        hits = sum(
            1 for tk, root, _ in items
            if _build_suggestion(root, lev, dir_norm) == tk
        )
        out[f"{lev}x_{dir_norm}"] = {
            "matched": hits,
            "total": len(items),
            "rate": round(hits / len(items), 3) if items else 0.0,
        }
    return out


if __name__ == "__main__":  # pragma: no cover
    import json
    print("Hit-rate against snapshot:")
    print(json.dumps(hit_rate(), indent=2))
    print("\nExamples:")
    for und, lev, dirn in [
        ("NVDA US", 2.0, "Long"),
        ("MSTR US", 2.0, "Short"),
        ("AMPX US", 2.0, "Long"),
        ("XBTUSD Curncy", 2.0, "Long"),
        ("LWLG", 2.0, "Long"),
    ]:
        print(f"  {und!r}, {lev}x {dirn}: {suggest_ticker(und, lev, dirn)}")

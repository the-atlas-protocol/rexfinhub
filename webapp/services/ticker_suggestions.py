"""Ticker suggestion service for rex_products rows without a ticker.

Used by the Pipeline Operations table (``/operations/pipeline``) to render
green / yellow / gray chips in the Ticker column of rows where the real
ticker has not been assigned yet.

Pipeline (per row):
    1. Derive a candidate ticker from product_suite + underlier + direction
       (via ``screener.li_engine.data.rex_naming.suggest_ticker`` for the
       T-REX leveraged convention, plus a small Growth & Income / IncomeMax
       / Premium Income suite map for the actively-managed lines).
    2. Cross-check the candidate against three sources of truth:
         a. ``reserved_symbols``   -> 'reserved'   (REX has claimed it)
         b. ``mkt_master_data``    -> 'taken'      (live in the market)
         c. ``cboe_symbols`` (avail=False) -> 'taken' (CBOE says occupied)
       Otherwise -> 'available'.
    3. Build a chip dict the template can render verbatim.

Status semantics (matches O5 spec):
    - 'reserved'  : green   -> link to /operations/reserved-symbols?q=<sym>
    - 'available' : yellow  -> link to /tools/tickers?q=<sym>
    - 'taken'     : gray    -> no link (already in use elsewhere)
    - 'unknown'   : gray    -> no link, no suggestion possible
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Suite suffix map for actively-managed REX lines (not covered by rex_naming
# which only handles leveraged 1x/2x/3x). Derived from the live REX universe.
#
#   IncomeMax        -> <root[:3]>I    (e.g. AAPI for AAPL IncomeMax)
#   Growth & Income  -> <root[:2]>II   (e.g. TSII for TSLA G&I, NVII for NVDA)
#   Premium Income   -> <root[:3]>Y    (e.g. AAPY style)
#   Crypto / Thematic / IncomeMax non-singlestock -> fall through to rex_naming
# ---------------------------------------------------------------------------

_ACTIVE_SUITE_SUFFIX = {
    "IncomeMax":       ("I", 3),
    "Growth & Income": ("II", 2),
    "Premium Income":  ("Y", 3),
}


# Direction parsing from fund name when the DB direction column is null.
_LONG_RE  = re.compile(r"\b(?:long|bull|bullish)\b", re.IGNORECASE)
_SHORT_RE = re.compile(r"\b(?:short|inverse|bear|bearish)\b", re.IGNORECASE)
_LEV_RE   = re.compile(r"\b(\d+(?:\.\d+)?)\s*[xX]\b")


def _parse_direction(name: str | None, fallback: str | None) -> str | None:
    if fallback:
        return fallback
    if not name:
        return None
    if _SHORT_RE.search(name):
        return "Short"
    if _LONG_RE.search(name):
        return "Long"
    return None


def _parse_leverage(name: str | None) -> float | None:
    if not name:
        return None
    m = _LEV_RE.search(name)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _root_prefix(underlier: str, n: int) -> str:
    """Strip BBG suffixes and return up-to-n leading alphanumerics."""
    if not underlier:
        return ""
    u = underlier.strip().upper()
    for suf in (" US", " UA", " LN", " UN", " CN", " UQ", " CURNCY", " EQUITY"):
        if u.endswith(suf):
            u = u[: -len(suf)].strip()
            break
    return "".join(c for c in u if c.isalnum())[:n]


def _suggest_for_active_suite(suite: str, underlier: str) -> str | None:
    cfg = _ACTIVE_SUITE_SUFFIX.get(suite)
    if not cfg or not underlier:
        return None
    suffix, prefix_len = cfg
    prefix = _root_prefix(underlier, prefix_len)
    if not prefix:
        return None
    return f"{prefix}{suffix}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class TickerSuggestion:
    suggested_ticker: str | None
    status: str          # 'reserved' | 'available' | 'taken' | 'unknown'
    link_href: str | None
    tooltip: str
    chip_class: str      # 'chip-reserved' | 'chip-available' | 'chip-taken' | 'chip-unknown'

    def as_dict(self) -> dict:
        return {
            "suggested_ticker": self.suggested_ticker,
            "status": self.status,
            "link_href": self.link_href,
            "tooltip": self.tooltip,
            "chip_class": self.chip_class,
        }


def _derive_candidate(rex_product) -> str | None:
    """Run the suite-aware suggestion chain and return a single candidate
    ticker (uppercased) or None."""
    suite = (rex_product.product_suite or "").strip()
    underlier = (rex_product.underlier or "").strip()
    name = rex_product.name or ""

    # Path A: actively-managed suite with a known suffix convention.
    if suite in _ACTIVE_SUITE_SUFFIX and underlier:
        cand = _suggest_for_active_suite(suite, underlier)
        if cand:
            return cand.upper()

    # Path B: T-REX leveraged line -> defer to rex_naming.
    if suite == "T-REX" and underlier:
        try:
            from screener.li_engine.data.rex_naming import suggest_ticker
        except Exception as exc:  # pragma: no cover - import guard
            log.debug("rex_naming import failed: %s", exc)
            return None
        leverage = _parse_leverage(name) or 2.0
        direction = _parse_direction(name, rex_product.direction) or "Long"
        try:
            out = suggest_ticker(underlier, leverage, direction)
            tk = (out or {}).get("suggested_ticker")
            if tk:
                return tk.upper()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("suggest_ticker failed for %r: %s", underlier, exc)
            return None

    # Path C: Crypto / Thematic / IncomeMax-non-singlestock — try rex_naming
    # only if we have a usable underlier + parseable direction.
    if underlier:
        leverage = _parse_leverage(name)
        direction = _parse_direction(name, rex_product.direction)
        if leverage and direction:
            try:
                from screener.li_engine.data.rex_naming import suggest_ticker
                out = suggest_ticker(underlier, leverage, direction)
                tk = (out or {}).get("suggested_ticker")
                if tk:
                    return tk.upper()
            except Exception as exc:  # pragma: no cover
                log.debug("rex_naming fallback failed: %s", exc)

    return None


def _lookup_status(db: Session, ticker: str) -> str:
    """Return 'reserved' | 'taken' | 'available' for a candidate ticker.

    Order of checks (first hit wins):
      1. reserved_symbols  (REX's own claim)
      2. mkt_master_data   (live in market)
      3. cboe_symbols.available is False (CBOE says taken by someone)
    """
    from webapp.models import ReservedSymbol, MktMasterData, CboeSymbol

    # 1) Reserved by REX.
    hit = (
        db.query(ReservedSymbol.id)
        .filter(ReservedSymbol.symbol == ticker)
        .first()
    )
    if hit is not None:
        return "reserved"

    # 2) Already trading.
    hit = (
        db.query(MktMasterData.id)
        .filter(MktMasterData.ticker == ticker)
        .first()
    )
    if hit is not None:
        return "taken"

    # 3) CBOE confirmed unavailable.
    cboe = (
        db.query(CboeSymbol.available)
        .filter(CboeSymbol.ticker == ticker)
        .first()
    )
    if cboe is not None and cboe[0] is False:
        return "taken"

    return "available"


def suggest_for_product(db: Session, rex_product) -> dict:
    """Return a TickerSuggestion dict for a rex_products row.

    The caller is responsible for ensuring the row's ticker is empty —
    this helper does NOT guard against that, so it can also be used in
    audit / "what would we have suggested" tooling.

    Returns a dict (not the dataclass) so Jinja can dot-access fields.
    """
    candidate = _derive_candidate(rex_product)

    if not candidate:
        return TickerSuggestion(
            suggested_ticker=None,
            status="unknown",
            link_href=None,
            tooltip="No suggestion — set underlier / direction on this row.",
            chip_class="chip-unknown",
        ).as_dict()

    try:
        status = _lookup_status(db, candidate)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("ticker status lookup failed for %s: %s", candidate, exc)
        return TickerSuggestion(
            suggested_ticker=candidate,
            status="unknown",
            link_href=None,
            tooltip=f"Suggested: {candidate} (lookup failed)",
            chip_class="chip-unknown",
        ).as_dict()

    if status == "reserved":
        return TickerSuggestion(
            suggested_ticker=candidate,
            status="reserved",
            link_href=f"/operations/reserved-symbols?q={candidate}",
            tooltip=f"Suggested: {candidate} (reserved)",
            chip_class="chip-reserved",
        ).as_dict()
    if status == "available":
        return TickerSuggestion(
            suggested_ticker=candidate,
            status="available",
            link_href=f"/tools/tickers?q={candidate}",
            tooltip=f"Suggested: {candidate} (available — click to reserve)",
            chip_class="chip-available",
        ).as_dict()
    # taken
    return TickerSuggestion(
        suggested_ticker=candidate,
        status="taken",
        link_href=None,
        tooltip=f"Suggested: {candidate} (already taken)",
        chip_class="chip-taken",
    ).as_dict()


def suggest_for_products(db: Session, products: Iterable) -> dict[int, dict]:
    """Batch helper — returns ``{rex_product.id: suggestion_dict}`` for the
    subset of rows where ticker is empty. Skips rows with a real ticker.

    Single DB session reused across all lookups; no N+1 commits.
    """
    out: dict[int, dict] = {}
    for p in products:
        if (p.ticker or "").strip():
            continue
        try:
            out[p.id] = suggest_for_product(db, p)
        except Exception as exc:  # pragma: no cover
            log.debug("suggest_for_product failed for id=%s: %s", p.id, exc)
            out[p.id] = TickerSuggestion(
                suggested_ticker=None,
                status="unknown",
                link_href=None,
                tooltip="No suggestion (service error)",
                chip_class="chip-unknown",
            ).as_dict()
    return out

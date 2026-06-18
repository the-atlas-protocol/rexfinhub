"""Self-healing resolution cascade — the single place the system answers
"what is this?" for any unresolved fact (category, brand, underlier, strategy).

Per the approved plan (Stage B): every fact that the deterministic engines cannot
place flows through ONE ordered escalation so the system resolves its own ambiguity
instead of waiting for Ryu to point it out:

    rung 1  deterministic rules        — the existing engines / rule CSVs
    rung 2  Bloomberg fund_description — read the issuer's own words (already in DB)
    rung 3  AI web search             — for names even the description can't place
    rung 4  human queue               — LAST resort; the only thing that reaches Ryu

Design goals:
  - Generic over fact *kind* (etp_category, issuer_brand, underlier_class, ...).
  - Graceful degradation: if anthropic / the API key is unavailable, the AI rungs
    return None and the cascade falls through — it never crashes the chain.
  - Idempotent + frugal: every decision is journaled to logs/ai_resolve_*.jsonl so a
    fact is resolved once and tokens / web-searches are spent only on genuinely-new
    items (mirrors the ai_classify_unmapped journal pattern).
  - Auditable: each Resolution records which rung answered and (for web) the source.

This module is intentionally dependency-light (stdlib only at import time) so it is
unit-testable without a DB, a Bloomberg file, or an API key. The AI rungs import
anthropic lazily.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_DIR = PROJECT_ROOT / "logs"

# Confidence levels, ordered. A rung may decline (return None) or answer with a
# confidence; LOW answers are journaled but routed to the human queue so a guess
# never silently ships.
CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"
_ACCEPT_CONF = {CONF_HIGH, CONF_MEDIUM}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FactRequest:
    """One unresolved fact about one fund."""
    kind: str                       # e.g. "etp_category" | "issuer_brand" | "underlier_class"
    ticker: str
    fund_name: str = ""
    fund_description: str = ""       # the Bloomberg `Des` column (rung 2 fuel)
    extra: dict = field(default_factory=dict)

    def journal_key(self) -> str:
        return f"{self.kind}:{self.ticker}"


@dataclass
class Resolution:
    """The answer a rung produced for a FactRequest."""
    kind: str
    ticker: str
    value: str
    confidence: str                  # HIGH | MEDIUM | LOW
    rung: int                        # 1=rules 2=description 3=web 4=queue
    source: str                      # human-readable provenance
    citation: str = ""               # URL / sheet / rule id, for audit
    reason: str = ""

    @property
    def accepted(self) -> bool:
        """True only when the answer is trustworthy enough to apply automatically."""
        return bool(self.value) and self.confidence in _ACCEPT_CONF


# A rung takes a FactRequest and returns a Resolution or None (declined).
Rung = Callable[[FactRequest], Optional[Resolution]]


# ---------------------------------------------------------------------------
# Journal — idempotency + audit
# ---------------------------------------------------------------------------

def _journal_path(d: Optional[date] = None) -> Path:
    d = d or date.today()
    return JOURNAL_DIR / f"ai_resolve_{d.isoformat()}.jsonl"


def already_resolved() -> set[str]:
    """journal_keys decided in any prior cascade run — skip them so a re-run only
    spends effort on genuinely-new facts (incl. ones queued for a human)."""
    seen: set[str] = set()
    if not JOURNAL_DIR.exists():
        return seen
    for j in JOURNAL_DIR.glob("ai_resolve_*.jsonl"):
        try:
            for line in j.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    if rec.get("journal_key"):
                        seen.add(rec["journal_key"])
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return seen


def _journal(req: FactRequest, res: Optional[Resolution]) -> None:
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "journal_key": req.journal_key(),
        "kind": req.kind,
        "ticker": req.ticker,
        "fund_name": req.fund_name,
        "resolution": asdict(res) if res else None,
        "queued": res is None or not res.accepted,
    }
    try:
        with _journal_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        log.warning("resolve journal append failed: %s", e)


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------

class Cascade:
    """Ordered rungs. resolve() returns the first accepted Resolution, journaling
    the outcome. A LOW / no answer falls through to the human queue (returns None)."""

    def __init__(self, rungs: list[Rung]):
        self.rungs = rungs

    def resolve(self, req: FactRequest) -> Optional[Resolution]:
        last_low: Optional[Resolution] = None
        for rung in self.rungs:
            try:
                res = rung(req)
            except Exception as e:  # a flaky rung must not crash the chain
                log.warning("resolve rung %s raised on %s: %s",
                            getattr(rung, "__name__", rung), req.journal_key(), e)
                res = None
            if res is None:
                continue
            if res.accepted:
                _journal(req, res)
                return res
            last_low = res  # remember a LOW answer for context, keep climbing
        # nobody could place it with confidence -> human queue
        _journal(req, last_low)
        return None

    def resolve_many(self, reqs: list[FactRequest],
                     skip_resolved: bool = True) -> tuple[list[Resolution], list[FactRequest]]:
        """Resolve a batch. Returns (accepted_resolutions, unresolved_requests).

        unresolved_requests is exactly the rung-4 human queue — the only thing that
        should ever be surfaced to Ryu (Stage D: silence by default)."""
        seen = already_resolved() if skip_resolved else set()
        accepted: list[Resolution] = []
        unresolved: list[FactRequest] = []
        for req in reqs:
            if skip_resolved and req.journal_key() in seen:
                continue
            res = self.resolve(req)
            if res is not None:
                accepted.append(res)
            else:
                unresolved.append(req)
        return accepted, unresolved


# ---------------------------------------------------------------------------
# Standard rungs
# ---------------------------------------------------------------------------

def make_rule_rung(lookup: Callable[[FactRequest], Optional[str]],
                   source: str = "rules") -> Rung:
    """Rung 1 — wrap a deterministic lookup (rule CSV / regex engine). The lookup
    returns a value string or None. A deterministic hit is always HIGH confidence."""
    def _rung(req: FactRequest) -> Optional[Resolution]:
        val = lookup(req)
        if not val:
            return None
        return Resolution(kind=req.kind, ticker=req.ticker, value=val,
                          confidence=CONF_HIGH, rung=1, source=source,
                          reason="deterministic rule match")
    _rung.__name__ = "rule_rung"
    return _rung


def make_description_rung(allowed_values: Optional[list[str]] = None,
                          model: str = "claude-sonnet-4-6") -> Rung:
    """Rung 2 — ask the model to place the fact using the Bloomberg fund_description
    (the issuer's own words). Declines (None) when there is no description or no API
    key, so the cascade degrades gracefully."""
    def _rung(req: FactRequest) -> Optional[Resolution]:
        if not (req.fund_description or "").strip():
            return None
        ans = _ask_model(req, model=model, allowed_values=allowed_values,
                         use_web=False)
        if ans is None:
            return None
        value, conf, reason = ans
        if not value:
            return None
        return Resolution(kind=req.kind, ticker=req.ticker, value=value,
                          confidence=conf, rung=2, source="bloomberg_description",
                          reason=reason)
    _rung.__name__ = "description_rung"
    return _rung


def make_web_search_rung(allowed_values: Optional[list[str]] = None,
                         model: str = "claude-sonnet-4-6") -> Rung:
    """Rung 3 — let the model search the web for names even the description can't
    place. Records the citation for audit. Declines when web search is unavailable."""
    def _rung(req: FactRequest) -> Optional[Resolution]:
        ans = _ask_model(req, model=model, allowed_values=allowed_values,
                         use_web=True)
        if ans is None:
            return None
        value, conf, reason = ans
        if not value:
            return None
        return Resolution(kind=req.kind, ticker=req.ticker, value=value,
                          confidence=conf, rung=3, source="ai_web_search",
                          citation=req.extra.get("_last_citation", ""), reason=reason)
    _rung.__name__ = "web_search_rung"
    return _rung


# ---------------------------------------------------------------------------
# Shared model call (rungs 2 & 3) — lazy import, degrades to None
# ---------------------------------------------------------------------------

def _ask_model(req: FactRequest, model: str,
               allowed_values: Optional[list[str]],
               use_web: bool) -> Optional[tuple[str, str, str]]:
    """Return (value, confidence, reason) or None if the model can't be reached.

    Thin wrapper over webapp.services.claude_service so all API plumbing /
    key-loading / error handling lives in one place. Returns None (declines) on any
    failure so a rung never crashes the cascade."""
    try:
        from webapp.services import claude_service
    except Exception:
        return None
    if not claude_service.is_configured():
        return None
    try:
        return claude_service.resolve_fact(
            kind=req.kind,
            ticker=req.ticker,
            fund_name=req.fund_name,
            fund_description=req.fund_description,
            allowed_values=allowed_values,
            use_web=use_web,
            model=model,
            citation_sink=req.extra,
        )
    except Exception as e:
        log.warning("claude_service.resolve_fact failed (%s): %s",
                    "web" if use_web else "desc", e)
        return None


def standard_cascade(rule_lookup: Callable[[FactRequest], Optional[str]],
                     allowed_values: Optional[list[str]] = None,
                     enable_web: bool = True) -> Cascade:
    """The default rules -> description -> web cascade. rung 4 (human queue) is the
    implicit fall-through (resolve() returns None)."""
    rungs: list[Rung] = [make_rule_rung(rule_lookup)]
    rungs.append(make_description_rung(allowed_values=allowed_values))
    if enable_web:
        rungs.append(make_web_search_rung(allowed_values=allowed_values))
    return Cascade(rungs)

"""Track 6 (ADR 0010) — edgartools-backed SEC client, SHADOW mode.

Wraps the `edgartools` library as an alternative to the in-house SEC
extraction stack (step3 / single_filing / bulk_loader / sec_client). Built to
run ALONGSIDE the legacy extractor: the legacy remains 100% authoritative;
this module is exercised ONLY by scripts/edgar_shadow_compare.py, which diffs
edgartools' output against the in-house data so the field-level gap can be
measured before any cutover.

Per Ryu's directive D1 — build everything; remove nothing until it is
absolutely proven to have no use. The shadow comparison is that proof
mechanism. This module is NOT wired into the live pipeline; importing it has
no side effects, and every public call is defensive — it returns empty/marked
results rather than raising, so a shadow run can never disturb production.

ADR 0010's cutover decision rests on the dual-run evidence this enables.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

_IDENTITY_SET = False


def _ensure_identity() -> None:
    """Set the SEC user-agent edgartools requires (idempotent)."""
    global _IDENTITY_SET
    if _IDENTITY_SET:
        return
    from edgar import set_identity
    ua = os.environ.get("SEC_USER_AGENT") or "REX FinHub rexfinhub@rexfin.com"
    set_identity(ua)
    _IDENTITY_SET = True


@dataclass
class EdgarFilingView:
    """edgartools' view of one filing — the fields compared to the in-house data."""
    accession: str | None = None
    form: str | None = None
    filing_date: str | None = None
    cik: str | None = None
    company: str | None = None
    ok: bool = False
    error: str | None = None


def edgartools_available() -> bool:
    """True if the edgartools library is importable in this environment."""
    try:
        import edgar  # noqa: F401
        return True
    except Exception:
        return False


def fetch_recent_filings(forms: list[str], since: str, until: str | None = None
                         ) -> list[EdgarFilingView]:
    """edgartools' view of all `forms` filings in [since, until] (YYYY-MM-DD).

    Read-only. Returns [] on any failure (logged) — shadow mode never raises.
    Note: edgartools returns the FULL SEC universe for a form, not a curated
    subset; the caller intersects against the in-house set by accession.
    """
    try:
        _ensure_identity()
        from edgar import get_filings
    except ImportError:
        log.warning("edgartools not installed — shadow client unavailable")
        return []
    except Exception as e:  # pragma: no cover - defensive
        log.warning("edgar identity setup failed: %s", e)
        return []

    date_arg = f"{since}:{until}" if until else f"{since}:"
    out: list[EdgarFilingView] = []
    for form in forms:
        try:
            fs = get_filings(form=form, filing_date=date_arg)
        except Exception as e:
            log.warning("edgar get_filings(form=%s, filing_date=%s) failed: %s",
                        form, date_arg, e)
            continue
        try:
            iterator = list(fs) if fs is not None else []
        except Exception as e:
            log.warning("edgar filings iteration failed for %s: %s", form, e)
            continue
        for f in iterator:
            try:
                acc = (getattr(f, "accession_no", None)
                       or getattr(f, "accession_number", None) or "")
                out.append(EdgarFilingView(
                    accession=str(acc),
                    form=str(getattr(f, "form", None) or form),
                    filing_date=str(getattr(f, "filing_date", None) or ""),
                    cik=str(getattr(f, "cik", None) or ""),
                    company=str(getattr(f, "company", None)
                               or getattr(f, "company_name", None) or ""),
                    ok=True,
                ))
            except Exception as e:  # pragma: no cover - defensive
                out.append(EdgarFilingView(ok=False, error=str(e)))
    return out

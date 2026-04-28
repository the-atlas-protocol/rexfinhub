"""Single source of truth for the email report catalog.

Refactored 2026-04-28 from `webapp/routers/admin_reports.py:REPORT_CATALOG`
(an opaque dict) into a typed dataclass registry. The legacy dict is now a
derived view (`as_legacy_dict()`) so existing imports keep working without
edits.

Why: tonight's session showed that adding a new report (or changing one)
required touching `send_email.py` (do_X function), `send_all.py` (REPORTS
dict), `admin_reports.py` (REPORT_CATALOG), `email_recipients` table, and
sometimes `webapp/routers/admin.py` send-button. With the registry, adding
a report is one entry here — preflight, send_all, dashboard, and admin
endpoints all read from this list.

Future (Phase 2 of plan task #6): wire `Report.builder` to make the registry
*executable* — `report.send(db)` instead of dispatching by key in send_all.py.
For now keep registry as metadata only; send_all.py keeps its REPORTS dict
mapping key -> builder. Migration path is straightforward when ready.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class Report:
    """One row of the report catalog.

    Fields:
        key:         stable identifier used in URLs, prebaked file names,
                     send_all bundle keys, and `email_recipients.list_type`.
        name:        human-readable title shown in admin UI.
        description: one-line summary of what the report contains.
        cadence:     "Daily" | "Weekly" | "Monday" | "On-demand" (display only).
        list_type:   key into `email_recipients.list_type` for recipient lookup.
                     If None, report is not yet wired to a DB recipient list.
        enabled:     False = report is built but not sent (WIP / staging).
        critical:    True = a failure aborts the rest of a batch send.
        bundle:      Which `send_all` bundle this report belongs to.
                     "daily" or "weekly" or "autocall" or "stock_recs" or "monday".
    """
    key: str
    name: str
    description: str
    cadence: str
    list_type: str | None
    enabled: bool = True
    critical: bool = False
    bundle: str = "weekly"


# ---------------------------------------------------------------------------
# Canonical registry — every report the system knows about.
# Order matters: matches the natural send-day sequence (daily -> weekly bundle
# -> autocall -> stock_recs).
# ---------------------------------------------------------------------------

REGISTRY: tuple[Report, ...] = (
    Report(
        key="daily_filing",
        name="Daily Filing Report",
        description="Daily SEC filings digest with market snapshot",
        cadence="Daily",
        list_type="daily",
        enabled=True,
        critical=True,
        bundle="daily",
    ),
    Report(
        key="weekly_report",
        name="Weekly ETP Report",
        description="Weekly roll-up of filings, market activity, REX performance",
        cadence="Weekly",
        list_type="weekly",
        enabled=True,
        bundle="weekly",
    ),
    Report(
        key="li_report",
        name="Leverage & Inverse Report",
        description="L&I market landscape — Index and Single Stock segments",
        cadence="Weekly",
        list_type="li",
        enabled=True,
        bundle="weekly",
    ),
    Report(
        key="income_report",
        name="Income Report",
        description="Covered-call and income ETF landscape",
        cadence="Weekly",
        list_type="income",
        enabled=True,
        bundle="weekly",
    ),
    Report(
        key="flow_report",
        name="Flow Report",
        description="Fund flows by category and direction",
        cadence="Weekly",
        list_type="flow",
        enabled=True,
        bundle="weekly",
    ),
    Report(
        key="autocall_report",
        name="Autocallable Report",
        description="Autocallable ETF weekly update — RBC + CAIS distribution",
        cadence="Weekly",
        list_type="autocall",
        enabled=True,
        bundle="autocall",
    ),
    Report(
        key="stock_recs",
        name="Stock Recommendations of the Week",
        description="L&I recommender — top filing + launch picks, IPO filer race, money flow",
        cadence="Weekly",
        list_type="stock_recs",
        enabled=True,
        bundle="stock_recs",
    ),
    Report(
        key="intelligence_brief",
        name="Filing Intelligence Brief",
        description="Executive-first daily — action required, competitive races, effectives",
        cadence="Daily",
        list_type="intelligence",
        enabled=False,  # WIP — preview only
        bundle="daily",
    ),
    Report(
        key="filing_screener",
        name="Filing Candidates",
        description="Top 5 filing picks from foundation_scorer",
        cadence="Weekly",
        list_type="screener",
        enabled=False,  # WIP — preview only
        bundle="weekly",
    ),
    Report(
        key="product_status",
        name="Product Pipeline",
        description="REX product lifecycle: Listed / Awaiting / Filed / Research",
        cadence="Monday",
        list_type="pipeline",
        enabled=False,  # WIP — disabled until copy approved
        bundle="monday",
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_all() -> tuple[Report, ...]:
    """Return every report in declaration order."""
    return REGISTRY


def get_active() -> tuple[Report, ...]:
    """Return enabled reports only."""
    return tuple(r for r in REGISTRY if r.enabled)


def get_by_key(key: str) -> Report | None:
    """Lookup a report by key. None if unknown."""
    for r in REGISTRY:
        if r.key == key:
            return r
    return None


def get_by_bundle(bundle: str) -> tuple[Report, ...]:
    """Reports in a named bundle (`daily`, `weekly`, `autocall`, `stock_recs`, `monday`)."""
    return tuple(r for r in REGISTRY if r.enabled and r.bundle == bundle)


def keys() -> tuple[str, ...]:
    """All registered keys."""
    return tuple(r.key for r in REGISTRY)


# ---------------------------------------------------------------------------
# Backwards-compat: `admin_reports.REPORT_CATALOG` style dict
# ---------------------------------------------------------------------------

def as_legacy_dict() -> dict[str, dict]:
    """Mirror the pre-refactor `REPORT_CATALOG` dict shape so existing
    callers (admin_reports.py preview_landing, preview_raw) keep working.

    Returns:
        {report_key: {"name", "description", "cadence", "list_type"}, ...}
    """
    return {
        r.key: {
            "name": r.name,
            "description": r.description,
            "cadence": r.cadence,
            "list_type": r.list_type or "",
        }
        for r in REGISTRY
    }

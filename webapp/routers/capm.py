"""Capital Markets product list routes.

Public (site-auth) page showing all CapM products in a tabbed, sortable table.
Admin users can edit individual product records inline.

Phase 1 of the v3 URL migration: the handler implementations have been
renamed to ``_*_impl`` and are imported by ``webapp.routers.operations``
to be mounted under ``/operations/products``. The old ``/capm/*`` routes
shrink to 301/307 redirects pointing at the new canonical URLs.

Legacy URL → new canonical URL:
    GET  /capm/                     → /operations/products
    GET  /capm/export.csv           → /operations/products/export.csv
    POST /capm/update/{product_id}  → /operations/products/update/{product_id}
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/capm", tags=["capm"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

VALID_SUITES = ["T-REX", "REX", "REX-OSPREY", "BMO"]


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_overrides(raw: str | None) -> list[str]:
    """Parse the manually_edited_fields JSON list (defensive)."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []


def _audit_log(
    db: Session,
    *,
    action: str,
    table_name: str,
    row_id: int | None,
    field_name: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    row_label: str | None = None,
    changed_by: str | None = None,
) -> None:
    """Insert a row into capm_audit_log. Caller is responsible for db.commit().

    Defensive: never raises — audit failure must not break the user write.
    """
    try:
        from webapp.models import CapMAuditLog
        entry = CapMAuditLog(
            action=action,
            table_name=table_name,
            row_id=row_id,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            row_label=row_label,
            changed_by=changed_by or "admin",
        )
        db.add(entry)
    except Exception as e:
        log.warning("Audit log insert failed (non-fatal): %s", e)


def _names_overlap(a: str | None, b: str | None) -> bool:
    """True if two fund names share a meaningful token.

    Used when one ticker maps to one capm row but multiple rex rows
    (recycled placeholder tickers during pre-launch). Only the rex row
    whose name actually matches the capm row should inherit the curated
    fields.

    Heuristic: split on whitespace, drop boilerplate ("REX","T-REX","2X",
    "DAILY","TARGET","ETF","ETN","STRATEGY","INCOMEMAX","LONG","INVERSE",
    "SHORT"), then check intersection. If either name is missing we
    optimistically attach (fail-open — ticker match is strong on its own
    when there's no name conflict).
    """
    if not a or not b:
        return True
    boilerplate = {
        "REX", "T-REX", "REX-OSPREY", "OSPREY", "2X", "3X", "DAILY",
        "TARGET", "ETF", "ETN", "STRATEGY", "INCOMEMAX", "LONG", "INVERSE",
        "SHORT", "PREMIUM", "INCOME", "GROWTH", "AND", "&", "THE", "OF",
        "MICROSECTORS", "FUND", "TRUST",
    }
    def _toks(s: str) -> set[str]:
        return {t.upper().strip(",.()-") for t in s.split() if t} - boilerplate
    return bool(_toks(a) & _toks(b))


def _classify_rex_suite(name: str | None) -> str:
    """Classify a rex_products name into the REX suite tab buckets.

    Used when a row only exists in rex_products (not capm_products) so the
    suite filter still works. Mirrors the visible naming convention:
      - "T-REX 2X ..." → T-REX
      - "REX-Osprey ..." → REX-OSPREY
      - "REX ..." (income, premium, crypto, autocallable, etc.) → REX
      - "MicroSectors ..." → REX (legacy ETN family)
    """
    upper = (name or "").upper()
    if "OSPREY" in upper:
        return "REX-OSPREY"
    if upper.startswith("T-REX"):
        return "T-REX"
    if upper.startswith("REX") or "MICROSECTORS" in upper:
        return "REX"
    return "REX"  # default — these are all REX-branded by virtue of _rex_only_filter


# Status sort priority for the unified table — Listed first (operational
# focus), then awaiting/filed (in-flight), then research/delisted at bottom.
_UNIFIED_STATUS_PRIORITY = {
    "Listed": 0,
    "Awaiting Effective": 1,
    "Filed": 2,
    "Filed (485A)": 3,
    "Pending": 4,
    "Research": 5,
    "Target List": 6,
    "Delisted": 9,
}


def _capm_index_impl(
    request: Request,
    suite: str | None = None,
    q: str | None = None,
    tab: str | None = None,
    db: Session = Depends(get_db),
):
    """REX product list page. Mounted at /operations/products in PR 1.

    Unified view: shows EVERY REX-branded product, not just the 74 with
    curated CapM data. Sources merged in priority order:

      1. rex_products (REX-branded only via _rex_only_filter) — broad base
         covering the full filing pipeline (~552 rows)
      2. capm_products (74 curated rows) — joined on ticker, contributes
         fees, custodian, LMM, AP-relevant fields. ONLY these rows are
         editable inline (capm_products is the curated maintenance table).
      3. mkt_master_data (96 with is_rex=1) — joined on ticker_clean,
         contributes live AUM, fund_type (ETF/ETN), market_status.
      4. capm_products with no rex_products match — appended at the end so
         no curated product disappears even if it's not REX-branded
         (e.g. BMO suite, legacy entries).

    For tickerless rex_products rows, the row is keyed by `(name|trust)`
    so each survives independently. ~414 of 552 REX rows lack tickers —
    they are pre-launch filings still resolving Series/Class IDs.
    """
    from webapp.models import (
        CapMProduct,
        CapMTrustAP,
        CapMAuditLog,
        RexProduct,
        MktMasterData,
    )
    # Imported lazily to avoid module-level circular import — pipeline_calendar
    # imports from capm in some downstream code paths.
    from webapp.routers.pipeline_calendar import _rex_only_filter

    active_tab = "trust_aps" if tab == "trust_aps" else "products"

    # ------------------------------------------------------------------
    # Pull all three sources in full. Filtering happens in Python after
    # the merge so suite/q apply to the unified set, not just one side.
    # ------------------------------------------------------------------
    rex_rows = _rex_only_filter(db.query(RexProduct)).all()
    capm_rows = db.query(CapMProduct).all()

    # Index capm by uppercase ticker — every capm row has a ticker today
    capm_by_ticker: dict[str, CapMProduct] = {}
    for c in capm_rows:
        if c.ticker:
            capm_by_ticker[c.ticker.upper().strip()] = c

    # mkt_master_data: keyed by ticker_clean (e.g. "AAPX" not "AAPX US")
    mkt_by_ticker: dict[str, MktMasterData] = {}
    rex_tickers_upper = {(r.ticker or "").upper().strip() for r in rex_rows if r.ticker}
    capm_tickers_upper = set(capm_by_ticker.keys())
    needed_mkt_tickers = rex_tickers_upper | capm_tickers_upper
    if needed_mkt_tickers:
        mkt_rows = (
            db.query(MktMasterData)
            .filter(or_(
                func.upper(MktMasterData.ticker_clean).in_(needed_mkt_tickers),
                func.upper(MktMasterData.ticker).in_(needed_mkt_tickers),
            ))
            .all()
        )
        for m in mkt_rows:
            key = (m.ticker_clean or m.ticker or "").upper().strip()
            # mkt_master_data.ticker is "AAPX US" — strip " US" suffix
            if key.endswith(" US"):
                key = key[:-3].strip()
            if key and key not in mkt_by_ticker:
                mkt_by_ticker[key] = m

    # ------------------------------------------------------------------
    # Merge strategy: each rex_products row is its OWN entry — even when
    # multiple share a ticker. This is intentional: during "Awaiting
    # Effective" the SEC ticker reservation can be reassigned multiple
    # times across distinct fund names (e.g. APHU is currently both the
    # listed APH 2X fund AND the placeholder for ~10 awaiting funds). All
    # of them are real products in flight; collapsing would hide pipeline.
    #
    # capm_products is joined on UPPER(ticker) — at most one capm row per
    # ticker (capm has a unique-ish editorial dataset). The same capm row
    # may attach to multiple rex rows sharing that ticker, but only the
    # one matching by name (when possible) gets the editable affordance.
    # ------------------------------------------------------------------
    unified: list[SimpleNamespace] = []
    seen_capm_ids: set[int] = set()
    overrides_count = 0

    # Walk every rex row directly — preserves all 552 entries.
    for r in rex_rows:
        ticker = (r.ticker or "").upper().strip() or None
        capm = capm_by_ticker.get(ticker) if ticker else None
        # Attach capm only to the rex row whose name is a reasonable match.
        # If the capm row's fund_name shares the underlying name token with
        # this rex row, attach. Otherwise the capm row stays available for
        # the rex row that matches better (or falls through unattached).
        if capm and not _names_overlap(capm.fund_name, r.name):
            capm = None
        if capm:
            seen_capm_ids.add(capm.id)
        mkt = mkt_by_ticker.get(ticker) if ticker else None
        row = _build_unified_row(r, capm, mkt, ticker=ticker)
        if row.edited_fields:
            overrides_count += 1
        unified.append(row)

    # capm rows whose ticker was never claimed by a rex row OR whose name
    # didn't overlap any rex row sharing the ticker — still surface them
    # so curated CapM data is never dropped.
    for c in capm_rows:
        if c.id in seen_capm_ids:
            continue
        ticker = (c.ticker or "").upper().strip() or None
        mkt = mkt_by_ticker.get(ticker) if ticker else None
        row = _build_unified_row(None, c, mkt, ticker=ticker)
        if row.edited_fields:
            overrides_count += 1
        unified.append(row)

    # ------------------------------------------------------------------
    # Stats from the FULL unified set BEFORE filtering — KPIs always show
    # the true totals so the user knows the universe size, not just what
    # passed the current filter.
    # ------------------------------------------------------------------
    total = len(unified)
    suite_counts: dict[str, int] = {}
    for u in unified:
        if u.suite_source:
            suite_counts[u.suite_source] = suite_counts.get(u.suite_source, 0) + 1

    avg_fees: dict[str, int | None] = {}
    for s in VALID_SUITES:
        nums: list[float] = []
        for u in unified:
            if u.suite_source != s or not u.fixed_fee:
                continue
            try:
                nums.append(float(str(u.fixed_fee).replace(",", "").replace("$", "")))
            except (ValueError, TypeError):
                pass
        avg_fees[s] = round(sum(nums) / len(nums)) if nums else None

    # ------------------------------------------------------------------
    # LIVE-ONLY default (per Ryu 2026-05-11): /operations/products is the
    # live REX product registry, not a pipeline view. Filter to Listed
    # funds only. Pipeline/pre-effective filings live on /operations/pipeline.
    # ?include_all=1 query param opens the full set for admin review.
    # ------------------------------------------------------------------
    include_all = bool(request.query_params.get("include_all"))
    if not include_all:
        unified = [u for u in unified if (u.status_display or "").lower() == "listed"]

    # ------------------------------------------------------------------
    # Filter (suite + free-text) AFTER stats so KPIs stay stable and users
    # see consistent header counts regardless of active filter.
    # ------------------------------------------------------------------
    if suite and suite in VALID_SUITES:
        unified = [u for u in unified if u.suite_source == suite]

    if q:
        ql = q.lower()
        def _hit(u: SimpleNamespace) -> bool:
            for field in ("ticker", "fund_name", "trust", "lmm", "custodian", "underlying_name", "underlying_ticker"):
                v = getattr(u, field, None)
                if v and ql in str(v).lower():
                    return True
            return False
        unified = [u for u in unified if _hit(u)]

    # Sort: Listed → in-flight → research → delisted, then by ticker (then name)
    unified.sort(key=lambda u: (
        _UNIFIED_STATUS_PRIORITY.get(u.status_display, 7),
        (u.ticker or ""),
        (u.fund_name or ""),
    ))

    # Trust & APs — always loaded so the tab is instantly available
    trust_aps = (
        db.query(CapMTrustAP)
        .order_by(
            CapMTrustAP.trust_name.asc(),
            CapMTrustAP.sort_order.asc().nulls_last(),
            CapMTrustAP.ap_name.asc(),
        )
        .all()
    )

    is_admin = request.session.get("is_admin", False)
    trust_count = len({r.trust_name for r in trust_aps})

    # Recent activity log — last 20 admin actions (most recent first).
    audit_entries = (
        db.query(CapMAuditLog)
        .order_by(CapMAuditLog.changed_at.desc())
        .limit(20)
        .all()
    )

    return templates.TemplateResponse("capm.html", {
        "request": request,
        "products": unified,
        "total": total,
        "filtered_count": len(unified),
        "suite_counts": suite_counts,
        "avg_fees": avg_fees,
        "valid_suites": VALID_SUITES,
        "filter_suite": suite or "",
        "filter_q": q or "",
        "is_admin": is_admin,
        "trust_aps": trust_aps,
        "trust_count": trust_count,
        "active_tab": active_tab,
        "audit_entries": audit_entries,
        "overrides_count": overrides_count,
    })


def _build_unified_row(
    rex,           # RexProduct | None
    capm,          # CapMProduct | None
    mkt,           # MktMasterData | None
    ticker: str | None,
) -> SimpleNamespace:
    """Combine up to three source rows into a single template-ready view object.

    Field-resolution priority (best operational truth wins):
      ticker          — explicit param (already upper-stripped)
      fund_name       — capm.fund_name → rex.name → mkt.fund_name
      bb_ticker       — capm.bb_ticker only (CapM-curated)
      inception_date  — capm.inception_date → rex.official_listed_date
      trust           — capm.trust → rex.trust
      issuer          — capm.issuer (CapM-curated only)
      exchange        — capm.exchange → rex.exchange
      cu_size         — capm.cu_size → rex.cu_size (str-coerced)
      fixed_fee       — capm.fixed_fee (CapM-curated)
      variable_fee    — capm.variable_fee (CapM-curated)
      cut_off         — capm.cut_off (CapM-curated)
      custodian       — capm.custodian (CapM-curated)
      lmm             — capm.lmm → rex.lmm
      suite_source    — capm.suite_source → derived from rex.name
      product_type    — capm.product_type → mkt.fund_type
      status_display  — rex.status → mkt.market_status (mapped) → from inception
      prospectus      — rex.latest_prospectus_link → capm.prospectus_link
      editable_capm_id — capm.id (None if no curated record — disables inline edit)
    """
    # editable_capm_id drives the JS inline-edit affordance. Only capm_products
    # rows are editable today since rex_products / mkt_master_data are
    # pipeline-driven and would be silently overwritten on next sync.
    editable_capm_id = capm.id if capm else None
    # Use the capm.id when present so the existing update endpoint URL works.
    # For non-editable rows we still need a unique row id for the DOM —
    # negative ids signal "not editable" without colliding with real rows.
    if capm:
        dom_id = capm.id
    elif rex:
        dom_id = -rex.id  # negative => non-editable, but unique
    else:
        dom_id = 0

    fund_name = (
        (capm.fund_name if capm else None)
        or (rex.name if rex else None)
        or (mkt.fund_name if mkt else None)
        or "—"
    )

    inception_date = (
        (capm.inception_date if capm else None)
        or (rex.official_listed_date if rex else None)
    )

    # Status — REX pipeline status is most accurate for in-flight; mkt
    # gives us live exchange status for trading funds.
    if rex and rex.status:
        status_display = rex.status
    elif mkt and mkt.market_status:
        status_display = {
            "ACTV": "Listed", "PEND": "Pending", "LIQU": "Delisted",
        }.get(mkt.market_status, mkt.market_status)
    elif capm and capm.inception_date:
        status_display = "Listed"
    else:
        status_display = "—"

    # Fund type — legal structure ONLY (ETF / ETN). Bloomberg mkt.fund_type
    # is the canonical source. capm.product_type contains the suite name
    # (T-REX / Premium Income / etc.) which is wrong for this column; never
    # use it as fund_type. Fall back to "ETF" only if rex.product_suite
    # equals "MicroSectors ETN" (the only REX ETN family), else default ETF.
    if mkt and mkt.fund_type:
        fund_type_display = mkt.fund_type
    elif rex and rex.product_suite == "MicroSectors ETN":
        fund_type_display = "ETN"
    else:
        fund_type_display = "ETF"

    # Prospectus — prefer the live SEC link from the pipeline, fall back
    # to the legacy xlsx-imported link.
    prospectus_display = (
        (rex.latest_prospectus_link if rex and rex.latest_prospectus_link else None)
        or (capm.prospectus_link if capm else None)
    )
    prospectus_source = "live" if (rex and rex.latest_prospectus_link) else (
        "imported" if (capm and capm.prospectus_link) else None
    )

    # Suite — capm's curated suite_source if present, else derive from name
    suite_source = (
        (capm.suite_source if capm else None)
        or _classify_rex_suite(fund_name)
    )

    # Edited fields badge only meaningful for capm rows
    edited_fields = _parse_overrides(capm.manually_edited_fields) if capm else []

    return SimpleNamespace(
        id=dom_id,
        editable_capm_id=editable_capm_id,
        ticker=ticker or "",
        fund_name=fund_name,
        bb_ticker=(capm.bb_ticker if capm else None),
        inception_date=inception_date,
        trust=(capm.trust if capm and capm.trust else (rex.trust if rex else None)),
        issuer=(capm.issuer if capm else None),
        exchange=(capm.exchange if capm and capm.exchange else (rex.exchange if rex else None)),
        cu_size=(capm.cu_size if capm and capm.cu_size else (str(rex.cu_size) if rex and rex.cu_size else None)),
        fixed_fee=(capm.fixed_fee if capm else None),
        variable_fee=(capm.variable_fee if capm else None),
        cut_off=(capm.cut_off if capm else None),
        custodian=(capm.custodian if capm else None),
        lmm=(capm.lmm if capm and capm.lmm else (rex.lmm if rex else None)),
        suite_source=suite_source,
        product_type=fund_type_display if fund_type_display != "—" else None,
        category=(capm.category if capm else None),
        status_display=status_display,
        fund_type_display=fund_type_display,
        prospectus_display=prospectus_display,
        prospectus_source=prospectus_source,
        # Legacy field — template reads these
        prospectus_link=(capm.prospectus_link if capm else None),
        edited_fields=edited_fields,
    )


def _capm_export_impl(
    request: Request,
    suite: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """Export filtered product list as CSV. Mounted at /operations/products/export.csv in PR 1."""
    from webapp.models import CapMProduct

    query = db.query(CapMProduct)

    if suite and suite in VALID_SUITES:
        query = query.filter(CapMProduct.suite_source == suite)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            CapMProduct.fund_name.ilike(like),
            CapMProduct.ticker.ilike(like),
            CapMProduct.underlying_name.ilike(like),
            CapMProduct.underlying_ticker.ilike(like),
            CapMProduct.lmm.ilike(like),
            CapMProduct.custodian.ilike(like),
        ))

    products = query.order_by(CapMProduct.suite_source, CapMProduct.ticker).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Ticker", "Fund Name", "Suite", "BB Ticker", "Inception Date",
        "Trust", "Issuer", "Exchange", "CU Size", "Fixed Fee", "Variable Fee",
        "Cut Off", "Custodian", "LMM", "Category", "Direction", "Leverage",
        "Underlying Ticker", "Underlying Name", "Expense Ratio",
        "Competitor Products", "BMO Suite", "Prospectus",
    ])
    for p in products:
        writer.writerow([
            p.ticker or "",
            p.fund_name or "",
            p.suite_source or "",
            p.bb_ticker or "",
            p.inception_date.isoformat() if p.inception_date else "",
            p.trust or "",
            p.issuer or "",
            p.exchange or "",
            p.cu_size or "",
            p.fixed_fee or "",
            p.variable_fee or "",
            p.cut_off or "",
            p.custodian or "",
            p.lmm or "",
            p.category or "",
            p.direction or "",
            p.leverage or "",
            p.underlying_ticker or "",
            p.underlying_name or "",
            f"{p.expense_ratio:.4f}" if p.expense_ratio is not None else "",
            p.competitor_products or "",
            p.bmo_suite or "",
            p.prospectus_link or "",
        ])

    output.seek(0)
    filename = f"capm_products_{date.today().isoformat()}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# Whitelist of fields that the products-update endpoint will write.
# Maps form field name -> (CapMProduct attribute, type_coercer).
# Anything not in this map is silently ignored — keeps injection attack
# surface tight.
_CAPM_UPDATE_FIELDS = {
    "fund_name":         ("fund_name",         "str_required"),
    "ticker":            ("ticker",            "str_or_none"),
    "bb_ticker":         ("bb_ticker",         "str_or_none"),
    "suite_source":      ("suite_source",      "suite_or_none"),
    "exchange":          ("exchange",          "str_or_none"),
    "cu_size":           ("cu_size",           "str_or_none"),
    "fixed_fee":         ("fixed_fee",         "str_or_none"),
    "variable_fee":      ("variable_fee",      "str_or_none"),
    "cut_off":           ("cut_off",           "str_or_none"),
    "custodian":         ("custodian",         "str_or_none"),
    "lmm":               ("lmm",               "str_or_none"),
    "direction":         ("direction",         "str_or_none"),
    "leverage":          ("leverage",          "str_or_none"),
    "underlying_ticker": ("underlying_ticker", "str_or_none"),
    "underlying_name":   ("underlying_name",   "str_or_none"),
    "inception_date":    ("inception_date",    "date"),
    "notes":             ("notes",             "str_or_none"),
    "product_type":      ("product_type",      "str_or_none"),
    "category":          ("category",          "str_or_none"),
}


def _coerce_capm(coerce_type: str, raw: str):
    """Coerce a raw form string into a CapMProduct attribute value."""
    s = (raw or "").strip()
    if coerce_type == "str_required":
        if not s:
            raise HTTPException(400, "Value cannot be empty")
        return s
    if coerce_type == "str_or_none":
        return s or None
    if coerce_type == "suite_or_none":
        if not s:
            return None
        if s not in VALID_SUITES:
            raise HTTPException(400, f"Invalid suite. Valid: {VALID_SUITES}")
        return s
    if coerce_type == "date":
        return _parse_date(s)
    raise HTTPException(500, f"Unknown coercer: {coerce_type}")


def _stringify(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


async def _capm_update_impl(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Admin-only: update a CapM product record.

    Mounted at /operations/products/update/{product_id} in PR 1.

    Accepts partial updates — only fields that appear in the submitted form
    are modified. This supports inline cell-by-cell editing on the
    /operations/products page while remaining compatible with full-form
    submissions.

    Side effects:
    - Records every changed field to capm_audit_log.
    - Adds the changed field name to manually_edited_fields so the daily
      auto-import skips it (override-block behavior).
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from webapp.models import CapMProduct

    form = await request.form()
    submitted = {k: v for k, v in form.items() if k in _CAPM_UPDATE_FIELDS}
    if not submitted:
        raise HTTPException(400, "No valid fields submitted")

    p = db.query(CapMProduct).filter(CapMProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")

    changed_by = request.session.get("user") or "admin"
    overrides = set(_parse_overrides(p.manually_edited_fields))
    row_label = p.ticker or p.fund_name or f"#{p.id}"

    actually_changed: list[str] = []
    for form_key, raw_val in submitted.items():
        attr, coercer = _CAPM_UPDATE_FIELDS[form_key]
        old_val = getattr(p, attr, None)
        new_val = _coerce_capm(coercer, raw_val if isinstance(raw_val, str) else "")
        if old_val == new_val:
            continue
        setattr(p, attr, new_val)
        actually_changed.append(form_key)
        overrides.add(attr)
        _audit_log(
            db,
            action="UPDATE",
            table_name="capm_products",
            row_id=p.id,
            field_name=attr,
            old_value=_stringify(old_val),
            new_value=_stringify(new_val),
            row_label=row_label,
            changed_by=changed_by,
        )

    if actually_changed:
        p.manually_edited_fields = json.dumps(sorted(overrides))
        p.updated_at = datetime.utcnow()
        db.commit()

    # Inline fetch() call: return JSON rather than redirect.
    if len(submitted) <= 2:
        return {
            "ok": True,
            "updated": list(submitted.keys()),
            "changed": actually_changed,
            "overrides": sorted(overrides),
        }

    # Full-form submission (legacy): redirect with filter params preserved.
    suite_param = ""
    if "suite_source" in submitted and submitted["suite_source"]:
        suite_param = f"&suite={submitted['suite_source']}"
    return RedirectResponse(url=f"/operations/products/?msg=updated{suite_param}", status_code=302)


# ---------------------------------------------------------------------------
# Phase 1 legacy redirects (old URL → new canonical URL).
# GET → 301 (permanent). POST → 307 (preserve method).
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def capm_index_redirect():
    return RedirectResponse("/operations/products", status_code=301)


@router.get("/export.csv")
def capm_export_redirect():
    return RedirectResponse("/operations/products/export.csv", status_code=301)


@router.post("/update/{product_id}")
def capm_update_redirect(product_id: int):
    return RedirectResponse(f"/operations/products/update/{product_id}", status_code=307)

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

    Phase 3 (ADR 0007) collapsed this from a 3-way merge to a 2-way merge.
    capm_products was folded into rex_products on 2026-05-19 — every CapM-
    derived field (bb_ticker, inception_date, issuer, fee fields, classification
    axes, leverage, underlying_*, expense_ratio, bmo_suite, custodian, etc.)
    now lives on rex_products itself.

    Sources:
      1. rex_products (REX-branded via _rex_only_filter) — the canonical
         products table after Phase 3. Carries lifecycle status, SEC
         identifiers AND the CapM operational fields. Inline-editable.
      2. mkt_master_data — joined on ticker_clean, contributes live AUM,
         fund_type (ETF/ETN), market_status.

    For tickerless rex_products rows, each survives independently — during
    "Awaiting Effective" the SEC ticker reservation can be reassigned
    multiple times across distinct fund names.
    """
    from webapp.models import (
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
    # Pull rex_products (REX-only) + mkt_master_data. Filtering happens in
    # Python after the merge so suite/q apply to the unified set.
    # ------------------------------------------------------------------
    rex_rows = _rex_only_filter(db.query(RexProduct)).all()

    # mkt_master_data: keyed by ticker_clean (e.g. "AAPX" not "AAPX US")
    mkt_by_ticker: dict[str, MktMasterData] = {}
    rex_tickers_upper = {(r.ticker or "").upper().strip() for r in rex_rows if r.ticker}
    if rex_tickers_upper:
        mkt_rows = (
            db.query(MktMasterData)
            .filter(or_(
                func.upper(MktMasterData.ticker_clean).in_(rex_tickers_upper),
                func.upper(MktMasterData.ticker).in_(rex_tickers_upper),
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

    unified: list[SimpleNamespace] = []
    overrides_count = 0

    for r in rex_rows:
        ticker = (r.ticker or "").upper().strip() or None
        mkt = mkt_by_ticker.get(ticker) if ticker else None
        row = _build_unified_row(r, mkt, ticker=ticker)
        if row.edited_fields:
            overrides_count += 1
        unified.append(row)

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
    # Stats computed AFTER the live-only filter so KPIs match what the
    # table shows. With ?include_all=1 they reflect the full unified set.
    # Stats run BEFORE the suite/q filter so they stay stable while the
    # user narrows the table.
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
    rex,           # RexProduct (required after Phase 3)
    mkt,           # MktMasterData | None
    ticker: str | None,
) -> SimpleNamespace:
    """Combine a rex_products row + optional mkt_master_data into a single
    template-ready view object.

    After Phase 3 (ADR 0007), rex_products is the canonical products table.
    The CapM-derived columns (bb_ticker, inception_date, issuer, fees,
    classification axes, leverage, underlying_*, expense_ratio, bmo_suite,
    custodian, etc.) live directly on rex_products. The old 3-way merge with
    a separate capm_products table is gone.

    Field-resolution priority (best operational truth wins):
      ticker          — explicit param (already upper-stripped)
      fund_name       — rex.name → mkt.fund_name
      inception_date  — rex.inception_date (CapM-curated) → rex.official_listed_date
      product_type    — mkt.fund_type (Bloomberg-canonical for ETF/ETN)
      status_display  — rex.status → mkt.market_status (mapped) → from inception
      prospectus      — rex.latest_prospectus_link
      editable_rex_id — rex.id (drives inline-edit affordance; every rex_products
                        row is editable via /operations/products/update/{id})
    """
    fund_name = (rex.name or (mkt.fund_name if mkt else None) or "—")

    # inception_date — prefer the explicit CapM-derived column (rex.inception_date,
    # added in Stage 1) over the looser official_listed_date (which sometimes
    # carries the SEC effective date instead of true first-trade).
    inception_date = rex.inception_date or rex.official_listed_date

    # Status — rex.status is most accurate for in-flight; mkt gives us live
    # exchange status for trading funds.
    if rex.status:
        status_display = rex.status
    elif mkt and mkt.market_status:
        status_display = {
            "ACTV": "Listed", "PEND": "Pending", "LIQU": "Delisted",
        }.get(mkt.market_status, mkt.market_status)
    elif rex.inception_date:
        status_display = "Listed"
    else:
        status_display = "—"

    # Fund type — legal structure ONLY (ETF / ETN). Bloomberg mkt.fund_type
    # is the canonical source. Fall back to "ETN" only if rex.product_suite
    # equals "MicroSectors ETN" (the only REX ETN family), else default ETF.
    if mkt and mkt.fund_type:
        fund_type_display = mkt.fund_type
    elif rex.product_suite == "MicroSectors ETN":
        fund_type_display = "ETN"
    else:
        fund_type_display = "ETF"

    prospectus_display = rex.latest_prospectus_link
    prospectus_source = "live" if rex.latest_prospectus_link else None

    # Suite — rex.product_suite if curated, else derive from name
    suite_source = rex.product_suite or _classify_rex_suite(fund_name)

    edited_fields = _parse_overrides(rex.manually_edited_fields)

    # cu_size on rex_products is Integer; render as string for the template.
    cu_size_display = str(rex.cu_size) if rex.cu_size else None

    return SimpleNamespace(
        id=rex.id,
        # editable_capm_id kept as template field-name for backward template
        # compat; populated with rex.id since rex_products is now the write
        # target and every row is inline-editable.
        editable_capm_id=rex.id,
        editable_rex_id=rex.id,
        ticker=ticker or "",
        fund_name=fund_name,
        bb_ticker=rex.bb_ticker,
        inception_date=inception_date,
        trust=rex.trust,
        issuer=rex.issuer,
        exchange=rex.exchange,
        cu_size=cu_size_display,
        fixed_fee=rex.fixed_fee,
        variable_fee=rex.variable_fee,
        cut_off=rex.cut_off,
        custodian=rex.custodian,
        lmm=rex.lmm,
        suite_source=suite_source,
        product_type=fund_type_display if fund_type_display != "—" else None,
        category=rex.category,
        status_display=status_display,
        fund_type_display=fund_type_display,
        prospectus_display=prospectus_display,
        prospectus_source=prospectus_source,
        # Legacy field — template still reads `prospectus_link`
        prospectus_link=rex.latest_prospectus_link,
        edited_fields=edited_fields,
    )


def _capm_export_impl(
    request: Request,
    suite: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """Export filtered product list as CSV. Mounted at /operations/products/export.csv in PR 1.

    Phase 3 (ADR 0007): now reads from rex_products (was capm_products).
    Column set is unchanged — every column the CSV exposes has a backing
    column on rex_products after the Stage 1+2 migration. `competitor_products`
    maps to rex.competitors (renamed during the merge).
    """
    from webapp.models import RexProduct

    query = db.query(RexProduct)

    if suite and suite in VALID_SUITES:
        query = query.filter(RexProduct.product_suite == suite)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            RexProduct.name.ilike(like),
            RexProduct.ticker.ilike(like),
            RexProduct.underlying_name.ilike(like),
            RexProduct.underlying_ticker.ilike(like),
            RexProduct.lmm.ilike(like),
            RexProduct.custodian.ilike(like),
        ))

    products = query.order_by(RexProduct.product_suite, RexProduct.ticker).all()

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
            p.name or "",
            p.product_suite or "",
            p.bb_ticker or "",
            p.inception_date.isoformat() if p.inception_date else "",
            p.trust or "",
            p.issuer or "",
            p.exchange or "",
            str(p.cu_size) if p.cu_size else "",
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
            p.competitors or "",
            p.bmo_suite or "",
            p.latest_prospectus_link or "",
        ])

    output.seek(0)
    filename = f"rex_products_{date.today().isoformat()}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# Whitelist of fields that the products-update endpoint will write.
# Maps form field name -> (RexProduct attribute, type_coercer).
# Anything not in this map is silently ignored — keeps injection attack
# surface tight.
#
# Phase 3 (ADR 0007): repointed from CapMProduct to RexProduct. Form field
# names are kept stable (`fund_name`, `suite_source`) for backward-compat
# with the template + JS, but they now map to RexProduct attributes:
#   fund_name    -> RexProduct.name
#   suite_source -> RexProduct.product_suite
# All other CapM-derived attrs (bb_ticker, inception_date, fees, custodian,
# classification axes, leverage, underlying_*, ...) live on RexProduct
# directly after the Stage 1+2 migration.
_CAPM_UPDATE_FIELDS = {
    "fund_name":         ("name",              "str_required"),
    "ticker":            ("ticker",            "str_or_none"),
    "bb_ticker":         ("bb_ticker",         "str_or_none"),
    "suite_source":      ("product_suite",     "suite_or_none"),
    "exchange":          ("exchange",          "str_or_none"),
    "cu_size":           ("cu_size",           "int_or_none"),
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
    """Coerce a raw form string into a RexProduct attribute value."""
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
    if coerce_type == "int_or_none":
        if not s:
            return None
        try:
            # Strip commas for human-friendly input ("50,000" -> 50000)
            return int(s.replace(",", ""))
        except ValueError:
            raise HTTPException(400, f"Invalid integer: {s!r}")
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
    """Admin-only: update a REX product record.

    Mounted at /operations/products/update/{product_id} in PR 1.

    Phase 3 (ADR 0007): now writes to rex_products instead of capm_products.
    URL + form-field names are stable for backward-compat with the template.
    Audit log entries are written with table_name='rex_products' (capm_audit_log
    table_name column already supports both per Phase 2 work).

    Accepts partial updates — only fields that appear in the submitted form
    are modified. Supports inline cell-by-cell editing on the
    /operations/products page while remaining compatible with full-form
    submissions.

    Side effects:
    - Records every changed field to capm_audit_log with table_name='rex_products'.
    - Adds the changed field name to manually_edited_fields so the daily
      auto-import skips it (override-block behavior).
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from webapp.models import RexProduct

    form = await request.form()
    submitted = {k: v for k, v in form.items() if k in _CAPM_UPDATE_FIELDS}
    if not submitted:
        raise HTTPException(400, "No valid fields submitted")

    p = db.query(RexProduct).filter(RexProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")

    changed_by = request.session.get("user") or "admin"
    overrides = set(_parse_overrides(p.manually_edited_fields))
    row_label = p.ticker or p.name or f"#{p.id}"

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
            table_name="rex_products",
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

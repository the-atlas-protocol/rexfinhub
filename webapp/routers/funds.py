"""
Funds router - Fund list and detail pages.

PR 2a (rexfinhub v3): canonical /funds/{ticker} surface that merges:
  * SEC fund_status / extractions / name_history (Page A)
  * Bloomberg mkt_master_data (Page B)

URL shapes:
  /funds/                       — fund list (unchanged)
  /funds/{key}                  — disambiguates:
        key looks like S000NNNNNN -> 301 to /funds/series/{key}
        otherwise treated as a ticker; if Bloomberg has no row but the SEC
        side has a series matching that ticker, we still render the SEC view
  /funds/series/{series_id}     — filed-only / SEC-only view; if a ticker has
                                  since been assigned, 301 to /funds/{ticker}
"""
from __future__ import annotations

import json
import logging
import math
import re

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.fund_filters import MUTUAL_FUND_EXCLUSIONS
from webapp.models import Trust, FundStatus, NameHistory, FundExtraction, Filing

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

# Series ID looks like "S000074123" — capital S followed by 6+ digits.
_SERIES_ID_RE = re.compile(r"^S\d{6,}$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_prospectus_link(extractions, fallback: str | None = None) -> str | None:
    """Pick the best prospectus link from a fund's full extraction history.

    Preference order: 485B(POS) > POS AM > S-3 > 485A(POS) > S-1 > newest. The
    most recent match in each tier wins because `extractions` is ordered by
    filing_date descending. Falls back to fund_status.prospectus_link.
    """
    def latest(predicate):
        for row in extractions:
            form = (row.form or "").upper()
            if predicate(form) and row.primary_link:
                return row.primary_link
        return None

    return (
        latest(lambda f: f.startswith("485B") and "BXT" not in f)
        or latest(lambda f: f == "POS AM")
        or latest(lambda f: f.startswith("S-3"))
        or latest(lambda f: f.startswith("485A"))
        or latest(lambda f: f.startswith("S-1"))
        or (extractions[0].primary_link if extractions else None)
        or fallback
    )


def _load_sec_context(db: Session, fund: FundStatus) -> dict:
    """Load trust + name_history + filing history for an SEC fund row."""
    trust = db.execute(
        select(Trust).where(Trust.id == fund.trust_id)
    ).scalar_one_or_none()

    names = db.execute(
        select(NameHistory)
        .where(NameHistory.series_id == fund.series_id)
        .order_by(NameHistory.first_seen_date.desc())
    ).scalars().all()

    extractions = db.execute(
        select(
            FundExtraction,
            Filing.id.label("filing_id"),
            Filing.form,
            Filing.filing_date,
            Filing.primary_link,
        )
        .join(Filing, Filing.id == FundExtraction.filing_id)
        .where(FundExtraction.series_id == fund.series_id)
        .order_by(Filing.filing_date.desc())
    ).all()

    best_prospectus_link = _pick_prospectus_link(
        extractions, fallback=fund.prospectus_link
    )

    return {
        "fund": fund,
        "trust": trust,
        "names": names,
        "extractions": extractions,
        "best_prospectus_link": best_prospectus_link,
    }


def _load_bloomberg_context(db: Session, ticker: str) -> dict:
    """Load Bloomberg DES context for a ticker. Returns empty dict if no row.

    All keys are populated even when bbg_fund is None so the template can
    `{% if bbg_fund %}` once at the section level.
    """
    import pandas as pd
    from webapp.services.market_data import (
        get_master_data,
        data_available,
        _fmt_currency,
        _fmt_flow,
        get_data_as_of,
    )

    ctx = {
        "bbg_fund": None,
        "bbg_available": False,
        "bbg_data_as_of": None,
        "aum_history": [],
        "flows": {},
        "flows_fmt": {},
        "competitors": [],
        "total_returns": None,
    }

    if not data_available(db):
        return ctx

    ctx["bbg_available"] = True
    ctx["bbg_data_as_of"] = get_data_as_of(db)

    try:
        master = get_master_data(db)
        match_col = "ticker_clean" if "ticker_clean" in master.columns else "ticker"
        upper = ticker.upper()
        row = master[master[match_col].str.upper() == upper]
        if row.empty:
            return ctx

        r = row.iloc[0]
        d = r.to_dict()

        # AUM history (12 months + current)
        aum_col = "t_w4.aum"
        aum_history: list[float] = []
        for i in range(12, 0, -1):
            col = f"t_w4.aum_{i}"
            aum_history.append(float(r.get(col, 0) or 0) if col in master.columns else 0.0)
        aum_history.append(float(r.get(aum_col, 0) or 0))
        ctx["aum_history"] = aum_history

        # Flows
        flows = {
            "1d": float(r.get("t_w4.fund_flow_1day", 0) or 0),
            "1w": float(r.get("t_w4.fund_flow_1week", 0) or 0),
            "1m": float(r.get("t_w4.fund_flow_1month", 0) or 0),
            "3m": float(r.get("t_w4.fund_flow_3month", 0) or 0),
            "6m": float(r.get("t_w4.fund_flow_6month", 0) or 0),
            "ytd": float(r.get("t_w4.fund_flow_ytd", 0) or 0),
            "1y": float(r.get("t_w4.fund_flow_1year", 0) or 0),
        }
        ctx["flows"] = flows
        ctx["flows_fmt"] = {k: _fmt_flow(v) for k, v in flows.items()}

        # Returns table
        returns = {}
        for period, col in [
            ("1D", "t_w3.total_return_1day"), ("1W", "t_w3.total_return_1week"),
            ("1M", "t_w3.total_return_1month"), ("3M", "t_w3.total_return_3month"),
            ("6M", "t_w3.total_return_6month"), ("YTD", "t_w3.total_return_ytd"),
            ("1Y", "t_w3.total_return_1year"), ("3Y", "t_w3.total_return_3year"),
        ]:
            val = r.get(col)
            returns[period] = float(val) if val is not None and val == val else None

        def _safe(key):
            v = r.get(key)
            return float(v) if v is not None and v == v else None

        inception = None
        inc_raw = r.get("inception_date")
        if inc_raw is not None and not (isinstance(inc_raw, float) and pd.isna(inc_raw)):
            try:
                inception = pd.Timestamp(inc_raw).strftime("%b %d, %Y")
            except Exception:
                inception = str(inc_raw)

        ctx["bbg_fund"] = {
            "ticker": upper,
            "name": d.get("fund_name", upper),
            # PR 2b will canonicalize issuer_display in get_master_data();
            # for now we surface whatever the service returned.
            "issuer": d.get("issuer_display", d.get("issuer", "")),
            "category": d.get("category_display", ""),
            "fund_type": d.get("fund_type", d.get("t_w1.fund_type", "")),
            "inception": inception,
            "is_rex": bool(d.get("is_rex")),
            "aum": _safe("t_w4.aum"),
            "aum_fmt": _fmt_currency(float(r.get("t_w4.aum", 0) or 0)),
            "expense_ratio": _safe("t_w2.expense_ratio"),
            "spread": _safe("t_w2.average_bidask_spread"),
            "volume_30d": _safe("t_w2.average_vol_30day"),
            "short_interest": _safe("t_w2.percent_short_interest"),
            "premium_discount": _safe("t_w2.percentage_premium"),
            "nav_tracking_error": _safe("t_w2.nav_tracking_error"),
            "open_interest": _safe("t_w2.open_interest"),
            "annualized_yield": _safe("t_w3.annualized_yield"),
            "returns": returns,
            "leverage": d.get("q_category_attributes.map_li_leverage_amount", ""),
            "direction": d.get("q_category_attributes.map_li_direction", ""),
            "underlier": d.get("q_category_attributes.map_li_underlier", ""),
            "primary_strategy": d.get("primary_strategy") or "",
            "asset_class": d.get("asset_class") or "",
            "sub_strategy": d.get("sub_strategy") or "",
            "row": d,
        }

        # Competitors: same underlier (preferred) or same category
        cat = d.get("category_display", "")
        underlier = d.get("q_category_attributes.map_li_underlier", "")
        if cat or underlier:
            comp_filter = master[match_col].str.upper() != upper
            comp_filter &= master["market_status"] == "ACTV"
            if underlier:
                comp_filter &= (
                    master.get("q_category_attributes.map_li_underlier", pd.Series(dtype=object)) == underlier
                )
            elif cat:
                comp_filter &= (master.get("category_display", pd.Series(dtype=object)) == cat)
            comp_rows = master[comp_filter].sort_values("t_w4.aum", ascending=False).head(10)
            competitors = []
            for _, cr in comp_rows.iterrows():
                competitors.append({
                    "ticker": cr.get(match_col, ""),
                    "name": cr.get("fund_name", ""),
                    "issuer": cr.get("issuer_display", ""),
                    "aum": float(cr.get("t_w4.aum", 0) or 0),
                    "aum_fmt": _fmt_currency(float(cr.get("t_w4.aum", 0) or 0)),
                    "expense_ratio": float(cr.get("t_w2.expense_ratio", 0) or 0),
                    "flow_1m": float(cr.get("t_w4.fund_flow_1month", 0) or 0),
                    "return_ytd": float(cr.get("t_w3.total_return_ytd", 0) or 0),
                    "is_rex": bool(cr.get("is_rex")),
                })
            ctx["competitors"] = competitors

    except Exception:
        log.exception("Error loading Bloomberg context for %s", ticker)
        return ctx

    # Total return scrape (best-effort; degrades silently)
    try:
        from scripts.scrape_total_returns import scrape as scrape_returns
        tr_result = scrape_returns([ticker.upper()])
        if "error" not in tr_result and tr_result.get("dates"):
            ctx["total_returns"] = {
                "dates_json": json.dumps(tr_result["dates"]),
                "series_json": json.dumps({
                    ticker.upper(): tr_result["growth_series"].get(ticker.upper(), [])
                }),
                "stats": tr_result.get("stats", {}).get(ticker.upper(), {}),
            }
    except Exception:
        log.exception("Error fetching total returns for %s", ticker)

    return ctx


def _render_detail(
    request: Request,
    db: Session,
    *,
    fund: FundStatus | None,
    ticker: str | None,
) -> "templates.TemplateResponse":
    """Render the merged fund detail page for the SEC fund + Bloomberg ticker.

    Either ``fund`` or ``ticker`` must be provided (typically both). When ``fund``
    is None the SEC-side sections render empty / fallback. When ``ticker`` is
    None or has no Bloomberg row the bbg sections gracefully hide.
    """
    sec_ctx: dict = {
        "fund": fund,
        "trust": None,
        "names": [],
        "extractions": [],
        "best_prospectus_link": None,
    }
    if fund is not None:
        sec_ctx = _load_sec_context(db, fund)

    # Resolve effective ticker for bbg lookup. Prefer the explicit URL ticker;
    # fall back to fund.ticker when we entered via /funds/series/{id}.
    effective_ticker = (ticker or (fund.ticker if fund else "") or "").strip()
    bbg_ctx = (
        _load_bloomberg_context(db, effective_ticker)
        if effective_ticker
        else {
            "bbg_fund": None,
            "bbg_available": False,
            "bbg_data_as_of": None,
            "aum_history": [],
            "flows": {},
            "flows_fmt": {},
            "competitors": [],
            "total_returns": None,
        }
    )

    # 13F scaffold — dormant on prod
    holders_ticker = effective_ticker or (fund.ticker if fund else None)

    # Header values used by template (prefer bbg, fall back to SEC).
    bbg_fund = bbg_ctx["bbg_fund"]
    page_ticker = (
        (bbg_fund["ticker"] if bbg_fund else None)
        or effective_ticker
        or (fund.ticker if fund else None)
    )
    page_name = (
        (bbg_fund["name"] if bbg_fund else None)
        or (fund.fund_name if fund else page_ticker or "Fund")
    )

    return templates.TemplateResponse("fund_detail.html", {
        "request": request,
        # SEC side
        "fund": sec_ctx["fund"],
        "trust": sec_ctx["trust"],
        "names": sec_ctx["names"],
        "extractions": sec_ctx["extractions"],
        "best_prospectus_link": sec_ctx["best_prospectus_link"],
        # Bloomberg side
        "bbg_fund": bbg_fund,
        "bbg_available": bbg_ctx["bbg_available"],
        "bbg_data_as_of": bbg_ctx["bbg_data_as_of"],
        "aum_history": bbg_ctx["aum_history"],
        "flows": bbg_ctx["flows"],
        "flows_fmt": bbg_ctx["flows_fmt"],
        "competitors": bbg_ctx["competitors"],
        "total_returns": bbg_ctx["total_returns"],
        # Page-level helpers
        "page_ticker": page_ticker,
        "page_name": page_name,
        # 13F scaffold (always empty on prod)
        "holders_13f": [],
        "holders_count": 0,
        "holders_total_value": 0.0,
        "holders_ticker": holders_ticker,
        "holders_quarter": None,
    })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/")
def fund_list(
    request: Request,
    q: str = "",
    status: str = "",
    trust_id: int = 0,
    show_mutual: str = "",
    page: int = 1,
    per_page: int = 100,
    db: Session = Depends(get_db),
):
    """Paginated fund list with search and filters."""
    if per_page not in (25, 50, 100, 250):
        per_page = 100
    if page < 1:
        page = 1

    query = select(FundStatus, Trust.name.label("trust_name"), Trust.slug.label("trust_slug")).join(
        Trust, Trust.id == FundStatus.trust_id
    )

    query = query.where(FundStatus.fund_name != "")

    if show_mutual != "true":
        for pattern in MUTUAL_FUND_EXCLUSIONS:
            query = query.where(~FundStatus.fund_name.ilike(pattern))

    if q:
        query = query.where(or_(
            FundStatus.fund_name.ilike(f"%{q}%"),
            FundStatus.ticker.ilike(f"%{q}%"),
        ))
    if status:
        query = query.where(FundStatus.status == status.upper())
    if trust_id:
        query = query.where(FundStatus.trust_id == trust_id)

    count_query = select(func.count()).select_from(query.subquery())
    total_results = db.execute(count_query).scalar() or 0
    total_pages = max(1, math.ceil(total_results / per_page))

    if page > total_pages:
        page = total_pages

    query = query.order_by(FundStatus.fund_name)
    query = query.offset((page - 1) * per_page).limit(per_page)
    results = db.execute(query).all()

    total_all = db.execute(select(func.count()).select_from(FundStatus)).scalar() or 0

    trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

    trusts_with_no_funds = db.execute(
        select(Trust.name).where(Trust.is_active == True)
        .where(~Trust.id.in_(select(FundStatus.trust_id).distinct()))
        .order_by(Trust.name)
    ).scalars().all()

    return templates.TemplateResponse("fund_list.html", {
        "request": request,
        "funds": results,
        "trusts": trusts,
        "q": q,
        "status": status,
        "trust_id": trust_id,
        "show_mutual": show_mutual,
        "total": total_results,
        "total_all": total_all,
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": total_pages,
        "trusts_with_no_funds": trusts_with_no_funds,
    })


@router.get("/series/{series_id}")
def fund_detail_by_series(series_id: str, request: Request, db: Session = Depends(get_db)):
    """SEC-only fund detail (filed-only; no Bloomberg row expected).

    If a ticker has been assigned since launch, 301 to /funds/{ticker} so the
    canonical surface always has the merged Bloomberg + SEC view.
    """
    # series_id can occasionally appear in multiple fund_status rows (rare —
    # historical artifact of trust restructurings). Prefer the EFFECTIVE row,
    # then fall back to the most recently filed.
    fund = db.execute(
        select(FundStatus)
        .where(FundStatus.series_id == series_id)
        .order_by(
            (FundStatus.status != "EFFECTIVE").asc(),
            FundStatus.latest_filing_date.desc().nulls_last(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if not fund:
        raise HTTPException(status_code=404, detail="Fund not found")

    if fund.ticker:
        return RedirectResponse(url=f"/funds/{fund.ticker.strip().upper()}", status_code=301)

    return _render_detail(request, db, fund=fund, ticker=None)


@router.get("/{key}")
def fund_detail(key: str, request: Request, db: Session = Depends(get_db)):
    """Canonical fund detail surface.

    Disambiguation:
      * key matches series_id pattern (S000NNNNNN) -> 301 to /funds/series/{key}
      * otherwise treated as a ticker — load Bloomberg row + SEC fund_status

    If neither Bloomberg nor SEC has a row for this ticker, 404.
    """
    raw = key.strip()

    # Series-id shape: redirect to canonical filed-only URL
    if _SERIES_ID_RE.match(raw):
        return RedirectResponse(url=f"/funds/series/{raw}", status_code=301)

    ticker = raw.upper()

    # SEC bridge: ticker is bare (e.g., "AAPB"); fund_status.ticker stores it bare.
    # A ticker can appear in multiple fund_status rows (e.g., a series has been
    # reissued under a new trust). Prefer the EFFECTIVE row, then the most
    # recently filed.
    sec_fund = db.execute(
        select(FundStatus)
        .where(FundStatus.ticker.ilike(ticker))
        .order_by(
            (FundStatus.status != "EFFECTIVE").asc(),
            FundStatus.latest_filing_date.desc().nulls_last(),
        )
        .limit(1)
    ).scalar_one_or_none()

    # Probe Bloomberg side. _load_bloomberg_context tolerates missing rows.
    bbg_ctx = _load_bloomberg_context(db, ticker)

    if sec_fund is None and bbg_ctx["bbg_fund"] is None:
        raise HTTPException(status_code=404, detail=f"No fund found for ticker '{ticker}'")

    return _render_detail(request, db, fund=sec_fund, ticker=ticker)

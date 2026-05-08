"""Canonical issuer detail surface (PR 2b).

URL design:
    /issuers/                    -> browse-all index, ranked by AUM
    /issuers/{canonical_name}    -> detail page (full roster, AUM trend, categories)

Variant URLs (e.g. /issuers/iShares Delaware Trust Sponsor) 301 to the
canonical name when a row exists in issuer_canonicalization.csv (AUTO only).

Replaces legacy /market/issuer/detail?issuer=X (kept as a 301 redirect).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.services import market_data
from webapp.services.market_data import _get_issuer_canon_map

log = logging.getLogger(__name__)

router = APIRouter(prefix="/issuers", tags=["issuers"])
templates = Jinja2Templates(directory="webapp/templates")


def _fmt_currency(val: float) -> str:
    """Match market_data._fmt_currency formatting (values in $M)."""
    if val is None:
        return "$0"
    try:
        if isinstance(val, float) and math.isnan(val):
            return "$0"
    except Exception:
        pass
    if abs(val) >= 1_000:
        return f"${val/1_000:,.1f}B"
    if abs(val) >= 1:
        return f"${val:,.1f}M"
    return f"${val:.2f}M"


def _aum_col(df) -> str | None:
    """Find the current AUM column (handles legacy prefixed name)."""
    if df is None or df.empty:
        return None
    candidates = [c for c in df.columns if c.lower().strip() == "t_w4.aum"]
    if candidates:
        return candidates[0]
    candidates = [c for c in df.columns if c.lower().strip() == "aum"]
    if candidates:
        return candidates[0]
    candidates = [
        c for c in df.columns
        if c.endswith(".aum") and not any(c.endswith(f".aum_{i}") for i in range(1, 37))
    ]
    return candidates[0] if candidates else None


@router.get("/")
def issuers_index(request: Request, db: Session = Depends(get_db)):
    """Browse all canonical issuers, ranked by AUM."""
    try:
        df = market_data.get_master_data(db)
    except Exception as e:
        log.error("Issuers index load error: %s", e, exc_info=True)
        df = None

    if df is None or df.empty or "issuer_display" not in df.columns:
        return templates.TemplateResponse("issuers/index.html", {
            "request": request,
            "issuers": [],
            "total_issuers": 0,
            "total_funds": 0,
            "total_aum_fmt": "$0",
            "data_as_of": market_data.get_data_as_of(db) if df is not None else "",
        })

    aum_col = _aum_col(df)
    ticker_col = "ticker" if "ticker" in df.columns else df.columns[0]

    # ACTV filter (consistent with KPIs)
    if "market_status" in df.columns:
        actv_mask = df["market_status"].fillna("ACTV").str.strip().str.upper() == "ACTV"
        df = df[actv_mask]

    agg_dict: dict = {"n_funds": (ticker_col, "count")}
    if aum_col:
        agg_dict["total_aum"] = (aum_col, "sum")

    grouped = (
        df.groupby("issuer_display", dropna=True)
        .agg(**agg_dict)
        .reset_index()
    )
    if aum_col:
        grouped = grouped.sort_values("total_aum", ascending=False)
    else:
        grouped = grouped.sort_values("n_funds", ascending=False)

    issuers = []
    for _, r in grouped.iterrows():
        name = str(r["issuer_display"]).strip()
        if not name or name.lower() == "nan":
            continue
        total_aum = float(r["total_aum"] or 0) if "total_aum" in grouped.columns else 0.0
        issuers.append({
            "name": name,
            "n_funds": int(r["n_funds"]),
            "total_aum": total_aum,
            "total_aum_fmt": _fmt_currency(total_aum),
        })

    total_aum_all = sum(i["total_aum"] for i in issuers)
    total_funds_all = sum(i["n_funds"] for i in issuers)

    return templates.TemplateResponse("issuers/index.html", {
        "request": request,
        "issuers": issuers,
        "total_issuers": len(issuers),
        "total_funds": total_funds_all,
        "total_aum_fmt": _fmt_currency(total_aum_all),
        "data_as_of": market_data.get_data_as_of(db),
    })


@router.get("/{name}")
def issuer_detail(request: Request, name: str, db: Session = Depends(get_db)):
    """Issuer detail page. If `name` is a known variant, 301 to the canonical name."""
    canon_map = _get_issuer_canon_map()
    name_clean = name.strip()
    if name_clean in canon_map and canon_map[name_clean] != name_clean:
        return RedirectResponse(f"/issuers/{canon_map[name_clean]}", status_code=301)

    try:
        df = market_data.get_master_data(db)
    except Exception as e:
        log.error("Issuer detail load error for %s: %s", name, e, exc_info=True)
        df = None

    variants_known_as = sorted({k for k, v in canon_map.items() if v == name_clean and k != name_clean})

    if df is None or df.empty or "issuer_display" not in df.columns:
        return templates.TemplateResponse("issuers/detail.html", {
            "request": request,
            "issuer_name": name_clean,
            "issuer_data": None,
            "funds": [],
            "categories": [],
            "aum_trend": {"labels": [], "values": []},
            "variants_known_as": variants_known_as,
            "data_as_of": "",
        })

    funds_df = df[df["issuer_display"].fillna("").str.strip() == name_clean].copy()

    if funds_df.empty:
        return templates.TemplateResponse("issuers/detail.html", {
            "request": request,
            "issuer_name": name_clean,
            "issuer_data": None,
            "funds": [],
            "categories": [],
            "aum_trend": {"labels": [], "values": []},
            "variants_known_as": variants_known_as,
            "data_as_of": market_data.get_data_as_of(db),
        })

    aum_col = _aum_col(funds_df)
    cat_col = next((c for c in funds_df.columns if c.lower().strip() == "category_display"), None)
    name_col = next((c for c in funds_df.columns if c.lower().strip() == "fund_name"), None)
    ticker_col = "ticker_clean" if "ticker_clean" in funds_df.columns else "ticker"

    # ACTV filter for headline numbers (consistent with the rest of Market Intel)
    if "market_status" in funds_df.columns:
        actv_mask = funds_df["market_status"].fillna("ACTV").str.strip().str.upper() == "ACTV"
        actv_df = funds_df[actv_mask]
    else:
        actv_df = funds_df

    total_aum = float(actv_df[aum_col].fillna(0).sum()) if aum_col else 0.0
    n_funds = int(len(actv_df))
    n_categories = int(actv_df[cat_col].nunique()) if cat_col else 0

    # Category breakdown
    categories = []
    if cat_col and aum_col:
        cat_grp = (
            actv_df.groupby(cat_col)[aum_col]
            .sum()
            .reset_index()
            .sort_values(aum_col, ascending=False)
        )
        for _, r in cat_grp.iterrows():
            cat_name = str(r[cat_col]).strip()
            if not cat_name or cat_name.lower() == "nan":
                continue
            aum_val = float(r[aum_col] or 0)
            categories.append({
                "name": cat_name,
                "aum": aum_val,
                "aum_fmt": _fmt_currency(aum_val),
            })

    # Full fund roster (sorted by AUM desc)
    funds_sorted = (
        funds_df.sort_values(aum_col, ascending=False)
        if aum_col and aum_col in funds_df.columns
        else funds_df
    )
    funds = []
    for _, row in funds_sorted.iterrows():
        aum_val = float(row.get(aum_col, 0) or 0) if aum_col else 0.0
        funds.append({
            "ticker": str(row.get(ticker_col, "") or ""),
            "fund_name": str(row.get(name_col, "") or "") if name_col else "",
            "category": str(row.get(cat_col, "") or "") if cat_col else "",
            "aum": aum_val,
            "aum_fmt": _fmt_currency(aum_val),
            "is_rex": bool(row.get("is_rex", False)),
        })

    # 12-month AUM trend
    months_labels: list[str] = []
    months_values: list[float] = []
    now = datetime.now()
    try:
        from dateutil.relativedelta import relativedelta
        _has_relativedelta = True
    except ImportError:
        _has_relativedelta = False

    for i in range(12, -1, -1):
        col_name = f"t_w4.aum_{i}" if i > 0 else aum_col
        if not col_name or col_name not in funds_df.columns:
            continue
        val = float(funds_df[col_name].fillna(0).sum())
        if _has_relativedelta:
            dt = now - relativedelta(months=i)  # type: ignore[possibly-undefined]
        else:
            dt = now - timedelta(days=30 * i)
        months_labels.append(dt.strftime("%b %Y"))
        months_values.append(round(val, 2))
    aum_trend = {"labels": months_labels, "values": months_values}

    is_rex = bool(funds_df["is_rex"].any()) if "is_rex" in funds_df.columns else False

    issuer_data = {
        "name": name_clean,
        "total_aum": total_aum,
        "total_aum_fmt": _fmt_currency(total_aum),
        "n_funds": n_funds,
        "num_products": n_funds,  # alias for templates
        "n_categories": n_categories,
        "is_rex": is_rex,
    }

    return templates.TemplateResponse("issuers/detail.html", {
        "request": request,
        "issuer_name": name_clean,
        "issuer_data": issuer_data,
        "funds": funds,
        "categories": categories,
        "aum_trend": aum_trend,
        "variants_known_as": variants_known_as,
        "data_as_of": market_data.get_data_as_of(db),
    })

"""Structured Notes Dashboard — standalone FastAPI app.

Run: python -m dashboard.app
Browse: http://127.0.0.1:8050
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.queries import (
    get_conn, kpis, monthly_issuance, product_type_mix, issuer_share,
    top_underliers, barrier_distribution, coupon_distribution,
    annual_issuance, explorer_products, filter_options,
)

BASE = Path(__file__).parent

app = FastAPI(title="Structured Notes Intelligence")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


# ---------------------------------------------------------------------------
# Jinja2 filters
# ---------------------------------------------------------------------------

def fmt_number(value, decimals=0):
    if value is None:
        return "-"
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.{decimals}f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:,.{decimals}f}M"
    if abs(value) >= 1e3:
        return f"${value / 1e3:,.0f}K"
    return f"${value:,.{decimals}f}"


def fmt_pct(value, decimals=1):
    if value is None:
        return "-"
    return f"{value * 100:.{decimals}f}%"


def fmt_comma(value):
    if value is None:
        return "-"
    return f"{value:,}"


templates.env.filters["money"] = fmt_number
templates.env.filters["pct"] = fmt_pct
templates.env.filters["comma"] = fmt_comma
templates.env.globals["json_dumps"] = json.dumps


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def overview(request: Request, year: int | None = Query(None)):
    conn = get_conn()
    try:
        filters = filter_options(conn)
        data = {
            "request": request,
            "kpis": kpis(conn, year),
            "monthly": monthly_issuance(conn, year),
            "product_types": product_type_mix(conn, year),
            "issuers": issuer_share(conn, year),
            "underliers": top_underliers(conn, year),
            "barriers": barrier_distribution(conn, year),
            "coupons": coupon_distribution(conn, year),
            "annual": annual_issuance(conn),
            "years": filters["years"],
            "current_year": year,
        }
    finally:
        conn.close()
    return templates.TemplateResponse("overview.html", data)


@app.get("/explorer", response_class=HTMLResponse)
async def explorer(
    request: Request,
    issuer: str | None = Query(None),
    type: str | None = Query(None),
    underlier: str | None = Query(None),
    year: int | None = Query(None),
    page: int = Query(1, ge=1),
):
    conn = get_conn()
    try:
        products = explorer_products(
            conn, issuer=issuer, product_type=type,
            underlier=underlier, year=year, page=page,
        )
        filters = filter_options(conn)
        metrics = kpis(conn)
    finally:
        conn.close()
    return templates.TemplateResponse("explorer.html", {
        "request": request,
        "products": products,
        "filters": filters,
        "kpis": metrics,
        "current_issuer": issuer,
        "current_type": type,
        "current_underlier": underlier or "",
        "current_year": year,
    })


# ---------------------------------------------------------------------------
# JSON API (for dynamic chart updates)
# ---------------------------------------------------------------------------

@app.get("/api/kpis")
async def api_kpis():
    conn = get_conn()
    try:
        return kpis(conn)
    finally:
        conn.close()


@app.get("/api/monthly")
async def api_monthly(year: int | None = Query(None), months: int = Query(24)):
    conn = get_conn()
    try:
        return monthly_issuance(conn, year, months)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.app:app", host="127.0.0.1", port=8050, reload=True)

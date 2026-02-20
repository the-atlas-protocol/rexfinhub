# Agent: Market-Backend
# Branch: feature/market-backend
# Worktree: .worktrees/market-backend

## Your Files (ONLY touch these)
- webapp/services/market_data.py (EDIT)
- webapp/routers/market.py (EDIT)
- webapp/templates/market/base.html (EDIT)

## Task: TASK-003
### Market Intelligence Backend — New Routes + Service Functions

Add 4 new market intelligence pages (treemap, issuer analysis, market share timeline, underlier deep-dive) and enhance the REX View. This task covers ONLY the backend (service functions + routes + nav). A separate agent (3b) will implement the templates, CSS, and JS.

**Context**: The `webapp/services/market_data.py` file loads data from `data/DASHBOARD/The Dashboard.xlsx` (note: space not underscore). It has two sheets: `q_master_data` (fund-level data) and `q_aum_time_series_labeled` (monthly AUM rows). Key columns in master data: `t_w4.aum`, `t_w4.fund_flow_1week`, `t_w4.fund_flow_1month`, `t_w4.aum_1`–`t_w4.aum_4` (last 4 months), `category_display`, `issuer_display`, `ticker`, `fund_name`, `is_rex`, and `q_category_attributes.map_cc_underlier`, `q_category_attributes.map_li_underlier`, `q_category_attributes.map_li_direction`, `q_category_attributes.map_li_leverage_amount`. Time series columns: `date`, `aum_value`, `category_display`, `issuer_display`, `is_rex`, `ticker`.

All existing service functions (`get_master_data()`, `get_rex_summary()`, `get_category_summary()`, `get_kpis()`, `get_time_series()`, `get_slicer_options()`) MUST remain unchanged.

---

**Step 1 — Add 4 New Service Functions to market_data.py**

Read the full `webapp/services/market_data.py` first to understand patterns.

**Function 1: `get_treemap_data(category: str | None = None) -> dict`**
```python
def get_treemap_data(category: str | None = None) -> dict:
    """Return product list for treemap rendering (top 200 by AUM)."""
    df = get_master_data()
    if category and category != "All":
        df = df[df["category_display"] == category].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    df = df.sort_values("t_w4.aum", ascending=False).head(200)
    products = []
    for _, row in df.iterrows():
        aum = float(row.get("t_w4.aum", 0))
        products.append({
            "label": str(row.get("ticker", "")),
            "value": round(aum, 2),
            "group": str(row.get("category_display", "Other")),
            "is_rex": bool(row.get("is_rex", False)),
            "ticker": str(row.get("ticker", "")),
            "fund_name": str(row.get("fund_name", "")),
            "issuer": str(row.get("issuer_display", "")),
            "aum_fmt": _fmt_currency(aum),
        })
    total = float(df["t_w4.aum"].sum()) if not df.empty else 0.0
    return {
        "products": products,
        "total_aum": round(total, 2),
        "total_aum_fmt": _fmt_currency(total),
        "categories": ALL_CATEGORIES,
    }
```

**Function 2: `get_issuer_summary(category: str | None = None) -> dict`**
```python
def get_issuer_summary(category: str | None = None) -> dict:
    """Return per-issuer AUM, flows, product count, market share."""
    df = get_master_data()
    if category and category != "All":
        df = df[df["category_display"] == category].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    total_aum = float(df["t_w4.aum"].sum()) if not df.empty else 0.0

    # Identify REX issuers
    rex_issuers = set(df[df["is_rex"] == True]["issuer_display"].dropna().unique())

    grouped = df.groupby("issuer_display")
    issuers = []
    for issuer_name, grp in grouped:
        aum = float(grp["t_w4.aum"].sum())
        flow_1w = float(grp["t_w4.fund_flow_1week"].sum()) if "t_w4.fund_flow_1week" in grp.columns else 0.0
        flow_1m = float(grp["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in grp.columns else 0.0
        share = (aum / total_aum * 100) if total_aum > 0 else 0.0
        issuers.append({
            "issuer_name": str(issuer_name),
            "total_aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": flow_1w,
            "flow_1w_fmt": _fmt_flow(flow_1w),
            "flow_1m": flow_1m,
            "flow_1m_fmt": _fmt_flow(flow_1m),
            "num_products": int(len(grp)),
            "market_share_pct": round(share, 1),
            "is_rex": str(issuer_name) in rex_issuers,
        })

    issuers.sort(key=lambda x: x["total_aum"], reverse=True)
    return {
        "issuers": issuers,
        "total_aum": round(total_aum, 2),
        "total_aum_fmt": _fmt_currency(total_aum),
        "categories": ALL_CATEGORIES,
    }
```

**Function 3: `get_market_share_timeline() -> dict`**
```python
def get_market_share_timeline() -> dict:
    """Return monthly category market share % over last 24 months."""
    ts = get_time_series_df()

    if ts.empty or "date" not in ts.columns or "category_display" not in ts.columns:
        return {"labels": [], "series": []}

    ts = ts.dropna(subset=["date", "category_display"]).copy()

    # Aggregate: for each (date, category), sum aum_value
    agg = (
        ts.groupby(["date", "category_display"])["aum_value"]
        .sum()
        .reset_index()
        .sort_values("date")
    )

    # Keep last 24 months
    dates = sorted(agg["date"].unique())
    if len(dates) > 24:
        dates = dates[-24:]
    agg = agg[agg["date"].isin(dates)]

    labels = [d.strftime("%b %Y") for d in dates]

    # For each date, compute total AUM across all categories
    date_totals = agg.groupby("date")["aum_value"].sum()

    series = []
    for cat in ALL_CATEGORIES:
        cat_data = agg[agg["category_display"] == cat].set_index("date")
        values = []
        for d in dates:
            cat_aum = float(cat_data.loc[d, "aum_value"]) if d in cat_data.index else 0.0
            total = float(date_totals.get(d, 1.0))
            pct = round(cat_aum / total * 100, 1) if total > 0 else 0.0
            values.append(pct)
        series.append({
            "name": cat,
            "short_name": _suite_short(cat),
            "values": values,
        })

    return {"labels": labels, "series": series}
```

**Function 4: `get_underlier_summary(underlier_type: str = "income", underlier: str | None = None) -> dict`**
```python
def get_underlier_summary(underlier_type: str = "income", underlier: str | None = None) -> dict:
    """Return underlier-level stats for covered call (income) or L&I single stock."""
    df = get_master_data()

    if underlier_type == "income":
        cat_filter = "Income - Single Stock"
        field = "q_category_attributes.map_cc_underlier"
    else:
        cat_filter = "Leverage & Inverse - Single Stock"
        field = "q_category_attributes.map_li_underlier"

    df = df[df["category_display"] == cat_filter].copy()

    if field not in df.columns:
        # Field missing — return empty
        return {"underliers": [], "products": [], "underlier_type": underlier_type, "selected": underlier}

    if underlier is None:
        # Return list of underliers with aggregated stats
        grouped = df.groupby(field)
        underliers = []
        for ul_name, grp in grouped:
            if not str(ul_name).strip():
                continue
            aum = float(grp["t_w4.aum"].sum())
            rex_count = int(grp["is_rex"].sum())
            underliers.append({
                "name": str(ul_name),
                "aum": aum,
                "aum_fmt": _fmt_currency(aum),
                "num_products": int(len(grp)),
                "num_rex": rex_count,
            })
        underliers.sort(key=lambda x: x["aum"], reverse=True)
        return {"underliers": underliers, "products": [], "underlier_type": underlier_type, "selected": None}
    else:
        # Return products for this underlier
        sub = df[df[field] == underlier].copy()
        products = []
        for _, row in sub.sort_values("t_w4.aum", ascending=False).iterrows():
            aum = float(row.get("t_w4.aum", 0))
            flow_1w = float(row.get("t_w4.fund_flow_1week", 0))
            raw_yield = row.get("t_w3.annualized_yield")
            try:
                yield_val = float(raw_yield) if raw_yield is not None and not (isinstance(raw_yield, float) and math.isnan(raw_yield)) else None
            except (TypeError, ValueError):
                yield_val = None
            products.append({
                "ticker": str(row.get("ticker", "")),
                "fund_name": str(row.get("fund_name", "")),
                "direction": str(row.get("q_category_attributes.map_li_direction", "")) if underlier_type == "li" else "",
                "leverage": str(row.get("q_category_attributes.map_li_leverage_amount", "")) if underlier_type == "li" else "",
                "aum": aum,
                "aum_fmt": _fmt_currency(aum),
                "flow_1w": flow_1w,
                "flow_1w_fmt": _fmt_flow(flow_1w),
                "yield_val": yield_val,
                "yield_fmt": f"{yield_val:.1f}%" if yield_val is not None else "-",
                "is_rex": bool(row.get("is_rex", False)),
            })
        underliers_list = []  # Also return the full list so UI can show selector
        for ul_name, grp in df.groupby(field):
            if not str(ul_name).strip():
                continue
            underliers_list.append({
                "name": str(ul_name),
                "aum_fmt": _fmt_currency(float(grp["t_w4.aum"].sum())),
                "num_products": int(len(grp)),
                "num_rex": int(grp["is_rex"].sum()),
            })
        underliers_list.sort(key=lambda x: x["num_products"], reverse=True)
        return {"underliers": underliers_list, "products": products, "underlier_type": underlier_type, "selected": underlier}
```

**Modification to existing `get_rex_summary()` suites**: In the suites loop, after building `kpis`, add `sparkline_data` — 4 floats for the last 4 months of REX AUM in that suite (from `t_w4.aum_1` through `t_w4.aum_4` columns). These are per-fund monthly AUM values, so sum them per suite:
```python
sparkline = []
for col in ["t_w4.aum_4", "t_w4.aum_3", "t_w4.aum_2", "t_w4.aum_1"]:
    if col in rex_suite.columns:
        sparkline.append(round(float(rex_suite[col].sum()), 2))
    else:
        sparkline.append(0.0)
suites_entry["sparkline_data"] = sparkline  # oldest to newest
```
Add this to each suite dict in the existing suites.append() call.

---

**Step 2 — Add 4 New Routes to market.py + Enhance REX View**

Read the full `webapp/routers/market.py` first.

Add these imports if not present: `from typing import Optional`

**Route: Treemap**
```python
@router.get("/treemap")
def treemap_view(request: Request, cat: str = Query(default="All")):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/treemap.html", {"request": request, "available": False, "active_tab": "treemap", "categories": svc.ALL_CATEGORIES})
    try:
        cat_arg = cat if cat != "All" else None
        summary = svc.get_treemap_data(cat_arg)
        return templates.TemplateResponse("market/treemap.html", {
            "request": request, "available": True, "active_tab": "treemap",
            "summary": summary, "categories": svc.ALL_CATEGORIES, "category": cat,
        })
    except Exception as e:
        log.error("Treemap error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/treemap.html", {"request": request, "available": False, "active_tab": "treemap", "categories": svc.ALL_CATEGORIES, "error": str(e)})
```

**Route: Issuer Analysis**
```python
@router.get("/issuer")
def issuer_view(request: Request, cat: str = Query(default="All")):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/issuer.html", {"request": request, "available": False, "active_tab": "issuer", "categories": svc.ALL_CATEGORIES})
    try:
        cat_arg = cat if cat != "All" else None
        summary = svc.get_issuer_summary(cat_arg)
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": True, "active_tab": "issuer",
            "summary": summary, "categories": svc.ALL_CATEGORIES, "category": cat,
        })
    except Exception as e:
        log.error("Issuer view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/issuer.html", {"request": request, "available": False, "active_tab": "issuer", "categories": svc.ALL_CATEGORIES, "error": str(e)})
```

**Route: Market Share Timeline**
```python
@router.get("/share")
def share_timeline_view(request: Request):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/share_timeline.html", {"request": request, "available": False, "active_tab": "share"})
    try:
        timeline = svc.get_market_share_timeline()
        return templates.TemplateResponse("market/share_timeline.html", {
            "request": request, "available": True, "active_tab": "share", "timeline": timeline,
        })
    except Exception as e:
        log.error("Share timeline error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/share_timeline.html", {"request": request, "available": False, "active_tab": "share", "error": str(e)})
```

**Route: Underlier Deep-Dive**
```python
@router.get("/underlier")
def underlier_view(request: Request, type: str = Query(default="income"), underlier: str = Query(default=None)):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/underlier.html", {"request": request, "available": False, "active_tab": "underlier"})
    try:
        summary = svc.get_underlier_summary(type, underlier)
        return templates.TemplateResponse("market/underlier.html", {
            "request": request, "available": True, "active_tab": "underlier",
            "summary": summary, "underlier_type": type, "selected_underlier": underlier,
        })
    except Exception as e:
        log.error("Underlier view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/underlier.html", {"request": request, "available": False, "active_tab": "underlier", "error": str(e)})
```

**4 new API routes** (JSON, for AJAX):
```python
@router.get("/api/treemap")
def api_treemap(category: str = Query(default="All")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_treemap_data(cat))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/api/issuer")
def api_issuer(category: str = Query(default="All")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_issuer_summary(cat))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/api/share")
def api_share():
    try:
        return JSONResponse(_svc().get_market_share_timeline())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/api/underlier")
def api_underlier(type: str = Query(default="income"), underlier: str = Query(default=None)):
    try:
        return JSONResponse(_svc().get_underlier_summary(type, underlier))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
```

**Enhance existing REX View route**: Add `product_type: str = Query(default="All")` param. Pass it through to `get_rex_summary()`. In `get_rex_summary()`, if `product_type` in ("ETF", "ETN") and a `product_type` column exists in master data, filter before computing suites. Add `product_type=product_type` to the rex.html template context.

---

**Step 3 — Update market/base.html Nav**

Read `webapp/templates/market/base.html` first.

In the `.market-nav-pills` div, replace the existing 2 pills with 6 pills:
```html
<a href="/market/rex" class="pill {% if active_tab == 'rex' %}active{% endif %}">REX View</a>
<a href="/market/category" class="pill {% if active_tab == 'category' %}active{% endif %}">Category View</a>
<a href="/market/treemap" class="pill {% if active_tab == 'treemap' %}active{% endif %}">Treemap</a>
<a href="/market/issuer" class="pill {% if active_tab == 'issuer' %}active{% endif %}">Issuer Analysis</a>
<a href="/market/share" class="pill {% if active_tab == 'share' %}active{% endif %}">Market Share</a>
<a href="/market/underlier" class="pill {% if active_tab == 'underlier' %}active{% endif %}">Underlier</a>
```

---

**Acceptance Criteria**:
- [ ] `get_treemap_data()`, `get_issuer_summary()`, `get_market_share_timeline()`, `get_underlier_summary()` all added to market_data.py
- [ ] `get_rex_summary()` suites now include `sparkline_data` (list of 4 floats)
- [ ] 4 new GET routes in market.py: /market/treemap, /market/issuer, /market/share, /market/underlier
- [ ] 4 new API routes: /market/api/treemap, /market/api/issuer, /market/api/share, /market/api/underlier
- [ ] market/base.html nav has 6 pills linking to all 6 market pages
- [ ] All routes pass `available`, `active_tab`, and data dicts to templates
- [ ] Existing routes (/market/rex, /market/category) and API endpoints are unchanged

---

## Status: IN_PROGRESS

## Log:

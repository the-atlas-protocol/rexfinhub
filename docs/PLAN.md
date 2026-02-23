# REX Intelligence Hub - Development Plan

**Created**: 2026-02-18
**Project**: C:\Projects\rexfinhub
**Vision**: Transform the ETP Tracker into REX's central intelligence platform

---

## Vision Statement

The REX Intelligence Hub consolidates competitive intelligence, regulatory filings, and market data into one platform. Product teams and executives can answer:

- "How is REX performing across our suites?"
- "Where do we rank against competitors in each category?"
- "What's happening in the market?"

This plan covers **Phase 1: Market Intelligence Module** - two views built from The Dashboard.xlsx data.

---

## Architecture Overview

```
REX Intelligence Hub
├── Filings (existing)        → Regulatory intelligence
├── Fund Details (existing)   → Product deep dives
├── Market Intelligence (NEW) → Competitive landscape
│   ├── REX View             → Suite-by-suite REX performance
│   └── Category View        → Market analysis with dynamic filters
├── Screener (existing)       → Quantitative analysis
└── Reports (future)          → Automated email summaries
```

---

## Agent Assignment

| Agent | Branch | Scope |
|-------|--------|-------|
| Agent 1 | `feature/market-intelligence` | Market Intelligence module (REX View + Category View) |
| Agent 2 | `feature/webapp-fixes` | Screener fix, Downloads pagination, 33 Act identification |

---

# Agent 1: Market Intelligence Module

## Overview

Build `/market/` routes with two primary views:

1. **REX View** (`/market/rex`) - How REX is performing by suite
2. **Category View** (`/market/category`) - Market landscape with dynamic filters

Both views share common KPIs and chart patterns but serve different questions.

> **Note**: `/screener/market` already exists with a basic market landscape (top 2x ETFs by AUM,
> most popular underliers, market snapshot KPIs). The new `/market/` module is a separate,
> more comprehensive competitive intelligence tool using `The Dashboard.xlsx` data - not a
> replacement for the screener market tab. Do not duplicate or modify the existing screener.

---

## Data Source

**File**: `data/DASHBOARD/The Dashboard.xlsx` (already exists, 15.8 MB)

### Sheet Reference

| Sheet | Purpose | Key Columns |
|-------|---------|-------------|
| `q_master_data` | Fund universe (5,078 funds) | All metrics, classifications, 36-month AUM history |
| `q_aum_time_series_labeled` | Time series for charts | ticker, date, aum_value, category_display, is_rex |
| `dim_fund_category` | Fund-to-category mapping | ticker, category_display, issuer_display, fund_type |
| `rex_funds` | REX product list | ticker, fund_type |

### Key Column Groups in q_master_data

**Identifiers**:
- `ticker` - Bloomberg ticker (e.g., "MSTU US")
- `fund_name` - Full product name
- `is_rex` - Boolean, True for REX products (90 funds)

**Classification**:
- `etp_category` - Short code: LI, Defined, Thematic, CC, Crypto (1,887 funds have this)
- `category_display` - Human-readable: "Leverage & Inverse - Single Stock", etc.
- `issuer_display` - Normalized issuer name: "REX", "YieldMax", "ProShares", etc.

**Metrics (prefix indicates source table)**:
- `t_w4.aum` - Current AUM
- `t_w4.fund_flow_1day`, `t_w4.fund_flow_1week`, `t_w4.fund_flow_1month`, `t_w4.fund_flow_3month`
- `t_w4.aum_1` through `t_w4.aum_36` - Historical AUM (months ago)
- `t_w3.total_return_1week`, `t_w3.total_return_1month`, etc.
- `t_w3.annualized_yield`
- `t_w2.expense_ratio`, `t_w2.average_vol_30day`

**Category Attributes (for dynamic slicers)**:
- `q_category_attributes.map_crypto_is_spot` - "Spot Single Asset", "Derivatives-based; leveraged", etc.
- `q_category_attributes.map_crypto_underlier` - "BTC only", "alt-coin only (ETH)", etc.
- `q_category_attributes.map_cc_underlier` - "NVDA US", "TSLA US", etc.
- `q_category_attributes.map_cc_index` - "SPX", "NDX", "Basket", etc.
- `q_category_attributes.map_li_direction` - "Long", "Short", "Tactical"
- `q_category_attributes.map_li_leverage_amount` - 2.0, 3.0, etc.
- `q_category_attributes.map_li_category` - "Equity", "Crypto", "Commodity", etc.
- `q_category_attributes.map_li_underlier` - Specific underlier ticker
- `q_category_attributes.map_defined_category` - "Buffer", "Ladder", "Shield", "Floor", etc.
- `q_category_attributes.map_thematic_category` - "Innovation", "Healthcare", "Fintech", etc.

### REX Suite Mapping

REX products (`is_rex == True`) fall into these `category_display` values:

| Suite | Count | category_display |
|-------|-------|------------------|
| Leverage & Inverse - Single Stock | 41 | "Leverage & Inverse - Single Stock" |
| Leverage & Inverse - Index/ETF | 17 | "Leverage & Inverse - Index/Basket/ETF Based" |
| Crypto | 15 | "Crypto" |
| Income - Single Stock | 9 | "Income - Single Stock" |
| Income - Index/ETF | 4 | "Income - Index/Basket/ETF Based" |
| Thematic | 2 | "Thematic" |

---

## KPIs (Used in Both Views)

All views should display these consistently:

| KPI | Calculation | Format |
|-----|-------------|--------|
| Total AUM | Sum of `t_w4.aum` | "$X.XB" or "$XXX.XM" |
| Weekly Flows | Sum of `t_w4.fund_flow_1week` | "+$X.XM" or "-$X.XM" |
| Monthly Flows | Sum of `t_w4.fund_flow_1month` | "+$X.XM" or "-$X.XM" |
| 3-Month Flows | Sum of `t_w4.fund_flow_3month` | "+$X.XM" or "-$X.XM" |
| # Products | Count of funds | "XX" |
| Market Share | REX AUM / Category AUM × 100 | "X.X%" |

---

## View 1: REX View (`/market/rex`)

### Purpose
Executive dashboard showing REX performance broken down by suite.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ REX Intelligence Hub - REX View                    [date]   │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │
│ │Total AUM│ │1W Flows │ │1M Flows │ │3M Flows │ │# Products│ │
│ │ $X.XB   │ │ +$XXM   │ │ +$XXM   │ │ +$XXM   │ │   90    │ │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ │
├─────────────────────────────────────────────────────────────┤
│ [AUM by Suite - Pie Chart]    │ [Total AUM Trend - Line]    │
├─────────────────────────────────────────────────────────────┤
│ ┌─── Income - Single Stock ────────────────────────────────┐│
│ │ AUM: $XXM  │  1W: +$XM  │  Share: X.X%  │  # Products: 9 ││
│ │ Top: FEPI (+$5M), AIPI (+$2M), CEPI (-$1M)               ││
│ └──────────────────────────────────────────────────────────┘│
│ ┌─── Income - Index/ETF ───────────────────────────────────┐│
│ │ AUM: $XXM  │  1W: +$XM  │  Share: X.X%  │  # Products: 4 ││
│ │ Top: ...                                                 ││
│ └──────────────────────────────────────────────────────────┘│
│ ... (repeat for all 6 suites)                               │
└─────────────────────────────────────────────────────────────┘
```

### Components

1. **KPI Cards** (top row)
   - Total REX AUM (all 90 products)
   - Total 1-Week Flows
   - Total 1-Month Flows
   - Total 3-Month Flows
   - Total # Products

2. **AUM by Suite Pie Chart**
   - 6 slices, one per suite
   - Show $ amount and % in tooltip
   - REX brand colors if available

3. **Total AUM Trend Line Chart**
   - X-axis: Last 12-24 months
   - Y-axis: Total REX AUM
   - Use `q_aum_time_series_labeled` filtered to `is_rex == True`, aggregated by date

4. **Suite Cards** (6 cards, one per category_display where REX competes)
   Each card shows:
   - Suite name
   - AUM total for REX products in that suite
   - 1-Week flows for that suite
   - Market share (REX suite AUM / total category AUM)
   - # REX products in suite
   - Top 3 movers (by 1-week flow, positive and negative)
   - Click to expand or link to Category View filtered to that category

### Interactivity

- Clicking a suite card navigates to Category View with that category pre-selected
- Time period toggle (optional): 1W / 1M / 3M for flow metrics
- Hover on charts shows detailed tooltips

---

## View 2: Category View (`/market/category`)

### Purpose
Analyze any ETP category, see competitive landscape, understand REX's position.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ REX Intelligence Hub - Category View               [date]   │
├─────────────────────────────────────────────────────────────┤
│ Category: [Dropdown: All | Crypto | CC | LI | Defined | ...]│
│ ┌─── Dynamic Filters (appear based on category) ──────────┐ │
│ │ [Spot/Derivatives ▼] [Underlier ▼] [Direction ▼] ...    │ │
│ └──────────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────────┤
│ CATEGORY TOTALS                     │ REX IN THIS CATEGORY  │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ │ ┌─────────┐ ┌───────┐ │
│ │Total AUM│ │1W Flows │ │# Funds  │ │ │REX AUM  │ │Share %│ │
│ │ $X.XB   │ │ +$XXM   │ │  173    │ │ │ $XXM    │ │ X.X%  │ │
│ └─────────┘ └─────────┘ └─────────┘ │ └─────────┘ └───────┘ │
├─────────────────────────────────────────────────────────────┤
│ [Market Share by Issuer - Bar]  │ [Category AUM Trend-Line] │
├─────────────────────────────────────────────────────────────┤
│ TOP PRODUCTS (by AUM)                                       │
│ ┌───┬────────┬─────────────────────────┬───────┬──────────┐ │
│ │Rnk│ Ticker │ Fund Name               │  AUM  │ 1W Flow  │ │
│ ├───┼────────┼─────────────────────────┼───────┼──────────┤ │
│ │ 1 │IBIT US │ iShares Bitcoin Trust   │$52.4B │  -$1.2B  │ │
│ │ 2 │FBTC US │ Fidelity Wise Origin... │$18.2B │  -$400M  │ │
│ │...│        │                         │       │          │ │
│ │ 8 │SSK US  │ REX-Osprey SOL+Staking │ $50M  │   +$5M   │ │ ← REX highlighted
│ └───┴────────┴─────────────────────────┴───────┴──────────┘ │
├─────────────────────────────────────────────────────────────┤
│ REX PRODUCTS IN THIS CATEGORY                               │
│ (Table of REX products with rank, AUM, flows, yield, etc.)  │
└─────────────────────────────────────────────────────────────┘
```

### Dynamic Slicer Logic

When a category is selected, show relevant filters:

```python
CATEGORY_SLICERS = {
    "Crypto": [
        {"field": "q_category_attributes.map_crypto_is_spot", "label": "Type", "type": "dropdown"},
        {"field": "q_category_attributes.map_crypto_underlier", "label": "Underlier", "type": "multi-select"},
    ],
    "Income - Single Stock": [
        {"field": "q_category_attributes.map_cc_underlier", "label": "Underlier", "type": "multi-select"},
    ],
    "Income - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_cc_index", "label": "Index", "type": "multi-select"},
    ],
    "Leverage & Inverse - Single Stock": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_underlier", "label": "Underlier", "type": "multi-select"},
    ],
    "Leverage & Inverse - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_category", "label": "Asset Class", "type": "dropdown"},
    ],
    "Defined Outcome": [
        {"field": "q_category_attributes.map_defined_category", "label": "Outcome Type", "type": "dropdown"},
    ],
    "Thematic": [
        {"field": "q_category_attributes.map_thematic_category", "label": "Theme", "type": "multi-select"},
    ],
}
```

### Components

1. **Category Selector** (dropdown at top)
   - Options: All Categories, Crypto, Income - Single Stock, Income - Index/ETF, Leverage & Inverse - Single Stock, Leverage & Inverse - Index/ETF, Defined Outcome, Thematic

2. **Dynamic Slicer Panel**
   - Appears below category selector
   - Shows only filters relevant to selected category
   - Filters update all data on the page via AJAX

3. **KPI Cards - Split View**
   - Left: Category totals (all funds in filtered category)
   - Right: REX totals (just REX funds in filtered category) + Market Share %

4. **Market Share by Issuer Bar Chart**
   - Horizontal bars
   - Top 10-15 issuers by AUM in this category
   - REX bar highlighted in brand color

5. **Category AUM Trend Line Chart**
   - Total category AUM over time
   - Optional: overlay REX AUM as second line

6. **Top Products Table**
   - All products in category, sorted by AUM
   - Columns: Rank, Ticker, Fund Name, Issuer, AUM, 1W Flow, 1M Flow, Yield (if applicable)
   - REX products highlighted with background color
   - Pagination or "Show Top 20 / Show All" toggle

7. **REX Products Panel**
   - Filtered to just REX products in this category
   - Shows rank within category, performance vs category average

### Interactivity

- Category dropdown triggers page update (can be full reload or AJAX)
- Slicer changes trigger AJAX update of all charts/tables
- Table sorting by clicking column headers
- Hover tooltips on charts

---

## Files to Create

### 1. Data Service
**Path**: `webapp/services/market_data.py`

```python
"""
Market Intelligence data loader and cache.

Loads The Dashboard.xlsx and provides:
- get_master_data() -> Full fund universe
- get_rex_data() -> REX products only
- get_category_data(category, filters) -> Filtered category data
- get_time_series(tickers) -> AUM time series
- get_kpis(df) -> Calculate standard KPIs from a dataframe
- get_category_slicers(category) -> Return available filter options
"""
from pathlib import Path
import pandas as pd
import threading
import time
from functools import lru_cache

DATA_FILE = Path("data/DASHBOARD/The Dashboard.xlsx")

# Cache with 1-hour TTL (data updates daily)
_cache = {}
_cache_lock = threading.Lock()
_cache_time = 0
CACHE_TTL = 3600

def _load_fresh():
    """Load all sheets from Excel."""
    pass

def get_master_data() -> pd.DataFrame:
    """Return full q_master_data."""
    pass

def get_rex_summary() -> dict:
    """Return REX totals and by-suite breakdown."""
    pass

def get_category_summary(category: str, filters: dict = None) -> dict:
    """Return category totals, REX share, top products."""
    pass

def get_slicer_options(category: str) -> dict:
    """Return available filter values for a category."""
    pass

def get_time_series(category: str = None, is_rex: bool = None) -> pd.DataFrame:
    """Return time series data for charts."""
    pass

def invalidate_cache():
    """Clear cache (call from admin if needed)."""
    pass
```

### 2. Router
**Path**: `webapp/routers/market.py`

```python
"""
Market Intelligence routes.

Routes:
- GET /market/ -> Redirect to /market/rex
- GET /market/rex -> REX View
- GET /market/category -> Category View
- GET /market/api/rex-summary -> JSON for REX View
- GET /market/api/category-summary -> JSON for Category View (with filters)
- GET /market/api/time-series -> JSON for charts
- GET /market/api/slicers/{category} -> JSON filter options
"""
from fastapi import APIRouter, Query
from webapp.services import market_data

router = APIRouter(prefix="/market", tags=["market"])

@router.get("/")
async def market_index():
    return RedirectResponse("/market/rex")

@router.get("/rex")
async def rex_view(request: Request):
    """REX View - suite-by-suite performance."""
    pass

@router.get("/category")
async def category_view(request: Request, cat: str = None):
    """Category View - competitive landscape."""
    pass

# API endpoints for AJAX updates
@router.get("/api/rex-summary")
async def api_rex_summary():
    pass

@router.get("/api/category-summary")
async def api_category_summary(
    category: str = Query(...),
    filters: str = Query(None)  # JSON-encoded filter dict
):
    pass

@router.get("/api/slicers/{category}")
async def api_slicers(category: str):
    pass
```

### 3. Templates

**Path**: `webapp/templates/market/`

Create these templates:
- `base.html` - Shared layout with nav pills (REX View | Category View)
- `rex.html` - REX View layout
- `category.html` - Category View layout
- `_kpi_cards.html` - Reusable KPI card partial
- `_suite_card.html` - Reusable suite card partial
- `_product_table.html` - Reusable product table partial

### 4. Static Assets

**Path**: `webapp/static/js/market.js`

```javascript
/**
 * Market Intelligence interactive charts and filters.
 * 
 * Uses Chart.js for:
 * - Pie chart (AUM by suite)
 * - Bar chart (market share by issuer)
 * - Line chart (AUM trends)
 * 
 * Handles:
 * - Dynamic slicer loading based on category
 * - AJAX updates when filters change
 * - Table sorting
 */
```

**Path**: `webapp/static/css/market.css`

```css
/* 
 * Market Intelligence styles.
 * - KPI card styling
 * - Suite card styling  
 * - REX product highlighting in tables
 * - Chart containers
 * - Responsive layout
 */
```

### 5. Register Router
**Edit**: `webapp/main.py`

```python
from webapp.routers import market
app.include_router(market.router)
```

### 6. Add Navigation
**Edit**: `webapp/templates/base.html`

Add "Market" link to main nav, positioned prominently (this is a key feature).

---

## Chart Library

Use **Chart.js** (CDN). Include in market templates:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
```

Chart types needed:
- `pie` / `doughnut` - AUM by suite
- `bar` (horizontal) - Market share by issuer
- `line` - AUM trends over time

---

## Design Guidelines

### Colors
- REX products: Use a distinct highlight color (suggest: #1E40AF blue or brand color)
- Positive flows: Green (#10B981)
- Negative flows: Red (#EF4444)
- Neutral/other issuers: Gray scale

### Typography
- KPI numbers: Large, bold
- Labels: Smaller, muted
- Tables: Compact but readable

### Responsiveness
- KPI cards should wrap on mobile
- Charts should resize
- Tables should scroll horizontally on small screens

---

## Acceptance Criteria

### REX View
- [ ] Loads at `/market/rex` without errors
- [ ] Shows correct total REX AUM (sum of all 90 products)
- [ ] Shows 6 suite cards with correct metrics
- [ ] Pie chart renders with correct proportions
- [ ] Line chart shows historical trend
- [ ] Suite cards link to Category View

### Category View
- [ ] Category dropdown works, defaults to "All" or first category
- [ ] Dynamic slicers appear/disappear based on category
- [ ] Filters update data via AJAX (no full page reload)
- [ ] KPI cards show both category totals and REX share
- [ ] Top products table shows rankings
- [ ] REX products highlighted in table
- [ ] Market share bar chart renders correctly

### Data Accuracy
- [ ] KPIs match Excel calculations
- [ ] Market share % is correct (REX AUM / Category AUM)
- [ ] Time series chart matches q_aum_time_series_labeled data
- [ ] Filter combinations return correct subsets

### Performance
- [ ] Initial page load < 3 seconds
- [ ] Filter changes update < 1 second
- [ ] Data cached appropriately (1-hour TTL)

---

## Future: Automated Emails (Not in Scope, But Design For)

The data service should be structured so a future email module can:

```python
from webapp.services.market_data import get_rex_summary, get_category_summary

def generate_weekly_email():
    rex = get_rex_summary()
    categories = [get_category_summary(cat) for cat in CATEGORIES]
    
    # Render HTML template with charts
    # Send via email service
```

Email will be HTML with inline charts, linking back to `/market/rex` and `/market/category`.

---

## Do NOT Touch

- `webapp/routers/screener.py` - Agent 2's scope
- `etp_tracker/*` - Pipeline code
- `screener/*` - Screener module
- `data/SCREENER/` - Screener data

---


# Agent 2: Webapp Fixes & Improvements

## Overview

Fix existing issues and improve UX while Agent 1 builds Market Intelligence.

---

## Task 1: Fix Screener Cache Persistence (HIGHEST PRIORITY)

### Problem
Screener shows "No Bloomberg Data" on Render after deploys/restarts, even though the data file exists.

### Root Cause (Investigated 2026-02-19)
The code works correctly on local. The issue is **Render-specific**:
1. `_data_available()` returns True (file exists on disk) - this is correct
2. `_get_3x_data()` calls `get_3x_analysis()` which returns **None** (empty in-memory cache)
3. `compute_and_cache()` is triggered but takes **6-9 seconds** to load data + run analysis
4. Render's HTTP timeout kills the request before computation finishes
5. Exception is silently caught, template receives `analysis=None`, shows "No Bloomberg Data"

The in-memory cache (`screener_3x_cache.py`) is lost on every Render restart/deploy.

### Fix Approach
1. **Add disk-based cache**: Save analysis results to `data/SCREENER/cache.pkl` after computation
2. **Load from disk on startup**: Check for disk cache before recomputing from scratch
3. **Add startup pre-computation**: FastAPI `@app.on_event("startup")` hook to warm the cache
4. **Fix error message**: When `data_available=True` but `analysis=None`, show "Data is loading..." not "No Bloomberg Data"

### Files to Edit
- `webapp/services/screener_3x_cache.py` - Add disk persistence (pickle/JSON) and disk cache loading
- `webapp/main.py` - Add startup event to pre-warm screener cache
- `webapp/routers/screener.py` - Fix template context when cache is warming up

---

## Task 2: Downloads Page UI Improvements

### Problem
Too many funds (7,000+) displayed on one page. Overwhelming and slow.

### Location
- `webapp/routers/downloads.py`
- `webapp/templates/downloads.html`

### Improvements
1. **Pagination**: 50 funds per page with page navigation
2. **Search Box**: Filter by fund name, ticker, or trust name
3. **Category Collapse**: Group by category with expandable sections
4. **Quick Stats**: Show total count, filtered count

### Implementation Notes
- Search should be client-side for speed (data is already loaded)
- Pagination can be client-side or server-side
- Consider using a lightweight table library if needed

---

## Task 3: Identify 33 Act Products (Scope: Identification Only)

### Problem
Some trusts file N-1A forms (1933 Act) instead of 485 forms (1940 Act). These show as "no ETFs" on dashboard.

### Approach (Minimal Scope)
1. Query SEC EDGAR for each trust in `etp_tracker/trusts.py`
2. Check if they have 485 filings OR N-1A filings
3. Create a mapping: `{cik: "1940" | "1933"}`
4. Update dashboard display to show "33 Act Filer - N-1A" instead of "No 485 filings"

### Do NOT (Out of Scope)
- Build full N-1A parsing pipeline
- Extract fund data from N-1A forms
- This is identification only, not full integration

### Files to Edit
- `etp_tracker/trusts.py` - Add `act_type` field or create separate mapping
- `webapp/routers/dashboard.py` - Update display logic

---

## Task 4: Background Loading Indicator (Polish)

### Problem
Dashboard loads 122 trust cards synchronously. No visual feedback during load.

### Solution
Add loading skeleton or spinner while trust data loads.

### Implementation
1. Add CSS skeleton animation styles
2. Show skeleton cards initially
3. Replace with real data when loaded
4. Can use JavaScript fetch or keep server-side rendering with loading state

### Files to Edit
- `webapp/templates/dashboard.html`
- `webapp/static/css/style.css`

---

## Task Priority Order

1. **Screener fix** - Broken feature, highest priority
2. **Downloads pagination** - UX improvement, high impact
3. **33 Act identification** - Data accuracy, medium priority
4. **Loading indicator** - Polish, lower priority

---

## Do NOT Touch

- `webapp/routers/market.py` - Agent 1's scope
- `webapp/services/market_data.py` - Agent 1's scope
- `webapp/templates/market/` - Agent 1's scope
- `data/DASHBOARD/` - Agent 1's data

---

# Execution via Dev Orchestrator

## Option A: VS Code Chat Panel (Recommended)

1. Open VS Code Chat panel (`Ctrl+Alt+I`)
2. Select **Claude Opus 4.6** from model picker
3. Select **Orchestrator** from agent dropdown
4. Type:
   > Build Market Intelligence module (Agent 1 scope) and fix webapp issues (Agent 2 scope) per docs/PLAN.md
5. Orchestrator plans tasks, creates worktrees in `.worktrees/`, spawns parallel workers
6. Check progress: ask "What's the status?"
7. When done: ask "Merge the completed work"

## Option B: CLI (Terminal)

```bash
cd C:\Projects\rexfinhub

# Plan both agents' work
orchestrate plan "Build Market Intelligence module and fix webapp issues per docs/PLAN.md"

# Spawn parallel workers (worktrees created automatically in .worktrees/)
orchestrate run

# Check progress
orchestrate status

# Merge when all agents are DONE
orchestrate merge

# Push
git push origin main
```

## Option C: Run Agents Separately

For more control, run one agent at a time:

```bash
# Agent 1 only
orchestrate plan "Build Market Intelligence module per docs/PLAN.md Agent 1 scope"
orchestrate run

# Wait for completion, then Agent 2
orchestrate plan "Fix webapp issues per docs/PLAN.md Agent 2 scope"
orchestrate run

# Merge all
orchestrate merge
```

## Data File

`data/DASHBOARD/The Dashboard.xlsx` already exists in the project root (15.8 MB).
Worktrees in `.worktrees/` share the same git history - no need to copy data files.

---

# Estimated Timeline

| Agent | Tasks | Estimated Hours |
|-------|-------|-----------------|
| Agent 1 | Market Intelligence (REX View + Category View) | 10-14 hours |
| Agent 2 | Screener fix + Downloads + 33 Act + Loading | 6-8 hours |

Agent 2 will likely finish first. They can then assist Agent 1 or start on stretch tasks.

---

# Success Metrics

## Agent 1 (Market Intelligence)
- [ ] `/market/rex` displays all 90 REX products with correct KPIs
- [ ] 6 suite cards show accurate breakdown
- [ ] `/market/category` allows filtering by all 7 categories
- [ ] Dynamic slicers work for each category
- [ ] REX products highlighted in peer tables
- [ ] Charts render correctly with real data

## Agent 2 (Webapp Fixes)
- [ ] `/screener/` shows actual data, not "No Bloomberg Data"
- [ ] Downloads page loads quickly with pagination
- [ ] 33 Act trusts identified and flagged in dashboard
- [ ] Dashboard shows loading indicator while trust cards load

---

# Questions? Issues?

If agents encounter blockers:
1. Check this PLAN.md first
2. Refer to existing codebase patterns
3. Document assumptions in code comments
4. If truly stuck, leave TODO comments and move on

The goal is working software, not perfection. Ship it, then iterate.

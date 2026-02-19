# MARKET Agent - Market Intelligence Module

## Status: NOT STARTED

## Mission
Build the Market Intelligence module with two views:
1. **REX View** (`/market/rex`) - Executive dashboard showing REX performance by suite
2. **Category View** (`/market/category`) - Competitive landscape with dynamic filters

## My Files (I own these)
```
webapp/routers/market.py          # Create - Routes
webapp/services/market_data.py    # Create - Data loading & cache
webapp/templates/market/          # Create folder
  ├── base.html                   # Shared layout with nav pills
  ├── rex.html                    # REX View template
  ├── category.html               # Category View template
  ├── _kpi_cards.html             # Reusable KPI partial
  ├── _suite_card.html            # Reusable suite card partial
  └── _product_table.html         # Reusable table partial
webapp/static/js/market.js        # Create - Chart.js interactions
webapp/static/css/market.css      # Create - Styling
```

## Shared Files (Coordinate)
```
webapp/main.py                    # Add: from webapp.routers import market
                                  #      app.include_router(market.router)
webapp/templates/base.html        # Add: "Market" nav link
```

## Do Not Touch
- `webapp/routers/screener.py` (FIXES agent)
- `webapp/routers/downloads.py` (FIXES agent)
- `data/SCREENER/*` (FIXES agent)
- `etp_tracker/*` (pipeline code)
- `screener/*` (screener module)

## Data Source
**File**: `data/DASHBOARD/The Dashboard.xlsx`

### Key Sheets
| Sheet | Rows | Purpose |
|-------|------|---------|
| `q_master_data` | 5,078 | Fund universe with all metrics |
| `q_aum_time_series_labeled` | 69,820 | Time series for charts |
| `dim_fund_category` | 1,888 | Fund-to-category mapping |
| `rex_funds` | 88 | REX product list |

### Critical Columns in q_master_data
- `is_rex` - Boolean, True for REX products (90 funds)
- `category_display` - Human-readable category name
- `etp_category` - Short code: LI, Defined, Thematic, CC, Crypto
- `issuer_display` - Normalized issuer name
- `t_w4.aum` - Current AUM
- `t_w4.fund_flow_1week`, `t_w4.fund_flow_1month`, `t_w4.fund_flow_3month`
- `q_category_attributes.*` - Dynamic slicer fields

### REX Suites (6 categories)
1. Leverage & Inverse - Single Stock (41 products)
2. Leverage & Inverse - Index/Basket/ETF Based (17 products)
3. Crypto (15 products)
4. Income - Single Stock (9 products)
5. Income - Index/Basket/ETF Based (4 products)
6. Thematic (2 products)

## KPIs to Display
| KPI | Calculation | Format |
|-----|-------------|--------|
| Total AUM | Sum of `t_w4.aum` | "$X.XB" or "$XXX.XM" |
| Weekly Flows | Sum of `t_w4.fund_flow_1week` | "+$X.XM" or "-$X.XM" |
| Monthly Flows | Sum of `t_w4.fund_flow_1month` | "+$X.XM" or "-$X.XM" |
| 3-Month Flows | Sum of `t_w4.fund_flow_3month` | "+$X.XM" or "-$X.XM" |
| # Products | Count of funds | "XX" |
| Market Share | REX AUM / Category AUM × 100 | "X.X%" |

## Current Task
> Not started - Begin by reading PLAN.md for full specifications

## Progress Log
- [ ] Read PLAN.md for complete specifications
- [ ] Create `webapp/services/market_data.py` with data loading
- [ ] Create `webapp/routers/market.py` with routes
- [ ] Create `webapp/templates/market/` folder
- [ ] Create REX View template and logic
- [ ] Create Category View template and logic
- [ ] Add dynamic slicers for each category
- [ ] Create Chart.js visualizations
- [ ] Register router in main.py
- [ ] Add nav link to base.html
- [ ] Test all routes

## Notes / Context for Next Session
- See PLAN.md for detailed component specifications
- Use Chart.js (CDN) for charts: pie, bar (horizontal), line
- REX products should be highlighted in distinct color (#1E40AF suggested)
- Positive flows: Green (#10B981), Negative flows: Red (#EF4444)
- Data should cache for 1 hour (updates daily)

## Blockers
None currently

---

## Reference: Dynamic Slicers by Category

```python
CATEGORY_SLICERS = {
    "Crypto": [
        {"field": "q_category_attributes.map_crypto_is_spot", "label": "Type"},
        {"field": "q_category_attributes.map_crypto_underlier", "label": "Underlier"},
    ],
    "Income - Single Stock": [
        {"field": "q_category_attributes.map_cc_underlier", "label": "Underlier"},
    ],
    "Income - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_cc_index", "label": "Index"},
    ],
    "Leverage & Inverse - Single Stock": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage"},
        {"field": "q_category_attributes.map_li_underlier", "label": "Underlier"},
    ],
    "Leverage & Inverse - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage"},
        {"field": "q_category_attributes.map_li_category", "label": "Asset Class"},
    ],
    "Defined Outcome": [
        {"field": "q_category_attributes.map_defined_category", "label": "Outcome Type"},
    ],
    "Thematic": [
        {"field": "q_category_attributes.map_thematic_category", "label": "Theme"},
    ],
}
```

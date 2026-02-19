# MARKET Agent Task

## Mission
Build Market Intelligence module with two views:
1. **REX View** (`/market/rex`) - Executive dashboard by suite
2. **Category View** (`/market/category`) - Competitive landscape with filters

## Status: NOT STARTED

## Files To Create
```
webapp/routers/market.py
webapp/services/market_data.py
webapp/templates/market/
  ├── base.html
  ├── rex.html
  └── category.html
webapp/static/js/market.js
webapp/static/css/market.css
```

## Data Source
`data/DASHBOARD/The Dashboard.xlsx`

Key sheets:
- `q_master_data` - 5,078 funds, all metrics
- `q_aum_time_series_labeled` - Time series for charts

Key columns:
- `is_rex` - Boolean for REX products (90 total)
- `category_display` - Human-readable category
- `t_w4.aum` - Current AUM
- `t_w4.fund_flow_1week/1month/3month` - Flows

## Full Specifications
Read `docs/PLAN.md` for complete specs including:
- Layout mockups
- KPI calculations
- Dynamic slicer logic
- Chart requirements

## Checklist
- [ ] Read docs/PLAN.md
- [ ] Create market_data.py service
- [ ] Create market.py router
- [ ] Create templates
- [ ] Add Chart.js visualizations
- [ ] Register router in webapp/main.py
- [ ] Add nav link in webapp/templates/base.html
- [ ] Test /market/rex
- [ ] Test /market/category

## Notes
(Update with progress and context for next session)

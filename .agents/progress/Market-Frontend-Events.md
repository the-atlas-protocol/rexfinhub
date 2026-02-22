# Market-Frontend-Events Progress

## 2026-02-21 — COMPLETED

### compare.html (TASK D.1)
- Added JS to strip " US" suffix from ticker input on form submit
- Added `totalrealreturns_url` link block (guarded with `is defined`)
- Added AUM Over Time line chart (Chart.js, guarded for `aum_history`)
- Added Flow Bar Chart with 1W/1M/3M/6M/YTD period toggle buttons (guarded for `flows`)
- Expanded comparison table: added Fund Type, Inception Date, 3M Flow, 6M Flow rows
- Kept existing metrics (1W/1M Return, Annualized Yield, 1D/1W/1M Flow, REX Product)
- Commit: 8c5bbae

### calendar.html (TASK D.2)
- Renamed from "Compliance Calendar" to "Fund Activity"
- Added two-column grid layout: Recent Launches + Upcoming Events
- Backward compatibility: supports both `recent_launches` (new) and `recently_effective` (old) context vars
- Handles both `filing.form_type` (new) and `filing.form` (old) field names
- Handles effective dates at both `filing.effective_date` and `item.effective_date` levels
- Commit: 2a28528

### timeline.html (TASK D.3)
- Renamed from "Fund Lifecycle Timeline" to "Filing History"
- Added educational subtitle explaining 485BPOS and 485BXT
- Added summary stats bar: BPOS count (green), BXT count (amber), total filings
- Color-coded timeline dots: 485BPOS=green, 485BXT=amber, other=gray
- Uses both CSS variable `--dot-color` and class-based fallback (`entry-bpos`, `entry-bxt`, `entry-other`)
- Backward compatibility guards for `form_type` vs `form` and effective date fields
- Commit: bc8ab41

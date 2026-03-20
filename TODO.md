# REXFINHUB -- Development Status

## Completed
- Home page pillar card links updated to match new page names (L&I Filing Landscape, LI Filing Candidates, LI Stock Evaluator)
- Base.html mega-menu already had correct names (updated in prior session)
- Screener subnav tabs already had correct names (updated in prior session)
- Filing section rename: L&I Filing Landscape, LI Filing Candidates, LI Stock Evaluator (commit a59f198)
- Merged 3x+4x+2x into single LI Filing Candidates page (commit 7d344ba)
- Render deployment prep: ownership conditional behind ENABLE_13F, notes path fallback (commit f40d999)
- Screener pages full style revamp: K-A KPIs, T-B tables, neutral filters (commit d8f9db5)
- Filing Explorer: merged funds+filings into unified search (commit eca5aea)
- Filing Activity page rebuild: clean, focused layout (commit edc46f3)
- Global CSS alignment with STYLE_GUIDE (commit 782de2b)
- Pipeline perf: async submissions + If-Modified-Since 304 skip (commits 4407258, b06c1ed)

## In Progress
- Nothing actively in-flight

## Next Up

### Market Section
- 14 templates exist (rex, category, issuer, issuer_detail, compare, underlier, treemap, share_timeline, calendar, plus partials)
- All routes mounted and functional locally
- Home page links to 4 pages: REX View, Category View, Issuer Analysis, Compare Products
- Mega-menu also exposes: Underlier Analysis, Launch Calendar, Market Monitor, Market Share (coming soon)
- No known issues; data depends on Bloomberg data.xlsx refresh cycle

### Ownership Section
- 13 intel templates + 5 holdings templates (holdings list, fund, institution, history, crossover)
- Gated behind `ENABLE_13F=1` env var (not enabled on Render)
- Home page links: Market Overview, REX Quarter Report, Browse Institutions, Sales Intelligence
- Data: 13F pipeline complete (960MB/quarter, 10,535 institutions) but too large for Render Starter plan
- Deployment strategy TBD: either PostgreSQL migration, pre-aggregated SQLite, or Render upgrade

### Structured Notes Section
- 3 templates: notes_overview, notes_issuers, notes_search
- Routes mounted unconditionally (with path fallback for missing DB on Render)
- DB at D:/sec-data/databases/structured_notes.db (423K products, 73% extracted, 19 issuers)
- Home page links: Market Overview, Issuer Dashboard, Product Search
- Local-only data; Render deployment needs DB upload or remote DB strategy

### Render Deployment
- Live at rexfinhub.com (Render URL: rex-etp-tracker.onrender.com)
- Auto-deploys on push to main
- Filing pipeline + screener: fully deployed and working
- Market section: deployed and working (depends on data.xlsx on persistent disk)
- Ownership (13F): NOT deployed (ENABLE_13F not set, data too large)
- Structured Notes: routes deployed but no data on Render (DB is local-only)
- Persistent disk: 1GB at /opt/render/project/src/data

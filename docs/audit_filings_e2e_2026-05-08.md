# /filings/{filing_id} — E2E audit (filing 628498)

**Surface:** `/filings/628498` (renamed from `/analysis/filing/628498`)
**Date:** 2026-05-08
**Status:** PASS

## Input -> output trace

```
1. DB layer
   SELECT * FROM filings WHERE id = 628498 LIMIT 1
   -> 1 row: form='485BPOS', filing_date=..., trust_id=...,
     accession_number='0001234567-XX-XXXXXX',
     primary_link='https://www.sec.gov/...'

   SELECT name FROM trusts WHERE id = trust_id
   SELECT * FROM fund_extractions WHERE filing_id = 628498
   SELECT * FROM analysis_results WHERE filing_id = 628498 ORDER BY ts DESC

2. Router layer
   /filings/{filing_id} (NEW v3 URL) -> webapp.routers.filings_detail.router
   -> add_api_route registers _filing_analysis_get_impl + _filing_analysis_post_impl
     (impls imported from webapp.routers.analysis.py)

   /analysis/filing/{filing_id} (LEGACY) -> 301 -> /filings/{filing_id}

3. Template layer
   analysis.html sections:
     Header: form type, filing date, accession number, trust name
     Meta grid: Form, date, text size (chars), estimated Claude cost,
                fund names extracted
     Run Analysis panel: radio buttons for ANALYSIS_TYPES, daily quota
                        counter (10/day cap), cost warning notice
     Previous Analyses: chronological list of prior Claude runs with
                        model, token counts, HTML-rendered result

4. HTTP response
   GET /filings/628498                    -> 200
   GET /analysis/filing/628498            -> 301 -> /filings/628498
   POST /filings/628498 (run analysis)    -> 200 -> renders updated page
   POST /analysis/filing/628498 (legacy)  -> 307 -> /filings/628498 POST
```

## Migration verification

The rename from `/analysis/filing/{id}` to `/filings/{id}` was specifically to fix the IA mismatch — `/analysis/` implied a dashboard or report surface, but this is a filing artifact viewer with an AI action attached.

Linked from 5 source pages (all updated in PR 4):
- `dashboard.html` — "Analyze" button column
- `filing_explorer.html` — "Analyze" link per row
- `filing_list.html` — "Analyze" button column
- `fund_detail.html` (now in 15-section merged template) — per-filing row in filing history
- `trust_detail.html` — per-filing row in Recent Filings

All 5 templates verified post-PR-4 to use `/filings/{id}` URL form.

## Cross-link verification

From `/filings/{filing_id}`:
- Trust link -> `/trusts/{slug}` (in header)
- Filing index links back -> `/sec/etp/filings` (filings explorer)
- View raw -> primary_link (SEC.gov direct)

## Pass criteria — met

- [x] /filings/628498 200 OK
- [x] Legacy /analysis/filing/628498 301s to /filings/628498
- [x] POST 308/307 preserves form body for "Run Analysis" submission
- [x] All 5 source templates updated to use /filings/{id} URL
- [x] Daily Claude quota tracking still works (cost warning + 10/day limit)
- [x] Previous analyses render with HTML formatting preserved
- [x] No /analysis/filing/ references remain in templates or JS (CI lint passes)

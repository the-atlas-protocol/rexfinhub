# /funds/{ticker} — E2E audit (NVDX)

**Surface:** `/funds/NVDX` (live REX flagship)
**Date:** 2026-05-08
**Status:** PASS

## Input -> output trace

```
1. DB layer
   SELECT * FROM mkt_master_data WHERE ticker_clean = 'NVDX' LIMIT 1
   -> 1 row, issuer_display='REX', primary_strategy='L&I',
     leverage='2.0', direction='Long', map_li_underlier='NVDA'

   SELECT * FROM fund_status WHERE ticker = 'NVDX' LIMIT 1
   -> 1 row, status='EFFECTIVE', series_id='S0000XXXXX',
     latest_form='485BPOS', latest_filing_date='2025-XX-XX'

2. Service layer
   webapp.services.market_data.get_master_data(db)
   -> 7,486-row DataFrame; canonicalization applied (12 AUTO map entries)
   -> NVDX row resolves with canonical issuer_display='REX'

3. Router layer
   webapp.routers.funds.fund_detail_by_ticker(ticker='NVDX', db)
   -> calls _load_sec_context(db, ticker='NVDX')
   -> calls _load_bloomberg_context(db, ticker='NVDX')
   -> renders fund_detail.html with merged context

4. Template layer
   fund_detail.html — 15 sections rendered:
     Section 1 breadcrumb (Home -> Filings -> Trust -> NVDX)
     Section 2 header (ticker badge, REX badge, fund name, issuer link)
     Section 3 action buttons (SEC Filings anchor, Compare)
     Section 4 SEC meta grid (Status, Effective Date, etc)
     Sections 5-11 Bloomberg DES (KPIs, taxonomy, returns, flows, charts)
     Section 12 Name History
     Section 13 Filing History (with form filter pills, View/Analyze)
     Section 14 13F scaffold (empty per design)
     Section 15 Competitors table

5. HTTP response
   GET /funds/NVDX -> 200, 98029 bytes

6. Cross-link verification (HTML inspection)
   /issuers/REX        -> in section 2 header (canonical link)
   /stocks/NVDA        -> in section 9 (underlier pill)
   /funds/{competitor} -> in section 15 (competitors table)
   /filings/{id}       -> in section 13 (Analyze buttons)
   /trusts/{slug}      -> in section 1 (breadcrumb)
```

## Edge cases verified

- **Bloomberg-only fund (NVDL):** Bloomberg sections 5-11+15 render, SEC sections 4+12+13 gracefully degrade to "no SEC row" message
- **Filed-only fund:** GET /funds/series/S000074123 -> 301 -> /funds/PISDX (transition to ticker URL once assigned)
- **Unknown ticker:** GET /funds/ZZZNOTAREALTICKER -> 404 (correct)

## Pass criteria — met

- [x] Page loads in 200 OK for 5 representative tickers (NVDX, DJTU, NVDL, JEPI, PISDX)
- [x] SEC breadcrumb preserved exactly (Ryu's explicit ask)
- [x] Bloomberg DES sections layered without breaking SEC story
- [x] Cross-links to /issuers/, /stocks/, /funds/{competitor}, /filings/{id}, /trusts/{slug} all present
- [x] Issuer link uses canonical name from canonicalization map
- [x] Competitor links land on `/funds/{competitor}` not `/market/fund/{competitor}`
- [x] Legacy `/funds/{series_id}` 301s correctly to /funds/{ticker} or /funds/series/{id}
- [x] Legacy `/market/fund/{ticker}` 301s to /funds/{ticker}

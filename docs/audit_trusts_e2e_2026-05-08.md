# /trusts/{slug} — E2E audit (Schwab Strategic Trust + index)

**Surface:** `/trusts/schwab-strategic-trust` (existing detail) + `/trusts/` (NEW PR 2d index)
**Date:** 2026-05-08
**Status:** PASS

## Input -> output trace

```
1. DB layer
   SELECT * FROM trusts WHERE slug = 'schwab-strategic-trust' LIMIT 1
   -> 1 row, name='Schwab Strategic Trust', cik='0001454889',
     regulatory_act='40_act', is_active=True

   SELECT * FROM fund_status WHERE trust_id = X
   -> N fund series rows (one per fund in the trust)

   SELECT * FROM filings WHERE trust_id = X ORDER BY filing_date DESC LIMIT 20
   -> 20 most recent filings

2. Router layer
   /trusts/{slug} -> webapp.routers.trusts.trust_detail
   -> Existing handler (verified intact post-PR 1)
   -> Sorts funds: PENDING -> DELAYED -> EFFECTIVE
   -> Computes status counts, expected effective dates

   /trusts/ -> webapp.routers.trusts.trusts_index (NEW PR 2d)
   -> Subquery for per-trust fund counts (total/effective/pending)
   -> Subquery for recent-filing counts (last 30 days)
   -> Sort: most-active first (recent filings desc, then total funds desc)

3. Template layer
   trust_detail.html (existing, verified working):
     Header (name + REX badge + CIK + fund count)
     KPI row (total/effective/pending/delayed)
     Funds table (each fund -> /funds/{series_id} or /funds/{ticker})
     Recent filings table (each -> /filings/{id})
     13F scaffold (dormant)

   trusts_index.html (NEW PR 2d):
     Search input (client-side JS filter)
     Trusts table: name (link to /trusts/{slug}), CIK, Act badge,
                   total funds, effective, pending, recent filings 30d

4. HTTP response
   GET /trusts/schwab-strategic-trust -> 200
   GET /trusts/                       -> 200
```

## Cross-link verification

Detail page (`/trusts/{slug}`):
- Funds table -> `/funds/{key}` (each fund row)
- Filings table -> `/filings/{filing_id}` (each filing row)
- Breadcrumb -> `/sec/etp/` (Filings dashboard)

Index page (`/trusts/`):
- Each trust name -> `/trusts/{slug}` (detail)

## Discoverability gap closed

Pre-PR-2d state: no `/trusts/` index existed — only the slug-detail route. Discovery happened only via incidental links from dashboard, fund_detail, filing_explorer, etc. Now `/trusts/` provides a canonical browse-all surface, mirroring the pattern of `/funds/`, `/issuers/`, `/stocks/`.

## Pass criteria — met

- [x] /trusts/{slug} 200 OK for schwab-strategic-trust + all REX trusts
- [x] /trusts/ index 200 OK with all 122 active trusts listed
- [x] Trust links from dashboard, fund_detail, filing_explorer, home all reach /trusts/{slug}
- [x] Funds table on detail page links to /funds/{key}
- [x] Recent filings table on detail page links to /filings/{id}
- [x] Index page sortable + filterable (client-side search)
- [x] No regressions to existing trust_detail.html (PR 1 didn't touch its routes; PR 4 only updated 'Analyze' link target to /filings/{id})

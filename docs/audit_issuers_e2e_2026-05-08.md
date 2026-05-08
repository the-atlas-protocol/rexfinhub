# /issuers/{name} — E2E audit (BlackRock — read-side canonicalization)

**Surface:** `/issuers/BlackRock` (the canonicalization fix)
**Date:** 2026-05-08
**Status:** PASS — plumbing verified; data scope decision deferred

## Input -> output trace

```
1. CSV ground truth
   config/rules/issuer_canonicalization.csv -> 12 AUTO rows
   (3 bad REVIEW rows already deleted in Phase 0.1)
   No 'iShares -> BlackRock' mapping currently — explicit data choice

2. Service layer (NEW — read-side canonicalization)
   webapp.services.market_data._get_issuer_canon_map()
   -> {variant: canonical} dict, AUTO-only filter, lifetime-cached

   webapp.services.market_data._apply_issuer_canonicalization(df)
   -> rewrites issuer_display in DataFrame at read time
   -> applied inside get_master_data() so all downstream consumers benefit

3. Router layer
   webapp.routers.issuers.issuer_detail(name='BlackRock', db)
   -> if name in canon_map: 301 to canonical (variant -> canonical redirect)
   -> else: filter master DataFrame by issuer_display == name
   -> compute total_aum, n_funds, category breakdown, AUM trend, fund roster

4. Template layer
   issuers/detail.html sections:
     Header (canonical name + REX badge + "Also known as: variants")
     KPI row (total_aum, n_funds, n_categories)
     12-month AUM trend chart (Chart.js)
     Category breakdown (donut + table)
     Full fund roster (links each /funds/{ticker})

5. HTTP response
   GET /issuers/BlackRock -> 200 (70 KB)
   47 fund-detail links rendered to /funds/{ticker}

6. Variant -> canonical redirect verified
   GET /issuers/iShares Delaware Trust Sponsor -> 301 -> /issuers/iShares
   GET /market/issuer/detail?issuer=BlackRock -> 301 -> /issuers/BlackRock
   GET /market/issuer/detail -> 301 -> /issuers/
```

## Bug fix verified

The original bug at `webapp/routers/market.py:288` (now obsolete):
```python
df = master[master[issuer_col].fillna("").str.strip() == issuer.strip()]
```
This exact-string match excluded variants. Now replaced by:
```python
# in services/market_data.py get_master_data():
df["issuer_display"] = df["issuer_display"].map(
    lambda v: canon_map.get(str(v).strip(), str(v).strip())
)
```
Applied at read time so variant labels in the DB resolve to canonical strings before any filter call.

## Important finding (deferred to task #89)

The "BlackRock shows 47 funds" complaint is ROOT-CAUSED to the Bloomberg ingestion scope, NOT the canonicalization gap:
- `mkt_master_data` has 47 rows literally labeled "BlackRock"
- `mkt_master_data` has 681 rows with "iShares" in fund_name BUT all are status='LIQU' with issuer=NULL
- Active iShares ETFs (~400 funds) are NOT in our curated DB
- Same gap for SPDR (State Street: 31 funds), Invesco (25 funds)

Decision needed: expand Bloomberg ingestion universe OR accept curated scope. See task #89.

## Pass criteria — met (plumbing) / deferred (data)

- [x] Canon map loaded with 12 AUTO entries (REVIEW rows excluded)
- [x] _apply_issuer_canonicalization called on get_master_data output
- [x] All read-side filter sites use canonicalized values
- [x] Variant URLs 301 to canonical
- [x] Legacy /market/issuer/detail 301s correctly
- [x] /issuers/ index renders ranked by AUM
- [ ] BlackRock count >100 — DEFERRED until task #89 expands ingestion

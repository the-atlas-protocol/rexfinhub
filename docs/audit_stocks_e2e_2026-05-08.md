# /stocks/{ticker} — E2E audit (NVDA + RXT)

**Surface:** `/stocks/NVDA` (popular underlier) and `/stocks/RXT` (parquet whitespace ticker)
**Date:** 2026-05-08
**Status:** PASS

## Input -> output trace

```
1. Parquet inputs
   data/analysis/whitespace_v4.parquet      (~200-600 rows, indexed by ticker)
   data/analysis/launch_candidates.parquet  (~15-50 rows)
   data/analysis/filed_underliers.parquet   (~300-800 rows)

2. DB layer
   SELECT ticker, fund_name, issuer_display, aum, map_li_*, map_cc_*
   FROM mkt_master_data
   WHERE UPPER(TRIM(map_li_underlier)) = 'NVDA' OR
         UPPER(TRIM(map_cc_underlier)) = 'NVDA'
   -> ETP coverage rows (every ETP that tracks NVDA as underlier)

3. Router layer
   webapp.routers.stocks.stock_detail(ticker='NVDA', db)
   -> Load 3 parquets, look up ticker in whitespace + launch indices
   -> Run SQL for ETP coverage from mkt_master_data
   -> Look up filed_underliers.parquet for n_filings_total

4. Template layer
   stocks/detail.html sections:
     Header (ticker, name, sector, exchange, market_cap)
     Signal panel (composite_score, score_pct, mentions+z, RVol, returns,
                   SI, insider/inst, theme tags, HOT THEME badge if applicable)
     ETP coverage table (links each row to /funds/{ticker})
     Filing whitespace panel (n_filings_total, last_filing_date)
     "No signals yet" banner if no parquet data + no ETP coverage

5. HTTP responses verified
   /stocks/NVDA          -> 200 (38001 bytes) — ETP coverage + filing whitespace
                             (no parquet signal: NVDA already heavily covered)
   /stocks/MSTR          -> 200 (36937 bytes) — ETP coverage
   /stocks/TSLA          -> 200 (37999 bytes) — ETP coverage
   /stocks/UNKNOWN_XYZ   -> 200 (36591 bytes) — "No signals yet" banner
   /stocks/RXT           -> 200 — Full Signal panel renders (RXT is a top
                             whitespace_v4 candidate; verified all KPIs +
                             z-scores present)
   /market/stocks/       -> 200 (248845 bytes) — full index, filters working
```

## Cross-link verification

Each ETP coverage row links to:
- `/funds/{ticker_clean}` — strips " US" suffix from Bloomberg ticker
- `/issuers/{issuer_display}` — canonicalized issuer

Index page rows link to `/stocks/{ticker}` for detail.

## Pass criteria — met

- [x] /stocks/{ticker} 200 OK for all test tickers (popular + whitespace + unknown)
- [x] Signal panel populates from whitespace_v4 OR launch_candidates
- [x] ETP coverage table populates from mkt_master_data joined on map_li_underlier / map_cc_underlier
- [x] Filing whitespace populates from filed_underliers.parquet
- [x] Graceful "No signals yet" banner when no parquet + no ETP coverage
- [x] Cross-links to /funds/{ticker} and /issuers/{name} on every row
- [x] /market/stocks/ index page renders ~all stocks with filter UI
- [x] Sort by composite_score desc; tickers without signals fall to bottom but remain browseable

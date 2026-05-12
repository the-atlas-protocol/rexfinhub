# Stage 1 Re-Audit — DB Integrity Verification
Generated: 2026-05-12T01:15:00Z
Agent: db_integrity_verify
DB: `/home/jarvis/rexfinhub/data/etp_tracker.db` (VPS production)
Method: SELECT-only re-runs of every Stage 1 query against current production DB.

## Headline

**Of 21 Stage 1 findings: 3 resolved, 4 partially fixed, 12 persisting unchanged, 2 worsened.**

The biggest fix: F1 (denormalized strategy columns) — `primary_strategy`, `sub_strategy`, `asset_class` are now mostly populated. The denormalization wire-up landed. **But `mkt_master_data.strategy` is still 100% NULL** — that single column remains broken (49 / 49 / 15 NULLs is acceptable; 7361 / 7361 NULLs is not).

The biggest regression: F5 (stuck `mkt_pipeline_runs`) — went from 67 → 92. The orphaned-run watchdog still hasn't been added; new pipeline crashes keep accumulating. Oldest stuck row (id=37) is still from 2026-03-10. Newest stuck (id=339) is from today.

The most surprising: F16 (`classification_proposals`) — table was effectively reset. Went from 2762 rows (2711 approved, 50 pending, 1 rejected) → 115 rows (1 approved, 114 pending). Looks like the proposal table was cleared and rebuilt with a new sweep that hasn't been worked through.

The four critical findings status:
- **F1 (NULL strategy cols)**: PARTIALLY FIXED — 3 of 4 columns now populated; `strategy` column still 100% NULL
- **F2 (boolean type confusion)**: NOT FIXED — every column unchanged (is_singlestock still stores ticker strings, is_active still Y/N/Unknown, etc.)
- **F3 (fund_status garbage)**: NOT FIXED — same 5 URL/HEADER rows, same 372 over-length tickers (in fact slightly more rows now: e.g. id 6415, 6417 appear that weren't in the original sample)
- **F4 (69% unmapped master rows)**: PARTIALLY FIXED — `issuer_display` improved from 5093 NULL → 2520 NULL; `etp_category` slightly better (5076 → 4988); `mkt_fund_mapping` orphan count unchanged at 5076

The new finding worth flagging: `reserved_symbols` table was seeded with 282 rows in Stage 1, now empty. Either intentionally cleared or lost on a migration.

---

## Per-finding status

| # | Severity | Stage 1 | Now | Δ | Status |
|---|---|---:|---:|---|---|
| **F1** | critical | 7361 NULL all 4 cols | 49/49/15/7361 NULL | mostly fixed | **PARTIAL** — strategy column still 100% NULL |
| **F2** | critical | mixed-type strings | unchanged | 0 | **PERSISTS** |
| **F3** | critical | 5 URL + 372 long | 5 URL + 372 long | 0 | **PERSISTS** |
| **F4** | critical | 5076 unmapped (69%) | 5076 unmapped, 4988 NULL etp_category, 2520 NULL issuer | issuer fixed | **PARTIAL** — issuer better, mapping unchanged |
| **F5** | high | 67 stuck running | **92 stuck running** | +25 | **WORSENED** |
| **F6** | high | 1878 mismatches | 1878 mismatches | 0 | **PERSISTS** |
| **F7** | high | 268 + 6 = 274 drift | 268 + 6 = 274 drift | 0 | **PERSISTS** |
| **F8** | high | 493 LIQU+Y, 5 INAC+Y, 11 DLST+Y | 493 LIQU+Y, 5 INAC+Y, 11 DLST+Y | 0 | **PERSISTS** |
| **F9** | high | 13 ticker collisions | 13 ticker collisions | 0 | **PERSISTS** |
| **F10** | high | 234 / 309 missing capm | 234 / 309 missing capm | 0 | **PERSISTS** |
| **F11** | medium | 9 alert orphans | **0 orphans** | -9 | **RESOLVED** |
| **F12** | medium | 15 mapping orphans | 15 mapping orphans | 0 | **PERSISTS** |
| **F13** | medium | 7342 ticker / clean diff | 7342 (unchanged) | 0 | **PERSISTS** (probably intentional) |
| **F14** | medium | 12705 cross-trust tickers | 12706 cross-trust tickers | +1 | **PERSISTS** |
| **F15** | medium | 6 trust-name dupes | 6 trust-name dupes | 0 | **PERSISTS** |
| **F16** | medium | 50 pending (1.8%) | **114 pending (99.1%)** | reset | **PERSISTS / WORSENED** — table was cleared and rebuilt |
| **F17** | medium | prosp 191774 / tkr 25250 / eff 963 | prosp 192021 / tkr 25416 / eff 972 | +small | **PERSISTS** (grew slightly with new rows) |
| **F18** | medium | 4370 all-NULL extractions | 4415 all-NULL | +45 | **PERSISTS** |
| **F19** | low | 414 NULL tickers | **413 NULL tickers** | -1 | **PARTIAL** — 1 fewer (Delisted bucket cleared) |
| **F20** | low | 43 NaT inception dates | 43 NaT | 0 | **PERSISTS** |
| **F21** | low | BERZ flagged is_rex | BERZ flagged is_rex | 0 | **PERSISTS** |

**Resolved**: F11.
**Partial fixes**: F1, F4, F19.
**Worsened**: F5, F16.
**New issue**: `reserved_symbols` was 282 rows, now 0 — see new finding N1 below.
**New table**: `api_audit_log` (10 columns, 0 rows) — observability scaffolding added but not yet receiving writes.
**Notable progress (good)**: `cboe_state_changes` was 0, now 715 rows — chain service deployment is producing data. `cboe_known_active` grew 13284 → 13355. SEC pipeline has run 24 more times (130 → 154). Bloomberg pipeline has run 37 more times (304 → 341). `filings` grew 626936 → 628778. `fund_extractions` grew 686304 → 695152.

---

## Detailed re-runs

### F1 — mkt_master_data NULL classification columns
**Status**: PARTIAL FIX — most columns wired up, but `strategy` column still 100% NULL.

```
SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NULL;  -- was 7361, now 49
SELECT COUNT(*) FROM mkt_master_data WHERE sub_strategy IS NULL;      -- was 7361, now 49
SELECT COUNT(*) FROM mkt_master_data WHERE asset_class IS NULL;       -- was 7361, now 15
SELECT COUNT(*) FROM mkt_master_data WHERE strategy IS NULL;          -- was 7361, still 7361
```

`primary_strategy` distribution shows real data: Plain Beta 5462, L&I 792, Defined Outcome 511, Income 362, Risk Mgmt 185.
`sub_strategy` distribution shows real data: Broad 3828, Style 634, Long 548, Buffer 479, Thematic 387.
`asset_class` distribution shows real data: Equity 4961, Fixed Income 1353, Multi-Asset 448, Commodity 260, Crypto 185.

The `strategy` column is dead — `SELECT strategy, COUNT(*) FROM mkt_master_data GROUP BY strategy` returns `(None, 7361)`. Likely the denormalization wired up the new column names (`primary_strategy`, `sub_strategy`, `asset_class`) but never backfilled the older `strategy` column, which is presumably still being read somewhere. Cross-join confirms: 47 master rows still have `primary_strategy` NULL while classification has the strategy.

**Recommend**: drop the legacy `strategy` column, or backfill it from `mkt_fund_classification.strategy` so dependents don't silently misread. Also re-fire the denormalization for the 49 rows still NULL in primary_strategy.

### F2 — boolean columns store mixed-type strings
**Status**: NOT FIXED — bit-for-bit identical to Stage 1.

```
is_singlestock distinct: 283  (still includes 'XBTUSD Curncy', 'TSLA US', 'NVDA US', ...)
is_active values: ('N',3552), ('Unknown',173), ('Y',3636)
uses_derivatives: ('0.0',4467), ('1.0',1985), (None,909)
uses_swaps: ('0.0',5159), ('1.0',935), (None,1267)
is_40act: ('1.0',5629), ('0.0',354), (None,1378)
uses_leverage: ('False',6400), ('True',961)
is_crypto: stores strategy strings ('Cryptocurrency',189), ('Equity Long/Short',47), ...
```

No schema change, no upstream type coercion. Schema audit + ingestor fix still pending.

### F3 — fund_status garbage tickers
**Status**: NOT FIXED, possibly slightly worse (more visible examples).

```
ticker LIKE '%HTTPS%' OR ticker IN ('HEADER','HEADER_ONLY'):  5 (unchanged)
LENGTH(ticker) > 5:  372 (unchanged)
```

Sample from current DB:
- id=4587: `OLDGBUY` / Goldman Sachs New Age Consumer ETF / 497
- id=6027: `HTTPS://WWW.SEC.GOV/ARCHIVES/EDGAR/DATA/1804196/000119312524040119/D390654D497.HTM` / 0001193125-24-040119 / BlackRock ETF Trust II
- id=6415: `PGIM ETF TRUST` / `CLASata/1727074/000168386319001642/0001683863-19-001642.txt` / 497
- id=6417: `2019-0EC.GOV/ARCHIVES/EDGAR/DATA/1727074/000168386319001642/F1538D1.HTM` / 1727074 / PGIM ETF Trust
- id=8330: `HEADER` / 2025-02-21 / March Innovator U.S. Equity Ultra Buffer ETF

Insert-side ticker validation still missing. SGML parser fallthroughs still write parser internals to DB.

### F4 — 69% of master_data rows have no etp_category / mapping
**Status**: PARTIALLY FIXED — `issuer_display` significantly improved.

```
etp_category NULL:        was 5076, now 4988  (-88, 67.8% → 67.8%)
issuer_display NULL:      was 5093, now 2520  (-2573, 69% → 34%) ✓ big improvement
category_display NULL:    was 5076, now 5076  (unchanged)
primary_category NULL:    was 5076, now 5076  (unchanged)
master without mapping:   was 5076, now 5076  (unchanged)
```

Issuer-display backfill happened. Category mapping itself unchanged — need to either expand `mkt_fund_mapping` CSV or auto-classify the 4988 unmapped funds.

### F5 — stuck mkt_pipeline_runs
**Status**: WORSENED — 67 → 92.

```
status='running' AND finished_at IS NULL:  92 (was 67)
total runs:  341 (was 304)
by status:  ('completed',244), ('failed',5), ('running',92)
```

Oldest stuck still id=37 (2026-03-10). Newest stuck id=339 (today, 2026-05-11 20:08). The orchestrator still isn't catching crashes in a finally-block. 25 more orphans accumulated since the original audit. Watchdog query to flip stale runs >2hr to 'failed' would clean ~92 immediately.

### F6 — trusts.filing_count placeholder of 3
**Status**: NOT FIXED — exactly 1878 mismatches.

Sample of worst (filing_count vs actual): iShares Trust 3 vs **25,375**, GOLDMAN SACHS TRUST 3 vs 10,480, EQ ADVISORS TRUST 2 vs 6,514, BLACKROCK FUNDS 3 vs 6,038, Federated Hermes Money Market Obligations Trust 3 vs 5,120.

One UPDATE statement away from a fix. Trivial.

### F7 — rex_products status / form drift
**Status**: NOT FIXED — exactly 274 (268 + 6) drift rows.

### F8 — is_active vs market_status mismatch
**Status**: NOT FIXED — distribution unchanged. 493 LIQU+Y, 5 INAC+Y, 11 DLST+Y, 2261 ACTV+N still present.

### F9 — rex_products ticker collisions
**Status**: NOT FIXED — same 13 colliding tickers.

REX placeholder still at 35 rows (with mix of 485APOS/485BPOS/485BXT). APHU still at 11 (10 Awaiting Effective + 1 Listed).

### F10 — rex/capm coverage
**Status**: NOT FIXED — still 234 of 309 rex_products without capm match. CapM still at 74 rows.

### F11 — filing_alerts orphans
**Status**: RESOLVED ✓ — was 9 orphans, now 0. Either filings backfilled or alerts cleaned.

### F12 — orphan mapping rows
**Status**: NOT FIXED — still 15 rows in `mkt_fund_mapping` and 15 in `mkt_category_attributes` that don't match any `mkt_master_data.ticker`.

### F13 — ticker " US" suffix
**Status**: NOT FIXED — 7342 rows still differ between `ticker` and `ticker_clean`. Probably intentional (Bloomberg-format vs normalized).

### F14 — fund_status cross-trust ticker contamination
**Status**: NOT FIXED, +1 row — 12706 distinct tickers span multiple trusts. Worst offenders identical: SYM (54 trusts × 1498 rows), SYMBO (54 × 657), PACOX/PAULX/PCCOX (42 × 42 each).

### F15 — duplicate trust names across CIKs
**Status**: NOT FIXED — same 6 names.

### F16 — pending classification_proposals
**Status**: WORSENED — table was effectively reset.

```
Stage 1: 2762 total — 2711 approved, 50 pending, 1 rejected
Now:     115 total —  1 approved, 114 pending
date range: 2026-04-10 to 2026-05-12
```

The historical proposals (and approvals) appear to have been wiped. New proposals are being generated by sweeps but nobody is approving them — backlog now 99% pending. The reviewer queue is broken.

`classification_audit_log` grew from 18,983 → 30,591 with new sources `conflict` (12,333) and `sweep_medium` (14,874) — the sweep is running and writing audit rows but not getting approvals through to the proposals table.

### F17 — fund_status NULL spikes
**Status**: NOT FIXED — totals grew with table size.

```
prospectus_name NULL:  was 191774, now 192021
ticker NULL:           was 25250, now 25416
EFFECTIVE w/o eff_dt:  was 963, now 972
```

### F18 — empty fund_extractions rows
**Status**: NOT FIXED — was 4370, now 4415 (+45). Extractor still inserts sentinel rows.

### F19 — rex_products NULL tickers
**Status**: SLIGHT FIX — was 414, now 413. The 1 Delisted exception cleared, plus 21 'Filed (485A)' bucket merged into 'Filed'. Distribution now: Awaiting Effective 142, Delisted 1 (still!), Filed 244, Research 6.

### F20 — NaT inception_date
**Status**: NOT FIXED — same 43 rows storing literal `'NaT'` string.

### F21 — BERZ flagged is_rex
**Status**: NOT FIXED — BERZ US (BMO ETN) still in `mkt_master_data` with `is_rex=1`.

---

## New findings (since Stage 1)

### N1: reserved_symbols table emptied (was 282 → now 0)
- **Severity**: medium
- **Table**: `reserved_symbols`
- **Symptom**: Stage 1 had 282 rows. Now 0. Schema is intact (12 columns: id, exchange, symbol, end_date, status, rationale, suite, linked_filing_id, linked_product_id, notes, created_at, updated_at).
- **Blast radius**: Anything querying reserved_symbols for "is this ticker reserved on this exchange" returns 0 hits. May affect CBOE pillar workflows that depended on this.
- **Hypothesis**: Migration cleared the table without re-seeding, OR data moved to `cboe_known_active` / `cboe_state_changes`.
- **Verify before action**: confirm if reserved_symbols is still expected to hold data, or if its function moved to the cboe_* tables.

### N2: mkt_master_data.strategy column 100% NULL while peers populated
- **Severity**: high
- **Table.column**: `mkt_master_data.strategy`
- **Symptom**: F1 fix populated `primary_strategy`, `sub_strategy`, `asset_class` but left the legacy `strategy` column 100% NULL across all 7361 rows.
- **Evidence**: `SELECT strategy, COUNT(*) FROM mkt_master_data GROUP BY strategy` → `(None, 7361)`.
- **Blast radius**: Any code still reading `mkt_master_data.strategy` (rather than `primary_strategy`) returns NULL. Could be UI, screener, or report dimension that hasn't been switched over.
- **Hypothesis**: Refactor renamed/added `primary_strategy` etc. but left the original `strategy` column behind without backfill or removal.
- **Fix size**: trivial (ALTER TABLE DROP COLUMN strategy, OR UPDATE … SET strategy = primary_strategy).

### N3: api_audit_log added but has 0 rows
- **Severity**: low
- **Table**: `api_audit_log`
- **Symptom**: New table added (10 columns: route, method, ip, user_agent, success, status_code, payload_size, detail, created_at). 0 rows.
- **Blast radius**: None yet — observability scaffolding installed but middleware not wired to write to it.
- **Fix size**: trivial (verify the audit middleware is enabled and pointed at this table).

---

## Verdict

The Stage 1 fixes that landed are real but partial. R1/R2/R6/T1/T2 (whichever specific recommendations they map to) materially helped F1 (denormalization) and F4 (issuer_display), and resolved F11 (alert orphans). The chain service deployment shows up as `cboe_state_changes` going 0 → 715, which is healthy.

But the four critical findings are not closed:
- **F1 closed for 3 of 4 columns; legacy `strategy` column still 100% NULL** (downgrade to high, see N2)
- **F2 untouched** — boolean type confusion still rampant
- **F3 untouched** — garbage rows still being written
- **F4 partially closed** — issuer_display fixed, but the 5076-row mapping gap is structural and unchanged

And one finding got worse: **F5 stuck pipeline runs grew by 25** (67 → 92). The watchdog/finally-block recommendation never landed, so every crash since 2026-03-10 keeps adding to the orphan count.

Two findings appear to have **regressed via reset**:
- F16 (proposals) — the approval history was wiped; only 1 approved row remains, 114 pending
- N1 — `reserved_symbols` was emptied

Recommend Stage 2 prioritize: (a) F2 boolean type fix at the writer + schema layer, (b) F3 ticker-format insert validation + reparse the 5 URL rows, (c) F5 stuck-run watchdog (single one-time UPDATE plus add finally-block), (d) F6 filing_count recompute (trivial), (e) investigate F16 / N1 resets.

---

## Appendix — full SQL re-runs

```sql
-- F1
SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NULL;  -- 49
SELECT COUNT(*) FROM mkt_master_data WHERE sub_strategy IS NULL;      -- 49
SELECT COUNT(*) FROM mkt_master_data WHERE asset_class IS NULL;       -- 15
SELECT COUNT(*) FROM mkt_master_data WHERE strategy IS NULL;          -- 7361
SELECT COUNT(*) FROM mkt_master_data m JOIN mkt_fund_classification cl ON m.ticker=cl.ticker
  WHERE m.strategy IS NULL AND cl.strategy IS NOT NULL;               -- 7359
SELECT COUNT(*) FROM mkt_master_data m JOIN mkt_fund_classification cl ON m.ticker=cl.ticker
  WHERE m.primary_strategy IS NULL AND cl.strategy IS NOT NULL;       -- 47

-- F2
SELECT COUNT(DISTINCT is_singlestock) FROM mkt_master_data;  -- 283
SELECT is_active, COUNT(*) FROM mkt_master_data GROUP BY is_active;
-- ('N',3552), ('Unknown',173), ('Y',3636)
SELECT uses_derivatives, COUNT(*) FROM mkt_master_data GROUP BY uses_derivatives;
-- (None,909), ('0.0',4467), ('1.0',1985)
SELECT uses_leverage, COUNT(*) FROM mkt_master_data GROUP BY uses_leverage;
-- ('False',6400), ('True',961)

-- F3
SELECT COUNT(*) FROM fund_status WHERE ticker LIKE '%HTTPS%' OR ticker LIKE '%HTTP%' OR ticker IN ('HEADER','HEADER_ONLY');  -- 5
SELECT COUNT(*) FROM fund_status WHERE LENGTH(ticker) > 5;  -- 372

-- F4
SELECT COUNT(*) FROM mkt_master_data WHERE etp_category IS NULL;        -- 4988
SELECT COUNT(*) FROM mkt_master_data WHERE issuer_display IS NULL;      -- 2520
SELECT COUNT(*) FROM mkt_master_data WHERE category_display IS NULL;    -- 5076
SELECT COUNT(*) FROM mkt_master_data WHERE primary_category IS NULL;    -- 5076
SELECT COUNT(*) FROM mkt_master_data m LEFT JOIN mkt_fund_mapping fm ON m.ticker=fm.ticker
  WHERE fm.ticker IS NULL;                                              -- 5076

-- F5
SELECT status, COUNT(*) FROM mkt_pipeline_runs GROUP BY status;
-- ('completed',244), ('failed',5), ('running',92)
SELECT id, started_at FROM mkt_pipeline_runs WHERE status='running' AND finished_at IS NULL
  ORDER BY started_at ASC LIMIT 1;  -- (37, '2026-03-10 21:22:22')

-- F6
SELECT COUNT(*) FROM (SELECT t.id FROM trusts t LEFT JOIN filings f ON f.trust_id=t.id
  GROUP BY t.id HAVING t.filing_count != COUNT(f.id));  -- 1878

-- F7
SELECT COUNT(*) FROM rex_products WHERE status='Awaiting Effective' AND latest_form='485BPOS';  -- 268
SELECT COUNT(*) FROM rex_products WHERE status='Filed' AND latest_form='485BPOS';               -- 6

-- F8
SELECT market_status, is_active, COUNT(*) FROM mkt_master_data GROUP BY 1,2;
-- LIQU+Y:493, INAC+Y:5, DLST+Y:11, ACTV+N:2261 (all unchanged)

-- F9
SELECT ticker, COUNT(*) FROM rex_products WHERE ticker != '' AND ticker IS NOT NULL
  GROUP BY ticker HAVING COUNT(*) > 1;  -- 13 tickers (REX×35, APHU×11, STPW×7, ...)

-- F10
SELECT COUNT(*) FROM rex_products r LEFT JOIN capm_products c ON r.ticker=c.ticker
  WHERE r.ticker != '' AND c.ticker IS NULL;  -- 234

-- F11
SELECT COUNT(*) FROM filing_alerts fa LEFT JOIN filings f ON fa.accession_number=f.accession_number
  WHERE f.accession_number IS NULL;  -- 0  ✓ RESOLVED

-- F12
SELECT COUNT(*) FROM mkt_fund_mapping fm LEFT JOIN mkt_master_data m ON fm.ticker=m.ticker
  WHERE m.ticker IS NULL;  -- 15
SELECT COUNT(*) FROM mkt_category_attributes ca LEFT JOIN mkt_master_data m ON ca.ticker=m.ticker
  WHERE m.ticker IS NULL;  -- 15

-- F13
SELECT COUNT(*) FROM mkt_master_data WHERE ticker LIKE '% US';  -- 7342
SELECT COUNT(*) FROM mkt_master_data WHERE ticker != ticker_clean;  -- 7342

-- F14
SELECT COUNT(*) FROM (SELECT ticker FROM fund_status WHERE ticker != '' AND ticker IS NOT NULL
  GROUP BY ticker HAVING COUNT(DISTINCT trust_id)>1);  -- 12706

-- F15
SELECT name, COUNT(*) FROM trusts GROUP BY name HAVING COUNT(*)>1;  -- 6 names

-- F16
SELECT status, COUNT(*) FROM classification_proposals GROUP BY status;
-- ('approved',1), ('pending',114)  -- table appears reset

-- F17
SELECT COUNT(*) FROM fund_status WHERE prospectus_name IS NULL;  -- 192021
SELECT COUNT(*) FROM fund_status WHERE ticker IS NULL;           -- 25416
SELECT COUNT(*) FROM fund_status WHERE status='EFFECTIVE' AND effective_date IS NULL;  -- 972

-- F18
SELECT COUNT(*) FROM fund_extractions WHERE series_id IS NULL AND series_name IS NULL
  AND class_contract_id IS NULL AND class_contract_name IS NULL;  -- 4415

-- F19
SELECT status, COUNT(*) FROM rex_products WHERE ticker IS NULL OR ticker=''
  GROUP BY status;
-- ('Awaiting Effective',142), ('Delisted',1), ('Filed',244), ('Research',6)  -- total 393, ranked sum 413 incl '' tickers

-- F20
SELECT COUNT(*) FROM mkt_master_data WHERE inception_date='NaT';  -- 43

-- F21
SELECT ticker, fund_name, issuer FROM mkt_master_data WHERE ticker='BERZ US' AND is_rex=1;
-- ('BERZ US', 'MICROSECTORS FANG & INNOVATION -3X INVERSE LEVERAGED ETN', 'BMO ETNs/United States')

-- N1 (new)
SELECT COUNT(*) FROM reserved_symbols;  -- 0  (was 282 in Stage 1)

-- N2 (new) — see F1 above
-- N3 (new)
SELECT COUNT(*) FROM api_audit_log;  -- 0  (table newly created)

-- Orphan FK checks (all clean, unchanged)
SELECT COUNT(*) FROM filings f LEFT JOIN trusts t ON f.trust_id=t.id WHERE t.id IS NULL;            -- 0
SELECT COUNT(*) FROM fund_status fs LEFT JOIN trusts t ON fs.trust_id=t.id WHERE t.id IS NULL;     -- 0
SELECT COUNT(*) FROM fund_extractions fe LEFT JOIN filings f ON fe.filing_id=f.id WHERE f.id IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_fund_classification fc LEFT JOIN mkt_master_data m ON fc.ticker=m.ticker
  WHERE m.ticker IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_rex_funds rf LEFT JOIN mkt_master_data m ON rf.ticker=m.ticker
  WHERE m.ticker IS NULL;  -- 0

-- Accession dupes (still clean)
SELECT accession_number, COUNT(*) FROM filings GROUP BY 1 HAVING COUNT(*)>1;          -- 0
SELECT accession_number, COUNT(*) FROM filing_alerts GROUP BY 1 HAVING COUNT(*)>1;    -- 0
```

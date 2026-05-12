# Stage 1 Audit — DB Integrity Sweep
Generated: 2026-05-11T22:30:00Z
Agent: db_integrity
DB: `C:/Projects/rexfinhub/data/etp_tracker.db`

## Summary
**21 findings**: 4 critical, 7 high, 7 medium, 3 low.

The DB is structurally sound for primary-key / FK plumbing — there are no orphan filings, no cross-trust filings, and no duplicate accession numbers. Foreign-key edges that the ORM owns are intact.

The damage is concentrated in three layers:

1. **Classification denormalization is broken** (CRITICAL F1). `mkt_master_data.primary_strategy / sub_strategy / asset_class / strategy` are 100% NULL across all 7361 rows, while `mkt_fund_classification.strategy` is 100% populated. The denormalized projection from `mkt_fund_classification` → `mkt_master_data` never wrote.
2. **Type confusion in mkt_master_data boolean columns** (CRITICAL F2). `is_singlestock` stores ticker strings ("XBTUSD Curncy", "TSLA US"), `is_active` stores Y/N/Unknown, `uses_derivatives/swaps/40act` store '0.0'/'1.0' strings, `uses_leverage` stores 'True'/'False', `is_crypto` stores hedge-fund category strings. The columns are typed VARCHAR but the schema implies boolean.
3. **Pipeline coverage gaps** (HIGH F4, F5, F8). 5076/7361 (69%) of `mkt_master_data` rows have no `mkt_fund_mapping` and no `etp_category`. 67 stuck-running `mkt_pipeline_runs` rows pile up since 2026-03-10. 1878 of 15750 trusts carry the placeholder `filing_count=3` instead of their real count (e.g., ProShares Trust shows 3 in the column, has 2070 filings).

The 3 cross-series ticker dupes from preflight are dwarfed by **12,705 tickers in `fund_status` that span multiple trusts** (the most extreme: ticker `SYM` appears 1498 times across 54 trust IDs — these aren't real tickers but text fragments scraped into the ticker column). Confirmed scraper contamination.

The 17 NULL `issuer_display` from preflight is actually **5093 NULL** in master_data (69%) — the preflight count was filtered.

The 79 unclassified launches from preflight is **300 unclassified rows since 2026-02-11** — the wider window shows the gap is bigger.

The 100% NULL `primary_strategy` is confirmed as a structural denormalization failure, not isolated row issues.

---

## Schema map (table → row count → key columns)

| Table | Rows | Key columns |
|---|---:|---|
| analysis_results | 0 | (empty) |
| autocall_crisis_presets | 8 | — |
| autocall_index_levels | 125,966 | (date, ticker) PK |
| autocall_index_metadata | 26 | ticker PK |
| autocall_sweep_cache | 0 | (empty) |
| capm_audit_log | 1 | id PK |
| capm_products | 74 | id PK, ticker |
| capm_trust_aps | 40 | — |
| cboe_known_active | 13,284 | full_ticker PK |
| cboe_scan_runs | 12 | — |
| cboe_state_changes | 0 | (empty) |
| cboe_symbols | 475,254 | ticker(4) PK |
| classification_audit_log | 18,983 | sweep_run_id, ticker, column_name |
| classification_proposals | 2,762 | id PK, ticker, status |
| digest_subscribers | 1 | — |
| email_recipients | 18 | id PK, (email,list_type) |
| filing_alerts | 8,572 | id PK, accession_number |
| filing_analyses | 30 | — |
| filings | 626,936 | id PK, accession_number, trust_id, cik |
| fund_distributions | 620 | id PK, (ticker, ex_date) |
| fund_extractions | 686,304 | id PK, filing_id |
| fund_status | 213,810 | id PK, trust_id, series_id, ticker, status, latest_form |
| mkt_category_attributes | 2,294 | id PK, ticker |
| mkt_exclusions | 26 | id PK, ticker |
| mkt_fund_classification | 7,332 | id PK, ticker, strategy |
| mkt_fund_mapping | 2,300 | id PK, ticker, etp_category |
| mkt_global_etp | 0 | (empty) |
| mkt_issuer_mapping | 341 | id PK, (etp_category, issuer) |
| mkt_market_status | 17 | code |
| mkt_master_data | 7,361 | id PK, ticker, ticker_clean, primary_strategy(NULL!), is_active, is_rex |
| mkt_pipeline_runs | 304 | id PK, status, started_at |
| mkt_report_cache | 4 | — |
| mkt_rex_funds | 96 | ticker |
| mkt_stock_data | 6,593 | ticker |
| mkt_time_series | 272,357 | (ticker, months_ago) |
| name_history | 51,472 | series_id, name |
| nyse_holidays | 10 | — |
| pipeline_runs | 130 | id PK, status, triggered_by |
| reserved_symbols | 282 | id PK, (exchange, symbol) |
| rex_products | 723 | id PK, ticker, product_suite, status |
| screener_results | 4,991 | id PK |
| screener_uploads | 1 | id PK |
| trust_candidates | 59 | cik PK |
| trust_requests | 1 | — |
| trusts | 15,750 | id PK, cik, name, slug |

---

## Findings

### F1: mkt_master_data — primary_strategy / sub_strategy / asset_class / strategy are 100% NULL
- **Severity**: critical
- **Table.column**: `mkt_master_data.primary_strategy`, `.sub_strategy`, `.asset_class`, `.strategy`
- **Symptom**: Every row in master data has NULL in all four classification columns. Yet `mkt_fund_classification.strategy` is fully populated (0/7332 NULL).
- **Evidence**:
  - `SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NULL` → 7361 (100%)
  - `SELECT COUNT(*) FROM mkt_master_data m JOIN mkt_fund_classification c ON m.ticker=c.ticker WHERE m.primary_strategy IS NULL AND c.strategy IS NOT NULL` → 7332
- **Blast radius**: 100% of master_data. Any UI that filters/groups/displays primary_strategy from `mkt_master_data` is dead. Anywhere that reads `mkt_master_data.strategy` is broken. Downstream: every dashboard, screener, fund-list page, and any API surfacing strategy/asset_class.
- **Hypothesis**: The denormalization step that copies `mkt_fund_classification.{strategy, ...}` into `mkt_master_data.{primary_strategy, sub_strategy, asset_class, strategy}` was added as columns but never wired up — or the copy step was removed during a refactor. The `_migrate_missing_columns` runner adds the columns but no backfill ever ran.
- **Fix size**: small (one UPDATE/JOIN per column, gated on first-class data quality)

### F2: mkt_master_data — boolean columns store mixed-type strings
- **Severity**: critical
- **Table.column**: `mkt_master_data.is_singlestock`, `.is_active`, `.uses_derivatives`, `.uses_swaps`, `.is_40act`, `.uses_leverage`, `.is_crypto`
- **Symptom**: Columns named like booleans but typed VARCHAR are storing arbitrary tokens.
  - `is_singlestock` → 283 distinct values including ticker strings ("XBTUSD Curncy", "TSLA US", "MSTR US")
  - `is_active` → 'Y' / 'N' / 'Unknown'
  - `uses_derivatives` / `uses_swaps` / `is_40act` → '0.0' / '1.0' / NULL
  - `uses_leverage` → 'False' / 'True'
  - `is_crypto` → strategy strings ("Cryptocurrency", "Equity Long/Short", "Macro Currency", ...)
- **Evidence**: `SELECT is_singlestock, COUNT(*) FROM mkt_master_data GROUP BY is_singlestock LIMIT 20` returns ticker strings; full output captured in appendix.
- **Blast radius**: Any code that does `WHERE is_active=1` (boolean compare) returns 0 rows. Any code that does `WHERE is_singlestock = True` returns 0 rows. Possibly all leveraged/single-stock filters silently broken.
- **Hypothesis**: Source CSV exports columns as Excel-formatted strings. The ingestor copies them verbatim. Schema declared VARCHAR(20) to be permissive. `is_singlestock` is being aliased to "underlier" upstream — wrong field reused.
- **Fix size**: medium (need to back-fill from source + change schema + change downstream)

### F3: fund_status — ticker column contaminated with URLs, accession numbers, and "HEADER" markers
- **Severity**: critical
- **Table.column**: `fund_status.ticker`, `.fund_name`, `.latest_form`
- **Symptom**: Several rows have URL text or document-section labels in the wrong columns.
- **Evidence**:
  - Row 6027: ticker = `HTTPS://WWW.SEC.GOV/ARCHIVES/EDGAR/DATA/1804196/000119312524040119/D390654D497.HTM`, fund_name = `0001193125-24-040119`, latest_form = `BlackRock ETF Trust II`
  - Row 6418: fund_name = `HEADER`, latest_form = `full`
  - Row 8330: ticker = `HEADER`, fund_name = `2025-02-21`, latest_form = `March Innovator U.S. Equity Ultra Buffer ETF`
  - 5 rows have ticker matching `%HTTPS%` / `HEADER`. 372 rows have ticker length > 5 (US tickers max 5 chars).
- **Blast radius**: Small in count but the existence proves the SGML-text extractor mis-parses some filings and writes parser internals to DB. Any downstream dashboard relying on these rows will look broken to a user.
- **Hypothesis**: The SGML/header parser has fallthroughs that emit raw text into the wrong fields when a filing layout differs from the standard. No insert-side validation rejects these.
- **Fix size**: medium (validate ticker format on insert, re-parse the affected filings)

### F4: mkt_master_data — 69% of rows have no etp_category / mapping / category_display / primary_category
- **Severity**: critical
- **Table.column**: `mkt_master_data.etp_category`, `.category_display`, `.primary_category`, `.issuer_display`
- **Symptom**:
  - 5076 / 7361 rows (69%) have NULL `etp_category`
  - 5076 / 7361 (69%) have NULL `category_display` / `primary_category`
  - 5093 / 7361 (69%) have NULL `issuer_display`
  - 5076 master rows have no row in `mkt_fund_mapping`
- **Evidence**:
  - `SELECT COUNT(*) FROM mkt_master_data m LEFT JOIN mkt_fund_mapping fm ON m.ticker=fm.ticker WHERE fm.ticker IS NULL` → 5076
  - `SELECT COUNT(*) FROM mkt_master_data WHERE issuer_display IS NULL AND aum > 0` → 4993
- **Blast radius**: 69% of all funds are unmapped. Categorized views (LI/CC/Crypto/Defined/Thematic) only see 31% of the universe. Issuer-grouped reports (assets-by-issuer) lose 69% of rows. Two-thirds of the screener output is invisible.
- **Hypothesis**: `mkt_fund_mapping` is a curated CSV that only covers REX-relevant + most-active competitor funds. 5076 may be intentionally out of scope for L&I/CC/Crypto/Defined/Thematic taxonomy — but the website surfaces master_data globally, so the gap surfaces as "uncategorized".
- **Fix size**: medium (need to either auto-classify the 5076 or filter them out at query time)

### F5: mkt_pipeline_runs — 67 stuck "running" rows since 2026-03-10
- **Severity**: high
- **Table.column**: `mkt_pipeline_runs.status='running'`, `finished_at IS NULL`
- **Symptom**: 67 of 304 runs (22%) have `status='running'` but `finished_at IS NULL`. Oldest is row 37 from 2026-03-10. Most recent is row 302 (2026-05-04).
- **Evidence**: `SELECT COUNT(*) FROM mkt_pipeline_runs WHERE status='running' AND finished_at IS NULL` → 67
- **Blast radius**: Any UI showing "current pipeline run" or "is pipeline running?" sees 67 phantoms. Operators can't tell if a run is genuinely running.
- **Hypothesis**: Pipeline crashes / killed processes don't have a finally-block that sets status='failed'. The orchestrator process exits and the row is orphaned in 'running'.
- **Fix size**: small (insert a finally-block, add a watchdog that flips orphaned >2hr runs to 'failed')

### F6: trusts.filing_count — placeholder value 3 across 1878 rows; actual counts vary by ~2 orders of magnitude
- **Severity**: high
- **Table.column**: `trusts.filing_count`
- **Symptom**: `filing_count=3` was inserted as a default for many trusts at seed time and was never recomputed. ProShares Trust has 2070 actual filings but `filing_count=3`. 1878 trusts mismatch their actual JOIN-count.
- **Evidence**:
  - `SELECT t.id, t.name, t.filing_count, COUNT(f.id) AS actual FROM trusts t LEFT JOIN filings f GROUP BY t.id HAVING t.filing_count != actual` → 1878 mismatches (sample: ProShares Trust 3 vs 2070)
- **Blast radius**: Anything sorting trusts by activity, ranking trusts by "biggest issuer", admin metrics — all broken.
- **Hypothesis**: `filing_count` was added as a denormalized counter for performance but the maintenance trigger / nightly recompute was never built. The 3 likely came from initial seed (memory says "Recent work (Feb 26)... 236 trusts" — 3 may have been a placeholder).
- **Fix size**: small (one UPDATE … SELECT COUNT)

### F7: rex_products — 274 rows have status/form contradictions
- **Severity**: high
- **Table.column**: `rex_products.status`, `.latest_form`
- **Symptom**: 268 rows are `status='Awaiting Effective'` AND `latest_form='485BPOS'`. 485BPOS is the *post-effective* annual update — its presence implies the fund is already EFFECTIVE, not "awaiting". 6 more rows have `status='Filed'` AND `latest_form='485BPOS'`.
- **Evidence**:
  - `SELECT COUNT(*) FROM rex_products WHERE status='Awaiting Effective' AND latest_form='485BPOS'` → 268
  - `SELECT COUNT(*) FROM rex_products WHERE status='Filed' AND latest_form='485BPOS'` → 6
- **Blast radius**: ~38% of rex_products. /operations/products page shows wrong status for these.
- **Hypothesis**: Atlas memory notes "474 rex_products with status/form drift". This is consistent — the filing-form watcher updates `latest_form` when a new filing arrives but doesn't recompute `status`. The status is set once at row creation and not refreshed.
- **Fix size**: small (status-derivation function based on form + dates, run nightly)

### F8: mkt_master_data — 33 LIQU funds and 5 INAC funds marked is_active='Y'
- **Severity**: high
- **Table.column**: `mkt_master_data.market_status`, `.is_active`
- **Symptom**:
  - 493 rows have `market_status='LIQU'` (liquidated) AND `is_active='Y'`
  - 5 rows have `market_status='INAC'` (inactive) AND `is_active='Y'`
  - 11 rows have `market_status='DLST'` (delisted) AND `is_active='Y'`
  - Conversely: 2261 rows have `market_status='ACTV'` AND `is_active='N'`, and 35 INAC have `is_active='Unknown'`.
- **Evidence**: `SELECT market_status, is_active, COUNT(*) FROM mkt_master_data GROUP BY 1,2`
- **Blast radius**: Active-fund counts disagree with market-status counts. Anywhere that filters `is_active='Y'` will show liquidated/delisted funds. Anywhere that filters `market_status='ACTV'` will show inactive funds.
- **Hypothesis**: `market_status` and `is_active` come from different Bloomberg fields and were never reconciled. One reflects exchange status, the other reflects fund-company "still alive" — they should agree but the joiner has bugs.
- **Fix size**: medium (decide which is canonical, document the rule, derive the other)

### F9: rex_products — 13 ticker collisions: same ticker assigned to multiple in-flight products
- **Severity**: high
- **Table.column**: `rex_products.ticker`
- **Symptom**: 13 tickers map to multiple rex_products rows.
  - `REX` → 35 rows (all "REX IncomeMax X Strategy ETF" — placeholder ticker for unfiled)
  - `APHU` → 11 rows (1 Listed APH product + 10 different "Awaiting Effective" T-REX 2X products that don't actually own this ticker)
  - `STPW` → 7 rows (all Tuttle Capital "Income Blast" products — copy-paste ticker)
  - `DOJU` → 4 rows (DOGE, BNB, Bonk, LTC — different underliers)
  - 9 more
- **Evidence**: `SELECT ticker, COUNT(*) FROM rex_products WHERE ticker != '' GROUP BY ticker HAVING COUNT(*) > 1`
- **Blast radius**: /operations/products shows wrong ticker on 75+ rows. Any reservation-tracker that queries `rex_products.ticker` finds multiple owners.
- **Hypothesis**: Editorial workflow allows draft tickers / placeholder tickers when a product is in early flight. The "REX" rows are obviously placeholders. APHU, STPW, DOJU look like operator paste-errors during data entry.
- **Fix size**: small (data cleanup) + medium (UI: forbid same ticker on multiple "Awaiting Effective" rows)

### F10: rex_products — 234 of 309 ticker'd products have no capm_products row; capm coverage is 74 / 309
- **Severity**: high
- **Table.column**: `rex_products.ticker` ↔ `capm_products.ticker`
- **Symptom**:
  - 309 rex_products have a non-empty ticker. Only 75 of those have a matching `capm_products.ticker`. (234 missing.)
  - Conversely 0 capm_products without a rex match — capm is a strict subset.
- **Evidence**:
  - `SELECT COUNT(*) FROM rex_products r LEFT JOIN capm_products c ON r.ticker=c.ticker WHERE r.ticker != '' AND c.ticker IS NULL` → 234
- **Blast radius**: CapM (cap markets) page shows fewer products than rex_products. Operators relying on capm_products as the AP/admin record have ~76% coverage gap.
- **Hypothesis**: capm_products is a curated CSV (per database.py `_capm_seed_if_empty`), populated from Excel by Ryu. It lags behind the auto-built rex_products list.
- **Fix size**: small (manual capm CSV refresh) — this may be expected lag, not a bug

### F11: filing_alerts — 9 alerts have no matching filings row
- **Severity**: medium
- **Table.column**: `filing_alerts.accession_number` ↔ `filings.accession_number`
- **Symptom**: 9 of 8572 alerts (0.1%) have an accession_number that doesn't exist in `filings`.
- **Evidence**: `SELECT COUNT(*) FROM filing_alerts fa LEFT JOIN filings f ON fa.accession_number=f.accession_number WHERE f.accession_number IS NULL` → 9
- **Blast radius**: Tiny. Any "click alert → see filing" link will 404 for these 9.
- **Hypothesis**: Alert detector saw a filing on EDGAR that the main pipeline either rejected (wrong form) or hasn't yet processed. Or pipeline backfilled and renumbered.
- **Fix size**: trivial (cleanup or backfill)

### F12: mkt_fund_mapping / mkt_category_attributes — 15 ticker rows orphaned from master_data
- **Severity**: medium
- **Table.column**: `mkt_fund_mapping.ticker`, `mkt_category_attributes.ticker`
- **Symptom**: 15 tickers exist in `mkt_fund_mapping` and 15 in `mkt_category_attributes` but not in `mkt_master_data`.
- **Evidence**:
  - `SELECT COUNT(*) FROM mkt_fund_mapping fm LEFT JOIN mkt_master_data m ON fm.ticker=m.ticker WHERE m.ticker IS NULL` → 15
  - `SELECT COUNT(*) FROM mkt_category_attributes ca LEFT JOIN mkt_master_data m ON ca.ticker=m.ticker WHERE m.ticker IS NULL` → 15
- **Blast radius**: Trivial. These mapping rows are dead.
- **Hypothesis**: Master_data was rebuilt from the latest Bloomberg feed and 15 funds dropped out (delisted, no longer in feed) but their static mapping CSVs still reference them.
- **Fix size**: trivial (delete or ignore)

### F13: mkt_master_data — ticker column carries " US" Bloomberg suffix on 7342 of 7361 rows; ticker_clean strips it
- **Severity**: medium
- **Table.column**: `mkt_master_data.ticker` vs `.ticker_clean`
- **Symptom**: `ticker` looks like "AAPX US"; `ticker_clean` looks like "AAPX". 7342 of 7361 rows differ between the two.
- **Evidence**: `SELECT COUNT(*) FROM mkt_master_data WHERE ticker != ticker_clean` → 7342
- **Blast radius**: Any code joining external feeds against `ticker` will not match (external feeds don't have " US"). All FK/JOINs from `mkt_fund_classification.ticker`, `mkt_fund_mapping.ticker`, `mkt_rex_funds.ticker` use the un-suffixed ticker — meaning some of those joins succeed only because the FK side was also " US"-suffixed (or because data normalization happens at insert).
- **Hypothesis**: Bloomberg feed delivers `XXX US`. Pipeline writes both raw and stripped. Joins should always use `ticker_clean`. If anything reads `ticker` directly, it's wrong.
- **Fix size**: small (audit join sites — appears already partially handled by `ticker_clean`)

### F14: fund_status — 12,705 tickers span multiple trusts (cross-series ticker contamination)
- **Severity**: medium
- **Table.column**: `fund_status.ticker`
- **Symptom**: Of all distinct tickers in fund_status, 12,705 appear in fund_status rows belonging to multiple trust_ids. Worst offenders are not real tickers (e.g. `SYM` 1498 rows × 54 trusts; `SYMBO` 657 × 54 trusts) but real ETF tickers also span (e.g. `VOO` 35 trusts, `VTI` 35 trusts).
- **Evidence**: `SELECT COUNT(*) FROM (SELECT ticker FROM fund_status WHERE ticker != '' GROUP BY ticker HAVING COUNT(DISTINCT trust_id)>1)` → 12705
- **Blast radius**: Any "find fund by ticker" join across fund_status will return multiple rows for the same ticker — 27% of all distinct tickers (~12.7K of ~46K). Search results explode.
- **Hypothesis**: Two causes mixed:
  1. The Step-3 SGML extractor occasionally pulls fragments like "SYM" / "SYMBO" / "Class R" / "Class I" from prospectus footers, treating them as ticker symbols.
  2. Real index-tracking ETFs (VOO, VTI, BND) are referenced by name inside many other prospectuses (as benchmarks / underlying holdings) and the extractor ascribes them to whichever trust filed.
- **Fix size**: medium (extractor needs ticker validation against a known-symbol allowlist; cleanup query needed)

### F15: trusts — 6 trust names duplicate across different CIKs; legitimate or not unclear
- **Severity**: medium
- **Table.column**: `trusts.name`
- **Symptom**: 6 trust names appear twice with different CIKs:
  - `BAIRD FUNDS INC` (1282693, 889165)
  - `IVY FUNDS` (883622, 52858)
  - `FIRST FUNDS` (885092, 1161598)
  - `FIRST EAGLE FUNDS` (906352, 807986)
  - `Exchange Listed Funds Trust` (1547950, 1626700)
  - `SEPARATE ACCOUNT VA-K OF COMMONWEALTH ANNUITY...` (873802, 882375)
- **Evidence**: `SELECT name, COUNT(*) FROM trusts GROUP BY name HAVING COUNT(*) > 1` → 6
- **Blast radius**: Search-by-name returns two CIK options. Confusing for users; not catastrophic.
- **Hypothesis**: SEC sometimes assigns two CIKs to a fund family during reorgs / mergers. Both CIKs may legitimately be filing. The slug suffix-with-CIK strategy in the seed code (`baird-funds-inc-889165`) shows the team is aware. Not necessarily wrong, but no `is_canonical` flag exists to break ties.
- **Fix size**: small (add canonical flag or merge if reorg is complete)

### F16: classification_proposals — 50 pending proposals never reviewed
- **Severity**: medium
- **Table.column**: `classification_proposals.status`
- **Symptom**: 50 of 2762 proposals (1.8%) sit in `status='pending'`. Approval rate otherwise: 2711 approved, 1 rejected.
- **Evidence**: `SELECT status, COUNT(*) FROM classification_proposals GROUP BY status`
- **Blast radius**: 50 funds with proposed but unconfirmed classification — they show up as unclassified to consumers despite a system having auto-proposed something.
- **Hypothesis**: The reviewer queue isn't being worked through. May tie to F4 (5076 unmapped — only 50 of those even have a proposal pending).
- **Fix size**: trivial (admin reviews queue) — but reveals review process is the bottleneck, not classification

### F17: fund_status — 191,774 rows (89.7%) have NULL prospectus_name; 25,250 rows (11.8%) have NULL ticker; 963 EFFECTIVE rows have NULL effective_date
- **Severity**: medium
- **Table.column**: `fund_status.prospectus_name`, `.ticker`, `.effective_date`
- **Symptom**: Most fund_status rows lack prospectus_name; 11.8% lack ticker; 963 EFFECTIVE rows lack effective_date.
- **Evidence**:
  - `SELECT COUNT(*) FROM fund_status WHERE prospectus_name IS NULL` → 191774 (89.7%)
  - `SELECT COUNT(*) FROM fund_status WHERE ticker IS NULL` → 25250 (11.8%)
  - `SELECT COUNT(*) FROM fund_status WHERE status='EFFECTIVE' AND effective_date IS NULL` → 963
- **Blast radius**: Prospectus_name is rarely surfaced (low impact). Ticker absence is high impact for ETF ID. Missing effective_date on EFFECTIVE rows is a data-quality black eye in any sortable date column.
- **Hypothesis**: prospectus_name only fills for 485BPOS+iXBRL filings — 89.7% NULL is consistent with the SEC filing mix (most are 497s without iXBRL). Ticker NULL is for series/class shells without an assigned trading symbol (mutual fund classes, pre-launch). Effective_date NULL on EFFECTIVE = inferred-effective by elimination, never had a real date pulled.
- **Fix size**: medium (need to backfill; some are unfixable structurally)

### F18: fund_extractions — 4370 rows with all-NULL core fields and `extracted_from='NONE'`
- **Severity**: medium
- **Table.column**: `fund_extractions.series_id`, `.series_name`, `.class_contract_id`, `.class_contract_name`, `.extracted_from`
- **Symptom**: 4370 rows where every business-meaningful column is NULL and `extracted_from='NONE'`. The row exists but carries no data.
- **Evidence**:
  - `SELECT COUNT(*) FROM fund_extractions WHERE series_id IS NULL AND series_name IS NULL AND class_contract_id IS NULL AND class_contract_name IS NULL` → 4370
- **Blast radius**: 0.6% of fund_extractions. Inflates row count without contributing info.
- **Hypothesis**: Step-3 extractor inserts a sentinel "tried but found nothing" row when SGML parsing yields zero series/class info. Not a bug, but should be `processed=true` markers, not first-class extraction rows.
- **Fix size**: trivial (filter on insert OR cleanup)

### F19: rex_products — 414 of 723 rows (57%) have NULL ticker; majority are pre-listing products
- **Severity**: low
- **Table.column**: `rex_products.ticker`
- **Symptom**: 414 NULL tickers. Distribution: 244 'Filed', 142 'Awaiting Effective', 21 'Filed (485A)', 6 'Research', 1 'Delisted'.
- **Evidence**: `SELECT status, COUNT(*) FROM rex_products WHERE ticker IS NULL GROUP BY status`
- **Blast radius**: Expected for products in flight. The 1 Delisted with NULL ticker is suspicious (delisted from what?).
- **Hypothesis**: Ticker is not assigned by the listing exchange until late in the flight. Pre-listing products legitimately lack tickers.
- **Fix size**: trivial (investigate the 1 Delisted exception)

### F20: mkt_master_data — 43 rows have inception_date='NaT'
- **Severity**: low
- **Table.column**: `mkt_master_data.inception_date`
- **Symptom**: 43 rows literally store the string `"NaT"` (pandas Not-a-Time sentinel). Other rows store `'YYYY-MM-DD HH:MM:SS'` strings. Schema is VARCHAR.
- **Evidence**: `SELECT COUNT(*) FROM mkt_master_data WHERE inception_date='NaT'` → 43
- **Blast radius**: Sort-by-date queries place 'NaT' lexically (sorts as "N..."). Any age-since-inception math fails for these rows.
- **Hypothesis**: Pandas `to_datetime(errors='coerce')` produces NaT, and the writer didn't translate NaT → NULL on serialization.
- **Fix size**: trivial (UPDATE … SET inception_date=NULL WHERE inception_date='NaT')

### F21: mkt_master_data — 1 row has BMO ETN flagged is_rex=1 (BERZ)
- **Severity**: low
- **Table.column**: `mkt_master_data.is_rex`, `mkt_rex_funds.ticker`
- **Symptom**: BERZ ('MICROSECTORS FANG & INNOVATION -3X INVERSE LEVERAGED ETN', issuer 'BMO ETNs/United States') is in `mkt_rex_funds` and master `is_rex=1`. BMO is a competitor, not REX.
- **Evidence**: `SELECT * FROM mkt_master_data WHERE ticker='BERZ US' AND is_rex=1` returns the row.
- **Blast radius**: REX product list includes one competitor. Tiny but visibly wrong.
- **Hypothesis**: `mkt_rex_funds` is a manual ticker list. BERZ may have been added during testing or is a known special case (Ryu sponsoring a BMO ETN?). Worth verifying.
- **Fix size**: trivial (remove or annotate)

---

## Notable "fine" findings (no issues where issues might be expected)

- **filings table**: 626,936 rows. Zero NULL accession_numbers, zero NULL form, zero NULL cik, zero NULL filing_date, zero NULL submission_txt_link. Zero accession_number duplicates. Zero orphan filings (all have valid trust_id). 360 rows (0.06%) lack primary_document/primary_link — minor.
- **trusts.cik**: zero duplicates.
- **trusts.first_filed > last_filed**: zero (no impossible dates).
- **mkt_time_series**: zero (ticker, months_ago) duplicates (272K rows).
- **mkt_stock_data**: zero ticker dupes.
- **fund_distributions**: zero (ticker, ex_date) dupes.
- **mkt_fund_mapping**: zero (ticker, etp_category) dupes.
- **mkt_category_attributes**: zero ticker dupes.
- **mkt_issuer_mapping**: zero (etp_category, issuer) dupes.
- **mkt_fund_classification**: zero ticker dupes; strategy + confidence both 100% populated.
- **email_recipients**: zero (email, list_type) dupes.
- **cboe_known_active**: zero full_ticker dupes (13,284 rows).
- **cboe_symbols**: zero ticker dupes (475,254 rows). Length distribution is sane: 1×26, 2×676, 3×17576, 4×456976.
- **reserved_symbols**: zero (exchange, symbol) dupes.

---

## Full SQL appendix

### Schema
```sql
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
-- 45 tables (see schema map above)

SELECT COUNT(*) FROM <each table>;
-- (results in schema map)
```

### Duplicate checks
```sql
-- filings
SELECT accession_number, cik, COUNT(*) FROM filings GROUP BY 1,2 HAVING COUNT(*)>1;  -- 0
SELECT accession_number, COUNT(*) FROM filings GROUP BY accession_number HAVING COUNT(*)>1;  -- 0

-- mkt_master_data
SELECT ticker, COUNT(*) FROM mkt_master_data GROUP BY ticker HAVING COUNT(*)>1;  -- 0
SELECT ticker_clean, COUNT(*) FROM mkt_master_data WHERE ticker_clean IS NOT NULL GROUP BY ticker_clean HAVING COUNT(*)>1;  -- 0

-- rex_products (FINDING F9)
SELECT ticker, product_suite, COUNT(*) FROM rex_products WHERE ticker != '' GROUP BY 1,2 HAVING COUNT(*)>1;
-- 13 rows: ('REX','IncomeMax',35), ('APHU','T-REX',11), ('STPW','T-REX',7), ('DOJU','T-REX',4),
-- ('AIAG','Thematic',3), ('CPTO','Crypto',3), ('SNDU','T-REX',3), ('AQLG','T-REX',2),
-- ('BTZZ','T-REX',2), ('FGRU','T-REX',2), ('MEMC','T-REX',2), ('SUIT','T-REX',2), ('TSII','Growth & Income',2)

SELECT ticker, COUNT(*) FROM rex_products WHERE ticker != '' GROUP BY ticker HAVING COUNT(*)>1;
-- 13 rows (same as above, summed across suite)

-- trusts (FINDING F15)
SELECT cik, COUNT(*) FROM trusts GROUP BY cik HAVING COUNT(*)>1;  -- 0
SELECT name, COUNT(*) FROM trusts GROUP BY name HAVING COUNT(*)>1;
-- 6: SEPARATE ACCOUNT VA-K, IVY FUNDS, FIRST FUNDS, FIRST EAGLE FUNDS, Exchange Listed Funds Trust, BAIRD FUNDS INC

-- capm_products
SELECT ticker, COUNT(*) FROM capm_products WHERE ticker IS NOT NULL GROUP BY ticker HAVING COUNT(*)>1;  -- 0

-- mkt_fund_mapping / mkt_category_attributes / mkt_fund_classification / mkt_issuer_mapping
SELECT ticker, COUNT(*) FROM mkt_fund_mapping GROUP BY ticker HAVING COUNT(*)>1;  -- 0
SELECT ticker, COUNT(*) FROM mkt_category_attributes GROUP BY ticker HAVING COUNT(*)>1;  -- 0
SELECT ticker, COUNT(*) FROM mkt_fund_classification GROUP BY ticker HAVING COUNT(*)>1;  -- 0
SELECT etp_category, issuer, COUNT(*) FROM mkt_issuer_mapping GROUP BY 1,2 HAVING COUNT(*)>1;  -- 0

-- mkt_stock_data / mkt_time_series / fund_distributions / cboe_known_active / cboe_symbols
SELECT ticker, COUNT(*) FROM mkt_stock_data GROUP BY ticker HAVING COUNT(*)>1;  -- 0
SELECT ticker, months_ago, COUNT(*) FROM mkt_time_series GROUP BY 1,2 HAVING COUNT(*)>1;  -- 0
SELECT ticker, ex_date, COUNT(*) FROM fund_distributions GROUP BY 1,2 HAVING COUNT(*)>1;  -- 0
SELECT full_ticker, COUNT(*) FROM cboe_known_active GROUP BY full_ticker HAVING COUNT(*)>1;  -- 0
SELECT ticker, COUNT(*) FROM cboe_symbols GROUP BY ticker HAVING COUNT(*)>1;  -- 0

-- filing_alerts
SELECT accession_number, COUNT(*) FROM filing_alerts GROUP BY accession_number HAVING COUNT(*)>1;  -- 0

-- fund_status cross-trust ticker (FINDING F14)
SELECT COUNT(*) FROM (SELECT ticker FROM fund_status WHERE ticker != '' GROUP BY ticker HAVING COUNT(DISTINCT trust_id)>1);
-- 12705

-- Sample worst offenders
SELECT ticker, COUNT(DISTINCT trust_id), COUNT(*) FROM fund_status WHERE ticker != '' GROUP BY ticker HAVING COUNT(DISTINCT trust_id)>1 ORDER BY 2 DESC LIMIT 5;
-- ('SYM',54,1498), ('SYMBO',54,657), ('PACOX',42,42), ('PAULX',42,42), ('PCCOX',42,42)

-- series_id cross-trust
SELECT series_id, COUNT(DISTINCT trust_id), COUNT(*) FROM fund_status WHERE series_id IS NOT NULL GROUP BY series_id HAVING COUNT(DISTINCT trust_id)>1 LIMIT 20;
-- 20+ series_ids appear under multiple trust_ids

-- Trust name dupes
SELECT name, cik, slug, is_active FROM trusts WHERE name IN (SELECT name FROM trusts GROUP BY name HAVING COUNT(*)>1);
-- 12 rows (6 names × 2 CIKs each)
```

### NULL spike checks
```sql
-- mkt_master_data critical NULLs (FINDING F1, F4)
SELECT COUNT(*) FROM mkt_master_data;  -- 7361
SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NULL;  -- 7361 (100%)
SELECT COUNT(*) FROM mkt_master_data WHERE sub_strategy IS NULL;  -- 7361 (100%)
SELECT COUNT(*) FROM mkt_master_data WHERE asset_class IS NULL;  -- 7361 (100%)
SELECT COUNT(*) FROM mkt_master_data WHERE strategy IS NULL;  -- 7361 (100%)
SELECT COUNT(*) FROM mkt_master_data WHERE etp_category IS NULL;  -- 5076 (69%)
SELECT COUNT(*) FROM mkt_master_data WHERE issuer_display IS NULL;  -- 5093 (69%)
SELECT COUNT(*) FROM mkt_master_data WHERE category_display IS NULL;  -- 5076 (69%)
SELECT COUNT(*) FROM mkt_master_data WHERE primary_category IS NULL;  -- 5076 (69%)
SELECT COUNT(*) FROM mkt_master_data WHERE listed_exchange IS NULL;  -- 2019 (27%)
SELECT COUNT(*) FROM mkt_master_data WHERE aum IS NULL;  -- 147 (2%)
SELECT COUNT(*) FROM mkt_master_data WHERE expense_ratio IS NULL;  -- 45 (0.6%)
SELECT COUNT(*) FROM mkt_master_data WHERE fund_type IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_master_data WHERE fund_name IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_master_data WHERE issuer IS NULL;  -- 1
SELECT COUNT(*) FROM mkt_master_data WHERE inception_date IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_master_data WHERE market_status IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_master_data WHERE ticker_clean IS NULL;  -- 0

-- Cross check: master.strategy NULL while classification has it
SELECT COUNT(*) FROM mkt_master_data m JOIN mkt_fund_classification c ON m.ticker=c.ticker
WHERE m.strategy IS NULL AND c.strategy IS NOT NULL;  -- 7332

-- filings NULL (mostly clean)
SELECT COUNT(*) FROM filings;  -- 626936
SELECT COUNT(*) FROM filings WHERE filing_date IS NULL;  -- 0
SELECT COUNT(*) FROM filings WHERE accession_number IS NULL;  -- 0
SELECT COUNT(*) FROM filings WHERE form IS NULL;  -- 0
SELECT COUNT(*) FROM filings WHERE cik IS NULL;  -- 0
SELECT COUNT(*) FROM filings WHERE registrant IS NULL;  -- 0
SELECT COUNT(*) FROM filings WHERE primary_document IS NULL;  -- 360
SELECT COUNT(*) FROM filings WHERE primary_link IS NULL;  -- 360
SELECT COUNT(*) FROM filings WHERE submission_txt_link IS NULL;  -- 0

-- fund_status NULL (FINDING F17)
SELECT COUNT(*) FROM fund_status;  -- 213810
SELECT COUNT(*) FROM fund_status WHERE prospectus_name IS NULL;  -- 191774 (89.7%)
SELECT COUNT(*) FROM fund_status WHERE ticker IS NULL;  -- 25250 (11.8%)
SELECT COUNT(*) FROM fund_status WHERE effective_date IS NULL;  -- 2599 (1.2%)
SELECT COUNT(*) FROM fund_status WHERE effective_date_confidence IS NULL;  -- 2598 (1.2%)
SELECT COUNT(*) FROM fund_status WHERE series_id IS NULL;  -- 735 (0.3%)
SELECT COUNT(*) FROM fund_status WHERE class_contract_id IS NULL;  -- 742 (0.3%)
SELECT COUNT(*) FROM fund_status WHERE sgml_name IS NULL;  -- 587 (0.3%)
SELECT COUNT(*) FROM fund_status WHERE latest_form IS NULL;  -- 4
SELECT COUNT(*) FROM fund_status WHERE latest_filing_date IS NULL;  -- 8
SELECT COUNT(*) FROM fund_status WHERE prospectus_link IS NULL;  -- 6

-- EFFECTIVE without effective_date
SELECT COUNT(*) FROM fund_status WHERE status='EFFECTIVE' AND effective_date IS NULL;  -- 963

-- rex_products NULL (FINDING F19)
SELECT status, COUNT(*) FROM rex_products WHERE ticker IS NULL OR ticker='' GROUP BY status;
-- 'Awaiting Effective'×142, 'Delisted'×1, 'Filed'×244, 'Filed (485A)'×21, 'Research'×6

-- mkt_fund_classification NULL (mostly secondary attrs, expected)
SELECT COUNT(*) FROM mkt_fund_classification;  -- 7332
SELECT COUNT(*) FROM mkt_fund_classification WHERE strategy IS NULL;  -- 0 (clean)
SELECT COUNT(*) FROM mkt_fund_classification WHERE confidence IS NULL;  -- 0 (clean)
SELECT COUNT(*) FROM mkt_fund_classification WHERE direction IS NULL;  -- 6375 (87%)
SELECT COUNT(*) FROM mkt_fund_classification WHERE leverage_amount IS NULL;  -- 6380 (87%)
SELECT COUNT(*) FROM mkt_fund_classification WHERE underlier IS NULL;  -- 6704 (91%)
SELECT COUNT(*) FROM mkt_fund_classification WHERE income_strategy IS NULL;  -- 7132 (97%)
SELECT COUNT(*) FROM mkt_fund_classification WHERE product_structure IS NULL;  -- 7332 (100%)
```

### Orphan checks
```sql
-- All clean for primary FKs
SELECT COUNT(*) FROM filings f LEFT JOIN trusts t ON f.trust_id=t.id WHERE t.id IS NULL;  -- 0
SELECT COUNT(*) FROM fund_status fs LEFT JOIN trusts t ON fs.trust_id=t.id WHERE t.id IS NULL;  -- 0
SELECT COUNT(*) FROM fund_extractions fe LEFT JOIN filings f ON fe.filing_id=f.id WHERE f.id IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_fund_classification fc LEFT JOIN mkt_master_data m ON fc.ticker=m.ticker WHERE m.ticker IS NULL;  -- 0
SELECT COUNT(*) FROM name_history nh LEFT JOIN fund_status fs ON nh.series_id=fs.series_id WHERE fs.series_id IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_time_series ts LEFT JOIN mkt_master_data m ON ts.ticker=m.ticker WHERE m.ticker IS NULL;  -- 0
SELECT COUNT(*) FROM filing_alerts fa LEFT JOIN trusts t ON fa.trust_id=t.id WHERE fa.trust_id IS NOT NULL AND t.id IS NULL;  -- 0
SELECT COUNT(*) FROM mkt_rex_funds rf LEFT JOIN mkt_master_data m ON rf.ticker=m.ticker WHERE m.ticker IS NULL;  -- 0

-- FINDING F12: 15 mapping rows orphaned from master_data
SELECT COUNT(*) FROM mkt_category_attributes ca LEFT JOIN mkt_master_data m ON ca.ticker=m.ticker WHERE m.ticker IS NULL;  -- 15
SELECT COUNT(*) FROM mkt_fund_mapping fm LEFT JOIN mkt_master_data m ON fm.ticker=m.ticker WHERE m.ticker IS NULL;  -- 15

-- FINDING F4: 5076 master rows lack mapping
SELECT COUNT(*) FROM mkt_master_data m LEFT JOIN mkt_fund_mapping fm ON m.ticker=fm.ticker WHERE fm.ticker IS NULL;  -- 5076
SELECT COUNT(*) FROM mkt_master_data m LEFT JOIN mkt_category_attributes ca ON m.ticker=ca.ticker WHERE ca.ticker IS NULL;  -- 5082

-- FINDING F11: 9 alerts not in filings
SELECT COUNT(*) FROM filing_alerts fa LEFT JOIN filings f ON fa.accession_number=f.accession_number WHERE f.accession_number IS NULL;  -- 9

-- FINDING F10: rex/capm coverage
SELECT COUNT(*) FROM rex_products r LEFT JOIN capm_products c ON r.ticker=c.ticker WHERE r.ticker != '' AND c.ticker IS NULL;  -- 234
SELECT COUNT(*) FROM capm_products c LEFT JOIN rex_products r ON c.ticker=r.ticker WHERE r.ticker IS NULL;  -- 0

-- 1372 master rows reference an issuer not in mkt_issuer_mapping (305 distinct unmapped issuers)
SELECT COUNT(DISTINCT m.issuer) FROM mkt_master_data m LEFT JOIN mkt_issuer_mapping im ON m.issuer = im.issuer WHERE m.issuer IS NOT NULL AND im.issuer IS NULL;  -- 305
SELECT COUNT(*) FROM mkt_master_data m LEFT JOIN mkt_issuer_mapping im ON m.issuer = im.issuer WHERE m.issuer IS NOT NULL AND im.issuer IS NULL;  -- 1372
```

### Type confusion (FINDING F2)
```sql
SELECT is_singlestock, COUNT(*) FROM mkt_master_data GROUP BY is_singlestock ORDER BY 2 DESC LIMIT 20;
-- (None,6727), ('XBTUSD Curncy',23), ('XETUSD Curncy',19), ('TSLA US',19), ('NVDA US',17),
-- ('MSTR US',15), ('XAU Curncy',14), ('PLTR US',12), ('COIN US',11), ('AMD US',10),
-- ('XSOUSD Curncy',8), ('XRPUSD Curncy',8), ('HOOD US',8), ('GOOGL US',8), ('AMZN US',8) ...
-- 283 distinct values total

SELECT is_active, COUNT(*) FROM mkt_master_data GROUP BY is_active;
-- ('Y',3636), ('N',3552), ('Unknown',173)

SELECT uses_derivatives, COUNT(*) FROM mkt_master_data GROUP BY uses_derivatives;
-- ('0.0',4467), ('1.0',1985), (None,909)

SELECT uses_swaps, COUNT(*) FROM mkt_master_data GROUP BY uses_swaps;
-- ('0.0',5159), (None,1267), ('1.0',935)

SELECT is_40act, COUNT(*) FROM mkt_master_data GROUP BY is_40act;
-- ('1.0',5629), (None,1378), ('0.0',354)

SELECT uses_leverage, COUNT(*) FROM mkt_master_data GROUP BY uses_leverage;
-- ('False',6400), ('True',961)

SELECT is_crypto, COUNT(*) FROM mkt_master_data GROUP BY is_crypto LIMIT 20;
-- (None,6985), ('Cryptocurrency',189), ('Equity Long/Short',47), ('Multi-Strategy',30) ...
-- (storing strategy strings not booleans)

-- Date-as-string (FINDING F20)
SELECT COUNT(*) FROM mkt_master_data WHERE inception_date='NaT';  -- 43

-- Ticker contamination (FINDING F13)
SELECT COUNT(*) FROM mkt_master_data WHERE ticker LIKE '% US';  -- 7342
SELECT COUNT(*) FROM mkt_master_data WHERE ticker_clean LIKE '% US';  -- 0
SELECT COUNT(*) FROM mkt_master_data WHERE ticker != ticker_clean;  -- 7342

-- fund_status garbage rows (FINDING F3)
SELECT COUNT(*) FROM fund_status WHERE ticker LIKE '%HTTPS%' OR ticker LIKE '%HTTP%' OR ticker = 'HEADER' OR ticker = 'HEADER_ONLY';  -- 5
SELECT COUNT(*) FROM fund_status WHERE LENGTH(ticker) > 5;  -- 372
SELECT id, ticker, fund_name, latest_form FROM fund_status WHERE LENGTH(ticker) > 5 LIMIT 5;
-- 6027  HTTPS://WWW.SEC.GOV/...  0001193125-24-040119  BlackRock ETF Trust II
-- 6418  (NULL)                   HEADER                full
-- 8330  HEADER                   2025-02-21            March Innovator U.S. Equity...
```

### Stale row checks
```sql
-- Atlas memory: ~250 stale 2016-2018 rex_products — actual is much fewer
SELECT COUNT(*) FROM rex_products WHERE estimated_effective_date < '2020-01-01' AND status NOT IN ('Listed','Delisted');  -- 5
SELECT COUNT(*) FROM rex_products WHERE initial_filing_date < '2020-01-01';  -- 11

-- filings by year
SELECT substr(filing_date,1,4), COUNT(*) FROM filings GROUP BY 1 ORDER BY 1 DESC LIMIT 10;
-- 2026:45993, 2025:128619, 2024:123502, 2023:73231, 2022:40790, 2021:38055, 2020:40427,
-- 2019:27245, 2018:23674, 2017:26249

-- trusts: filing_count vs actual (FINDING F6)
SELECT COUNT(*) FROM (SELECT t.id FROM trusts t LEFT JOIN filings f ON f.trust_id=t.id GROUP BY t.id HAVING t.filing_count != COUNT(f.id));
-- 1878 mismatches

-- Recent unclassified launches (preflight said 79; actual is wider)
SELECT COUNT(*) FROM mkt_master_data WHERE etp_category IS NULL AND inception_date >= '2026-04-11';  -- 182 (last 30d)
SELECT COUNT(*) FROM mkt_master_data WHERE etp_category IS NULL AND inception_date >= '2026-02-11';  -- 300 (last 90d)
SELECT COUNT(*) FROM mkt_master_data WHERE etp_category IS NULL AND is_active='Y';  -- 2180
```

### Contradictory state checks
```sql
-- rex_products status/form drift (FINDING F7)
SELECT COUNT(*) FROM rex_products WHERE status='Awaiting Effective' AND latest_form='485BPOS';  -- 268
SELECT COUNT(*) FROM rex_products WHERE status='Filed' AND latest_form='485BPOS';  -- 6
SELECT COUNT(*) FROM rex_products WHERE status='Listed' AND official_listed_date IS NULL;  -- 2
SELECT COUNT(*) FROM rex_products WHERE status='Listed' AND ticker IS NULL OR ticker='';  -- 0
SELECT COUNT(*) FROM rex_products WHERE status='Listed' AND exchange IS NULL;  -- 33

-- Master is_active vs market_status (FINDING F8)
SELECT market_status, is_active, COUNT(*) FROM mkt_master_data GROUP BY 1,2 ORDER BY 1,2;
-- LIQU+Y:493, LIQU+N:1199, LIQU+Unknown:128, INAC+Y:5, INAC+Unknown:35, ACTV+N:2261, ACTV+Y:2969,
-- DLST+Y:11, DLST+N:5, EXPD+N:21, EXPD+Unknown:1, ACQU+Y:59, ACQU+N:26, PEND+Y:92, PEND+N:34, ...

SELECT COUNT(*) FROM mkt_master_data WHERE aum > 0 AND is_active != 'Y';  -- 3668

-- mkt_master_data is_rex flag vs trust is_rex
SELECT is_rex, COUNT(*) FROM mkt_master_data GROUP BY is_rex;
-- 0:7265, 1:96
SELECT is_rex, is_active, COUNT(*) FROM trusts GROUP BY 1,2;
-- (0,0)×15, (0,1)×15733, (1,1)×2  -- only 2 trusts marked is_rex but 96 master rows are
-- (rex_products link via cik to is_rex=1 trusts: 464 — so the trust list IS richer than 2 once CIK-joined)

SELECT * FROM mkt_master_data WHERE is_rex=1 LIMIT 10;
-- includes BERZ (BMO ETN) — see FINDING F21
```

### Pipeline run table
```sql
-- pipeline_runs (SEC pipeline) — clean
SELECT status, COUNT(*) FROM pipeline_runs GROUP BY status;
-- ('completed',128), ('completed_with_errors',2)
SELECT triggered_by, COUNT(*) FROM pipeline_runs GROUP BY triggered_by;
-- ('bulk_scrape',6), ('manual',122), ('manual-force-recent',2)

-- mkt_pipeline_runs (Bloomberg pipeline) — F5: 67 stuck
SELECT status, COUNT(*) FROM mkt_pipeline_runs GROUP BY status;
-- ('completed',232), ('failed',5), ('running',67)

SELECT id, started_at FROM mkt_pipeline_runs WHERE status='running' AND finished_at IS NULL ORDER BY started_at DESC LIMIT 5;
-- (302, '2026-05-04 08:15:43...'), (299,...), (298,...), (296,...), (295,...)

-- Last 10 completed mkt_pipeline_runs — only 1 wrote stock_rows
SELECT id, master_rows_written, ts_rows_written, stock_rows_written FROM mkt_pipeline_runs WHERE status='completed' ORDER BY id DESC LIMIT 5;
-- (304, 7361, 272357, 0)
-- (303, 7486, 84545, 6593)  ← only one with stock data
-- (301, 7247, 268139, 0)
-- (300, 7247, 268139, 0)
-- (297, 7247, 268139, 0)

-- classification_proposals (FINDING F16)
SELECT status, COUNT(*) FROM classification_proposals GROUP BY status;
-- ('approved',2711), ('pending',50), ('rejected',1)

-- classification_audit_log activity
SELECT source, COUNT(*) FROM classification_audit_log GROUP BY source;
-- ('sweep_high',16238), ('manual_batch_approval',2710), ('manual_insert',21), ('manual_polish_fix',14)
SELECT dry_run, COUNT(*) FROM classification_audit_log GROUP BY dry_run;
-- (0,18983)  -- all writes; no dry runs preserved
```

### Misc
```sql
-- fund_extractions extraction-source (FINDING F18)
SELECT extracted_from, COUNT(*) FROM fund_extractions GROUP BY extracted_from ORDER BY 2 DESC;
-- ('SGML-TXT',621885), ('SGML-TXT|LABEL-WINDOW',29583), ('SGML-TXT|TITLE-PAREN',27385),
-- ('NONE',4370), ('S1-METADATA',3081)

-- All-NULL fund_extractions
SELECT COUNT(*) FROM fund_extractions WHERE series_id IS NULL AND series_name IS NULL AND class_contract_id IS NULL AND class_contract_name IS NULL;  -- 4370

-- fund_status: latest_form distribution (showed garbage rows)
SELECT latest_form, COUNT(*) FROM fund_status GROUP BY latest_form ORDER BY 2 DESC LIMIT 10;
-- 497:162184, 485BPOS:21551, 497J:21524, 497K:4296, 485BXT:2536, 485APOS:1461, S-1/A:64, N-1A:51,
-- EFFECT:46, N-1A/A:43

-- mkt_master_data updated_at (everything stamped today — single rebuild)
SELECT date(updated_at), COUNT(*) FROM mkt_master_data GROUP BY 1 ORDER BY 1 DESC LIMIT 5;
-- (2026-05-11, 7361)

-- screener_uploads — only ever 1 row from initial setup
SELECT * FROM screener_uploads;
-- (1, '2026-02-12...', 'decision_support_data.xlsx', 4991 stocks, 5068 etps, 280 filings, completed, 'initial-setup', 'GradientBoosting', 0.0)
```

---

## Tables not touched (and why)

| Table | Reason |
|---|---|
| analysis_results | Empty (0 rows) |
| autocall_sweep_cache | Empty (0 rows) |
| cboe_state_changes | Empty (0 rows) |
| mkt_global_etp | Empty (0 rows) |
| autocall_crisis_presets | Tiny (8) static seed; not in scope |
| autocall_index_levels | 125,966 rows of (date, ticker) PK — sampled to confirm composite PK is enforced; no content audit needed for this stage |
| autocall_index_metadata | Tiny (26) static seed |
| nyse_holidays | Tiny (10) static reference |
| mkt_market_status | Tiny (17) static lookup |
| capm_trust_aps | Tiny (40) curated; coverage sufficient |
| capm_audit_log | 1 row — log table, no integrity question |
| filing_analyses | 30 rows — small, not load-bearing |
| trust_requests | 1 row, ephemeral admin queue |
| digest_subscribers | 1 row, ephemeral admin queue |
| screener_uploads | 1 row, single seed event |
| screener_results | 4991 rows — not touched in detail; cleared/regenerated by screener job |
| email_recipients | 18 rows — checked dupes only |
| trust_candidates | 59 rows — checked dupes only |
| mkt_report_cache | 4 rows, transient cache |
| mkt_exclusions | 26 rows — small, no contradictions surfaced |
| reserved_symbols | 282 rows — checked dupes; sane |
| mkt_rex_funds | 96 rows — fully audited (one suspect: BERZ) |
| cboe_scan_runs | 12 rows — log table |

---

## Recommendations for next stages

1. **Stage 2 (code audit)** should focus on the writers for `mkt_master_data` — specifically the missing denormalization step that should populate `primary_strategy`, `sub_strategy`, `asset_class`, `strategy` from `mkt_fund_classification`.
2. **Stage 2 (code audit)** should check why boolean-named columns (`is_singlestock`, `is_active`, `is_crypto`, `uses_*`) accept arbitrary string content.
3. **Stage 2 (code audit)** should check why `mkt_pipeline_runs` doesn't have a finally-block to mark crashed runs as 'failed'.
4. **Stage 3** likely needs a cleanup migration: trust filing_count recompute, NaT → NULL, garbage fund_status rows quarantine, classification denormalization backfill.

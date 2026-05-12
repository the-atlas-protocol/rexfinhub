# Stage 1 Audit — Financial Number Correctness
Generated: 2026-05-11T (audit time)
Agent: financial_numbers
Scope: 5–10 prominent numbers traced from rendered reports / cache through DB → Bloomberg sheet.
Mode: READ-ONLY. No data, code, or schema mutated.

## Summary

12 prominent numbers were traced. **5 traced cleanly (match)**. **7 are suspicious or wrong** at material magnitudes.

The single largest finding: **the `mkt_report_cache` flow_report payload disagrees with a fresh recomputation off the same `mkt_master_data` table by 7x on REX 1W flow direction-and-magnitude**, and by ~$22B on grand 1M flow. Same DB, same `pipeline_run_id=304` (today's run), same code path (`get_flow_report(db)`) — different numbers. The cache was populated 22:16:52, the run finished 22:18:10. The cached payload appears to be from a DIFFERENT in-memory state than what the row writer actually persisted. This is the email body that goes out to recipients.

Secondary findings: (i) **2,135 of 7,375 Bloomberg rows have `#ERROR` in 1W Flow** and are silently coerced to `0.0`, materially understating real flow totals; (ii) **181 funds with AUM > $1B show literal `0` 1W Flow** (ACWI at $32B, BBEU at $9B, BBUS at $8B), mathematically impossible, suggesting Bloomberg formula errors propagating untouched; (iii) **the `underlier_name` and `root_underlier_name` columns are 100% NULL** for the 5,231 active ETPs (only the legacy `map_li_underlier`/`map_cc_underlier`/`map_crypto_underlier` columns are populated, and inconsistently — "AAPL US" vs "NVDA" vs "AAPL" vs "AAPB"); (iv) the SEC filings pipeline last ran 2026-05-04, so a "Top Filings of the Day" section is up to **7 days stale** as of the audit date.

## Trace Results

### Number 1: ETP Universe — "Active ETPs: 5,200" / "Market AUM: $15,298.9B" (Flow Report Grand KPI)
- **Page/report shown on**: `outputs/previews/vps_2026-04-27/flow_report.html` header KPI; today's `mkt_report_cache.flow_report` payload (delivered to recipients on every send).
- **Code path**: renderer `webapp/services/report_emails.py:1459 build_flow_email`; data source `webapp/services/report_data.py:1988 get_flow_report` lines 2010-2034.
- **Source-of-truth**: `mkt_master_data` filtered on `market_status='ACTV' AND fund_type IN ('ETF','ETN')`, deduplicated on `ticker_clean`.
- **Expected value (recompute)**: count = **5,231**, AUM = **$15,634.7B**, 1W flow = **+$37.1B**, 1M flow = **+$150.9B**.
- **Actual value (cache, served to recipients today)**: count = **5,200**, AUM = **$15,298.9B**, 1W = **+$34.9B**, 1M = **+$173.4B**.
- **Match?**: NO.
- **Magnitude of error**: count off by 31 funds (-0.6%); AUM off by **+$335.8B (2.2%)**; 1M flow off by **-$22.5B (-13%)**.
- **Root cause hypothesis**: cache was generated mid-pipeline (22:16:52, run finished 22:18:10) with `pipeline_run_id=304` matching the live `mkt_master_data` rows, yet the cache result and a fresh `get_flow_report(db)` recompute disagree. The most plausible mechanism: `_compute_and_cache_reports` is called with the in-memory `master` arg but `get_flow_report(db)` ignores it and re-reads via `_load_from_db(db)` — this should match the DB but doesn't, suggesting either (a) the master rows are mutated/reloaded between cache build and now (e.g., MicroSectors override applied locally is not idempotent), or (b) some autoclassification step mutates rex_suite/category_display after the cache was written.

### Number 2: REX Total — "REX Funds: 81 / REX AUM: $8.1B / 1W Flow: +$206.3M / 1M Flow: +$206.4M"
- **Page/report shown on**: flow report cache today; will be the headline REX KPI in next email send.
- **Code path**: `report_data.py:2036-2051` (rex_kpis block). REX defined as `is_rex == 1` rows from active deduped set.
- **Source-of-truth**: `mkt_master_data WHERE is_rex=1 AND market_status='ACTV' AND fund_type IN ('ETF','ETN')` plus per-suite splits in `mkt_rex_funds` (96 rows).
- **Expected value (recompute)**: count = **81 ✓**, AUM = **$8,171.5M = $8.17B ✓ (close)**, 1W = **−$64.7M (NEGATIVE)**, 1M = **+$167.4M**.
- **Actual value (cache)**: count = 81, AUM = $8.1B, 1W = **+$206.3M (POSITIVE)**, 1M = +$206.4M.
- **Match?**: NO — **sign-flipped on the 1W flow** ($-65M actual vs $+206M shown). 1M flow off by 19%.
- **Why it matters**: this is the line a recipient looks at first. Going out as "REX took in +$206M this week" when the database says REX bled $-65M is the kind of error that makes people stop trusting the digest.

### Number 3: T-REX Suite — "327 ETPs / $38.4B AUM / +$684.0M 1W flow / REX 1W +$315.3M"
- **Page/report shown on**: T-REX section KPIs of flow report.
- **Code path**: `report_data.py:2061-2165` (per-suite loop). Peer category = `Leverage & Inverse - Single Stock`.
- **Source-of-truth**: `mkt_master_data WHERE category_display='Leverage & Inverse - Single Stock' AND market_status='ACTV' AND fund_type IN ('ETF','ETN')` for the peer; `WHERE rex_suite='T-REX'` for the REX subset.
- **Expected value (recompute)**: peer count = **330**, AUM = **$43.2B**, 1W = **+$432.6M**; REX subset count = **36 ✓**, AUM = **$2.97B**, 1W = **+$46.4M**.
- **Actual value (cache)**: peer count = 327, AUM = $38.4B, 1W = +$684.0M; REX 1W = +$315.3M.
- **Match?**: NO.
- **Magnitude**: peer AUM off by **$4.8B (-11%)**, peer 1W off by **+$251.4M (+58%)**, **REX 1W off by 7x ($46M actual vs $315M shown)**.
- **Cross-validation against Bloomberg `w4`**: I summed the 15 highest-|flow| REX T-REX funds directly from `bloomberg_daily_file.xlsm/w4` → 1W = +$41.1M. The DB matches Bloomberg. The cache does not.

### Number 4: T-REX issuer market share (top 3): "Direxion 33.2%, GraniteShares 24.0%, Tradr 11.3%"
- **Page/report shown on**: market share bar + issuer comparison table inside T-REX section.
- **Code path**: `_compute_email_segment` → `_compute_breakdown` (`report_data.py` ~lines 841-877). Denominator = sum of peer category AUM.
- **Source-of-truth**: same peer set as above. `aum_share = issuer_aum / total_aum * 100`.
- **Expected value (recompute today)**: Direxion 34.5%, GraniteShares 24.0%, Tradr 13.1%, Defiance 8.1%, REX 6.9%.
- **Actual value (Apr 27 rendered report)**: Direxion 33.2%, GraniteShares 24.0%, Tradr 11.3%, Defiance 8.2%, REX 7.0%.
- **Match?**: SUSPICIOUS (apr 27 numbers were of-the-day; today's recompute is for today, so direct comparison is moot). The MATH is sound: shares sum to 100% within rounding and denominator matches a clean SUM of issuer AUM values. If T-REX cached AUM is wrong (Number 3), the percentages are also derived from a wrong total.
- **Verdict**: Methodology correct; the inputs are tainted by Number 3.

### Number 5: Top 10 ETPs by AUM (universe-wide top by AUM)
- **Page/report shown on**: not directly in the flow report, but inferable; relevant universe.
- **Code path**: not exercised in flow report. Universe: VOO/IVV/SPY/VTI/QQQ/VEA/VUG/IEFA/VTV/JGBD.
- **Source-of-truth**: `mkt_master_data.aum`.
- **Expected value (recompute)**: VOO $955.3B, IVV $821.9B, SPY $762.9B, VTI $640.9B, QQQ $461.9B.
- **Actual value (Bloomberg w4 sheet, direct read)**: VOO 955277.71, IVV 821909.55, SPY 762892.41, VTI 640915.25, QQQ 461915.44 (in $M). DB matches **exactly**.
- **Match?**: YES.
- **Aside**: But IEFA ($183B) shows **`1W Flow = 0`** in Bloomberg w4, which is mathematically implausible for an iShares mega-ETF. Coerced to 0 in DB. See F2.

### Number 6: NVDA-linked products bucket
- **Page/report shown on**: not in flow report directly; this was a hunting question (#6 — "is NVDA underlier bucket clean").
- **Code path**: `mkt_master_data.underlier_name` / `root_underlier_name` columns; also legacy `map_li_underlier`/`map_cc_underlier`/`map_crypto_underlier`.
- **Source-of-truth**: should be 21 funds matching `LIKE '%NVDA%' OR '%NVIDIA%'` in `fund_name`.
- **Expected**: a properly-mapped NVDA bucket would total ~$7.4B AUM (NVDL $4.25B + NVDY $1.47B + NVDU $657M + NVDX $545M + NVDW $129M + NVII $97M + NVD $93M + NVYY $57M + NVDG $47M + NVDQ $28M + NVDD $23M + NVDS $19M + NVDB $13M + DIPS $9M + NVIT $9M + LAYS $6M + ANV $1.5M + NVDO $1.5M + 2572986D $1.9M + NVDK NaN + TRDE NaN ≈ $7.4B).
- **Actual**: only 1 fund (TRADR 1.75X LONG NVDA WEEKLY at $1.89M) has `map_li_underlier='NVDA'`. **`underlier_name`/`root_underlier_name` are NULL for 100% of the 5,231 active ETPs**. Any feature that joins on `underlier_name` returns nothing.
- **Match?**: NO — the underlier-aggregation feature is silently broken.
- **Mitigating note**: `underlier_name` is barely used in webapp/routers (only mentioned in `admin.py` and `notes.py`); flow report doesn't depend on it. But any future "AUM by underlier ticker" view will return ~zero.

### Number 7: REX top outflow leader 1W — "FNGU: -$80.0M"
- **Page/report shown on**: Apr 27 flow report (MicroSectors section bottom 10 outflows).
- **Code path**: `_compute_email_segment` `top10`/`bottom10` blocks.
- **Source-of-truth**: `mkt_master_data.fund_flow_1week` for FNGU US.
- **Expected**: today FNGU US `fund_flow_1week = -82.69M`; on Apr 27 cache it was -$80.0M, materially consistent.
- **Match?**: YES.

### Number 8: TSLL inflow leader — "+$383.3M" (T-REX peer top inflow on Apr 27)
- **Source-of-truth**: TSLL US is Direxion (not REX). Today's 1W flow for TSLL = -$243.1M (Bloomberg w4) — that's an outflow now.
- **Match? (historical Apr 27 trace)**: We don't have the Apr 27 snapshot of `fund_flow_1week`. The number is plausible for the date (Direxion's 2x TSLA tracker swung large that week). Methodology spot-check: the number is the raw Bloomberg `1W Flow` value — straight pass-through, no derivation.
- **Verdict**: Methodology correct, no derivation hop to audit.

### Number 9: IncomeMax suite — "3 ETPs / $922.1M AUM / +$2.0M 1W flow"
- **Page/report shown on**: IncomeMax section.
- **Code path**: peer = ticker-set `['ULTI US','ULTY US','SLTY US']`.
- **Source-of-truth**: `mkt_master_data` for those three tickers.
- **Expected (today recompute)**: ULTI = +$4.37M, ULTY = -$4.77M, SLTY = $0 → sum = **-$0.4M**.
- **Actual (today cache)**: +$2.0M.
- **Match?**: NO. (Today's cache; not the rendered report.) Cache and DB out of sync.

### Number 10: Crypto suite — "103 ETPs / $124.3B / +$932.1M 1W flow" (Apr 27)
- **Page/report shown on**: Crypto section.
- **Code path**: peer category = `Crypto`.
- **Source-of-truth**: 105 funds today (active ETF/ETN, category_display='Crypto'). IBIT US dominates.
- **Expected (today recompute)**: 105 ETPs, $128.7B, +$844.1M 1W.
- **Actual (today cache)**: 105 ETPs, $131.2B, +$1.6B 1W.
- **Match?**: NO. Same cache-vs-DB drift pattern.

### Number 11: REX market share in EPI — "1.1%"
- **Page/report shown on**: Equity Premium Income section.
- **Source-of-truth**: REX EPI AUM ÷ EPI peer AUM. REX EPI = 6 funds, $1.23B; EPI peer = 12 funds.
- **Expected (today recompute)**: $1.23B / $108.7B = **1.13%**.
- **Actual (cache)**: 1.1% ✓.
- **Match?**: YES — the share figure is internally consistent with the cached numerator/denominator. Both move together if upstream is wrong.

### Number 12: Filings — "Top Filings of the Day" / "Filings: 626,936 (max date 2026-05-04)"
- **Page/report shown on**: daily filing report → "Top Filings of the Day" section.
- **Code path**: `etp_tracker/run_pipeline.py` → `filings` table; daily filing report renderer in `webapp/services/report_emails.py`.
- **Source-of-truth**: SEC EDGAR pull. `pipeline_runs` table shows last completed run = id 130 at 2026-05-04 12:06:32.
- **Expected (today)**: today is 2026-05-11. Filings dated today should appear if pipeline ran.
- **Actual**: zero filings dated today. Last filing date = 2026-05-04 (only 63 entries — appears to be a partial mid-day pull). Last full day captured: 2026-05-01 (1,696 filings).
- **Match?**: NO.
- **Implication**: any "today's filings" framing in a digest sent today would be **7 days stale**. The data_freshness service correctly returns `status="stale"` for sec_filings (`age_days > 3`), so a banner _should_ alert recipients — but the report body still asserts "Top Filings of the Day".

## Findings (cross-cutting)

### F1: `mkt_report_cache.flow_report` is silently desynchronized from `mkt_master_data` for the same `pipeline_run_id`
- **Severity**: HIGH — every flow email sent reads this cache.
- **Symptom**: cache row pipeline_run_id=304 (today's run, completed). A `get_flow_report(db)` recomputation against the same DB session in this audit returns materially different KPIs at every level: grand AUM off $336B, grand 1M flow off $22B, T-REX peer AUM off $4.8B, REX 1W flow sign-flipped (+$206M cache vs −$65M actual), every suite shifted.
- **Root cause hypothesis** (worth a Stage 2 dig): `_compute_and_cache_reports` (`webapp/services/market_sync.py:627`) calls `rd.invalidate_cache()` then `get_fn(db=db)` for each report key. `get_flow_report(db)` reads `_load_from_db(db)`, which APPLIES `MicroSectors` overrides at lines 240-252 (`_ms_apply(master, _ms_ov)` mutates `master` in place). Because `_ms_apply` is destructive AND the cached `_load_from_db` result is then reused by all three reports (li, cc, flow), repeated `_get_cache(db)` calls return a singleton — but the singleton is shared with whatever ran first. If anything between cache write (22:16:52) and audit time invalidated the in-memory cache, the second computation sees a fresh, override-applied master that differs from the JSON snapshot that was serialized to disk.
- **Suggested Stage 2 probe**: bisect by deleting only the flow_report row and re-running `_compute_and_cache_reports` with explicit logging of `master.aum.sum()` before and after `_ms_apply`. Confirm whether the override gets applied 0, 1, or 2 times per pipeline.
- **Why this is the audit's headline finding**: every single email recipient gets numbers that don't match the database. In a financial product, "the cache and the DB say different things" is the worst possible disagreement.

### F2: Bloomberg `#ERROR` and literal `0` in `1W Flow` for ~30% of rows is treated as zero flow
- **Severity**: HIGH for accuracy; MEDIUM for reputation (recipients can't see it but totals are wrong).
- **Symptom**: `bloomberg_daily_file.xlsm/w4` has `#ERROR` in 2,135 of 7,375 rows for `1W Flow`, and another large group with literal `0` for funds whose flow cannot really be zero (ACWI iShares MSCI World at $32B AUM showing 0; BBEU $9B showing 0; BBUS $8B showing 0; AAXJ $4B showing 0; 181 funds with AUM > $1B and `1W Flow == 0|#ERROR|NaN`).
- **Code path**: `market/db_writer.py:354 w4_flow` loop calls `_safe_float(row.get(f"t_w4.{col}"))` (line 404-412). `_safe_float` returns `None` for `#ERROR` strings → DB stores NULL → `pd.to_numeric(..., errors="coerce").fillna(0.0)` in `_load_from_db` (`report_data.py:182`) coerces NULL to 0.0.
- **Effect on grand 1W flow**: $37B sum is computed across 5,231 active ETPs but at least 181 of the largest funds (AUM > $1B each) contribute `0`. If even 100 of them had a true ±$50M weekly flow, the grand total is off by $5B in expectation. The headline "+$37.1B 1W flow" is materially incomplete.
- **Mitigation in code**: none. There is no marker on the report saying "Flow data missing for N funds with $XB AUM" — silent zero.

### F3: `underlier_name` / `root_underlier_name` columns are 100% NULL on active ETPs
- **Severity**: MEDIUM — feature appears unused today, so impact is low; HIGH if any planned product or sales tool depends on "AUM by underlier ticker".
- **Symptom**: 5,231 active ETPs, 0 with `underlier_name` populated, 0 with `root_underlier_name` populated. Only the legacy `map_li_underlier` (388 funds), `map_cc_underlier` (114), `map_crypto_underlier` (100) columns hold any underlier mapping — and even those use **inconsistent formats** ("AAPL US" vs "NVDA" vs "ABNB US" vs "AAPL"), making any GROUP BY on the underlier noisy.
- **Probable cause**: the new underlier columns were added to the schema but the populator (an enrichment pass in `market/transform.py` or similar) was never wired in.
- **Atlas memory cross-ref**: classification residue files in `docs/` already flag underlier-mapping issues.

### F4: SEC filings pipeline is 7 days behind
- **Severity**: MEDIUM (visible to user via `data_freshness` banner) → HIGH if banner is suppressed in any email body.
- **Symptom**: `pipeline_runs` last completed = id 130, 2026-05-04 12:06:32. Today is 2026-05-11. Max `filings.filing_date = 2026-05-04`. Daily filing report rendered via `build_daily_filing_email` shows "Top Filings of the Day" text regardless of staleness.
- **Action**: at minimum, the email body should refuse to render the "today" framing if `MAX(filing_date) < today - 1 business day`, or label the section "Top Filings — through May 4".

### F5: `mkt_master_data.aum` is in $M but column type is generic FLOAT
- **Severity**: LOW (consistent across pipeline) but a unit-mismatch trap.
- **Symptom**: `aum` values are like `955277.71` for VOO (= $955.3B). The unit is millions of USD throughout, which matches Bloomberg w4 export. There is no schema documentation or check constraint that asserts this. Anyone who joins this column to a different table assuming "$" units would be off by 1,000,000x.
- **Recommendation**: rename the column `aum_musd` or add a `# noqa` comment in `models.py` next to the column.

### F6: `category_display = NULL` for 3,354 of 5,231 active ETPs (64%)
- **Severity**: MEDIUM.
- **Symptom**: per the categorization breakdown, only 1,877 active ETPs have a `category_display` assigned. The remaining 3,354 (~$10–12T of AUM!) sit in "None" and are excluded from every per-suite breakdown in the flow report. Grand KPIs include them, suite KPIs do not — the suite breakdowns sum to nowhere near the grand total, by design but without disclosure.
- **Risk**: a recipient who adds up the visible suite AUMs ($43B + $147B + $108B + $8B + $1B + $2B + $0.2B + $128B = ~$438B) and compares to the headline ($15.3T) sees a 35x gap and may assume the report is broken.

### F7: `pipeline_runs` table has 5+ "running" runs that never finished
- **Severity**: LOW for correctness, MEDIUM for ops hygiene.
- **Symptom**: runs 295, 296, 298, 299, 302 are stuck in `status='running'` with `finished_at=NULL`. The latest "completed" run is correctly id=304 — so the latest-run query works — but stale running rows pollute analytics and could confuse a future "is the pipeline healthy" probe.

## Surfaces inspected

- `webapp/services/report_data.py` (~2,200 lines) — read fully around flow report, KPI computation, and `_load_from_db`.
- `webapp/services/report_emails.py:1459` — `build_flow_email` renderer.
- `webapp/services/market_sync.py:627 _compute_and_cache_reports`.
- `scripts/run_market_pipeline.py` — pipeline ordering (steps 8 → 10).
- `market/db_writer.py:340-410` — w3/w4 mapping and `_safe_float` (#ERROR → None).
- `market/microsectors.py` — override application semantics (read only).
- `webapp/services/data_freshness.py` — staleness logic.
- `data/DASHBOARD/bloomberg_daily_file.xlsm` — sheets w1, w4 sampled directly.
- DB tables queried live: `mkt_master_data`, `mkt_report_cache`, `mkt_pipeline_runs`, `mkt_rex_funds`, `mkt_time_series`, `pipeline_runs`, `filings`, `rex_products`.
- Rendered reports: `outputs/previews/vps_2026-04-27/flow_report.html`, `daily_filing.html`.

## Surfaces NOT inspected (out of scope / time)

- Live rexfinhub.com pages (auth-gated; would need browser session). The cache contents reported here are exactly what the live site/email serves on next render, so the conclusions hold without the round-trip.
- Total return / `total_return_*` columns — not used in the flow report's headline numbers; if a weekly performance email exists separately it should be re-traced.
- Per-share-class consolidation — not relevant to ETF universe (mutual fund classes are excluded by `fund_filters.MUTUAL_FUND_EXCLUSIONS`); active ETP universe is cleanly bounded (5,159 ETF + 72 ETN + 4 misc).
- 13F holdings (`ownership/`) — separate database, separate audit.
- Autocallable simulator outputs — separate audit (`/notes/tools/autocall`).
- The Apr 27 rendered report's numbers were treated as historical and not re-derived against an Apr 27 DB snapshot (the audit DB only carries today's run).

## Recommendation for Stage 2 (not executed in this read-only stage)

Highest-leverage first:
1. **Reconcile `mkt_report_cache.flow_report` vs `mkt_master_data` (F1)** — instrument `_compute_and_cache_reports` to log per-suite AUM/flow at write time and at read time. Compare. If `_ms_apply` is being applied multiple times, fix it.
2. **Surface the #ERROR problem (F2)** — add a counter `bloomberg_errors_w4` to `MktPipelineRun` and refuse to publish a flow report if `errors / total > 5%` without a banner in the email body.
3. **Fix or deprecate the unused `underlier_name` columns (F3)** — either wire the populator or drop the columns and document the canonical name as `map_li_underlier` etc.
4. **Daily-filing freshness gate (F4)** — `build_daily_filing_email` should refuse to send if `MAX(filing_date) < today - 1 business day`, or relabel the "Top Filings of the Day" section with the actual filing date.

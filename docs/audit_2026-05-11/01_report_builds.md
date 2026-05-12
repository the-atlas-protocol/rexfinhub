# Stage 1 Audit — Report Builds
Generated: 2026-05-11T18:55:00-04:00
Agent: report_builds

## Summary
Walked all five report builders + the standalone weekly-recommender (stock_recs).
Inspected today's prebaked HTML pulled from the VPS (built 2026-05-11 16:16-16:19 ET).
Ran live SQL against the local `data/etp_tracker.db` (note: laptop DB is a week stale —
filings only through 2026-05-04 — VPS DB has 2026-05-11 data; market cache rows on
laptop DB are from pipeline_run 304 finished 2026-05-11 22:18). 13 findings logged.
Severity: 2 critical, 4 high, 4 medium, 3 low. The headline issue is upstream
(100% NULL `primary_strategy`, 69% NULL `etp_category` and `category_display`,
69% NULL `issuer_display`) — the report builders themselves silently SKIP rather
than surface bad rows, so today's emails ship with **invisible holes** rather than
visible errors. The exception is the entire "Funds in Pipeline" section of the
Daily ETP report, which is hard-coded to require `primary_strategy IS NOT NULL` and
therefore renders **zero rows** every day until upstream classification is fixed.

## Report inventory
| Report | Builder file | DB tables read | Critical columns | Cache? |
|---|---|---|---|---|
| Daily ETP | `etp_tracker/email_alerts.py::build_digest_html_from_db` (renderer in `_render_daily_html`, data in `_gather_daily_data` + `_gather_market_snapshot` + `_gather_pipeline_funds`) | `mkt_master_data`, `mkt_report_cache(li/cc/flow)`, `filings`, `fund_extractions`, `fund_status`, `trusts`, `filing_analysis` | `etp_category`, `issuer_display`, `is_rex`, `fund_type`, `market_status`, `inception_date`, `t_w4.aum`, `t_w4.fund_flow_1day/1week`, `primary_strategy` (Funds-in-Pipeline only), `series_id` (filing-dedup) | reads `mkt_report_cache` for the Bloomberg-derived sections; LLM-driven Top Filings cached via `FilingAnalysis` table — re-renders make zero LLM calls |
| Weekly ETP | `etp_tracker/weekly_digest.py::build_weekly_digest_html` | `mkt_master_data`, `filings`, `fund_extractions`, `fund_status`, `trusts` | `category_display`, `rex_suite`, `issuer_display`, `is_rex`, `fund_type`, `market_status`, `aum`, `fund_flow_1week` | reuses `_get_cache(db)` master in-memory dict; no row-level cache |
| L&I weekly | `webapp/services/report_emails.py::build_li_email` → `webapp/services/report_data.py::get_li_report` | `mkt_report_cache(report_key='li_report')` (always), `mkt_master_data` + `mkt_time_series` (cache build only) | `etp_category='LI'`, `fund_type IN ('ETF','ETN')`, `issuer_display`, `map_li_subcategory`, `map_li_category`, `map_li_underlier`, `map_li_direction`, `is_rex`, `aum`, `fund_flow_1week/1month` | YES — `mkt_report_cache` row keyed `li_report`; rebuilt by pipeline run; staleness is run-id based |
| Income (CC) weekly | `webapp/services/report_emails.py::build_cc_email` → `get_cc_report` | `mkt_report_cache(report_key='cc_report')` | `etp_category='CC'`, `cc_category`, `cc_type`, `issuer_display`, `annualized_yield`, `is_rex`, `aum`, flows | YES — same caching pattern |
| Flow weekly | `webapp/services/report_emails.py::build_flow_email` → `get_flow_report` | `mkt_report_cache(report_key='flow_report')` | `category_display`, `rex_suite`, `cc_category`, `issuer_display`, `is_rex`, `fund_type`, `market_status`, `aum`, flows | YES — same; falls through to `get_flow_report(None)` if cache lacks `grand_kpis` (legacy v4 schema) |
| Autocall weekly | `webapp/services/report_emails.py::build_autocall_email` (extracts the `Autocallable` suite from `get_flow_report`) | same as Flow + `data/DASHBOARD/exports/autocall_ranks/*.json` (week-over-week rank deltas) + `attributes_CC.csv` | suite name match `"autocall" in label`, `peer_cc_category='Autocallable'`, `peer_name_filter=(?i)autocall` | piggy-backs on `flow_report` cache; rank delta files are filesystem JSON |
| stock_recs (Mon-only) | `screener/li_engine/analysis/weekly_v2_report.py` | `data/etp_tracker.db` (filings, fund_extractions, mkt_master_data) + parquets at `data/analysis/{whitespace_v4,launch_candidates,bbg_timeseries_panel,competitor_counts}.parquet` | `primary_category='LI'` (NOT `primary_strategy`), `map_li_underlier`, `map_li_direction`, `is_rex`, `market_status` | parquets last refreshed 2026-05-07 12:00 (4 days stale on a Mon-Tue send); pre-built HTML at `reports/li_weekly_v2_<date>.html` |

## Findings

### F1: Funds in Pipeline section ships ZERO rows (filter requires column that is 100% NULL)
- **Report affected**: daily
- **Severity**: critical
- **Surface**: `etp_tracker/email_alerts.py:1383-1393` (`_gather_pipeline_funds`)
- **Symptom**: The "Funds in Pipeline (PEND/DELAYED)" section is rendered conditionally: `if not pipeline_funds: return ""`. Section is silently OMITTED from every Daily ETP email until upstream classification fills `primary_strategy`.
- **Evidence**:
  - SQL filter literal: `WHERE market_status IN ('PEND', 'DLST') AND primary_strategy IS NOT NULL AND fund_name IS NOT NULL AND fund_name != ''`
  - DB check (laptop, pipeline_run 304): `SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NOT NULL` → **0**. Total rows: 7361. PEND/DLST rows that pass full pipeline filter: **0**.
  - Today's `daily_filing.html` from VPS: zero `Funds in Pipeline` substring matches. Section silently absent.
  - 142 rows have `market_status IN ('PEND','DLST')`. All would be eligible if `primary_category` were used instead of `primary_strategy`.
- **Blast radius**: Daily ETP report — entire pipeline section. Loss-of-information only (no false data shipped).
- **Hypothesis**: `primary_strategy` is a column added by a sweep that has never run, OR was renamed from `primary_category` and the consumer was not migrated. `primary_category` IS populated for 31% of rows (LI 877, Defined 520, Thematic 410, CC 344, Crypto 134).
- **Fix size**: trivial — one-line column swap, OR drop the `primary_strategy IS NOT NULL` predicate

### F2: 65% of active ETFs have NULL `etp_category` → silently dropped from L&I/CC/Flow filters
- **Report affected**: li, income, flow, autocall, weekly
- **Severity**: critical
- **Surface**: `webapp/services/report_data.py:1114` (LI), `:1409` (CC), `_load_all` lines 437-454 (where `etp_category` is merged)
- **Symptom**: `master[master["etp_category"] == "LI"]` and `== "CC"` filters silently exclude every fund without a classification. Flow report's `peer_category` filters do the same via `category_display` (also 69% NULL).
- **Evidence**:
  - `SELECT fund_type, COUNT(*), SUM(CASE WHEN etp_category IS NULL OR etp_category='' THEN 1 ELSE 0 END) FROM mkt_master_data WHERE market_status='ACTV' GROUP BY fund_type` → ETF 5159 / **3339 NULL** (65%); ETN 72 / 15 NULL.
  - `category_display` NULL count: 5076/7361 (69%). Only Defined (520), L&I-Index (481), Thematic (410), L&I-SS (396), Income-Index (222), Crypto (134), Income-SS (114), Income-Unknown (8) are populated.
  - 7d new launches with NO `etp_category`: **63 of 64**. Only `FIYY US` (GraniteShares YieldBoost 20Y+ Treasuries) made it into the LI bucket.
- **Blast radius**: Every category-segmented section in 4 reports. AUM totals, market-share %, flow rankings all under-count by ~65%.
- **Hypothesis**: Classification sweep doesn't fire fast enough; new launches arrive in `mkt_master_data` before `apply_classification_sweep.py` tags them.
- **Fix size**: small (extend default classification "Other" bucket so unclassified funds at least show up as Other in totals, or make the sweep blocking before report cache rebuild)

### F3: 79% of recent launches with no `issuer_display` ship to the daily report's "New Fund Launches" section as `issuer_display=None`
- **Report affected**: daily
- **Severity**: high
- **Surface**: `etp_tracker/email_alerts.py:1533` (`issuer = str(row.get("issuer_display", row.get("issuer", "")))`) — falls back to raw `issuer` field, which itself is "ETF Series Solutions/Aptus Cap" type Bloomberg-trust-name strings that aren't user-friendly
- **Symptom**: For 7d launches with NULL issuer_display, the rendered Launches table shows raw issuer text (e.g. "ETF Series Solutions/Aptus Cap") OR an empty cell when issuer is also NULL. Sample audit from local DB: 8/8 of 7d unclassified launches have `issuer_display=None`. Today's daily_filing.html includes IBCB GR (German UCITS) in the Launches list because the launch query does NOT filter to US-listed tickers.
- **Evidence**: `SELECT ticker, issuer, issuer_display FROM mkt_master_data WHERE inception_date >= date('now','-7 days') AND market_status='ACTV' AND fund_type IN ('ETF','ETN') AND (etp_category IS NULL OR etp_category='') ORDER BY inception_date DESC LIMIT 8` returned 8 rows, all with `issuer_display=None`. One row was IBCB GR (LSXExchange).
- **Blast radius**: Daily ETP "New Fund Launches" — up to ~10 launches/week shown with empty or raw issuer text; non-US tickers (e.g. `IBCB GR`) appear without filtering.
- **Hypothesis**: (a) issuer_mapping CSV is missing recent issuers; (b) launch query lacks `ticker LIKE '% US'` or `listed_exchange` US filter.
- **Fix size**: small (add US-listing predicate; fall back to a humanised issuer label rather than empty string)

### F4: `_send_html_digest` L7/L6/L8 safeguards are NOT applied to the Weekly digest send path
- **Report affected**: weekly
- **Severity**: high
- **Surface**: `etp_tracker/weekly_digest.py:1414-1439` (`_send_weekly_html`)
- **Symptom**: Weekly digest uses its own send wrapper that only checks the L1 gate file (`config/.send_enabled`). It does NOT enforce L6 (per-recipient daily rate limit), L7 (self-loop block), or L8 (audit log). The 4 L&I / Income / Flow / Autocall reports DO go through `_send_html_digest` (L1+L6+L7+L8). So the weekly is a privileged channel.
- **Evidence**: `_send_weekly_html` reads `_gate` then calls `webapp.services.graph_email.send_email` directly. No `_audit_send` calls. No `_recipients_over_limit_today` check. No self-loop check.
- **Blast radius**: Weekly bundle — one of 5 reports skips the safety net Ryu built after the autocall self-loop incident.
- **Hypothesis**: `weekly_digest.py` predates the L6/L7/L8 work in `email_alerts._send_html_digest`. Was never refactored to use the shared sender.
- **Fix size**: small (route through `_send_html_digest`)

### F5: Weekly digest defaults `_load_recipients()` to `list_type="daily"` instead of `"weekly"`
- **Report affected**: weekly
- **Severity**: medium
- **Surface**: `etp_tracker/weekly_digest.py:1448` calls `_load_recipients()` with no `list_type` arg → falls back to `list_type="daily"` (default in `email_alerts.py:61`)
- **Symptom**: The weekly DB recipient list is never queried. Subscribers added to the weekly list won't receive the weekly. Production happens to be safe today only because both lists currently contain the same single address (`etfupdates@rexfin.com`).
- **Evidence**: `get_recipients(db, 'daily')` and `get_recipients(db, 'weekly')` both return `['etfupdates@rexfin.com']`; coincidentally identical.
- **Blast radius**: The weekly's recipient list is invisible / non-functional. Anyone added via the weekly subscriber UI would never receive emails.
- **Fix size**: trivial (pass `list_type="weekly"`)

### F6: `_PER_RECIPIENT_DAILY_LIMIT = 6` is precisely sized for the all-bundle pile-up — one extra report would silently block delivery
- **Report affected**: all (when sent via `_send_html_digest`)
- **Severity**: medium
- **Surface**: `etp_tracker/email_alerts.py:1796` constant; checked in `_recipients_over_limit_today`
- **Symptom**: `etfupdates@rexfin.com` is on **5 lists** (daily, weekly, li, income, flow). A bundle send fires daily=1 + weekly=1 + li=1 + income=1 + flow=1 = 5 emails to that address. Limit is 6. If autocall list is ever added, OR the Monday product_status report is re-enabled, the 6th send proceeds and the 7th (or any same-day re-send / `--force`) silently 0-returns.
- **Evidence**: recipient-cross-list count audit (Counter): `etfupdates@rexfin.com → 5x`; only address on 2+ lists. Comment at line 1794 acknowledges sizing is deliberate.
- **Blast radius**: Future expansion of report count → silent send failures. Today: zero extra headroom.
- **Fix size**: trivial (raise to 8 or compute dynamically from registry)

### F7: Top Filings of the Day caches forever — re-runs after schema/prompt change still serve old analyses
- **Report affected**: daily
- **Severity**: medium
- **Surface**: `etp_tracker/filing_analysis.py:179-186` ("Canonical-set semantics: once we have MAX_PICKS cached entries for a date, further calls for that date do NOT re-pick or re-analyze")
- **Symptom**: Today's prebaked report's Top Filings section is healthy (2 well-formed analyses). But once 3 entries are cached for a date in `FilingAnalysis`, no further LLM calls happen for that date — even if the prompt has changed or a more interesting filing arrived later in the day. There's no staleness check on `analyzed_at`.
- **Evidence**: code comment "Cache-first ... ZERO LLM calls" + early-return at `if len(rendered) >= MAX_PICKS or not uncached`. No expiry on `FilingAnalysis` rows.
- **Blast radius**: Daily ETP — Top Filings can be silently stale (e.g. an interesting late-day OpenAI-style filing arrives after morning bake; never gets considered).
- **Fix size**: small (add a "candidate must have been seen at first bake" gate, or version-key the prompt)

### F8: Daily report pipeline filters use `since_date` (today's filings only) but laptop DB shows it can lag by days — no warning if the SEC pipeline hasn't run today
- **Report affected**: daily
- **Severity**: medium
- **Surface**: `etp_tracker/email_alerts.py:1502-1507` (`since_date = today.strftime("%Y-%m-%d")`)
- **Symptom**: When SEC pipeline doesn't run today, the report shows "No 485 filings today" (rendered via `_render_filings_block` empty branch) — but this is INDISTINGUISHABLE from a quiet-news day. Bloomberg sections still render normally because they read the cache, not today's filings.
- **Evidence**: Laptop DB latest filing = 2026-05-04 (today is 2026-05-11). Local report would say "No 485 filings today" for 7 consecutive days with no alarm. There IS a bbg-staleness preflight in `scripts/send_email.py:425-446`, but no analogous SEC-pipeline freshness guard.
- **Blast radius**: Daily ETP filings sections false-negative on stale pipeline.
- **Fix size**: small (add SEC freshness check parallel to bbg)

### F9: Daily report's "New Fund Launches" section pulls non-US tickers (e.g. German UCITS `IBCB GR`)
- **Report affected**: daily
- **Severity**: medium
- **Surface**: `etp_tracker/email_alerts.py:1521-1547` (Bloomberg launches query)
- **Symptom**: Filter is `fund_type IN ['ETF','ETN'] AND market_status IN ['ACTV','Active'] AND inception_date in last 7 days`. No US-listing filter. Today's launches list includes `IBCB GR` (iShares Emerging Asia Local Govt Bond UCITS ETF), which has nothing to do with REX's market.
- **Evidence**: live query returned `('IBCB GR', 'LSXExchange', 'ETF')` as 7d new launch with NULL issuer_display. Section logic does not exclude.
- **Blast radius**: Daily ETP launches — 1 spurious row visible today. Will grow with more EU UCITS data ingestion.
- **Fix size**: trivial (`AND ticker LIKE '% US'` or `listed_exchange in (US exchanges)`)

### F10: stock_recs report uses parquets that are 4 days stale (rebuilt 2026-05-07 12:00)
- **Report affected**: stock_recs (Mon bundle)
- **Severity**: medium
- **Surface**: `screener/li_engine/analysis/weekly_v2_report.py:32-35` reads `data/analysis/whitespace_v4.parquet`, `launch_candidates.parquet`, `bbg_timeseries_panel.parquet`, `competitor_counts.parquet`
- **Symptom**: `data/analysis/whitespace_v4.parquet` mtime 2026-05-07 12:01; `bbg_timeseries_panel.parquet` mtime 2026-04-23 17:21 (**18 days stale**). The whitespace + filing-race screener decisions for the week's stock recommendations are computed off these parquets. There is NO check that the parquet was rebuilt this week.
- **Evidence**: `ls -la data/analysis/`:
  - `bbg_timeseries_panel.parquet  Apr 23 17:21` (18 days old)
  - `whitespace_v4.parquet  May  7 12:01` (4 days old)
  - `launch_candidates.parquet  May  7 12:01`
  - `competitor_counts.parquet  May  7 12:00`
- **Blast radius**: Weekly L&I "Top Picks" reflect stale market signals; whitespace scores may not include this week's competitor filings.
- **Fix size**: medium (add freshness check + alert; or rebuild parquets pre-Monday in the systemd schedule)

### F11: `mkt_master_data` ticker is NOT unique — `master.drop_duplicates(subset=["ticker_clean"], keep="first")` is the only thing preventing fanout
- **Report affected**: all (Bloomberg-derived)
- **Severity**: high
- **Surface**: `webapp/services/report_data.py:1522` and `:2020` and `:2077` and `:2121` etc. — multiple sites do `drop_duplicates(subset=["ticker_clean"], keep="first")`
- **Symptom**: A multi-category ticker (one classification row per category, e.g. a fund tagged both LI-SS and Income-SS) appears N times. Every aggregation is wrapped in `drop_duplicates`, but if any future code path forgets the dedup, AUM and flow numbers double-count silently.
- **Evidence**: Comment at line 2019 "Deduplicate by ticker for grand KPIs (multi-category tickers appear N times)". The L&I `_compute_email_segment` does NOT dedup before summing — relies on `etp_category=='LI'` filter to keep one row per ticker. Defensible today, fragile to future schema changes.
- **Blast radius**: Future-tense — any new aggregation path that forgets dedup will silently inflate KPIs.
- **Fix size**: medium (move dedup to `_load_from_db`/`_load_all` so master is canonical; or enforce `(ticker, category)` PK + always pivot)

### F12: Daily ETP send uses bundle-level dedup keys but `.send_log.json` has stale legacy entries that could trigger BLOCKED
- **Report affected**: daily, weekly
- **Severity**: low
- **Surface**: `scripts/send_email.py:80-102` (`_already_sent_today`, `_already_sent_this_week`)
- **Symptom**: `.send_log.json` contains both legacy bundle-level keys (`"daily": "21:49"` from March) AND new per-report keys (`"daily_filing": "21:55"` from April). The dedup checks current keys (`daily_filing`, `weekly_report`, `li_report`, etc.) so the legacy entries are inert today. But ANY accidental key collision between a future report and an old bundle name would trigger a silent BLOCK.
- **Evidence**: `.send_log.json` shows entries dating back to 2026-03-23 with mixed key styles. Most recent entry: 2026-04-16 (laptop). Production `.send_log.json` lives on VPS; not inspected.
- **Blast radius**: Future bug surface; not actively breaking.
- **Fix size**: trivial (rotate the file; document the key namespace)

### F13: Hostname mismatch — all reports link to `rex-etp-tracker.onrender.com` not the custom domain `rexfinhub.com`
- **Report affected**: daily, weekly, li, income, flow (autocall correctly suppresses links)
- **Severity**: low
- **Surface**: `scripts/send_email.py:22` `DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"`; `scripts/prebake_reports.py:52` `DEFAULT_RENDER_URL = "https://rex-etp-tracker.onrender.com"`
- **Symptom**: Dashboard CTA buttons in 4 reports link to the Render-internal URL. The custom domain `rexfinhub.com` is what users were told the site lives at. Recipients clicking through see the Render URL, not the friendly one. Discovered via `grep -oE "https://[a-z0-9.-]+/" daily_filing.html` → only `https://rex-etp-tracker.onrender.com/` and `https://www.sec.gov/`.
- **Blast radius**: Branding / UX. No data correctness issue.
- **Fix size**: trivial (one-line constant change in two files)

## Rendered report inspection
Pulled today's prebaked HTML from `jarvis@46.224.126.196:/home/jarvis/rexfinhub/data/prebaked_reports/` (baked 2026-05-11 16:16-16:19 ET). Saved to `C:/Projects/rexfinhub/temp/audit_2026-05-11/`.

### `daily_filing.html` (68.8 KB)
1. **Funds in Pipeline section: ABSENT** — `grep -c "Funds in Pipeline"` returns 0. Should be ~142 PEND/DLST funds. F1.
2. **New Fund Launches: includes `IBCB GR`** — German UCITS in a US-product report. F9.
3. **Top Filings of the Day section healthy** — 2 well-rendered LLM analyses (YieldMax OpenAI synthetic CC + Themes Trust Applied Intuition 2X suite). Includes "Note: AI Generated Content" disclaimer.
4. **Upcoming Effectiveness — single trust block with 88 funds + dates** — `ETF Opportunities Trust` row visually overwhelming; entire fund list concatenated into one cell with parenthetical effective dates.
5. **No "Unknown" / "Unclassified" / "NaN" placeholder strings** — verified by `grep -oE ">--<|>None<|>nan<"` returns 0 matches in daily_filing.html.
6. **`https://rex-etp-tracker.onrender.com` is the only dashboard URL** — F13.
7. **Filings Section concatenates ALL fund names into one cell** — 24 funds for "Themes ETF Trust" all in one paragraph; very long visual rows.

### `li_report.html` (993 KB — heavy because of inline base64 chart images)
1. 4 instances of `nan` substring — all inside base64-encoded PNG chart blobs (matplotlib version string `Matplotlib version 3.10.8`), NOT in displayed text. Safe.
2. 27 `>--<` cells — likely empty flow/return values; expected behavior of `_fmt_currency(0)` returning `"--"`.
3. Issuer column populated; sample top10 issuers all canonical (Direxion, GraniteShares, Tradr, Defiance, REX). Cache `mkt_report_cache(li_report)` health verified: 48 providers, 0 with Unknown/None/blank issuer, 13 ss_issuers, 44 index_issuers.

### `flow_report.html` (173 KB)
1. 19 `>--<` cells. No `Unknown`. 8 suites all rendered: T-REX, MicroSectors, EPI, G&I, IncomeMax, Autocallable, Thematic, Crypto.
2. Cache shows: grand_kpis count=5200 (matches active ETF+ETN dedup), total AUM $15.3T, 1W flow +$34.9B. REX market share 0.1%.

### `income_report.html` (852 KB — base64 charts again)
1. 17 `>--<` cells. No suspicious strings.
2. CC cache: yields populated cleanly (NVDY 68.1%, MSTY 215.3%, JEPQ 10.4% — all reasonable).

### `autocall_report.html` (46 KB)
1. Zero `--`, zero Unknown, zero NaN. Cleanest report of the bunch.
2. Autocallable suite: 15 ETPs across 8 issuers — verified in cache.
3. Correctly suppresses internal dashboard links (`dashboard_url = ""`).

### `li_weekly_v2_2026-05-11.html` (83 KB — stock_recs)
1. 8 `--`/`Unknown`/`empty` matches — within typical limits for "no recommendation this week" placeholders. Not investigated further given parquet staleness flag in F10.

## Surfaces inspected
- `scripts/send_email.py` — full read
- `scripts/send_all.py` — full read
- `scripts/prebake_reports.py` — full read
- `scripts/generate_competitive_filing_report.py` — partial (header + ISSUER_MAP)
- `etp_tracker/email_alerts.py` — full read across 4 reads (lines 1-1500, 1500-2495)
- `etp_tracker/weekly_digest.py` — read top 90, builder 1303-1408, sender 1414-1467
- `etp_tracker/filing_analysis.py` — full read 1-288
- `webapp/services/report_emails.py` — read 1-300, 1326-1815
- `webapp/services/report_data.py` — read 1-200, 200-510, 893-1052, 1080-1380, 1380-1580, 1900-2187
- `screener/li_engine/analysis/weekly_v2_report.py` — read 1-150, 329-450
- VPS prebaked reports for 5 emails + stock_recs (HTML inspection)
- Live SQL: 7 queries against `data/etp_tracker.db` covering NULL counts, recipient lists, pipeline_runs, mkt_report_cache contents, fund_extractions duplicate hazard
- Recipient cross-list duplicate scan via `webapp.services.recipients.get_recipients`

## Surfaces NOT inspected
- `webapp/routers/reports.py` (preview routes) — not opened
- `webapp/routers/digest.py` (digest subscribe routes) — not opened
- `webapp/routers/analysis.py` — not opened
- `webapp/services/recipients.py` — only invoked, not read for L1-L7-equivalent enforcement
- `webapp/services/report_emails.py` lines 300-1326 (chart/segment renderers and Income highlights)
- `webapp/services/report_emails.py` lines 1815-2032 (autocall section continuation past Section 1)
- `webapp/services/report_data.py` lines 510-893 (chart helpers, time-series aggs), 1052-1080 (helpers), 1580-1900 (single-stock report `get_ss_report` — not in the 5 reports), 2187-end
- `webapp/services/recipients.py` — DB recipient model & list semantics
- `screener/li_engine/analysis/weekly_v2_report.py` lines 150-329 (yaml override + ticker-name resolver) and 450+ (other loaders)
- `screener/filing_screener_report.py` — referenced in the disabled `_build_filing_screener` builder but not currently shipped
- `etp_tracker/intelligence_brief.py` — referenced in the disabled `_build_intelligence_brief` builder
- `etp_tracker/product_status_report.py` — referenced in the disabled Monday `_build_product_status` builder
- `webapp/templates/reports/{leveraged_inverse,covered_call,single_stock}.html` — admin-preview templates, not the email-render path
- VPS `data/.send_log.json`, `data/.send_audit.json`, `data/.gate_state_log.jsonl` — only laptop versions inspected
- `config/email_recipients.txt` fallback path (only DB recipient path tested)

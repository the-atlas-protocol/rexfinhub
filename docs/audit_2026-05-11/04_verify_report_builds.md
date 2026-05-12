# Re-Audit — Report Builds Verification (Stage 1 → Stage 4)
Generated: 2026-05-11T22:00:00-04:00
Agent: report_builds_reverify
Verdict at a glance: **2 critical fixes verified, 1 critical PARTIALLY fixed (still 94% of AUM unclassified), 7 of 13 Stage 1 findings remain open, 1 NEW finding surfaced.**

## Inputs verified
- Stage 1 findings: `docs/audit_2026-05-11/01_report_builds.md`
- VPS prebaked HTML pulled to `temp/audit_reverify_2026-05-11/` (timestamps below)
- VPS DB: `jarvis@46.224.126.196:/home/jarvis/rexfinhub/data/etp_tracker.db`
- VPS clock: `Mon May 11 09:47 PM EDT 2026`

| Report | Baked at (UTC from meta) | Bytes |
|---|---|---|
| daily_filing | 2026-05-11T21:36:32 | 107,083 |
| li_report | 2026-05-11T21:37:59 | 978,871 |
| flow_report | 2026-05-11T21:38:10 | 173,285 |
| income_report | 2026-05-11T21:38:09 | 847,513 |
| autocall_report | 2026-05-11T21:38:25 | 46,001 |
| weekly_report | 2026-05-11T21:37:35 | 69,989 |

Pipeline runs: most recent `pipeline_runs.id=154` finished `2026-05-12 00:04:21 UTC` (8:04 PM ET). `mkt_report_cache` rows for li/cc/flow all have `data_as_of='May 11, 2026'`, `data_as_of_short='05/11/2026'`, written by run 341 at `2026-05-12 01:03 UTC` (9:03 PM ET). Subject date generator `_data_date()` reads `data_as_of_short` from li_report cache, so **subject lines say "05/11/2026"** — matches today's ET date. **R7 fix verified.**

## Stage 1 status table

| ID | Severity | Status | Notes |
|---|---|---|---|
| F1 — Funds in Pipeline ships zero rows | critical | **FIXED** | "Funds in Pipeline (PEND/DELAYED) — 132 classified" header rendered; 5 sub-strategy tables (Income/LI/Defined/Crypto/Thematic); 49 PEND+DLST badges in section. R1+R2 populated `primary_strategy` to 99.4% of ACTV. |
| F2 — 65% of ACTV ETFs NULL etp_category | critical | **PARTIALLY FIXED — STILL CRITICAL** | T1 backfill brought etp_category from ~31% to **37.1%** of ACTV rows. But **AUM-weighted impact is much worse**: of $15.63T ACTV ETF/ETN AUM, only $0.90T is classified — **$14.74T (94.3%) is still NULL etp_category**. See "Re-quantification of F2" below. |
| F3 — issuer_display NULL on 79% of new launches | high | **OPEN** | 826 of 5,231 ACTV ETF/ETN rows still have NULL `issuer_display` (15.8% of all, much higher among recent launches: 11/15 of latest 7d launches). Daily report's launches table now omits the Issuer column entirely (workaround, not a fix). |
| F4 — Weekly digest bypasses L6/L7/L8 safeguards | high | **OPEN** | `etp_tracker/weekly_digest.py::_send_weekly_html` still calls `graph_email.send_email` directly. No `_audit_send`, no `_recipients_over_limit_today`, no self-loop check. |
| F5 — Weekly defaults to `list_type="daily"` | medium | **OPEN** | `weekly_digest.py:1448` still calls `_load_recipients()` with no arg. Weekly subscriber list still ignored. |
| F6 — `_PER_RECIPIENT_DAILY_LIMIT = 6` is exactly tight | medium | **OPEN** | Constant unchanged in `etp_tracker/email_alerts.py`. `etfupdates@rexfin.com` still on 5 lists. |
| F7 — Top Filings caches forever | medium | **OPEN** | `filing_analysis.py` MAX_PICKS gate unchanged; no `analyzed_at` staleness/expiry. |
| F8 — No SEC pipeline freshness preflight | medium | **OPEN** | `scripts/send_email.py` has bbg preflight only; no SEC-side check. (Today VPS is fine: latest filing_date = 2026-05-11.) |
| F9 — Daily launches include non-US (`IBCB GR`) | medium | **MASKED, NOT FIXED** | `IBCB GR` no longer appears in today's launches table — but only because its `inception_date=NaT` accidentally trips the `(inception >= cutoff) & (inception <= today_ts)` filter and drops it. Source code unchanged: still no `ticker LIKE '% US'` filter. Any non-US ticker WITH a valid inception_date in the last 7d will still leak. |
| F10 — stock_recs parquets stale | medium | **OPEN** | Local parquets unchanged: `whitespace_v4.parquet` mtime 2026-05-07 (4d old), `bbg_timeseries_panel.parquet` mtime 2026-04-23 (**18d stale**). Today's `li_weekly_v2_2026-05-11.html` exists on VPS so a Mon report did bake — but on stale signals. |
| F11 — `mkt_master_data` ticker not unique; relies on dedup | high | **OPEN** | `webapp/services/report_data.py` still has 4 sites doing `drop_duplicates(subset=["ticker_clean"], keep="first")` (lines 1766, 2057, 2114, 2158) plus 3 other `drop_duplicates` patterns. Pattern unchanged. |
| F12 — `.send_log.json` legacy entries | low | **OPEN** | Code unchanged; not inspected on VPS this round. |
| F13 — Reports link to onrender.com not rexfinhub.com | low | **OPEN** | `scripts/send_email.py` `DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"`; `scripts/prebake_reports.py` `DEFAULT_RENDER_URL` same. Confirmed in rendered HTML: `li_report.html`, `flow_report.html`, `income_report.html` all link to `https://rex-etp-tracker.onrender.com/` (autocall correctly suppresses, daily has no dashboard link). |

## Re-quantification of F2 (the headline finding)

The Stage 1 audit reported "65% of active ETFs have NULL `etp_category`". After T1 backfill the percentage by **row count** improved to 62.9% NULL (37.1% populated) — directionally better. But the underlying business impact, **AUM-weighted classification coverage**, is far worse than the row count suggested:

```
ACTV ETF/ETN, by etp_category (counts and aum in $T):
  <NULL>      count=3292  aum=$14.74T   <-- still 94.3% of total AUM
  Thematic    count= 349  aum= $0.28T
  LI          count= 619  aum= $0.19T
  CC          count= 334  aum= $0.19T
  Crypto      count= 106  aum= $0.13T
  Defined     count= 531  aum= $0.11T
  ----
  TOTAL ACTV ETF/ETN AUM: $15.63T (matches Bloomberg market sum)
  Classified AUM:         $0.90T  (5.7%)
  Missing AUM:           $14.74T  (94.3%)
```

Implication: every "Total AUM" / "Market share" / "Top by AUM" computation in the L&I, Income, Flow, Autocall, and Weekly reports is anchored on 5.7% of the actual ETF/ETN universe. The reports' own counts confirm: weekly_report shows market totals like "$8.2B AUM by Suite" and individual issuer cards (Direxion $14.9B, ProShares $83.5B, JP Morgan $83.2B, BlackRock $73.1B, $62.0B) — but the broader "market share" denominator the reports use is itself the classified slice, not the full $15.63T market. T1 made a dent in the count, not the dollars.

The big classified-by-AUM names that are missing are the giant index ETFs (SPY, IVV, VOO, QQQ, etc. — ~$3T+ of AUM individually) which T1's backfill didn't touch because they live outside REX's L&I/CC/Crypto/Defined/Thematic taxonomy. **Recommendation: F2's fix needs an explicit "Other / Plain-Vanilla" bucket so AUM totals match Bloomberg's universe**, OR all per-report KPIs need to switch their denominator to the full ACTV ETF/ETN universe and treat unclassified as "Other".

## Re-quantification of F1 (verified clean)

Pipeline section is now populated:
- Source DB: 132 of 142 PEND/DLST rows pass the `primary_strategy IS NOT NULL AND fund_name IS NOT NULL` filter (was 0/142 in Stage 1)
- Rendered HTML: header reads "Funds in Pipeline (PEND/DELAYED) — 132 classified" with 5 sub-tables (Income 4, plus LI/Defined/Crypto/Thematic). 49 PEND/DLST status badges visible.
- 10 PEND/DLST rows still excluded — those without `primary_strategy` populated. Worth a Stage-2 check that R2 covers those edge cases (likely PEND funds with no Bloomberg fields populated yet).

## Visual placeholder scan (all 6 reports)

After stripping base64-encoded chart images, scanned for `nan`, `None`, `Unclassified`, `Unknown`, and `>--<` empty cells:

| Report | nan | None | Unclassified | Unknown | empty cells |
|---|---|---|---|---|---|
| daily_filing | 0 | 2* | 0 | 0 | 0 |
| li_report | 0 | 0 | 0 | 0 | 27 |
| flow_report | 0 | 0 | 0 | 0 | 18 |
| income_report | 0 | 0 | 0 | 0 | 16 |
| autocall_report | 0 | 0 | 0 | 0 | 0 |
| weekly_report | 0 | 0 | 0 | 0 | 5 |

\* The 2 `None` matches in daily_filing are inside Top Filings LLM analysis prose ("Distribution: None disclosed"), not data placeholders. Safe.

The `>--<` empty cells in li/flow/income are `_fmt_currency(0)` / missing-flow returns — expected behavior, matches Stage 1 baseline (Stage 1 reported 27/19/17 vs today's 27/18/16 — within rounding noise).

No `Unclassified` or `Unknown` strings reached any report. Categorisation undercount is **silent**, exactly as F2 predicted.

## URL hostname verification (F13)

```
li_report.html       https://rex-etp-tracker.onrender.com/  (CTA links)
flow_report.html     https://rex-etp-tracker.onrender.com/
income_report.html   https://rex-etp-tracker.onrender.com/
daily_filing.html    https://www.sec.gov/  (only SEC links; no dashboard CTA today)
weekly_report.html   no clickable host found in scan
autocall_report.html no dashboard link (correctly suppressed)
```

Custom domain `rexfinhub.com` still not used.

## NEW FINDING — F14: Daily-report launches table dropped the Issuer column

**Severity**: low (UX/regression)

`temp/audit_reverify_2026-05-11/daily_filing.html` "New Fund Launches (7d)" table now has only 4 columns: Ticker / Fund Name / AUM / Launched. Stage 1 audit (F3) reported a 5-column table including Issuer. The Issuer column has been removed from the launches table — likely as a workaround for F3's NULL issuer_display problem.

Surface: the loop in `etp_tracker/email_alerts.py:1536-1553` still BUILDS the issuer field and includes it in the dict (`"trust_name": issuer`), so the renderer is what dropped the column. Need to grep `_render_daily_html` for the launches block to confirm the rendering change.

Impact: recipients lose at-a-glance issuer context for new launches. They have to recognize the issuer from the fund name (which works for "WISDOMTREE..." but not for "RSIT" or "BUYB").

Fix size: trivial — re-add the column once F3 (issuer_display backfill) lands.

## NEW FINDING — F15: F9's apparent fix is coincidental, not real

**Severity**: medium (silent regression hazard)

The Stage 1 audit's `IBCB GR` example no longer appears in today's daily launches. But this is **not because the source was fixed** — it's because that single row's `inception_date='NaT'` (literal string in DB), which fails the `(inception >= cutoff) & (inception <= today_ts)` numeric comparison silently.

Source code at `etp_tracker/email_alerts.py:1517-1532` is unchanged: still no `ticker LIKE '% US'` predicate, still no `listed_exchange IN (...)` filter. Any future non-US ticker with a real inception_date will leak into the daily report.

Verification:
```sql
SELECT ticker, ticker_clean, market_status, inception_date FROM mkt_master_data WHERE ticker LIKE 'IBCB%';
-- ('IBCB GR', 'IBCB GR', 'ACTV', 'NaT')          <-- excluded by NaT, not by US filter
-- ('IBCB US', 'IBCB',    'ACTV', '2026-03-26')
```

Fix size: trivial — apply the original Stage 1 fix recommendation (`ticker LIKE '% US'`).

## Cache freshness verification

```
mkt_report_cache (most recent rows):
  screener_3x   run=None  as_of=May 11, 2026  updated=2026-05-12 01:05:37 UTC
  flow_report   run=341   as_of=May 11, 2026  updated=2026-05-12 01:03:37 UTC
  cc_report     run=341   as_of=May 11, 2026  updated=2026-05-12 01:03:36 UTC
  li_report     run=341   as_of=May 11, 2026  updated=2026-05-12 01:03:35 UTC
```

R5 cache rebuild verified clean: all four report caches built by run 341 within seconds of each other, all dated today's ET date. Prebaked HTML (baked 21:36-21:38 UTC = 5:36-5:38 PM ET) was generated 4-7 hours BEFORE the latest cache update at 9:03 PM ET — meaning the prebaked HTML on VPS may be slightly older than the latest cache. If anyone re-bakes after run 341, the reports would pull fresher data. Worth confirming the systemd schedule re-bakes after each cache rebuild.

## Verdict

**Pass with reservations.**

- F1: fully fixed, verified end-to-end (DB → builder → rendered HTML).
- R7 (subject date in ET): verified.
- R5 (cache rebuilt clean): verified.
- F2: only partially fixed. Row-count classification went 31% → 37%, but **AUM-weighted classification is still 5.7%** of the ACTV ETF/ETN universe. Reports' "market share" KPIs are computed against a tiny slice of the market. The fix isn't done.
- F3, F4, F5, F6, F7, F8, F10, F11, F12, F13 — code-side state unchanged from Stage 1. Treat as carried forward.
- 2 new findings (F14, F15) — both low/medium, both regressions/masking artefacts of the fix work.

If the goal of this audit cycle was "verify R1+R2+T1+R5+R7 landed cleanly", the answer is yes for R1+R2+R5+R7 (data flowed through to the rendered report) and **no** for T1 (it dented the row count but not the dollar weight, which is what readers actually look at).

## Surfaces inspected this round
- `docs/audit_2026-05-11/01_report_builds.md` — full read
- VPS `data/prebaked_reports/{daily_filing,li_report,flow_report,income_report,autocall_report,weekly_report}.html` and corresponding `.meta.json` — pulled and inspected
- VPS `data/etp_tracker.db` — 11 SQL queries (NULL counts, AUM aggregation, pipeline_runs, mkt_report_cache, IBCB rows, recent launches)
- `etp_tracker/email_alerts.py` — `_gather_pipeline_funds`, launches block (lines 1516-1555), `_PER_RECIPIENT_DAILY_LIMIT`
- `etp_tracker/weekly_digest.py` — `_send_weekly_html` (full read)
- `etp_tracker/filing_analysis.py` — MAX_PICKS gate
- `scripts/send_email.py` — `_data_date`, `_already_sent_today`, `do_daily`, `do_weekly`, `DASHBOARD_URL`
- `scripts/prebake_reports.py` — `DEFAULT_RENDER_URL`
- `webapp/services/report_data.py` — `drop_duplicates` sites (4 found)
- Local `data/analysis/*.parquet` mtimes

## Surfaces NOT inspected
- `etp_tracker/email_alerts.py::_render_daily_html` — to confirm the Issuer column was dropped from launches (F14)
- VPS `data/.send_log.json`, `data/.send_audit.json`, `data/.gate_state_log.jsonl`
- `webapp/services/report_emails.py` — segment renderers (carried-forward Stage 1 coverage)
- `screener/li_engine/analysis/weekly_v2_report.py` — stock_recs builder

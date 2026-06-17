# HANDOFF / LOG — Report finalization (2026-06-16→17)  [pre-compaction]

Durable state log so work survives context compaction. Goal: every report number
ties out, classification self-heals, effective dates 100%, all reports ready to send.

## WHERE THINGS LIVE (critical context)
- **Active worktree**: `C:\Projects\rexfinhub\.claude\worktrees\fix-reports-2026-06-16`
  (branch `worktree-fix-reports-2026-06-16`). The harness ENFORCES editing in a
  worktree — edits to the shared checkout are rejected. Do code edits here.
  (An earlier worktree `fix-rex-family-2026-06-08` got corrupted by a failed
  `git worktree remove --force`; abandoned. Don't use it.)
- **Main repo**: `C:\Projects\rexfinhub` — canonical. Synced to origin/main. After
  pushing from the worktree, `git -C C:/Projects/rexfinhub merge --ff-only origin/main`
  and `cp <worktree>/data/etp_tracker.db C:/Projects/rexfinhub/data/etp_tracker.db`.
- **DB**: `data/etp_tracker.db` (~807MB) — the good, synced+enriched copy. Both
  worktree and main repo have it. Data fixes applied directly to it; persisted to
  CSVs (config/rules/) so a re-sync reproduces them.
- **Daily file**: pulled from the VPS via `scripts/refresh_bloomberg.py` (the
  permanent stale-Excel fix — VPS has the Graph-fetched current file). 29 sheets,
  has BlueOcean + microsector_flow. T-REX = 41 on this data.
- **Push workflow**: commit in worktree → `git push origin HEAD:main` (account has
  branch-protection bypass) → sync main repo.

## CANONICAL NUMBERS (everything must tie to these)
- T-REX single-stock L&I (ACTV): **41**
- MicroSectors index L&I (ACTV): **22** (incl. DULL/AIQU/AIQD after the subcat fix)
- REX income single-stock (ACTV): **3** (NVII, TSII, WMTI)
- REX income index (ACTV): **8**
- REX total ACTV ETP (all suites): **79**
- Every ACTV ETP has a date (fund_status.effective_date OR inception_date): 0 missing.

## ROOT CAUSES FOUND (the "doesn't tie out" problem)
1. **Stale persisted cache** — `get_li_report`/`get_cc_report` read mkt_report_cache
   that lagged reality. FIX (done): email builders pass `use_cache=False`.
2. **Time-series category drift** — `mkt_time_series.category_display` diverged from
   master; the market-position card reads the time-series. FIX (done): synced the 4
   drifted tickers; a fresh sync rebuilds it. (See "systemic guard" below — TODO.)
3. **get_category_summary not ACTV-filtered** — counted PEND+liquidated (weekly 53/10).
   FIX (done): ACTV filter at top of the function.
4. **Suite rename fallout** (Autocallable→Structured) — hid the suite in autocall
   email, screener archive, Portfolio Suite. FIX (done): all updated to Structured/
   MoneyMarket.
5. **Keyword classification** can't tell bond-income from option-income. FIX (done):
   AI middleman `scripts/ai_classify_unmapped.py` in the nightly post-steps chain.

## DONE + PUSHED (commits on origin/main, newest last)
- 3c362f4 finalize: definition library (market/definitions.py), MicroSectors override,
  % share chart, OOM, dead-code quarantine (archive/retired-2026-06-16/).
- d5bb889 autocall report finds suite by key after Structured rename.
- 0ed3a03 permanent stale-Excel guard: market/config.py loud warning +
  scripts/refresh_bloomberg.py + scripts/build_previews.py.
- fcf8be9 weekly filing-activity removed + yielders ACTV-filtered; MicroSectors out
  of single-stock (DULL→Gold/AIQU,AIQD→Tech in attributes_LI.csv + DB); flow REX bars blue.
- aa6f4b1 STALE-CACHE FIX (use_cache=False for LI+CC email) → income 10→3, REX 40→41,
  $81B gone, Others→YieldMax. Portfolio Suite 2 charts restored. 25 AI classifications.
- e5114c8 flow REX blue in all charts + pending/filed autocall section + goal doc.
- 6c93000 AI classifier wired into nightly workflow (apply_bloomberg_post_steps).
- ba78885 T-REX real effective dates + honest "Pending" (no +75).
- bee94d1 get_category_summary ACTV filter (weekly 53→41, 10→3) + classification
  corrections (ACYQ/THOR→CC, CBIX/BBIX→none).
- 22edc03 T-REX status cleanup (Pending renders, no "Delayed") + autocall section
  moved from flow to autocall report.
- DB-only (not git, reproduced by re-sync): time-series category sync (4 tickers).

## VERIFIED TIE-OUT (this session)
LI 41/22 ✓ · Weekly 41/3 ✓ · Flow REX total 79 ✓ · Income YieldMax 72.6%/Other 12.8% ✓
· REX income single-stock 3 ✓ · stock_recs Pending renders + no Delayed ✓ · autocall
section in autocall not flow ✓.

## SEC SCRAPE RUNNING (background task `b3qzvkrf6`)
A full SEC EDGAR scrape is running (558 CIKs, since=2024-01-01, etf_only) to refresh
filing data — the last scrape was 2026-04-16, so today's filings (ProShares foreign,
new effective dates) weren't in the DB. This is the PREREQUISITE for T-REX D/E and the
47 filed-but-no-date effective dates. Log: `$CLAUDE_JOB_DIR/tmp/sec_scrape.log` (or the
task output file). **POST-SCRAPE STEPS (do these when it completes):**
1. `python scripts/run_sync_rex_products_from_filings.py` (or run_daily's sec step) +
   re-run apply_bloomberg_post_steps (classify→fund_master→brands→refresh_effective_dates).
2. `python scripts/refresh_effective_dates.py --apply` — should now fill more dates.
3. Rebuild T-REX (trex_combined_v9) → check Foreign now includes the new foreign-filed
   products (E), the filed-effective-date coverage improved (B), IPO refreshed if the
   yaml/source updated (D).
4. Then do F (competitors in Foreign + IPO) — see below.

## REMAINING (the "Do not stop" queue)
1. **Effective dates 100%** — DATA is 100% (every ACTV fund has inception). Reports
   show real eff date OR Pending. TODO: make every report's effective-date display
   fall back to inception_date for LAUNCHED funds (so launched never show Pending);
   the 47 FILED T-REX without a scraped estimated date stay Pending unless a TARGETED
   SEC scrape pulls their 485 dates.
2. **T-REX (D)** IPO watchlist stale — `load_ipo_yaml()` reads config/ipo_watchlist.yaml;
   needs refresh from current IPO data.
3. **T-REX (E)** Foreign Megacap must include ALL foreign-filed products (ProShares
   filed several today) — needs the SEC filings for foreign products (scrape) + widen
   `load_foreign()`.
4. **T-REX (F)** Include competitors in Foreign + IPO sections (like the Ad-Hoc Brief
   report — see screener/li_engine/analysis/recommendation_brief.py / foreign_ipo_brief.py).
5. **Systemic guard (TODO)**: add a step so mkt_time_series.category_display can't drift
   from master (rebuild on sync, or an assertion), so #2 root cause can't recur.
6. **Classification**: iterative — keep correcting via the AI middleman + manual fixes
   as new cases surface (Ryu is fine iterating).

## LOGGED IDEAS (no work now)
- Reports as PDF: MicroSectors report is meant to be PDF; Ryu wants other reports as
  PDF too. Logged, deferred.

## KEY FILES
- Reports: webapp/services/report_emails.py (email builders), report_data.py
  (get_li_report/get_cc_report/get_flow_report — note use_cache param),
  scripts/generate_market_share_charts.py (TOP_N=5, reads mkt_time_series),
  webapp/services/portfolio_suite_flow.py, etp_tracker/weekly_digest.py,
  screener/li_engine/analysis/trex_combined_v9.py.
- Classification: market/definitions.py (single source: suites/status/trusts),
  scripts/ai_classify_unmapped.py (AI middleman), config/rules/fund_mapping.csv,
  attributes_*.csv, scripts/apply_fund_master.py, scripts/apply_bloomberg_post_steps.py.
- Build previews: `python scripts/build_previews.py` (refresh→sync→enrich→build all 10).
- AI classify journal: logs/ai_classify_*.jsonl (idempotency).

## STATE: shadow-gated, nothing sent. Previews in <worktree>/reports/preview_*_2026-06-16.html
and copied to C:\Projects\rexfinhub\reports\.

---
doc: log
status: append-only
updated: 2026-05-19
---

# rexfinhub — Changelog

> Append-only. New entries at top (reverse chronological). Yearly rotation to `LOG_YYYY.md` at year boundary.
>
> Each entry: `## YYYY-MM-DD` header; bullet entries; link to PR / ADR / commit where appropriate.

## 2026-05-19

- Docs framework adopted: six canonical docs (`INDEX`, `SYSTEM`, `TARGET`, `RUNBOOK`, `GLOSSARY`, `LOG`) + `DECISIONS/` (ADRs) + `raw/` (preserved audits). See `DECISIONS/0001-docs-framework.md`.
- Reshape: ~8,000-word `REXFINHUB_ARCHITECTURE.md` v3 moved to `raw/`; content split across SYSTEM (as-is), TARGET (to-be), RUNBOOK (ops).
- Glossary bootstrapped with 15 terms-in-conflict: rex-product, capm-product, canonical-product-id, etp-category, is-rex-flag, gate, auto-go, preflight, send-pipeline, bloomberg-pull, cboe-cookie, fresh-poller, effective-date, inception-date, survivorship, underlier-master.
- `CLAUDE.md` load-order block added: every Claude session reads `docs/INDEX.md` first.
- Pipeline summary email moves to 08:00 ET (was 20:15 ET) deferred to Phase 6. Today still at 20:15.

## 2026-05-19 (earlier)

- PR #18 merged: `VALID_LIST_TYPES` now includes `portfolio_suite` (fix for `etfupdates` accidentally receiving the 06:00 frozen Portfolio Suite Flow Report). Recipients seeded: gking@rexfin.com, mmcnair@rexfin.com, tranney@rexfin.com.
- gking@rexfin.com added to `autocall` list (10 recipients total).
- 06:00 ET one-off freeze send executed; 7 of 8 reports landed correctly. Portfolio Suite went to wrong list (etfupdates) due to bug fixed in PR #18.
- Stock_recs v4 layout polished: removed REX Suggested column, removed Watch section, removed Killed section, expanded Foreign Candidates to 25, IPO Watchlist sorted by date, fixed Bug 2 base for duplicate-ticker audit (deferred).

## 2026-05-18

- PR #17 merged: end-of-day pipeline summary email at 20:15 ET (`scripts/pipeline_summary.py` + jarvis crontab). Aggregates Bloomberg / classification / preflight / decision / gate / send state into a single triage email to relasmar@rexfin.com.
- PR #16 merged: auto-GO logic in `scripts/preflight_check.py`. When overall_status=pass (or warn with `.autogo_on_warn` flag), preflight writes `data/.preflight_decision.json` so `send_all --use-decision` fires automatically. Override: `.send_paused` flag disables auto-GO.
- `.autogo_on_warn` flag enabled on VPS — auto-GO now also fires on WARN preflight (compatible with `.preflight_maintenance` state).
- CBOE session cookie rotated: 7tpji3sj6aqpolvqov08ojl4bl6bg7bu (via `/cboe-cookie` skill).
- VPS cache/sec (5.0 GB) archived to D:\sec-data\cache\rexfinhub_archives\cache_sec_20260518_2104.tar.gz; cache/sec cleared on VPS.
- 13F holdings DB uploaded to Render (`/api/v1/db/upload-holdings`, ~109 MB gzipped).
- PR #15 merged: stock_recs Defensive table redesigned with its own renderer + filing-race columns.
- PR #14 merged: Defensive cards enriched with `mkt_master_data` lookup for non-whitespace tickers.
- PR #13 merged: Defensive built from raw 7-day competitor 485APOS filings (replaces whitespace_v4 candidate filter).
- PR #12 merged: REX Suggested column actually removed; Defensive widened to include WATCH tier; Foreign table sorted by global market cap USD.
- PR #11 merged: Drop REX Suggested column, drop Killed section, fix Foreign columns, sort IPO by date.
- PR #10 merged: Kill JSON-key tickers (GENERATED_AT/MODEL/THESES) appearing in Killed section; remove Watch list; foreign cap 15→25; xAI clarified (X = Twitter, not SpaceX); Cerebras moved to recently_priced.
- PR #9 merged: Stock recs v4 table-first layout replacing decision-card scroll.
- PR #8 merged: Silent fallback when `signal_records` column absent (removed "Tiered signals not available" cosmetic note).
- PR #7 merged: NONE filing-status badge hidden; DEFENSIVE_LOOKBACK_DAYS 30→60; IPO watchlist 16→32 entries with current 2026 valuations; Portfolio Suite Flow Report bundle added (`webapp/services/portfolio_suite_flow.py`, builds 391 KB HTML with 4 matplotlib charts + 6 sparklines).

## 2026-05-18 (PR #6 KPIs fix)

- PR #6 merged: `/operations/products` KPI cards now reflect live-only filtered set, matching what the table shows. Previously KPIs counted full unified universe (~600 rows) while table showed only Listed.

## 2026-05-11 / 2026-05-12

- Rebuild day (see `raw/audit_2026-05-11/`). 17 systemd units pinned with `TZ=America/New_York`. `sqlite3` installed on VPS. `fail2ban` activated. `classify_engine.py` `RULES_DIR` flipped from `data/rules/` → `config/rules/`. 3-axis taxonomy (primary_strategy + asset_class + sub_strategy) wired (commit 63ab8fd). First successful 7-report send to etfupdates@rexfin.com at 07:00 ET Tuesday 5/12 — first send in 14 days.

## 2026-04-27

- Last "normal" send before the 21-day standdown that ended with the 5/12 rebuild.

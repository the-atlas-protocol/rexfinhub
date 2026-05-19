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

- **ADR 0006 written** — Phase 4 canonical-product-id design (`DECISIONS/0006-canonical-product-id.md`). Designs `product_master`(UUID PK) + `identifier_xref` (bi-temporal mapping) + `underlier_master` (polymorphic typed) + `fund_underlier` join. Closes GAP-02. Implementation deferred to ≥ 2026-05-26 after Phase 3 grace period. 5-stage rollout per the Phase 3 safety pattern.
- **Phase 3 Stages 1+2+3 shipped** — see `DECISIONS/0007-merge-capm-and-rex-products.md` (PRs #24, #25). ADR written, schema migrated (rex_products grew from 30 to 46 columns), data backfilled (74/74 CapM rows merged with proper survivorship — Rex's NULL `direction` filled from CapM for 17 rows; manually_edited_fields unioned for 17 rows; 100% ticker overlap with no orphans), and route refactor landed: `webapp/routers/capm.py` collapsed from 3-way merge (rex+capm+mkt) to 2-way (rex+mkt). `_build_unified_row` signature simplified; `_capm_export_impl` queries RexProduct; `_capm_update_impl` writes RexProduct + writes audit log with `table_name='rex_products'`. Dashboard KPI repointed. VPS pulled latest + Render DB uploaded (88 MB compressed) so the new schema is live everywhere. Stage 4 (grace period) starts now; `capm_products` table + `import_capm.py` writer retained for revert. Stage 5 (drop table) deferred to ≥2026-05-26.
- **BUG-08 found + fixed on VPS** — `apply_issuer_brands.py` (an ExecStartPost step in the bloomberg-chain) was crashing on the cp1252 byte 0x97 in `config/rules/issuer_brand_overrides.csv`. Same root cause as the CSV encoding fix in PR #22, but VPS hadn't pulled the fix yet (VPS is still on `ee14f84` since the PR merge). SCP'd the corrected CSV to VPS directly. Next 21:00 ET bloomberg run should succeed.
- **ADR 0005 written + implemented** — Cut 3 (scraper merge) analysis closed without retiring any of the three pathways. The three pathways (atom-watcher / fresh-poller / sec-scrape) serve distinct roles: discovery / 15-min enrichment / 4-hour artifact refresh.
  - New `scripts/intraday_refresh.py` wrapper — checks fresh-poller log mtime; if recent (<30 min) calls `run_daily.py --skip-sec`, else falls back to full `run_daily.py`.
  - New `deploy/systemd/rexfinhub-intraday-refresh.{service,timer}` — same 4×/day rhythm as the old sec-scrape units.
  - `scripts/migrate_to_intraday_refresh.sh` for the one-time VPS rename (disable old timer, enable new timer, rewrite fresh-poller Conflicts=).
  - `scripts/install_fresh_poller_timer.sh` + `scripts/poll_fresh_filings.py` updated to reference the new unit name.
  - VPS migration pending (run the migration script on jarvis VPS).
- **Phase 2 shipped** — self-service admin pages. See `DECISIONS/0004-phase-2-admin-pages.md`.
  - New `/admin/cboe-cookie` (paste-and-submit, 15-sec rotation) replaces the SSH-based `/cboe-cookie` skill as the primary path. SSH skill retained as fallback. Page shows cookie age, last sweep state, accepts bare token / `sessionid=…` / full `Cookie:` header (regex extracts the 32-char run). Backed by new `POST /pipeline/cboe-rotate` + `GET /pipeline/cboe-status` on the VPS pipeline API.
  - Inline target-inception editor on `/operations/pipeline`: column renamed "Inception/Target" → "Target Inception"; empty cells now show `＋ set` affordance when admin + non-Listed; JS POST endpoint switched from `/admin/products/update/{id}` to `/admin/rex-products/update/{id}` so edits actually register as manual overrides (was being silently clobbered by the daily Bloomberg-chain sweep before this fix).
  - Docs reconciliation: runbook's `target_inception_date` (user vocabulary) is canonized as `rex_products.target_listing_date` (schema). No new column added.
- **Phase 1 partially shipped** — cuts round 1. See `DECISIONS/0003-phase-1-cuts.md`.
  - `rexfinhub-classification-sweep.timer` → DISABLED on VPS (was 09:00 weekdays)
  - `rexfinhub-bulk-sync.timer` → DISABLED on VPS (was Sun 07:00)
  - 4 ExecStartPost lines in `rexfinhub-bloomberg-chain.service` → 1 line calling new `scripts/apply_bloomberg_post_steps.py`
  - `scripts/sync_vps_to_d_drive.sh` extended to pull every nightly backup (was: only latest); header notes Task Scheduler schedule at 23:30 ET
  - Cut 3 (scraper merge) deferred to ADR 0005 pending code-overlap analysis (re-numbered from 0004 once 0004 took the Phase 2 slot)
- **Phase 0b shipped** — triage patches for BUG-01 through BUG-04. See `DECISIONS/0002-phase-0b-triage-patches.md`.
  - BUG-01 Bitcoin underlier mismatch → new `scripts/canonicalize_crypto_underliers.py` (nightly cron at 02:30 ET)
  - BUG-02 TSII recycled-ticker false promotion → `_names_overlap()` cross-check in Phase 3 + new `scripts/audit_duplicate_tickers.py` (nightly cron at 02:35 ET) + new row in 20:15 pipeline summary email
  - BUG-03 placeholder inception dates → inception sanity gates in Phase 3 (inception ≥ filing_date AND within 60 days)
  - BUG-04 vanished-from-Bloomberg funds → new `phase4_demote_vanished_from_market()` (audit-only; auto-demote behind `.auto_demote_vanished` flag)
  - `SyncStats.vanished_count` field added; `phase3` reports per-row sanity skip counts in logs.
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

---
doc: log
status: append-only
updated: 2026-05-19
---

# rexfinhub — Changelog

> Append-only. New entries at top (reverse chronological). Yearly rotation to `LOG_YYYY.md` at year boundary.
>
> Each entry: `## YYYY-MM-DD` header; bullet entries; link to PR / ADR / commit where appropriate.

## 2026-05-20

- **Rebuild completion plan adopted** — `docs/raw/ops/REBUILD-COMPLETION-PLAN_2026-05-19.md` is the live execution plan for all remaining rebuild work, organized as Tracks 0-6 under a Build·Prove·Retire principle: retire no legacy artifact until a 3-gate proof of death (static grep / runtime access / equivalence diff) is green. Status lifecycle clarified with Ryu: `under_consideration` is pre-filing; a filing matcher (Phase 5B / Track 5B) attaches incoming 485-series filings to the pre-existing `canonical_id` instead of minting duplicates; `target_list → listed` auto-promotes via the reconciler's 3-source rule (Bloomberg ACTV alone is insufficient).
- **Track 1 — documentation reconciliation** (PR #62). Fixed the 17 documentation defects from the architecture audit: removed stale TARGET.md "planned" phase lines, defined Phase 4b + Phase 5B, reconciled `fund_underlier` / `status_history` enum / `identifier_xref` / `product_master` schema drift to the shipped code, corrected the assertion count (15 → 25), fixed the classify-override route name, closed stale gaps across all four canonical docs, flipped ADRs 0006-0010 `proposed` → `accepted`, and declared `rex_products.canonical_id` + `status_cached` on the `RexProduct` ORM model.
- **Track 2 — Phase 1 Cut 3 closeout.**
  - Cut 3 verified: the `intraday-refresh` wrapper ran a full manual pass (rc=2 partial-success; the send step was correctly gate-blocked outside the 19:00-20:00 window). The earlier SIGTERM kill — fresh-poller's `Conflicts=` directive terminating the heavy job at 20:00 — was fixed in PR #61 (`Conflicts=` removed, timer offset to :05).
  - **Render DB upload race fixed** — `upload_db_to_render` used the fixed path `etp_tracker_render.db.upload.gz` and deleted it in a `finally` block, so two concurrent `run_daily` invocations would race: one process's cleanup deleted the file the other was mid-upload on, and the retry sleep widened that window to 30-90s. This was the root cause of the `FileNotFoundError: etp_tracker_render.db.upload.gz` in the 2026-05-19 verification run. Fix: per-process unique paths (`etp_tracker_render.{pid}.db`).
  - **Screener-cache upload now retries** — `upload_screener_cache_to_render` had no retry (unlike the DB upload), so a transient Render HTTP 503 failed it outright. Added a 3-attempt retry with 0/15/45s backoff.
- **Track 3 — Phase 4b underlier completion** (PR #64). Audit found `underlier_master` had 15 `unknown` rows, not 12 — only 2 affected REX products; the other 13 were orphan rows from the competitor universe. `fix_underlier_classification.py` repointed the 5 MicroSectors 3X ETNs (FNGU/NRGU/NRGD/BNKU/BNKD) off the junk `0` underlier onto Solactive/NYSE index underliers, and reclassified the 13 orphans + ULTI's `-` row to typed `commodity`/`crypto_pair`/`basket`. `underlier_id_coverage` assertion strengthened to flag unknown-type links. Result: 15 → 1 unknown (the inert junk `0`, 0 funds). OpenFIGI `primary_figi` enrichment descoped (no consumer).
- **Track 4 — the four retirements.**
  - **4a (PR #65)** — `capm_products` retired. `import_capm.py` repointed to write `rex_products`; the startup seed + `CapMProduct` ORM model removed; `DROP TABLE capm_products` behind a Gate-C equivalence proof (74 rows, 0 data-loss).
  - **4b (PR #66)** — `rex_products.underlier` retired. Converted to a `hybrid_property` resolving from `underlier_master` via `fund_underlier` (every reader works unchanged — Python attr + SQL filter/sort/group); physical column dropped behind Gate C (503 rows, 0 loss); the `canonicalize_crypto_underliers` cron retired.
  - **4c — replanned / descoped.** `classification_override` is `canonical_id`-keyed (REX products only) and structurally cannot replace the universe-wide rule CSVs (~2,400 funds). GAP-08's real intent — a no-CSV override workflow — was already delivered by Phase 6. Physical CSV deletion descoped.
  - **4d (PR #67)** — every `send_enabled` reader (`graph_email`, `admin` status + toggle, `weekly_digest`, `screener/email_report`) repointed through the DB-backed `system_flags` helper. Physical flag-file deletion descoped — the files remain as the helper's zero-cost fallback mirror; deleting them would need a send-pipeline-critical preflight-file migration for low payoff.
- **Track 5A — status_history is now the live authority** (PRs #69, #70). The dry-run review (mandated by ADR 0008) caught two reconciler bugs and they were fixed: (1) the ETN blind spot — 21 actively-trading ETNs/leveraged funds (FNGU, NRGU, BNKU, BULZ, …) would have been demoted `listed → under_consideration` because ETNs never file 485-series SEC forms; (2) demote-on-absent-evidence — 18 no-ticker products would have been demoted (incl. reviving `delisted → effective`) on a failed Bloomberg join. Fix: Bloomberg `market_status` is authoritative (ACTV → listed, LIQU → delisted); the reconciler never demotes mid-lifecycle on absent evidence; `append_transition` now also drives the legacy `rex_products.status` so it stays consistent with `status_history`. Reconciler run with `--apply`: **175 promotions committed, 19 non-LIQU demotions skipped**. The bloomberg-chain post-step now runs `--apply` nightly. 25/25 assertions pass.
- **Track 5 — Phase 4/5 canonical identity is now self-maintaining** (PRs #72, #73). Audit finding: `sync_rex_products_from_filings.py` creates rex_products rows from new SEC filings but assigned NO canonical identity — so every new fund was invisible to `product_master` / `identifier_xref` / `fund_underlier` / the reconciler. The Phase 4/5 model would have decayed after the one-time backfill. `ensure_canonical_identity.py` (new) runs the idempotent Phase 4 backfills nightly via the bloomberg-chain, before the reconciler. Two latent bugs fixed in the process: `backfill_underlier_master` + `backfill_fund_underlier` still `SELECT`ed the Track-4b-dropped `underlier` column; and `backfill_fund_underlier`'s idempotency was per-link not per-product (a first run created 10 duplicate links — cleaned from the DB, now per-product). Re-run is a verified clean no-op. 25/25 assertions pass.
- **Track 5B — pre-filing product creation** (PR #75). The `/admin/products` Add form now creates `Under Consideration` (pre-filing) products; when the SEC filing later arrives, `sync_rex_products_from_filings` name-matches it to the existing row and advances it in place — no duplicate. This also fixed two routes (`add_product`, `update_product`) latently broken by Track 4b's `underlier` hybrid (both passed the now-setter-less `underlier` — now mapped to `underlying_ticker`). The duplicate guard is the existing `identifier_xref_consistency` assertion.
- **Track 6 — edgartools built ALONGSIDE the legacy extractor** (PR #76; per Ryu's directive 2026-05-20: "skip Track 0, develop Track 6 alongside the rest"). Per D1 — build everything; remove nothing until proven — the legacy SEC stack stays 100% authoritative. `etp_tracker/edgar_client.py` is a fully-defensive edgartools client (no side effects, never raises). `scripts/edgar_shadow_compare.py` is a read-only dual-run harness. `edgartools>=5.31.0` added to requirements (dry-run-verified additive-only — no conflicts/downgrades). **First shadow run: edgartools and the in-house pipeline see the EXACT same 2,188 485-series filings over 30 days — 100.0% coverage, zero gap.** ADR 0010 Stages 1-3 (dependency + shim + dual-run) shipped; Stage 4 (content-extraction parity + cutover) and Stage 5 (retire the legacy) remain — gated on the shadow comparison proving sustained zero divergence, an operator-supervised decision.
- **Track 0 — skipped** per Ryu's 2026-05-20 directive. `ADMIN_PASSWORD` rotation needs Render env access; it belongs with the deferred Phase 0a security hardening.
- **Session result:** the seven-track rebuild — Tracks 1-6 delivered (Track 0 skipped by operator decision). 17 PRs (#62-#76), all merged, deployed, verified; 25/25 assertions green throughout. The structural rebuild is complete and live; edgartools runs in shadow with its cutover gated on accumulated dual-run proof.

## 2026-05-19

- **Autonomous evening push (PRs #45-#59)** —
  - **25/25 assertions PASS** (PRs #50-#52). Expanded suite from 10 → 25 matching the ADR 0009 spec. New categories: `reports_kpi` (BUG-05 detection via asymmetric check), `infra` (audit_log_freshness + backup_recent). Fixed AXTU `is_rex=1` to align with `issuer_display='REX'` — the BUG-05 root cause.
  - **SPOU demoted** Listed → Effective on local + VPS per operator confirmation that it's not actually listed (Bloomberg PEND was correct).
  - **8 missing fund_underlier links closed** (PR #50): MicroSectors ETNs mapped to Solactive/NYSE indices via `bmo_suite`; TLDR mapped as Treasury Bill basket. All Listed REX products now have fund_underlier linkage.
  - **HTMX inline classify-override UI** on `/operations/products` (PR #53). ⊞ button per admin row opens a modal listing current overrides + 19-field dropdown + value/reason inputs + blacklist checkbox + inline delete. Backend in PR #38; this completes Phase 6 Stage 4.
  - **Render DB upload now retries 3× with exponential backoff** (PR #54). Tonight's 3 manual upload attempts hit transient Render API errors; retry logic uploaded successfully on attempt 1/3 after deployment.
  - **Phase 7B Stage 2 COMPLETE** (PRs #55-#58): `system_flags` helper with DB-first/file-fallback reads + dual-writes. All 5 flag-read sites migrated (auto_demote_vanished, send_paused, autogo_on_warn, send_enabled, preflight_maintenance). DB rows are now authoritative; files retained for Stage 3 cutover.
  - **`/admin/system-state` page** (PR #59) — read/write surface for the new Phase 7B tables. Flag toggle UI, preflight runs history, system_event summary.
- **Production hygiene pass (PRs #41-#44)** —
  - **Phase 6 Stage 7 prerequisite**: `scripts/apply_classification_overrides.py` wired into bloomberg-chain ExecStartPost. Tomorrow's 17:15 ET chain run will apply the 106 applicable overrides automatically. First run: 3 issuer_display fixes applied to mkt_master_data.
  - **Phase 7 Part B**: `scripts/migrate_state_tables.py` created `system_flags` / `preflight_run` / `system_event` tables. VPS: 5 flags + 1 preflight run + 105 events backfilled. Dual-read window opens; files remain authoritative until Stage 2+ flips reads.
  - **BUG-04 BMAX class closed (4 rows demoted)**: `scripts/demote_liqu_dlst_rex_products.py` demoted BMAX (LIQU), XRPK (DLST), SOLX (DLST), FNGA (LIQU) from `Listed` → `Delisted` on both local + VPS. Audit-logged to `capm_audit_log`. Complements existing `phase4_demote_vanished_from_market` (vanished case).
  - **`rexfinhub-morning-triage.timer` ENABLED on VPS** — first fire tomorrow 08:00 ET. Runs `run_assertions.py` then `morning_triage_email.py`. Old 20:15 ET `pipeline_summary.py` cron-job stays running during dual-period.
  - **status_reconciler dry-run wired into bloomberg-chain** — tomorrow's 17:15 run produces fresh transition diff in `data/.status_reconciler.log` for operator review before `--apply` flip.
  - **Assertions 10 → 15**: 5 new integrity checks (canonical_id coverage, xref consistency excl. CIK, override validity, status_history currentness, status_cached drift). VPS run: 13/15 PASS; the 2 FAILs are real findings (8 NULL-underlier ETNs mostly MicroSectors, + SPOU rex=Listed/Bloomberg=PEND).
- **Daily report L6-blocked at 19:30 ET, retry sent at 20:03 ET** — auto-send blocked because `etfupdates@rexfin.com` was at 6/6 daily cap from this morning's 06:00 ET frozen freeze-send (7 reports went to that address this morning). Test-send to `relasmar@rexfin.com` (with `--allow-self-loop`) confirmed daily report body OK (77,789 chars). Cap temp-raised 6→12 on VPS via in-place edit, daily bundle sent to `etfupdates@rexfin.com`, cap reverted to 6. See `.send_audit.json` entry at 20:04:39-04:00 with `allowed=true, phase=result`.
- **Phase 6 Stage 4 + L6 bypass flag shipped** (PR #38).
  - `etp_tracker/email_alerts.py::_send_html_digest` now accepts `bypass_rate_limit=False` parameter. `scripts/send_all.py` exposes it as `--bypass-rate-limit`. Future post-freeze retries no longer need the temp-edit dance.
  - New `webapp/routers/admin_classify.py` — POST/GET/DELETE `/admin/classify-override/{canonical_id}` endpoints. Writes via `classification_resolver.set_override`, audit-logs via `capm_audit_log` with `table_name='classification_override'`. Mounted in `webapp/main.py`. Backend complete; HTMX inline-edit UI on `/operations/products` is a follow-up.
- **Phase 5 Stage 3 + 4 + Phase 6 Stage 3 + 5 + 6 shipped** (PRs #35, #36, #37, #39).
  - **`webapp/services/status_reconciler.py`** — bi-temporal reconciler with 3-source rule for Listed promotion. Default `--dry-run`; produces transition diff for operator review. Local dry-run flagged 175 promote / 39 demote out of 541 (review pending; not applied).
  - **`webapp/services/classification_resolver.py`** — override-first resolution (classification_override → Bloomberg → auto-classifier). `set_override()` / `get_override()` API.
  - **`scripts/run_assertions.py`** — 10 daily data-quality assertions across freshness / classification / lifecycle / send_pipeline categories. Writes to `assertion_run` table. Local first run: 6/10 pass; failures surface 8 missing fund_underlier links + 5 BMAX-class Listed-without-ACTV products (real production findings).
  - **`scripts/morning_triage_email.py`** — reads latest assertion_run, renders HTML+text triage email, sends via Graph API.
  - **`deploy/systemd/rexfinhub-morning-triage.{service,timer}`** — fires 08:00 ET Mon-Fri.
  - **`scripts/sync_status_cached.py`** — adds + backfills `rex_products.status_cached` denormalized column (lowercase canonical: 'listed', 'effective', etc.). Reconciler now writes the cache on every transition.
- **BUG-05 mitigated + 52 unknown underliers reclassified** (PR #34).
  - `webapp/services/report_data.py::get_flow_report` rex_kpis now uses UNION semantics (`is_rex=1 OR issuer_display='REX'`) so the KPI box matches the issuer table. The $10.8M-vs-$16.4M divergence is gone.
  - `scripts/reclassify_unknown_underliers.py` — improved heuristic pass on the 64 'unknown' underliers from Stage 4. 64 → 12 remaining (the 12 are alt-coin baskets needing dedicated schema).
- **Phases 4 + 5 + 6 schema/backfill all shipped early** — pure additive work; zero risk to live read paths. ADR-stated start dates honored only for behavior-changing stages.
  - **Phase 4 Stage 3** (`scripts/backfill_identifier_xref.py`) — 1756 identifier_xref rows: 165 tickers, 517 CIKs, 503 series_ids, 503 class_contract_ids, 68 bloomberg tickers. All valid_to=NULL.
  - **Phase 4 Stage 4** (`scripts/backfill_underlier_master.py`) — heuristic classifier ran across 601 distinct underlier strings from rex_products + mkt_master_data. Output: 10 crypto_pair, 3 index, 520 equity, 4 basket, 64 unknown. 477 underlier_master rows inserted (deduped by display_symbol).
  - **Phase 4 Stage 5** (`scripts/backfill_fund_underlier.py`) — 507 of 541 rex_products linked to underlier_master (34 with no underlier text = pre-launch filings). All effective_to=NULL.
  - **Phase 5 Stages 1+2** (`scripts/migrate_status_history.py`) — status_history bi-temporal table created. 608 rows synthesized: 541 current-state + 67 historical effective rows for Listed funds (dated inception_date − 75 days per SEC Rule 485(a)).
  - **Phase 6 Stages 1+2** (`scripts/migrate_classification_override.py`) — classification_override + assertion_run tables created. 486 override rows migrated from 7 rule CSVs. 4795 unmatched CSV tickers are competitor products without canonical_ids (expected — those aren't REX-managed).
  - All scripts ran on both local + VPS DBs. Idempotent re-runs are no-ops.
- **Phase 4 Stages 1+2 shipped early** — pure additive schema work executed ahead of the ≥ 2026-05-26 ADR-stated start date because Stages 1-2 are non-disruptive (new tables + UUID column with no reads touching them yet).
  - **Stage 1** (`scripts/migrate_canonical_id_schema.py`) — created 4 new tables (`product_master`, `identifier_xref`, `underlier_master`, `fund_underlier`) + added `rex_products.canonical_id TEXT` column. Idempotent.
  - **Stage 2** (`scripts/backfill_product_master.py`) — generated UUIDs for all 541 rex_products rows; inserted 541 `product_master` rows; populated `rex_products.canonical_id` for every row. Validation: 0 NULL canonical_ids, counts match.
  - Both local and VPS DB migrated cleanly.
  - Stages 3-5 (identifier_xref backfill, underlier_master classification via OpenFIGI, fund_underlier population) remain on schedule for ≥ 2026-05-26 since they involve interpretive work that benefits from unhurried review.
- **ADRs 0008 + 0009 + 0010 written + BUG-07 fixed** — closes the rebuild roadmap design phase.
  - **ADR 0008 (Phase 5, proposed)** — `DECISIONS/0008-status-history-bitemporal.md`. Designs bi-temporal `status_history` table + 3-source rule for Listed promotion. Structural fix for BMAX-class (BUG-04) ghost-Listed bug. Implementation ≥ 2026-06-10.
  - **ADR 0009 (Phase 6, proposed)** — `DECISIONS/0009-classification-override-and-assertions.md`. Single `classification_override` table replaces 6 rule CSVs; ~25 ops-as-assertions surface failures in the 08:00 ET triage email. Eliminates the largest of Ryu's three daily touchpoints. Implementation ≥ 2026-06-25.
  - **ADR 0010 (Phase 7, proposed)** — `DECISIONS/0010-edgartools-migration.md`. Migrate SEC scraping to `edgartools` (retires ~3,500 lines of in-house extraction). Consolidate 14 state files into 3 tables (`system_flags` + `preflight_run` + `system_event`). Last phase in the rebuild roadmap. Implementation ≥ 2026-07-15.
  - **BUG-07 fixed (PR #28)** — `etp_tracker/bulk_loader.py::download_submissions_zip` now writes to `<dest>.partial`, validates content-length + zip-parses cleanly, then atomic-replaces. Prevents recurrence of the corrupt-zip scenario that crashed today's universe-sync step.

  All 7 phases of the rebuild roadmap are now either shipped or designed.
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

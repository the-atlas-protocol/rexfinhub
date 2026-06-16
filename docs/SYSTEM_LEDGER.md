---
doc: system-ledger
status: canonical
updated: 2026-06-15
---

# REXFINHUB - SYSTEM LEDGER (the consolidated purpose map)

> The single source of truth for WHAT every part of the system is and WHY it exists, across
> all three devices (local C:, VPS, D:). Built 2026-06-15 by a 12-agent discovery sweep ->
> synthesis -> coherence-critic loop (Part I methodology), with a round-2 correction pass that
> fixed the critic's findings. Reality = code + live VPS state; where they diverged the live
> query wins. docs/ARCHITECTURE.md is the narrative summary of this ledger.
>
> Governance (Part II): when this ledger and the code disagree, code is truth for behavior and
> the ledger is truth for intent - each divergence is classified bug-fix / doc-correct / retire
> (RUNBOOK). New stores/jobs are added here on discovery; a drift assertion flags any
> code-ref-not-in-ledger or ledger-entry-not-in-code.

## REXFINHUB CONSOLIDATED PURPOSE LEDGER (single source of truth, verified 2026-06-15)

Reality = code + live VPS state. Where discovery records diverged from production, the live query wins (see contradictions_resolved). Grouped by the 7 layers. `device` = where it runs; `authority` = authoritative|replica|na.

### LAYER 1 — SOURCES (external feeds entering the system)

| component | purpose | device | authority | writer | readers | health | deps |
|---|---|---|---|---|---|---|---|
| SEC EDGAR atom feed (Tier 1) | Detect new 485/497/N-1A/N-2 filings within 1-3 min of SEC acceptance | vps | authoritative | atom-watcher (60s daemon) | filing_alerts, single-filing-worker | healthy | SEC_USER_AGENT, requests+retry |
| SEC EDGAR submissions JSON | Per-CIK filing history (form/accession/date) for enrichment + batch | vps | authoritative | SECClient / async_client | single-filing-worker, run_pipeline, step2/3 | healthy | 6h TTL cache, If-Modified-Since, 5GB LRU |
| SEC EDGAR daily-index | Incremental same-day 485-series discovery skipping ~95% of trusts | vps | authoritative | index_client → poll_fresh_filings (15min) | fresh-poller, reconciler | healthy | SEC daily-index endpoint, trading calendar |
| SEC document text extraction (SGML+body) | Pull effective dates, tickers, fund names from prospectus | vps | authoritative | step3 (form-routed strategy) | fund_extractions, fund_status | healthy | SECClient, sgml.py, body_extractors |
| Bloomberg daily xlsm (SharePoint Graph pull) | Pull bloomberg_daily_file.xlsm twice daily (17:15/21:00 ET) — market data sync | vps | authoritative | graph_files.download_bloomberg_from_sharepoint | market/ingest, classify, reports | healthy | Azure AD creds, Graph API; hard-fail no stale fallback |
| Bloomberg sheets w1-w4/s1 + overlays | Read 5 ETP sheets + MicroSectors AUM overlay into DataFrames | vps | authoritative | market/ingest.read_input (openpyxl) | market/transform | healthy | W1-W4_COL_MAP, microsector_aum/sh sheets |
| CBOE symbol-reservation portal | (intended) nightly 475k symbol availability sweep | vps | na | CboeScanner (blocked) | /tools/tickers (frozen) | **dead** | Cloudflare WAF blocks VPS IP since 5/13; cookie cannot fix |
| Manual classification rule CSVs | Human-curated classification + REX-universe + issuer/underlier rules | vps | authoritative | Ryu + classify_daily mirror | classify_daily, market/transform | healthy | config/rules/ (see Layer 7) |
| edgartools shadow client (ADR 0010) | Off-path field-level extractor comparison; never wired live | vps | replica | edgar_client (shadow, returns []) | edgar_shadow_compare.py (test only) | unknown | edgartools (optional) |

### LAYER 2 — INGESTION (raw detection + sync into DB)

| component | purpose | device | authority | writer | readers | health | deps |
|---|---|---|---|---|---|---|---|
| atom-watcher (Tier 1, 60s) | Insert new alerts (enrichment_status=0) | vps | authoritative | systemd rexfinhub-atom-watcher | single-filing-worker, reconciler | healthy | filing_alerts, SEC atom |
| single-filing-worker (Tier 2, 30s) | Enrich alerts → trusts/fund_extractions/fund_status | vps | authoritative | systemd unit | filing_landscape, live feed | healthy | filing_alerts, SECClient |
| reconciler (Tier 3, 04:00/08:00) | Daily-index diff safety net; PEND→ACTV promotions | vps | authoritative | systemd rexfinhub-reconciler | rex_products, status_history | healthy | daily-index, rex_products |
| bulk_sync (Tier 4, weekly) | Discover net-new filers → stub trusts (is_active=False) | vps | authoritative | bulk-sync (timer DISABLED) | admin trust CRUD | healthy (untimered) | SEC company.idx |
| run_pipeline.py (batch, every 4h) | 4-step extraction → CSVs → sync_service | vps | authoritative | sec-scrape unit | sync_service | healthy | trusts, step2-5 |
| step2/3/4/5 (submissions/extract/rollup/name) | Per-trust extraction ladder → fund identity + lifecycle CSVs | vps | authoritative | run_pipeline workers | sync_service | healthy | SGML>TITLE-PAREN>LABEL-WINDOW ladder |
| body_extractors + sgml.py | HTML/PDF + SGML header ticker extraction for step3 | vps | replica/auth | step3 | step3 | healthy | bs4, pdfminer, deny-list |
| sync_service (CSV→DB) | Upsert filings/extractions/status/name_history; dedup on accession | vps | authoritative | run_pipeline post-step5 | webapp, reports | healthy | step CSVs, trusts |
| market/ingest+transform+db_writer (12-step) | Bloomberg DataFrame → full-refresh mkt_* tables | vps | authoritative | bloomberg-chain | screener, reports, KPIs | healthy | rules CSVs, openpyxl |
| status_reconciler (multi-source, bi-temporal) | 3-source rule → status_history (authority) drives rex_products.status+status_cached | vps | authoritative | bloomberg-chain --apply step | assertions, /operations | healthy | rex_products, mkt_master_data, identifier_xref |
| classify_daily.py (autonomous Tier 0-3) | Close classification loop; auto-apply HIGH, queue LOW/DISAGREE | vps | authoritative | classification-sweep 09:00 + bloomberg-chain | mkt_master_data, proposals, rules mirror | healthy | mkt_master_data, rules CSVs, Haiku+critic |

### LAYER 3 — STORAGE (the one production DB + supplementary DBs)

| component | purpose | device | authority | writer | readers | health | deps |
|---|---|---|---|---|---|---|---|
| etp_tracker.db (~860MB VPS, 60 tables) | THE single SQLite source of truth — everything | vps | authoritative | one-writer-per-table (ADR 0011 E2) | webapp, screener, reports, assertions | healthy | n/a |
| live_feed.db | Realtime filing alerts; separate to survive DB swaps | vps | authoritative | atom_watcher | single-filing-worker, live router | healthy | atom feed |
| 13f_holdings.db | (intended) 13F holdings | **local stub only** | na | run_13f (no-op) | 13F intel routers (ungated) | **dead on VPS** — file absent on prod | — |
| trusts (15.9K) | Fund-family root (CIK/name/slug) | vps | authoritative | bulk_sync + single-filing-worker | everything via FK | healthy | — |
| filings (633K) / fund_extractions (714K) / fund_status (215K) / name_history (52K) | SEC filing hierarchy + lifecycle | vps | authoritative | sync_service + single-filing-worker | /operations, reports, filing_analysis | healthy | trusts |
| mkt_master_data (~7.5K, 100+ cols) | Canonical live ETP facts + 3-axis taxonomy; full-refresh | vps | authoritative | market/db_writer + 10 post-steps | screener, reports, classify, reconciler | healthy | mkt_pipeline_runs |
| mkt_time_series (280K) | AUM history per fund per month (36mo) | vps | authoritative | db_writer.write_time_series | screener, whitespace, charts | healthy | W4 sheet |
| mkt_daily_snapshot | Append-only EOD history (WS-C1) — wired today as LAST post-step | vps | authoritative | snapshot_daily.py (now in chain) | future history queries | healthy (newly wired) | mkt_master_data |
| rex_products (586) | REX lifecycle Filed→Delayed→Effective→Listed | vps | authoritative | sync_rex_products (promote-only) + status_reconciler | /operations, T-REX, whitespace | healthy | fund_status, product_master, identifier_xref |
| status_history (1086) | Bi-temporal status audit; THE status authority | vps | authoritative | status_reconciler --apply | assertions, audit | healthy | rex_products |
| product_master (586) / identifier_xref (1.9K) | Canonical identity + ticker/CIK/series xref | vps | authoritative | ensure_canonical_identity | reconciler, reports | healthy | — |
| underlier_master (483) / fund_underlier (515) | Underlier dimension + fund→underlier bind | vps | authoritative | backfill_fund_underlier (in canonical chain) | reports, screener | healthy (LIVE, not stub) | product_master |
| classification_audit_log (519K) / classification_override (486) / classification_proposals | 3-axis decision journal + manual overrides + review queue | vps | authoritative | classify_daily / admin / apply_overrides | audit, admin UI | healthy | — |
| capm_audit_log (1916) | Every rex_products UPDATE audit | vps | authoritative | status_reconciler + sync_rex_products | audit, drift | healthy | rex_products |
| mkt_fund_classification (7.5K) | Legacy 5-category projection | vps | authoritative | apply_classification_sweep | legacy reports | healthy | mkt_master_data |
| system_flags | Gate store (send_enabled etc.); THE send gate | vps | authoritative | system_flags.set_flag | gates, preflight, assertions | healthy (send_enabled=False) | — |
| email_recipients (28) | Recipient registry by list_type | vps | authoritative | manage_recipients/admin | send_all, builders | fragile (no hard-error on unknown list_type) | — |
| mkt_report_cache | Pre-baked report JSON for Render zero-memory reads | vps | authoritative | prebake_reports | Render API, builders | healthy | — |
| li_engine_daily (72.5K) / li_etp_daily (10.7K) / li_sector_daily (121) | L&I daily scoring + metadata + sector aggregates | vps | authoritative | li_engine run_v1 (22:30 wd) | L&I reports, screener | healthy | mkt_master_data |
| recommendation_history (608) | Monday T-REX rec backfill | vps | authoritative | backfill_recommendation_history | T-REX report, archive | healthy | — |
| assertion_run | 28-assertion morning triage results | vps | authoritative | run_assertions 08:00 | triage email | healthy | — |
| filing_alerts | Tier-1 alert staging | vps | authoritative | atom_watcher | reconciler, live push | healthy | atom feed |
| cboe_symbols (475K, frozen) | CBOE reserved-symbol universe | vps | replica | run_cboe_scan (disabled) | /tools/tickers (stale) | **dead** (frozen 5/13) | — |
| mkt_pipeline_runs | Market pipeline run lineage/health | vps | authoritative | db_writer create/finish | ops, debugging | healthy | — |
| filing_analyses | Cached LLM "Top Filings" analysis per (filing,model) | vps | authoritative | filing_analysis.py | daily digest | healthy | filings |
| api_audit_log | M2M API call audit | vps | authoritative | api routers | security audit | healthy | — |
| mkt_global_etp | (unknown stub) | vps | na | none found | none | **dead** (0 rows, no writer) | — |

### LAYER 4 — ENRICHMENT (classification + identity post-steps)

| component | purpose | device | authority | writer | readers | health | deps |
|---|---|---|---|---|---|---|---|
| apply_bloomberg_post_steps.py | Orchestrates 11-step chain (classify→fund_master→canonical→underlier→issuer→sweep→override→eff-dates→reconciler→**snapshot**→commit) | vps | authoritative | bloomberg-chain ExecStart | n/a (orchestrator) | healthy | all post-step scripts |
| apply_fund_master.py | Replay fund_master.csv 3-axis onto 23 mkt_master_data cols | vps | authoritative | post-step 2 | reports, screener | healthy | fund_master.csv |
| ensure_canonical_identity.py | Mint canonical_id → xref → underlier bind (BEFORE underlier overrides, CIC-12) | vps | authoritative | post-step 3 | status_reconciler, overrides | healthy | product_master, identifier_xref |
| apply_underlier_overrides.py | Manual underlier corrections (keys off canonical_id) | vps | authoritative | post-step 4 | reports, screener | healthy | underlier_overrides.csv |
| apply_issuer_brands.py | Canonicalize issuer_display (re-run each sync; sync NULLs it) | vps | authoritative | post-step 5 | reports, screener | healthy | issuer_brand_overrides.csv |
| apply_classification_sweep.py | Legacy etp_category + map_* attribute sweep (HIGH+MEDIUM) | vps | authoritative | post-step 6 | reports, screener | healthy | fund_mapping + attributes CSVs |
| apply_classification_overrides.py | Apply classification_override rows (override-first) | vps | authoritative | post-step 7 | reports, admin | healthy | classification_override |
| refresh_effective_dates.py | Refresh rex_products.estimated_effective_date per series_id | vps | authoritative | post-step 8 | reconciler, /operations | healthy | fund_extractions |
| commit_rules_delta.py | Auto-commit+push day's rules mutations to git | vps | authoritative | post-step 11 | git main, VPS deploy | healthy | RULES_PATHS |

### LAYER 5 — SCHEDULING / ORCHESTRATION (systemd timers + cron)

| component | purpose | device | authority | writer | readers | health | deps |
|---|---|---|---|---|---|---|---|
| rexfinhub-atom-watcher / single-filing-worker | 60s/30s realtime detection daemons | vps | authoritative | systemd | filing_alerts → filings | healthy | — |
| rexfinhub-fresh-poller (15min Mon-Fri) | Lightweight daily-index scrape + promote-only | vps | authoritative | poll_fresh_filings | rex_products, intraday-refresh | healthy | overlap guard ADR 0005 |
| rexfinhub-reconciler (04:00/08:00) | Tier-3 safety net | vps | authoritative | reconciler | rex_products | healthy | — |
| rexfinhub-sec-scrape (every 4h) | 12-step batch scrape→sync→bake→Render | vps | authoritative | run_all_pipelines | reports, Render | healthy | — |
| rexfinhub-bloomberg(-chain) (17:15/21:00) | xlsm pull → sync → 11 post-steps | vps | authoritative | bloomberg-chain | mkt_*, reports | healthy | — |
| rexfinhub-classification-sweep (09:00) | classify_daily --apply + fund_master + report | vps | authoritative | systemd | mkt_master_data, proposals | healthy | — |
| rexfinhub-morning-triage (08:00) | 28 assertions + always-send triage email | vps | authoritative | run_assertions | triage email | healthy | — |
| rexfinhub-db-backup (23:00) | prune→.backup→integrity_check | vps | authoritative | sqlite3 .backup | D: archive, Render | healthy | — |
| rexfinhub-parquet-rebuild (Mon+Fri 06:00) | Rebuild L&I analysis parquets | vps | authoritative | screener analysis modules | weekly reports | healthy | — |
| rexfinhub-grade-recommendations (Sun 23:00) | Grade past stock recs | vps | authoritative | grade_recommendations | recommendation_history | healthy | — |
| rexfinhub-gate-close (20:00) | send_enabled=False nightly lockdown | vps | authoritative | set_flag | send_all | healthy | — |
| rexfinhub-alert@ (OnFailure template) | Critical-alert email on any unit failure | vps | authoritative | send_critical_alert | relasmar@rexfin.com | healthy | — |
| rexfinhub-api (always-on) | FastAPI server :8001; serves+replicates to Render | vps | authoritative | webapp routes (read-only) | rexfinhub.com proxy | healthy (no git-pull pre-step — drift risk) | etp_tracker.db |
| rexfinhub-cboe (03:00) | (intended) full symbol sweep | vps | na | run_cboe_scan (blocked) | frozen cboe_symbols | **dead** — fires into WAF wall nightly | — |
| rexfinhub-13f-quarterly (next 2026-08-19) | (intended) quarterly 13F ingest | vps | authoritative | run_13f backfill | 13f_holdings.db (absent) | **fragile** — Q1'26 never ingested; DB absent on VPS | — |
| rexfinhub-gate-open / -daily / -intraday-refresh / -preflight / -bulk-sync | Send-resume machinery (open gate 19:00, send 19:30, etc.) | vps | na | systemd (DISABLED) | — | **dead/disabled** — send pipeline dark | system_flags |
| cron: li_engine_v1.0.1 (22:30 wd) | Canonical L&I daily scoring (final_score v1.0.1) | vps | authoritative | run_v1 | li_engine_daily | healthy | — |
| cron: weekly_file_launch / weekly_system_report (Mon 07:00/07:05) | Weekly L&I file-launch + system report | vps | authoritative | screener analysis | email | healthy | — |
| cron: disk_hygiene (15min + 04:00) | Prune pre-backup DBs + /tmp tarballs | vps | authoritative | find -delete | disk monitor | healthy | — |
| cron: audit_duplicate_tickers (02:35) | Nightly duplicate-ticker pollution audit | vps | authoritative | audit_duplicate_tickers | log only | healthy | — |

### LAYER 6 — OUTPUTS (reports + webapp + distribution)

| component | purpose | device | authority | writer | readers | health | deps |
|---|---|---|---|---|---|---|---|
| Daily ETF Digest | 24h launches/filings/effectiveness brief | vps | authoritative | email_alerts.send_digest_from_db | Graph API (gated) | healthy | mkt_master_data, filings |
| Weekly Digest | 7-day exec summary (pulse/flows/landscape) | vps | authoritative | weekly_digest.build_html | Graph API | healthy | mkt_master_data, mkt_time_series, filings |
| L&I Weekly Report (v3 layout) | Whitespace HIGH/MED/WATCH + kill list | vps | authoritative | weekly_v2_report.main | send_li_report | healthy | whitespace_v4, li_engine_daily, parquets |
| Income Weekly Report | Income segment + yield column | vps | authoritative | report_emails (Income subset) | Graph API | healthy | mkt_master_data, mkt_time_series |
| Flow Report (Portfolio Suite) | 20-fund RPS suite flows | vps | authoritative | portfolio_suite_flow.build_html | report_emails | **fragile** — TICKER_SUITE hard-coded, drifts from mkt_master_data.suite | Bloomberg data_flow sheet |
| T-REX Combined v9 | Monday stock-rec report (pipeline+filings+killwatch) | vps | authoritative | trex_combined_v9.build | report_emails (Mon) | healthy | rex_products, ipo_watchlist, parquets |
| Blue Ocean Report | L&I overnight trading analysis | vps | authoritative | blue_ocean_report.build_html | **not wired to send** (P1-22 pending) | healthy (built, not sent) | Bloomberg BlueOcean sheet |
| Autocall Tool | Vol-based heuristic autocall analysis (NOT pricing) | vps | authoritative | autocall analyzer | /notes/tools/autocall | healthy | vol data, 3 Excel fixtures |
| Morning Triage Email | 08:00 health digest; always sends (bypass gate) | vps | authoritative | run_assertions + morning_triage_email | Graph API (critical alert) | healthy | system_flags, mkt_pipeline_runs |
| Webapp /filings, /operations, /notes, /tools | SEC tracker + pipeline + tools surface | all (vps+Render) | authoritative (vps) / replica (Render) | webapp routers (read-only) | rexfinhub.com | healthy | etp_tracker.db |
| Send pipeline (send_all + email_alerts + system_flags + recipients + preflight) | Atomic gated send with 7 safeguards | vps | authoritative | send_all.py | Graph API | **fragile** — exactly-once is JSON not DB ledger; recipient misroute silent-skips | system_flags, .send_audit.json |
| Render replica | Public read-only mirror | render | replica | 4-hourly DB+parquet+cache upload | rexfinhub.com public | healthy (parquets must be env vars, not disk) | VPS uploads |

### LAYER 7 — RULES / CONFIG / ARTIFACTS (data-as-code)

| component | purpose | device | authority | writer | readers | health | deps |
|---|---|---|---|---|---|---|---|
| config/rules/ (git-tracked) | THE rules truth; git-mirrored + Render replica | vps | authoritative | classify_daily + Ryu | market/config RULES_DIR (primary) | healthy | — |
| fund_master.csv (~7.2K rows, 28 cols) | Full 3-axis taxonomy master (Layer-1) | vps | authoritative | fund_master_writer + Ryu | apply_fund_master | healthy | — |
| fund_mapping.csv | Legacy 5-category projection (ticker→etp_category) | vps | authoritative | classify_daily | apply_classification_sweep, all reports | healthy (dual-layer by design) | — |
| attributes_{LI,CC,Crypto,Defined,Thematic}.csv | Per-category attribute specs | vps | authoritative | classify_daily | sweep, reports, screener | healthy | — |
| exclusions.csv | Out-of-scope (Other) full-ticker rows; suppress gap-nag | vps | authoritative | classify_daily | sweep, assertions | healthy | — |
| issuer_mapping.csv + issuer_brand_overrides.csv (231KB) | Issuer canonicalization + brand aliases | vps | authoritative | classify_daily + Ryu | transform, apply_issuer_brands | healthy | — |
| rex_funds.csv (63 rows) | REX universe → is_rex | vps | authoritative | Ryu + fund_master_writer | classify, transform | healthy | — |
| rex_suite_mapping.csv | ticker→suite_id for T-REX grouping | vps | authoritative | Ryu | trex_combined_v9 | healthy | — |
| underlier_overrides.csv | Underlier normalization exceptions | vps | authoritative | Ryu | apply_underlier_overrides | healthy | — |
| competitor_map.csv | Competitor ticker→name/REX-equiv | vps | authoritative | Ryu | filing_race, competitor reports | healthy | — |
| market_status.csv / issuer_canonicalization.csv | Reference enums + SEC↔Bloomberg issuer normalization | vps | reference | hardcoded/Ryu | transform, assertions | healthy | — |
| data/rules/ (legacy fallback) | Fallback-only rules dir | vps | replica | none (frozen 5/11) | RULES_DIR fallback only | **dead** — never read (config/rules/ exists) | — |
| Analysis parquets (whitespace_v4, filing_race, issuer_cadence, foreign_competitors, launch_candidates, whitespace_candidates) | Weekly L&I/launch-race/whitespace artifacts | vps+render | replica | parquet-rebuild (Mon+Fri 06:00) | weekly reports, screener UI | healthy (v1-v3 version sprawl pending archive) | mkt_master_data |
| Static CSVs (autocall_index_levels, aum_goals.json, ipo_watchlist.yaml, company_descriptions.yaml, expected_recipients.json, render.yaml) | Curated reference fixtures | vps(+Render) | reference | Ryu | tools, report builders | healthy | — |
| capm_products.csv / capm_trust_aps.csv | (legacy CAPM metadata) | vps | na | none (deprecated) | reader code present, table dropped | **dead/zombie** — CSV+reader code orphaned; DB table already DROPPED | — |
| config/.env | VPS secrets (CBOE cookie, Graph keys, DB paths) | vps | authoritative | Ryu/skill | systemd EnvironmentFile | healthy | — |
| config/.send_enabled | Send gate dotfile (currently 'false') | vps | authoritative (DB-first) | gate timers + set_flag | send_all (file path), DB-first read | fragile (dual-written w/ system_flags; DB wins) | — |
| config/rules.bak_2026-05-11/ | Pre-merge rules snapshot | vps | na | one-time snapshot | Ryu (recovery) | healthy (historical) | — |

## STORAGE - COMPLETE TABLE INVENTORY (all 64 live tables, verified 2026-06-15)

> Round-1 synthesis under-inventoried storage (caught by the critic). This is the full set.
> Row counts are live. Subsystems that were entirely undocumented before today are noted.

### SEC / filing identity

| rows | table | purpose |
|---:|---|---|
| 15,939 | `trusts` | Fund-family roots (CIK/name/slug); FK parent of all filing data |
| 633,183 | `filings` | Every SEC form-filing (form/accession/date/links) |
| 714,083 | `fund_extractions` | Per series/class extraction from each filing (effective date, ticker) |
| 215,381 | `fund_status` | Filings-universe lifecycle per series (PENDING/EFFECTIVE/DELAYED) - feed, not authority |
| 51,943 | `name_history` | Append-only fund-name changes over time |
| 13,789 | `filing_alerts` | Tier-1 realtime atom-feed alerts (enrichment queue) |
| 114 | `filing_analyses` | LLM per-filing analysis cache (Top Filings of the Day) |
| 59 | `trust_candidates` | Bulk-discovery review queue (net-new filers, is_active=False) |
| 1 | `trust_requests` | Admin trust-add requests |

### REX product + identity

| rows | table | purpose |
|---:|---|---|
| 586 | `rex_products` | REX product lifecycle authority (Filed->Delayed->Effective->Listed->Delisted) |
| 586 | `product_master` | Canonical product identity (canonical_id) |
| 1,891 | `identifier_xref` | ticker/CIK/series cross-reference to canonical_id |
| 1,086 | `status_history` | Bi-temporal status audit - THE status authority |
| 0 | `rex_product_status_history` | EMPTY duplicate of status_history - RETIRE (Phase 1) |
| 40 | `capm_trust_aps` | LIVE seed: Trust & APs webapp tab; auto-seeded on Render DB swap (database.py:236) - KEEP, load-bearing |
| 1,916 | `capm_audit_log` | Append-only admin product-edit audit |

### Market data (Bloomberg)

| rows | table | purpose |
|---:|---|---|
| 7,571 | `mkt_master_data` | Canonical live ETP facts + 3-axis taxonomy; FULL-REFRESH each sync |
| 7,571 | `mkt_daily_snapshot` | Append-only EOD history (WS-C1, wired to chain today) |
| 280,090 | `mkt_time_series` | Per-fund AUM monthly history (36mo); full-refresh |
| 6,594 | `mkt_stock_data` | Underlying STOCK data (mcap/price) for single-stock underlier analysis - DISTINCT from ETP data |
| 4 | `mkt_report_cache` | Baked li/cc/flow report JSON (read cache-first at send) |
| 491 | `mkt_pipeline_runs` | Market sync run ledger |
| 0 | `mkt_global_etp` | Empty - global ETP supplement (unused) |

### Rules materialized in DB (from config/rules CSVs)

| rows | table | purpose |
|---:|---|---|
| 2,626 | `mkt_fund_mapping` | ticker->etp_category (legacy projection) |
| 2,656 | `mkt_category_attributes` | map_li_*/map_cc_* attribute rows |
| 420 | `mkt_issuer_mapping` | issuer->display brand |
| 126 | `mkt_exclusions` | exclusion rows (pair + full-ticker Other) |
| 17 | `mkt_market_status` | market_status code map |
| 101 | `mkt_rex_funds` | REX universe -> is_rex |

### Classification

| rows | table | purpose |
|---:|---|---|
| 7,570 | `mkt_fund_classification` | FactSet-style 3rd encoding - STALE, near-zero readers; build-prove-retire |
| 519,269 | `classification_audit_log` | 3-axis classification decision journal (append-only) |
| 486 | `classification_override` | Admin per-field overrides (highest precedence) |
| 1,765 | `classification_proposals` | LLM/queue review backlog (Phase 1-E burn) |

### Underlier

| rows | table | purpose |
|---:|---|---|
| 483 | `underlier_master` | Underlier dimension (root economic exposure) |
| 515 | `fund_underlier` | fund->underlier bind |

### CBOE pillar (PARTLY LIVE - critic correction)

| rows | table | purpose |
|---:|---|---|
| 475,254 | `cboe_symbols` | 475k full availability sweep - FROZEN 2026-05-13 (Cloudflare WAF on VPS IP) |
| 13,512 | `cboe_known_active` | NASDAQ/SEC active-listing universe - LIVE, refreshed today 07:01 via rexfinhub-cboe.timer |
| 56 | `cboe_scan_runs` | CBOE scan run ledger (latest 2026-06-15 07:01 - pillar NOT fully dead) |
| 933 | `cboe_state_changes` | reserved-symbol state transitions |
| 309 | `reserved_symbols` | REX reserved-symbol universe - CORE CBOE-pillar asset (was uncatalogued) |
| 0 | `reserved_symbols_audit_log` | reserved-symbol change audit (empty) |

### L&I scoring engine

| rows | table | purpose |
|---:|---|---|
| 72,534 | `li_engine_daily` | Canonical L&I score history (final_score v1.0.1) - the scoring authority |
| 11 | `li_engine_runs` | engine run ledger |
| 10,729 | `li_etp_daily` | per-ETP daily engine inputs |
| 121 | `li_sector_daily` | sector-level daily |
| 608 | `recommendation_history` | L&I rec track record - POPULATED (was thought empty; backfilled) |

### Autocall subsystem (was uncatalogued - relevant to autocall report)

| rows | table | purpose |
|---:|---|---|
| 125,966 | `autocall_index_levels` | Daily index levels for autocall pricing/analytics |
| 26 | `autocall_index_metadata` | Autocall index definitions |
| 8 | `autocall_crisis_presets` | Stress/crisis scenario presets |
| 0 | `autocall_sweep_cache` | Autocall sweep cache (empty) |

### Screener / distributions / calendar

| rows | table | purpose |
|---:|---|---|
| 4,991 | `screener_results` | Screener computed results |
| 1 | `screener_uploads` | Screener cache upload marker |
| 620 | `fund_distributions` | Dividend/distribution data (income + autocall reports) |
| 10 | `nyse_holidays` | Trading-calendar holidays - drives Blue Ocean 'first business day' |

### Ops / observability / send

| rows | table | purpose |
|---:|---|---|
| 28 | `email_recipients` | Per-report recipient lists (incl. blue_ocean=5) |
| 1 | `digest_subscribers` | Public digest signup registry |
| 5 | `system_flags` | THE gate store (send_enabled/send_paused/preflight_maintenance/autogo_on_warn) |
| 105 | `system_event` | System event log |
| 858 | `assertion_run` | Morning-triage assertion results |
| 1 | `preflight_run` | Preflight audit result |
| 1,195 | `pipeline_runs` | SEC pipeline run ledger |
| 0 | `api_audit_log` | M2M upload audit (empty) |
| 0 | `analysis_results` | empty |
| 8 | `sqlite_sequence` | SQLite internal |

_Total: 64 tables._

## DEVICE MAP (cross-device coherence)

| layer | local C: | VPS (prod) | D: archive | authority | sync | drift risk |
|---|---|---|---|---|---|---|
| SOURCES | Code only (read-only dev). cache/sec 916MB local EDGAR cache, not synced | All live feed access: SEC EDGAR, Bloomberg SharePoint Graph, CBOE portal (dead). Bloomberg xlsm lands at data/DASHBOARD/ | D:/sec-data/archives/bloomberg holds raw xlsm 2026-04-13..2026-05-11 then STOPS (archive discontinued post-5/26) | VPS authoritative for all source pulls | None for cache; Bloomberg xlsm archived to D: was manual, now broken | D: Bloomberg archive is incomplete — only the VPS has post-5/11 raw pulls; no cold copy of source data since mid-May |
| INGESTION | Code only; pipeline never run authoritatively locally | All ingestion daemons + batch run here (atom-watcher, single-filing-worker, reconciler, run_pipeline, market pipeline) | None | VPS sole authority | N/A | Low — ingestion is VPS-exclusive |
| STORAGE | etp_tracker.db 15MB dev stub (6 days stale, 2026-06-08); live_feed.db; 13f_holdings.db 57KB local-only stub. Worktree holds 807MB near-full copy + etp_tracker.stub.db + etp_tracker_phase1_work.db (745MB work fixture) | etp_tracker.db 860MB = THE source of truth (60 tables). live_feed.db separate. 13f_holdings.db ABSENT on VPS. Plus ~4GB redundant: etp_tracker_deploy.db (109MB), 4x pre_*.db rollback snapshots (~790MB each), 3x etp_tracker_render.*.db staging copies | D:/sec-data/databases/etp_tracker_backup.db 1.2GB DEAD (2026-03-17, 89d stale). Nightly backups/ (newer) pull when laptop online | VPS authoritative; Render = replica; local + D: = never authoritative | VPS→Render 4-hourly .backup+gzip upload. VPS→D: nightly pull (Syncthing/sync_vps_to_d when laptop online). Local↔Render via Syncthing pull only | HIGH — local DB 6d stale and would mislead if used for decisions; worktree phase1_work fixtures undocumented; D: backup chain depends on laptop presence (>8d absence loses history, accepted); 13f DB exists only as local stub |
| ENRICHMENT | Code only | All 11 post-steps run in bloomberg-chain here; restamp mkt_master_data + rex_products | None | VPS sole authority | Rules deltas committed to git (commit_rules_delta) → Render replica | Low for compute; rules drift covered by git_tree_clean assertion + daily commit |
| SCHEDULING | Windows Task Scheduler setup scripts (setup_scheduler.py, setup_watcher.bat) exist but UNUSED — local dev has no scheduled automation | systemd timers + crontab = the only live scheduler. 13 active timers, 5 disabled (send machinery), 1 dead-firing (cboe) | None | VPS systemd authoritative | Unit files in deploy/systemd/ git-tracked; installed to /etc/systemd/system | MEDIUM — local schtasks scripts are misleading dead artifacts implying local automation that does not exist; send-machinery timers disabled (emails dark) |
| OUTPUTS | reports/ dir holds built HTML/docx locally during dev; not authoritative | All report builds + sends run here (gated). Webapp server :8001 here | D:/sec-data/rexfinhub/total_returns has hollow/stub snapshot CSVs (abandoned); some real ticker bundles | VPS authoritative; Render replica serves public read-only | VPS→Render 4-hourly (DB + 8 parquets + screener cache via bearer token; parquets MUST be Render env vars not disk) | MEDIUM — Render parquet-on-disk dies on deploy (caused 6/8-6/9 outage); send pipeline currently dark so no external outputs flowing |
| RULES/CONFIG | config/rules/ synced via Syncthing + git; local .send_enabled 5d stale (2026-06-08) does NOT reflect VPS gate state | config/rules/ = authoritative runtime+git mirror. data/rules/ legacy fallback (never read). .env + .send_enabled live here | config/rules.bak_2026-05-11 historical snapshot path | VPS config/rules/ authoritative (git is code truth, VPS pulls) | classify_daily mirrors after every apply; commit_rules_delta pushes to git main daily; Render auto-deploys main | MEDIUM — local .send_enabled stale and would misrepresent gate if read locally; data/rules/ fallback is confusing dead state; Syncthing replicates rule edits across devices (conflict risk if both edit) |

## PURPOSE VIOLATIONS (the Phase-1 work population - critic-corrected)

- **[critical] PV-01 Send pipeline dark — emails not firing for 35+ days** - intended: Daily/weekly reports send on schedule via gated send_all (gate-open 19:00, daily 19:30, gate-close 20:00) / actual: send_enabled=False (verified .send_enabled='false' on VPS); rexfinhub-daily, gate-open, intraday-refresh, preflight timers all DISABLED. Only gate-close (lock) still time -> P0 Task #6: re-enable gate-open + daily + preflight timers, set send_enabled=True under preflight gate, send one supervised bundle
- **[high] PV-02 status_reconciler vs sync_rex_products dual-write to rex_products.status with no transaction boundary** - intended: sync_rex_products promote-only evidence; status_reconciler is the authority and drives status+status_cached from status_history / actual: Both write rex_products.status; sec-scrape post-step runs sync_rex_products --apply every 4h while bloomberg-chain runs status_reconciler --apply at 17:15/21:00 — no shar -> Make status_history the only writer of rex_products.status (sync_rex_products writes evidence rows only, never .status); add a dri
- **[medium] PV-03 CBOE timer fires nightly into a dead WAF wall** - intended: 03:00 nightly 475k symbol availability sweep / actual: Cloudflare WAF blocks the VPS IP since 5/13; the timer still fires daily (last run 2026-06-15 03:00) producing no data writes — wasted run + misleading 'scheduled' state; -> Disable rexfinhub-cboe.timer (stop the nightly no-op) and add a banner/age-stamp on /tools/tickers; revisit only if a residential-
- **[high] PV-04 13f-quarterly is a no-op and the target DB does not exist on VPS** - intended: Quarterly 13F ingest populating data/13f_holdings.db for L&I intel routers / actual: 13f_holdings.db is ABSENT on VPS (only a 57KB local stub exists); Q1'26 never ingested; cron is a no-op backfill; 13F intel routers (/holdings/*, /intel/competitors) exis -> Either (a) gate the 13F routers behind @require_admin and mark 13F formally parked, or (b) re-ingest Q4'25+Q1'26 to VPS. At minimu
- **[medium] PV-05 Flow Report TICKER_SUITE hard-coded — drifts from mkt_master_data.suite** - intended: 20-fund RPS suite flows reflecting current suite assignments / actual: portfolio_suite_flow.py TICKER_SUITE is a dict literal, not a DB query; if a ticker's suite changes in mkt_master_data the report silently shows stale grouping -> Replace TICKER_SUITE literal with a lookup from mkt_master_data.suite (or rex_suite_mapping.csv) so the report self-updates
- **[high] PV-06 Exactly-once send is JSON-file, not a DB ledger — re-invocation can resend** - intended: Once a report ships to a recipient on a day, never resend the same bundle that day / actual: Dedup lives in .send_log.json/.send_audit.json (best-effort JSON, last-500 truncation, non-atomic write on VPS fs); send_all.py has no cross-bundle dedup; a re-invoke of  -> Slice 3: add a send_ledger DB table keyed (report_key, recipient, send_date) with a unique constraint; send_all checks it before f
- **[medium] PV-07 Recipient misroute silently skips instead of hard-erroring on unknown list_type** - intended: Unknown list_type must hard-error (fail loud) so a report never silently goes to no one / wrong list / actual: recipients.py returns empty list on unknown list_type; send_all._resolve_recipients silently skips when empty — a typo in list_type means the report just doesn't send, no -> Slice 3: raise on unknown list_type in recipient resolution; add the known-list_type set as a validated enum
- **[medium] PV-08 email_recipients has no validation guard** - intended: Registry validated against known list_types / actual: 28 rows; an unknown/typo list_type row would be accepted; couples to PV-07 -> Add CHECK/enum on list_type at write time (manage_recipients + admin UI)
- **[medium] PV-09 Blue Ocean report built but never sent** - intended: Weekly L&I overnight-trading report in the Monday send bundle / actual: blue_ocean_report.build_html() produces the HTML but there is no report_emails send wrapper — it never ships (P1-22 pending) -> P1-22: add report_emails.send_blue_ocean() + recipient list_type + wire into Monday bundle
- **[medium] PV-10 .send_enabled dual-written with system_flags; file layer can go stale** - intended: Single gate source of truth / actual: DB system_flags is read-first with file fallback; any code path that reads the dotfile directly can see a stale value; local copy is 5d stale and would mislead if used -> Retire the dotfile entirely once all readers go through system_flags.set_flag/get_flag (ARCHITECTURE.md notes dotfile 'retired for
- **[medium] PV-11 capm_products zombie CSVs + reader code outlive the dropped table** - intended: CAPM metadata merged into rex_products (Phase 3); CSVs+table retired after proof-of-death gate / actual: The capm_products TABLE is already DROPPED on VPS (no such table), but capm_products.csv + capm_trust_aps.csv remain in repo and reader code persists in webapp/routers/ca -> Proof-of-death cleanup: confirm capm router/dashboard/assertion paths no longer hit the table at runtime (or error), remove the de
- **[medium] PV-12 rexfinhub-api server has no git-pull pre-step — code drift risk** - intended: VPS deploys only via git pull --ff-only (ADR 0011 E4) / actual: rexfinhub-api.service (PID since 2026-06-10) has no ExecStartPre=git pull; the long-lived server can drift from main across days while other units pull daily -> Add ExecStartPre=git pull --ff-only to rexfinhub-api.service OR rely on the git_tree_clean assertion + a periodic restart; documen
- **[low] PV-13 Windows Task Scheduler setup scripts imply nonexistent local automation** - intended: Single scheduler authority (VPS systemd) / actual: setup_scheduler.py, setup_watcher.bat, run_daily_digest.bat carry schtasks references but nothing runs them; misleading dead artifacts on local -> Move to scripts/archive/ (or delete after surface-to-Ryu) so the scheduler story is unambiguously VPS-only
- **[medium] PV-14 ~4GB redundant DB copies on VPS** - intended: One live DB + 7 nightly backups / actual: VPS data/ holds etp_tracker_deploy.db (109MB, May 14), 4x pre_*.db rollback snapshots (~790MB each, 6/8-6/9), 3x etp_tracker_render.*.db staging copies (840MB each) — ~4G -> With Ryu go-ahead, prune pre_*.db snapshots >7d and stale render-staging copies; ensure disk-hygiene cron covers etp_tracker_rende
- **[low] PV-15 data/rules/ fallback path is dead but active — operator confusion** - intended: config/rules/ sole source / actual: market/config.py still has data/rules/ as RULES_DIR fallback; it never triggers (config/rules/ exists) but its presence creates split-brain confusion -> Keep the fallback for Render's persistent-disk edge case (it is load-bearing there per comment) but document clearly in RUNBOOK wh
- **[low] PV-16 mkt_global_etp orphan table (0 rows, no writer)** - intended: unknown — never documented / actual: 0 rows, no grep hits for a writer; stub/orphan in the 60-table DB -> Confirm zero readers then drop in a proof-of-death cleanup pass
- **[low] PV-17 35 of 36 Jinja templates bypass StrictUndefined** - intended: All templates catch missing context vars / actual: Only 1 of 36 template envs uses StrictUndefined; the rest render undefined as empty silently -> Slice: migrate template envs to StrictUndefined incrementally, starting with money-page templates
- **[high (recurrence risk)] PV-18 Render parquets volatile on deploy** - intended: Analysis parquets persist on Render across deploys / actual: Disk copies die on deploy; must live as Render env vars — a prior disk-copy implementation caused the 6/8-6/9 outage -> Verify the current Render parquet path reads from env-var/upload not disk; add a deploy smoke-test that the 8 parquets resolve
- **[low] PV-19 13 multi-series / NULL-ticker pollution — RESOLVED, retain as regression guard** - intended: No Listed rows with NULL ticker on /operations/pipeline / actual: NOW 0 (verified) — the 2026-06-02 memory open item is fixed (Task #7 completed); keep the orphan assertion live so it never regresses -> Mark the null-ticker memory file RESOLVED; confirm the Phase-D orphan assertion is in the 28-assertion suite
- **[low] PV-20 Worktree work-fixture DBs undocumented (etp_tracker.stub.db, etp_tracker_phase1_work.db 745MB)** - intended: Worktree data dir holds only the working DB / actual: fix-rex-family-2026-06-08/data/ holds an undocumented 745MB phase1_work.db + a stub.db with no code references; Syncthing would replicate these large files -> Document or remove the phase1_work fixtures; ensure .stignore excludes worktree data/*.db from Syncthing to avoid multi-GB cross-d

## ROUND-2 CRITIC CORRECTIONS (applied)
- **capm_trust_aps is LIVE load-bearing seed (40 rows)**, NOT an orphan - do NOT retire; it keeps
  the Trust & APs webapp tab populated (database.py:236 auto-seed). PV-11 downgraded from "runtime
  zombie" to cosmetic docstring cleanup (legacy_capm_retired assertion is defensive).
- **CBOE pillar is PARTLY LIVE**, not dead: cboe_known_active (13,512) refreshed today 07:01 via
  rexfinhub-cboe.timer; reserved_symbols (309) is a core asset. ONLY the 475k cboe_symbols full
  sweep is WAF-frozen (max 2026-05-13).
- **Blue Ocean recipients already exist** (email_recipients.blue_ocean=5) - only the send wrapper +
  monthly-first-business-day cadence are missing (PV-09 narrowed).
- **rex_product_status_history (0 rows) is an empty duplicate** of status_history -> retire.
- **VPS WORKING TREE IS DIRTY (device-coherence violation):** running code != git - modified
  screener/li_engine/analysis/ticker_analyze.py (the canonical-score rewire, never committed) +
  untracked Hub_Trex ad-hoc modules (adhoc_excel/adhoc_html/foreign_ipo_brief/recommendation_brief +
  LI_ADHOC_PLAYBOOK.md) + stale .bak/.PAUSED files. Phase-1 must commit the real work; the
  git_tree_clean assertion must flag this.

## HIDDEN ASSETS (discovered; keep/migrate/retire)
- `etp_tracker_phase1_work.db (745MB)` [local (worktree fix-rex-family-2026-06-08/data/)] ref_by_code=False -> **retire after Phase 1 merge — document pr**: Phase-1 family-rebuild work fixture (multi-series drop fix, Tasks #1-2)
- `etp_tracker.stub.db (15MB)` [local (worktree)] ref_by_code=False -> **retire — no references**: Test/stub DB, no code references
- `rexfinhub.db (0 bytes)` [local (repo root)] ref_by_code=False -> **retire — confirm database.py default-pat**: Empty stub created 2026-03-23, never used
- `etp_tracker_deploy.db (109MB, May 14)` [vps (data/)] ref_by_code=False -> **retire — stale staging artifact, prune w**: Old deploy-staging DB copy
- `4x etp_tracker.pre_*.db rollback snapshots (~790MB each)` [vps (data/)] ref_by_code=False -> **migrate the most recent to D: then retir**: Pre-destructive-apply rollback snapshots (pre_effectiveness_overhaul, pre_microsector_overlay, pre_null_repair, pre_phas
- `3x etp_tracker_render.*.db staging copies (840MB each)` [vps (data/)] ref_by_code=True -> **retire — these are transient; disk-hygie**: Render-upload staging snapshots from today's 4-hourly runs
- `13f_holdings.db (57KB local stub; ABSENT on VPS)` [local (data/)] ref_by_code=True -> **decide: migrate (re-ingest to VPS) or fo**: 13F holdings — intended Q4'25/Q1'26 institutional disclosures
- `D:/sec-data/databases/etp_tracker_backup.db (1.2GB, 2026-03-17)` [d] ref_by_code=False -> **retire — superseded by data/backups/ cha**: Old rolling backup, 89 days stale
- `D:/sec-data/archives/bloomberg (406MB, stops 2026-05-11)` [d] ref_by_code=False -> **keep + REPAIR — archive discontinued pos**: Raw Bloomberg xlsm archive for post-mortems
- `D:/sec-data/rexfinhub/total_returns (mostly hollow stubs)` [d] ref_by_code=False -> **retire — abandoned test output; real tic**: Nightly total-returns snapshots (abandoned — most files near-zero bytes)
- `D:/sec-data/archives/retired (empty dir, 2026-04-02)` [d] ref_by_code=False -> **keep as archive landing zone or retire i**: Archive for superseded outputs
- `mkt_global_etp table (0 rows)` [vps (etp_tracker.db)] ref_by_code=False -> **retire — drop after confirming zero read**: Unknown — orphan stub table
- `capm_products.csv + capm_trust_aps.csv (+ reader code in capm.py/dashboard.py/models.py/run_assertions.py)` [vps+local (webapp/data_static/)] ref_by_code=True -> **retire — table is gone; remove orphan CS**: Legacy CAPM metadata; merged into rex_products Phase 3; TABLE already dropped
- `data/rules/ (VPS legacy fallback dir)` [vps] ref_by_code=True -> **keep (load-bearing for Render persistent**: Pre-5/11 rules location; now fallback-only
- `edgartools shadow client + edgar_shadow_compare.py` [vps] ref_by_code=True -> **keep — proof mechanism for a future cuto**: ADR 0010 off-path extractor comparison harness
- `15 git worktrees (5 outside .claude/worktrees/, some dormant since 2026-04-02)` [local] ref_by_code=False -> **migrate/retire — merge or prune dormant **: Multi-agent feature isolation (blue-ocean, launch-race, flow-fix, null-ticker, etc.)
- `Windows Task Scheduler scripts (setup_scheduler.py, setup_watcher.bat, run_daily_digest.bat)` [local] ref_by_code=False -> **retire to scripts/archive/ — VPS systemd**: schtasks-based local automation that is never run
- `disabled send-machinery timers (gate-open, daily, intraday-refresh, preflight, bulk-sync)` [vps] ref_by_code=True -> **keep — re-enable as part of PV-01 send-g**: Scheduled send + refresh machinery

_(capm_trust_aps reclassified KEEP per round-2; CBOE assets reclassified LIVE.)_

---
doc: system
status: canonical
updated: 2026-05-19
---

# rexfinhub — System (As-Is)

> Canonical reference for what production does TODAY. Present tense only. Future-tense design statements belong in `TARGET.md`. Bug list lives in `### known-bugs` here until fixed.
>
> Source-of-truth date: VPS state as of 2026-05-19.

### topology

```
                External                          ┌─ atom feed (every 60s)
  SEC EDGAR ──────────────────────────────────────┼─ bulk 485-series (4×/day)
                                                  └─ submissions.zip (Sun)
  M365 SharePoint ──── Bloomberg .xlsm ─── (Graph API pull 17:15 + 21:00 ET) ──→ VPS
  CBOE issuer portal ─── (nightly 03:00 ET via session cookie) ──→ VPS
  Microsoft Graph API ─── (outbound email send) ←─── VPS
  SEC 13F-HR ──── (quarterly Feb/May/Aug/Nov) ──→ VPS

                Production VPS  jarvis@46.224.126.196 (Ubuntu, 38 GB disk)
                ─────────────────────────────────────────────────────────────
                Source of truth for all writes.
                Systemd timers orchestrate every workflow.
                SQLite at /home/jarvis/rexfinhub/data/etp_tracker.db (~650 MB)
                                │
                                ├──── (DB upload via /api/v1/db/upload, end of daily)
                                ▼
                Render webapp  rex-etp-tracker.onrender.com
                ─────────────────────────────────────────────────────────────
                Read-only DB replica.
                Public surface: rexfinhub.com
                Auto-deploys on push to main.

                D Drive (Transcend USB)  D:\sec-data\
                ─────────────────────────────────────────────────────────────
                Archive only. Nightly backup tarballs + cache snapshots.
                Not queried live.

                Local C:\Projects\rexfinhub\
                ─────────────────────────────────────────────────────────────
                Dev only. Synced laptop↔desktop via Syncthing.
                NEVER authoritative for any data.
```

### data-sources

| Source | Mechanism | Refresh cadence | Code |
|---|---|---|---|
| SEC EDGAR atom feed | HTTP poll every 60s | Real-time, 1-3 min lag | `etp_tracker/atom_watcher.py` |
| SEC EDGAR bulk scrape | 4×/day systemd | 08/12/16/20 ET | `etp_tracker/run_pipeline.py` |
| SEC submissions.zip | Sunday 07:00 | Weekly bulk | `scripts/sync_trust_universe.py` (REDUNDANT — see [[fresh-poller]]) |
| Bloomberg .xlsm | M365 SharePoint via Graph API | Mon-Fri 17:15 + 21:00 ET | `webapp/services/graph_files.py` |
| CBOE issuer portal | Authenticated session scrape | Nightly 03:00 ET | `webapp/services/cboe/`, `scripts/run_cboe_scan.py` |
| OpenFIGI | (not yet wired — planned Phase 4) | — | — |
| SEC 13F-HR | Quarterly bulk + 7-day incremental | Feb 19, May 20, Aug 19, Nov 19 @ 06:00 ET | `scripts/run_13f.py auto` |
| Microsoft Graph (outbound mail) | Service-principal app-only | On send (19:30 ET weekdays) | `webapp/services/graph_email.py` |

### workflows

All times Eastern. Mon-Fri unless noted.

| Time | Workflow | Code | Notes |
|---|---|---|---|
| 03:00 | CBOE symbol reservation scan | `scripts/run_cboe_scan.py` + systemd `rexfinhub-cboe.timer` | Requires [[cboe-cookie]] |
| Every 60s (market hrs) | [[fresh-poller]] (atom watcher) | `etp_tracker/atom_watcher.py` | Catches new filings ~1-3 min |
| Every 15 min (market hrs) | Fresh-poller systemd wrap | `scripts/poll_fresh_filings.py` + `rexfinhub-fresh-poller.timer` | Distinct role from atom watcher — see `### scraper-pathways` (ADR 0005) |
| 08:00 | SEC reconciler | `scripts/sec_reconciler.py` + `rexfinhub-reconciler.timer` | |
| 08:05/12:05/16:05/20:05 | SEC intraday refresh | `scripts/intraday_refresh.py` + `rexfinhub-intraday-refresh.timer` | 4×/day; skips the SEC scrape when fresh-poller is fresh (ADR 0005) |
| ~~09:00~~ | ~~Classification sweep email~~ | ~~`scripts/classification_sweep.py`~~ | **DISABLED 2026-05-19 (ADR 0003)** — unit file kept in repo for revert |
| 17:15 + 21:00 | [[bloomberg-pull]] + chain | `rexfinhub-bloomberg.timer` → `rexfinhub-bloomberg-chain.service` | One consolidated ExecStartPost: `scripts/apply_bloomberg_post_steps.py` (wraps fund_master / underlier_overrides / issuer_brands / classification_sweep). Per ADR 0003. |
| 18:30 | [[preflight]] audit + [[auto-go]] | `scripts/preflight_check.py` + `rexfinhub-preflight.timer` | PR #16: writes `.preflight_decision.json` on pass or warn-with-flag |
| 19:00 | [[gate]] auto-open | `rexfinhub-gate-open.timer` | Flips `.send_enabled` to true |
| 19:30 | Daily pipeline + send | `scripts/run_daily.py` + `scripts/send_all.py --use-decision --send` + `rexfinhub-daily.timer` | Mon=`all` bundle, Tue-Fri=`daily` bundle |
| 20:00 | [[gate]] auto-close | `rexfinhub-gate-close.timer` | |
| 20:15 | Pipeline summary email | `scripts/pipeline_summary.py` + jarvis crontab | PR #17: emails relasmar@rexfin.com |
| 23:00 | DB backup | `rexfinhub-db-backup.timer` | 14-day rotation on VPS |
| ~~Sun 07:00~~ | ~~Trust universe bulk sync~~ | ~~`scripts/sync_trust_universe.py`~~ | **DISABLED 2026-05-19 (ADR 0003)** — atom watcher covers new-CIK discovery |
| Feb/May/Aug/Nov 19-20 @ 06:00 | 13F quarterly + 7-day incremental | `scripts/run_13f.py auto` + `rexfinhub-13f-quarterly.timer` | Aligns with SEC quarterly publication |
| Fri 06:00 | Parquet rebuild | `rexfinhub-parquet-rebuild.timer` | Whitespace v4 + screener |

### databases

| DB / Path | Tables (primary) | Writer | Size |
|---|---|---|---|
| `/home/jarvis/rexfinhub/data/etp_tracker.db` | `mkt_master_data`, `rex_products`, `capm_products`, `filings`, `fund_extractions`, `fund_status`, `trusts`, `email_recipients`, `mkt_cboe_reserved_symbols`, `mkt_pipeline_runs`, `mkt_report_cache`, **`product_master`, `identifier_xref`, `underlier_master`, `fund_underlier`** (Phase 4), **`status_history`** (Phase 5), **`classification_override`, `assertion_run`** (Phase 6), **`system_flags`, `preflight_run`, `system_event`** (Phase 7B) | Daily pipeline + admin UI | ~669 MB |
| `/home/jarvis/rexfinhub/data/holdings.db` | `Institution`, `Holding`, `CusipMapping` | `scripts/run_13f.py` (quarterly) | ~850 MB |
| `/home/jarvis/rexfinhub/data/structured_notes.db` (if present) | Structured-product extraction tables | Structured notes pipeline | ~290 MB |

### webapp-surfaces

| Route group | Code | Backing data |
|---|---|---|
| `/operations/products` | `webapp/routers/capm.py::_capm_index_impl` (routed via `webapp/routers/operations.py`) | `rex_products` + `capm_products` + `mkt_master_data` (3-way merge — see GAP-07) |
| `/operations/pipeline` | `webapp/routers/pipeline_calendar.py::_pipeline_products_impl` | `rex_products` (all statuses) + `mkt_master_data` |
| `/operations/calendar` | `webapp/routers/pipeline_calendar.py::_pipeline_root_impl` | `fund_distributions` + `nyse_holidays` |
| `/filings/symbols` | `webapp/routers/symbols.py` | `mkt_cboe_reserved_symbols` |
| `/intel/holdings`, `/intel/competitors`, `/intel/insights` | `webapp/routers/intel.py` + `intel_competitors.py` + `intel_insights.py` | `holdings.db` |
| `/admin/*` | `webapp/routers/admin*.py` | Various; gated by `ADMIN_PASSWORD` |
| `/admin/cboe-cookie` | `webapp/routers/admin.py::cboe_cookie_page,cboe_cookie_rotate` (Phase 2, ADR 0004) | Proxies to VPS `POST /pipeline/cboe-rotate` + `GET /pipeline/cboe-status` for `.env` rewrite + `live_check("AAPL")` probe + recovery sweep dispatch |
| `/admin/classify-override/{canonical_id}` | `webapp/routers/admin_classify.py` (Phase 6 Stage 4, ADR 0009) | POST upserts a row into `classification_override` via `webapp/services/classification_resolver.py::set_override`; audit-logged via `capm_audit_log`. DELETE removes the override. GET lists all overrides for a canonical_id. |
| `/api/v1/db/upload`, `/api/v1/db/upload-notes`, `/api/v1/db/upload-holdings` | `webapp/routers/api.py` | Inbound from VPS daily |

### secrets-inventory

| Key | Location | Purpose | Risk if leaked | Rotation status |
|---|---|---|---|---|
| `AZURE_TENANT_ID` | .env (all hosts) | M365 tenant UUID | LOW (UUID alone useless) | OK |
| `AZURE_CLIENT_ID` | .env (all hosts) | App registration ID | LOW | OK |
| `AZURE_CLIENT_SECRET` | .env (all hosts) | Graph API service-principal secret | **CRITICAL** — email + SharePoint access | ⚠ Expiry unknown — check Azure Portal |
| `AZURE_SENDER` | .env (all hosts) | relasmar@rexfin.com | LOW | OK |
| `API_KEY` | .env (all hosts) | `/api/v1/*` auth | **CRITICAL** — overwrites prod DB | ⚠ Unrotated since ≥2026-04-24 |
| `SESSION_SECRET` | .env (all hosts) | Session cookie signing | HIGH | ⚠ Unrotated since ≥2026-04-24 |
| `SITE_PASSWORD` | .env (all hosts) | Site-wide gate | HIGH | ⚠ Unrotated since ≥2026-04-24 |
| `ADMIN_PASSWORD` | .env (all hosts) | Admin login | **CRITICAL** — was public on GitHub until 2026-05-05 | ⚠ Never rotated since exposure |
| `ANTHROPIC_API_KEY` | .env (all hosts) | Claude API (filing analysis) | HIGH (uncapped spend) | ⚠ Unrotated since ≥2026-04-24 |
| `SEC_USER_AGENT` | .env (all hosts) | SEC EDGAR identification | LOW | OK |
| `CBOE_SESSION_COOKIE` | .env (VPS only) | CBOE issuer portal session | MEDIUM | Rotates ~monthly |
| `SMTP_*` | .env (local + VPS, not Render) | SMTP fallback (dormant) | MEDIUM | Consider removing |
| `RENDER_UPLOAD_TOKEN` | .env (VPS only) | Possibly unused | MEDIUM if used | Investigate |

Secret values never appear in this doc — only locations + rotation status. Acute rotation (the GitHub-exposed `ADMIN_PASSWORD`) is Track 0 of the rebuild completion plan; broader hardening is the deferred Phase 0a.

### scheduled-units

| Unit | Cadence | What it runs |
|---|---|---|
| `rexfinhub-atom-watcher.service` | continuous daemon | SEC atom feed polling (60s cycles) |
| `rexfinhub-fresh-poller.timer` | every 15 min Mon-Fri 08:00-20:45 ET | Per-CIK SEC submissions JSON pull with daily-index pre-flight |
| `rexfinhub-bloomberg.timer` | 17:15 + 21:00 ET Mon-Fri | Graph-API pull from SharePoint + sync_market_data + post-steps wrapper (apply_fund_master, apply_underlier_overrides, apply_issuer_brands, apply_classification_sweep, apply_classification_overrides, status_reconciler dry-run) |
| `rexfinhub-preflight.timer` | 18:30 ET Mon-Fri | Preflight check; writes auto-GO decision if pass/warn |
| `rexfinhub-gate-open.timer` | 19:00 ET Mon-Fri | Opens the send gate |
| `rexfinhub-daily.timer` | 19:30 ET Mon-Fri | Send daily report via `send_all.py --bundle daily --use-decision` |
| `rexfinhub-gate-close.timer` | 20:00 ET Mon-Fri | Closes the send gate |
| `rexfinhub-intraday-refresh.timer` | 4×/day Mon-Fri 08:05/12:05/16:05/20:05 ET | classification + screener + parquets + DB compact + Render upload |
| `rexfinhub-morning-triage.timer` | 08:00 ET Mon-Fri | Phase 6 Stage 6: assertion runner + morning triage email |
| `rexfinhub-cboe.timer` | 03:00 ET daily | CBOE symbol reservation scanner (full sweep) |
| `rexfinhub-13f-quarterly.timer` | Feb/May/Aug/Nov 19th 06:00 ET | 13F holdings pipeline |
| `rexfinhub-db-backup.timer` | 23:00 ET daily | DB backup to `data/backups/` |
| `rexfinhub-reconciler.timer` | 08:00 ET weekdays | rex_products + market reconciliation |
| Cron: pipeline_summary | 20:15 ET Mon-Fri | Legacy 20:15 summary email (kept during morning-triage dual-period) |
| Cron: audit_duplicate_tickers | 02:35 ET daily | BUG-02 mitigation |

### scraper-pathways

Three pathways with distinct roles (ADR 0005 — closed Phase 1 Cut 3):

| Unit | Cadence | Source | CIK universe | Writes | Latency |
|---|---|---|---|---|---|
| `rexfinhub-atom-watcher.service` (daemon) | every 60 s, 24/7 | SEC atom feed | ALL filers | `filing_alerts`; auto-creates `trusts` | ~1-3 min |
| `rexfinhub-fresh-poller.timer` | every 15 min, Mon-Fri 08:00-20:45 ET | SEC submissions JSON + daily-index pre-flight | curated ~290 | `filings` + `rex_products` | ~15-20 min |
| `rexfinhub-intraday-refresh.timer` (was `rexfinhub-sec-scrape.timer`) | 4×/day, Mon-Fri 08:00/12:00/16:00/20:00 ET | (delegated; skips scrape if fresh-poller is fresh) | (delegated) | classification + screener + parquets + DB compact + Render upload | ~5-8 min fast path |

The intraday-refresh wrapper (`scripts/intraday_refresh.py`) reads `data/.poll_fresh_filings.log` mtime: if fresh-poller ran within 30 min, calls `run_daily.py --skip-sec`. Otherwise calls full `run_daily.py` as a safety fallback. See `DECISIONS/0005-scraper-merge-analysis.md`.

### known-bugs

- BUG-01: ~~Bitcoin shows 0 competitors~~ **RESOLVED 2026-05-20** — the Phase 4 structural fix is live: underliers are typed in `underlier_master` and `rex_products.underlier` is a hybrid resolving from it. The interim nightly `canonicalize_crypto_underliers` cron has been retired (Track 4b).
- BUG-02: ~~TSII recycled-ticker false promotion~~ **MITIGATED 2026-05-19 (Phase 0b, ADR 0002)** — Phase 3 of sync_rex_products_from_filings.py now requires fund-name overlap before promotion. Nightly duplicate-ticker audit (`scripts/audit_duplicate_tickers.py`) surfaces any new cases in the morning email.
- BUG-03: ~~13+ T-REX 2X products Listed with placeholder inception~~ **MITIGATED 2026-05-19 (Phase 0b, ADR 0002)** — Phase 3 now rejects inception dates before the filing date OR older than 60 days. Existing bad rows still need a one-time manual correction; new cases blocked.
- BUG-04: ~~BMAX US Listed despite Bloomberg vanish~~ **CLOSED 2026-05-19** for the LIQU/DLST case (BMAX, XRPK, SOLX, FNGA demoted by `scripts/demote_liqu_dlst_rex_products.py`); **`.auto_demote_vanished` flag enabled** on VPS so `phase4_demote_vanished_from_market` auto-runs on future vanish cases. The `scripts/run_assertions.py::listed_has_mkt_data` check surfaces any new BMAX-class candidates in the morning triage email.
- BUG-05: ~~Flow report's REX 1W flow KPI ($10.8M) does not match the issuer-table REX row ($16.4M)~~ **MITIGATED 2026-05-19** — `webapp/services/report_data.py::get_flow_report` rex_kpis now uses UNION semantics (`is_rex=1 OR issuer_display='REX'`) so it matches the issuer-table aggregation. The underlying data drift (AXTU has `is_rex=0` despite `issuer_display='REX'`) is still present and should be fixed at the classifier layer in Phase 6 — but the report itself no longer mis-reports.
- BUG-06: ~~Old `rexfinhub-sec-scrape.service` killed by SIGTERM at step 3.5/8 on 2026-05-19 16:00 ET~~ **MITIGATED 2026-05-19 (ADR 0005)** — the old unit had a drop-in (`10-sync-rex-products.conf`) that ran `sync_rex_products_from_filings.py --apply` as `ExecStartPost`, racing against the same call inside `run_daily.main()` step 3.5/8. The new `intraday-refresh` unit has no such drop-in and uses `--skip-sec` to bypass the whole code path when fresh-poller has run recently. Old unit + drop-in remain in `/etc/systemd/system/` (disabled) for revert; can be removed once new unit has run cleanly for a week.
- BUG-07: ~~`temp/submissions.zip` corrupted (partial download)~~ **FIXED 2026-05-19** — `etp_tracker/bulk_loader.py::download_submissions_zip` now writes to `<dest>.partial` while streaming, verifies content-length match + that the file parses as a valid zip, then atomic-replaces. Failure paths leave `.partial` in place + raise. Recurrence blocked.

### known-gaps

- GAP-01: `AZURE_CLIENT_SECRET` expiry date unknown. If it lapses, BOTH email send AND Bloomberg pull fail silently. Action: check Azure Portal + set calendar reminder (Track 0 of the completion plan).
- GAP-02: No CSRF middleware on FastAPI admin routes. Combined with `SameSite=Lax` cookies, a malicious link could trigger state changes if Ryu is signed in.
- GAP-03: No IP allowlist on `/api/v1/db/upload`. Anyone with `API_KEY` can overwrite production DB.
- GAP-04: `/operations/products` merge across `rex_products` + `capm_products` + `mkt_master_data` is still fragile while `capm_products` survives. Phase 3 Stages 1-3 shipped; dropping `capm_products` is Track 4a.
- GAP-05: The 6 manual classification CSVs in `config/rules/` still exist. `classification_override` shipped (Phase 6); deleting the CSVs is Track 4c.
- GAP-06: Direct in-place writes to `rex_products.status` still occur. `status_history` shipped (Phase 5); deprecating direct writes is Track 5A.
- GAP-07: 124 funds (2.4% of active) have NULL `primary_strategy`. Masked by the `preflight_maintenance` flag → preflight WARN instead of FAIL.
- GAP-08 (FIXED 2026-06-03): `upload_db_to_render()` *does* clean up its `data/etp_tracker_render.<pid>.db` + `.upload.gz` staging files in a `finally` — but that cleanup is skipped when the upload process is **killed** before it runs (disk-full / OOM crashes), leaving a ~700 MB orphan. This was self-reinforcing: a disk-full crash left a 700 MB file that filled the disk further. 4.4 GB across 19 files (back to May 20) found and cleared 2026-06-03; both 100% events (2026-05-25, 2026-06-01) trace to this. Fix shipped: a startup sweep in `upload_db_to_render()` deletes any `etp_tracker_render.*` staging file older than 1 h before each upload, so it self-heals. Deployed to VPS 2026-06-03; pending merge to `main` for permanence. Manual clear procedure: RUNBOOK `### touchpoint-vps-disk-cleanup`.

Resolved 2026-05-19 (ADR 0005): fresh-poller vs atom-watcher overlap — both kept, distinct roles. Resolved 2026-05-19 (ADR 0003): 09:00 classification sweep + weekly trust universe sync both disabled.

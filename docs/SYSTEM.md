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
| Every 15 min (market hrs) | Fresh-poller systemd wrap | `scripts/poll_fresh_filings.py` + `rexfinhub-fresh-poller.timer` | Redundant with atom watcher — see GAP-04 |
| 08:00 | SEC reconciler | `scripts/sec_reconciler.py` + `rexfinhub-reconciler.timer` | |
| 08/12/16/20:00 | SEC bulk scrape | `etp_tracker/run_pipeline.py` + `rexfinhub-sec-scrape.timer` | 4×/day batch |
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
| `/home/jarvis/rexfinhub/data/etp_tracker.db` | `mkt_master_data`, `rex_products`, `capm_products`, `filings`, `fund_extractions`, `fund_status`, `trusts`, `email_recipients`, `mkt_cboe_reserved_symbols`, `mkt_pipeline_runs`, `mkt_report_cache` | Daily pipeline + admin UI | ~650 MB |
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

Secret values never appear in this doc — only locations + rotation status. See `DECISIONS/0003-rotate-exposed-secrets.md` (proposed).

### scraper-pathways

Three pathways with distinct roles (ADR 0005 — closed Phase 1 Cut 3):

| Unit | Cadence | Source | CIK universe | Writes | Latency |
|---|---|---|---|---|---|
| `rexfinhub-atom-watcher.service` (daemon) | every 60 s, 24/7 | SEC atom feed | ALL filers | `filing_alerts`; auto-creates `trusts` | ~1-3 min |
| `rexfinhub-fresh-poller.timer` | every 15 min, Mon-Fri 08:00-20:45 ET | SEC submissions JSON + daily-index pre-flight | curated ~290 | `filings` + `rex_products` | ~15-20 min |
| `rexfinhub-intraday-refresh.timer` (was `rexfinhub-sec-scrape.timer`) | 4×/day, Mon-Fri 08:00/12:00/16:00/20:00 ET | (delegated; skips scrape if fresh-poller is fresh) | (delegated) | classification + screener + parquets + DB compact + Render upload | ~5-8 min fast path |

The intraday-refresh wrapper (`scripts/intraday_refresh.py`) reads `data/.poll_fresh_filings.log` mtime: if fresh-poller ran within 30 min, calls `run_daily.py --skip-sec`. Otherwise calls full `run_daily.py` as a safety fallback. See `DECISIONS/0005-scraper-merge-analysis.md`.

### known-bugs

- BUG-01: ~~Bitcoin shows 0 competitors~~ **MITIGATED 2026-05-19 (Phase 0b, ADR 0002)** — canonicalization script normalizes crypto underliers to XBTUSD/XETUSD nightly. Index-type underliers (BMAXATCL Index) still affected; structural fix in Phase 4.
- BUG-02: ~~TSII recycled-ticker false promotion~~ **MITIGATED 2026-05-19 (Phase 0b, ADR 0002)** — Phase 3 of sync_rex_products_from_filings.py now requires fund-name overlap before promotion. Nightly duplicate-ticker audit (`scripts/audit_duplicate_tickers.py`) surfaces any new cases in the morning email.
- BUG-03: ~~13+ T-REX 2X products Listed with placeholder inception~~ **MITIGATED 2026-05-19 (Phase 0b, ADR 0002)** — Phase 3 now rejects inception dates before the filing date OR older than 60 days. Existing bad rows still need a one-time manual correction; new cases blocked.
- BUG-04: ~~BMAX US Listed despite Bloomberg vanish~~ **DETECTED 2026-05-19 (Phase 0b, ADR 0002)** — new Phase 4 audit in sync_rex_products_from_filings.py flags vanished-from-Bloomberg tickers. Auto-demote opt-in via `data/.auto_demote_vanished` flag (off by default to avoid false positives from transient drop-outs).
- BUG-05: Flow report's REX 1W flow KPI ($10.8M) does not match the issuer-table REX row ($16.4M). Cause: KPI uses `is_rex=1` filter (82 funds); issuer table groups by `issuer_display="REX"` which includes 1 fund with `is_rex=0` but `issuer_display="REX"` (AXTU). Fix: Phase 6 — survivorship rules. Still open.
- BUG-06: ~~Old `rexfinhub-sec-scrape.service` killed by SIGTERM at step 3.5/8 on 2026-05-19 16:00 ET~~ **MITIGATED 2026-05-19 (ADR 0005)** — the old unit had a drop-in (`10-sync-rex-products.conf`) that ran `sync_rex_products_from_filings.py --apply` as `ExecStartPost`, racing against the same call inside `run_daily.main()` step 3.5/8. The new `intraday-refresh` unit has no such drop-in and uses `--skip-sec` to bypass the whole code path when fresh-poller has run recently. Old unit + drop-in remain in `/etc/systemd/system/` (disabled) for revert; can be removed once new unit has run cleanly for a week.
- BUG-07: ~~`temp/submissions.zip` corrupted (partial download)~~ **FIXED 2026-05-19** — `etp_tracker/bulk_loader.py::download_submissions_zip` now writes to `<dest>.partial` while streaming, verifies content-length match + that the file parses as a valid zip, then atomic-replaces. Failure paths leave `.partial` in place + raise. Recurrence blocked.

### known-gaps

- GAP-01: `AZURE_CLIENT_SECRET` expiry date unknown. If it lapses, BOTH email send AND Bloomberg pull fail silently. Action: check Azure Portal + set calendar reminder.
- GAP-02: No CSRF middleware on FastAPI admin routes. Combined with `SameSite=Lax` cookies, a malicious link could trigger state changes if Ryu is signed in.
- GAP-03: No IP allowlist on `/api/v1/db/upload`. Anyone with `API_KEY` can overwrite production DB.
- GAP-04: `rexfinhub-fresh-poller.timer` and `etp_tracker/atom_watcher.py` overlap. One should be retired.
- GAP-05: 09:00 morning classification sweep emails the same gap data shown in 18:30 preflight and 20:15 summary — redundant notification.
- GAP-06: Weekly trust universe sync redundant with [[fresh-poller]] for new-CIK discovery; only useful for metadata backfill (entity_type, regulatory_act).
- GAP-07: `/operations/products` 3-way merge between `rex_products` + `capm_products` + `mkt_master_data` is fragile. Merge `capm_products` → `rex_products` (Phase 3).
- GAP-08: 6 manual classification CSVs in `config/rules/` are the source of Ryu's "edit-CSV" pain. Replace with `classification_override` table (Phase 6).
- GAP-09: Status assignment is in-place string update on `rex_products.status`. No audit trail of when a product transitioned through Filed → Effective → Listed. Fix: bi-temporal `status_history` table (Phase 5).
- GAP-10: 124 funds (2.4% of active) have NULL `primary_strategy`. Masked by `.preflight_maintenance` flag → preflight WARN instead of FAIL.

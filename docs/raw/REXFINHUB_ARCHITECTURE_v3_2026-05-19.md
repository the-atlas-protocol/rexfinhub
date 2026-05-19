# REXFINHUB — Authoritative Architecture Reference

> **Version 3 — 2026-05-19** — Final synthesis of three deep audits (workflows, bugs, secrets) + Ryu's clarifications. This is the canonical document.

---

## Table of contents

0. Executive summary
1. System topology — where everything lives
2. The Microsoft Graph API backbone (the secret pipe that makes "no-touch" possible)
3. Complete data flow — from external sources to your inbox
4. Every workflow, with code references and necessity ratings
5. Secrets and credentials inventory (every key, every risk)
6. The four bugs — root causes + fixes (with BMAXATCL correction)
7. The capm_products / rex_products mess — merge plan
8. Classification redesign — eliminate the CSVs
9. Ryu's ideal workday — three touchpoints, no admin clicks
10. Best practices reference (what the world's best teams do)
11. Target architecture for v2 (the rebuild plan)
12. Phased roadmap — 8 phases, 8-11 weeks
13. What to kill (the brutal cuts list)
14. Open questions for Ryu — explicit decision points
15. Appendices

---

## 0. Executive summary

**What rexfinhub is today**: an automated SEC + Bloomberg + CBOE + 13F intelligence pipeline that powers a public webapp (rexfinhub.com on Render), serves daily/weekly email reports to internal and external lists, and tracks REX's competitive filing race. Built over ~14 months. ~21 workflows. ~10 systemd timers. SQLite production DB (~650 MB) on a single VPS.

**What's working right now**: Bloomberg file auto-pulls from SharePoint via Graph API. Classification, market sync, send pipeline, gate management — all autonomous as of yesterday's auto-GO deployment. You haven't clicked an admin button in a month and the system has been running.

**What's broken or fragile**:
- Four data-correctness bugs visible on `/operations/products` and `/operations/pipeline` (Bug 1-4 in §6)
- Two tables doing one job (`rex_products` + `capm_products`) creating constant inconsistency
- 6 manual classification CSVs that you don't want to edit
- ADMIN_PASSWORD publicly leaked on GitHub until 2026-05-05, never rotated
- 5 other API keys also unrotated since pre-April 24
- No CSRF on admin forms
- Same metric showing different values in different reports (the flow report $10.8M vs $16.4M discrepancy this morning)

**What the rebuild looks like**: canonical product ID + polymorphic underlier table + bi-temporal status history + one classification override table + survivorship rules between sources + daily ops as data-quality assertions. 8 phases over 8-11 weeks. Each phase ships independently.

**Your ideal day after this lands**:
1. Anytime 09:00-17:30: paste CBOE cookie on `/admin/cboe-cookie` page when shown stale (skill triggers auto-rescrape end-to-end)
2. Anytime 16:30-17:30: (no action — Bloomberg pulls itself via Graph API. You only check the morning email confirms it landed.)
3. When a new REX product is filed: enter target inception date inline on `/operations/pipeline`
4. 08:00 next morning: read the `[PIPELINE]` summary email — triage any red items by asking me

Zero CSV edits. Zero admin clicks. Zero classification work.

---

## 1. System topology

```
                    ┌──────────────────────────────────────┐
                    │  EXTERNAL SOURCES                    │
                    ├──────────────────────────────────────┤
                    │  SEC EDGAR (atom feed + bulk + idx)  │
                    │  M365 SharePoint (Bloomberg .xlsm)   │
                    │  CBOE issuer portal (session cookie) │
                    │  OpenFIGI (planned: identifier res.) │
                    │  Microsoft Graph (email send)        │
                    └──────────────────┬───────────────────┘
                                       │
                                       ▼
┌────────────────────────────────────────────────────────────────┐
│  PRODUCTION VPS (jarvis@46.224.126.196 / Ubuntu / 38 GB)       │
│  Source of truth for ALL writes.                               │
│                                                                │
│  Systemd timers (~10) orchestrate everything:                  │
│    03:00  CBOE scan                                            │
│    08:00/12:00/16:00/20:00  SEC scrape                         │
│    Every 15 min  fresh-poller (during market hours)            │
│    17:15 + 21:00  Bloomberg chain (Graph API pulls .xlsm)      │
│    18:30  Preflight (auto-GO writes decision)                  │
│    19:00  Gate auto-open                                       │
│    19:30  Daily pipeline + send                                │
│    20:00  Gate auto-close                                      │
│    23:00  DB backup                                            │
│    Feb/May/Aug/Nov 19-20  13F bulk + incremental               │
│                                                                │
│  Crontab (jarvis):                                             │
│    20:15 Mon-Fri  Pipeline summary email                       │
│                                                                │
│  Data:                                                         │
│    /home/jarvis/rexfinhub/data/etp_tracker.db    (~650 MB)     │
│    /home/jarvis/rexfinhub/data/holdings.db       (13F)         │
│    /home/jarvis/rexfinhub/data/DASHBOARD/        (BBG inputs)  │
│    /home/jarvis/rexfinhub/data/backups/          (14d rotation)│
│    /home/jarvis/rexfinhub/data/.preflight_*      (state files) │
│    /home/jarvis/rexfinhub/data/.pipeline_stages.jsonl          │
│    /home/jarvis/rexfinhub/config/.env            (secrets)     │
│    /home/jarvis/rexfinhub/cache/sec/             (HTTP cache)  │
│    /home/jarvis/rexfinhub/cache/submissions/     (SEC subs)    │
│    /home/jarvis/rexfinhub/reports/               (HTML output) │
└────────────────┬─────────────────────────────────┬─────────────┘
                 │                                 │
        push DB  │                                 │  on git push to main
         daily   │                                 │
                 ▼                                 ▼
       ┌──────────────────────┐         ┌──────────────────────┐
       │  RENDER WEBAPP       │         │  GITHUB              │
       │  rex-etp-tracker     │         │  the-atlas-protocol/ │
       │  (READ-ONLY DB)      │         │     rexfinhub        │
       │                      │         │                      │
       │  rexfinhub.com       │         │  Triggers Render     │
       │  /operations/...     │         │  auto-deploy + VPS   │
       │  /filings/symbols    │         │  git pull            │
       │  /intel/...          │         │                      │
       └──────────────────────┘         └──────────────────────┘

                 ▲
                 │  Daily 23:30 sync (laptop Task Scheduler — TO BUILD)
                 │
       ┌────────────────────────────────────────┐
       │  D DRIVE (Transcend external USB)      │
       │  D:\sec-data\                          │
       │    rexfinhub\db_backups\               │
       │    rexfinhub\screener_snapshots\       │
       │    cache\rexfinhub_archives\           │
       │    databases\structured_notes.db       │
       │                                        │
       │  Long-term archive of nightly backups, │
       │  cache snapshots. Not queried live.    │
       └────────────────────────────────────────┘

       ┌────────────────────────────────────────┐
       │  LOCAL (C:\Projects\rexfinhub)         │
       │  Dev only. Synced laptop↔desktop via   │
       │  Syncthing. NEVER authoritative for    │
       │  any data.                             │
       └────────────────────────────────────────┘
```

**Key takeaway**: VPS is the source of truth. Render is a read-only replica. D drive is the archive. Local is dev. The Bloomberg file is the only external input that mattered manually — and now it's not even manual (Graph API pulls it).

---

## 2. The Microsoft Graph API backbone

This is the thing you didn't fully realize is running. **Graph API is what makes your "no-touch" reality possible.** Two roles:

### 2.1 Bloomberg file delivery — fully automated

**File**: `webapp/services/graph_files.py::download_bloomberg_from_sharepoint()`

**Flow**:
1. Systemd timer `rexfinhub-bloomberg.timer` fires at 17:15 ET (and again at 21:00 ET as a catch-up)
2. Calls `rexfinhub-bloomberg-chain.service`, which invokes `download_bloomberg_from_sharepoint()` as its first step
3. The function:
   - Auths via MSAL client credentials flow (service principal, app-only)
   - Searches your M365 tenant for the "REX Financial" SharePoint site
   - Locates the drive containing `/Product Development/MasterFiles/MASTER Data/bloomberg_daily_file.xlsm`
   - Compares SharePoint's `lastModifiedDateTime` against VPS file mtime
   - If newer, downloads via `GET /v1.0/drives/{drive_id}/root:{path}:/content`
   - Validates size (must be > 1 MB and match content-length header)
   - Atomic rename from `.tmp` to final path
   - Archives snapshot to `data/DASHBOARD/history/`
4. Chain then runs: `apply_fund_master` → `apply_underlier_overrides` → `apply_issuer_brands` → `apply_classification_sweep --apply --apply-medium`
5. Daily pipeline at 19:30 uses the freshly-synced data

**Why Ryu hasn't touched Syncthing in a year**: he doesn't need to. The Bloomberg file is in SharePoint (where the spreadsheet team puts it), and rexfinhub fetches it directly. No laptop in the loop.

**Today's Bloomberg file mtime on VPS** (verified): 2026-05-19 00:58 EDT — the 21:00 ET previous-day pull, accounting for UTC. Working as designed.

### 2.2 Email send — via Graph, not SMTP

**File**: `webapp/services/graph_email.py` called by `etp_tracker/email_alerts.py::_send_html_digest()`

- Service principal auth (app-only) — token doesn't expire until the client secret does
- Sends as `relasmar@rexfin.com` via `POST /v1.0/users/{sender}/sendMail`
- Returns HTTP 202 on success
- SMTP fallback exists in code (Gmail app-password) but is **disabled** — Graph-only in production
- Tonight at 19:30 ET, the daily send will use Graph

### 2.3 The four Azure credentials

In `.env` on local + VPS + Render:
- `AZURE_TENANT_ID` — your M365 tenant UUID (immutable)
- `AZURE_CLIENT_ID` — the app registration ID (immutable)
- `AZURE_CLIENT_SECRET` — **client secret, has expiry** (default 1-6 months; unknown current expiry date)
- `AZURE_SENDER` — `relasmar@rexfin.com`

**Critical operational concern**: the client secret has an expiry. When it lapses, both Bloomberg pulls AND emails silently fail (no escalation). **Need to check expiry date in Azure Portal and set a calendar reminder before it expires.** This is the single highest-leverage operational risk in the system.

Recommended app registration permissions (verify in Azure Portal — Microsoft Graph, Application type):
- `Mail.Send` — for the email pipeline
- `Files.ReadWrite.All` + `Sites.ReadWrite.All` — for SharePoint Bloomberg pull
- Authority: `https://login.microsoftonline.com/{TENANT_ID}` (single-tenant)

---

## 3. Complete data flow

```
                     SEC EDGAR
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
   atom feed       4x/day scrape      bulk submissions.zip
   (every 60s)    (08/12/16/20 ET)      (Sun 07:00)
        │                 │                 │
        ▼                 ▼                 ▼
   single_filing    run_pipeline       bulk_loader
   resolve_or_      step2/3/4/5        sync_trust_universe
   create_trust                              │
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                 ┌──────────────────┐
                 │  filings         │
                 │  fund_extractions│
                 │  fund_status     │
                 │  name_history    │
                 │  trusts          │
                 └────────┬─────────┘
                          │
                          ▼
                  sync_rex_products_from_filings
                          │
                          ▼
                    rex_products (+ capm_products)
                          │
SharePoint Bloomberg.xlsm │
  (Graph API pull         │
   17:15 + 21:00 ET)      │
        │                 │
        ▼                 │
  market/ingest.py        │
        │                 │
        ▼                 │
  market/transform.py     │
   (12-step enrichment)   │
        │                 │
        ▼                 │
  market/auto_classify    │
   + apply_classification_sweep
        │                 │
        ▼                 │
   mkt_master_data ◄──────┤
   mkt_time_series        │
        │                 │
        │                 ▼
        │           classification_override (target — replaces CSVs)
        │                 │
        ▼                 │
   apply_fund_master      │
   apply_underlier_overrides
   apply_issuer_brands    │
        │                 │
        ▼                 ▼
   ┌─────────────────────────┐
   │  Unified data layer     │
   │  (read by surfaces)     │
   └────────────┬────────────┘
                │
   ┌────────────┼────────────────┐
   │            │                │
   ▼            ▼                ▼
/operations  prebake_reports  send_all.py
/products    (mkt_report_     (daily 19:30
/pipeline    cache)             via Graph)
/calendar          │
/filings/...       ▼
              email reports (8 bundles)

CBOE issuer portal ──→ (nightly 03:00) ──→ mkt_cboe_reserved_symbols ──→ /filings/symbols

SEC 13F-HR ──→ (quarterly Feb/May/Aug/Nov) ──→ holdings.db ──→ /intel/holdings
```

---

## 4. Every workflow with code reference and necessity rating

| # | Workflow | When | File | Necessity | Disposition |
|---|---|---|---|---|---|
| **Ingestion** | | | | | |
| 1 | Atom feed watcher | every 60s | `etp_tracker/atom_watcher.py` | CORE | Keep. This is how new CIKs are discovered. |
| 2 | SEC scrape (4×/day) | 08/12/16/20 ET | `etp_tracker/run_pipeline.py` + systemd `rexfinhub-sec-scrape.timer` | CORE | Keep. |
| 3 | Fresh poller (15 min) | market hours | `scripts/poll_fresh_filings.py` | OVERLAPS #2 | Merge — keep fresh-poller, retire 4×/day timer (or vice versa) |
| 4 | CBOE scan | 03:00 ET nightly | `scripts/run_cboe_scan.py` | CORE | Keep. |
| 5 | Trust universe bulk sync | Sun 07:00 | `scripts/sync_trust_universe.py` | **REDUNDANT** | **Kill**. Atom watcher already discovers new CIKs in 1-3 min. The weekly bulk scan only backfills metadata (entity_type, regulatory_act) for orphans — not worth weekly cost. If we want metadata enrichment, run quarterly. |
| 6 | Bloomberg chain (Graph API + classify + overlays) | 17:15 + 21:00 ET | `webapp/services/graph_files.py` → `market/ingest.py` → 4 ExecStartPost overlays | CORE | Keep but **consolidate** the 4 ExecStartPost into 1 script (no logic change). |
| 7 | 13F quarterly | Feb 19, May 20, Aug 19, Nov 19 @ 06:00 ET | `scripts/run_13f.py auto` + `rexfinhub-13f-quarterly.timer` | OPTIONAL | **Keep** (I was wrong to call it monthly). Quarterly is correct because 13F-HR is filed quarterly per SEC rule. `auto` mode includes a 7-day incremental scan for late filings. |
| **Classification** | | | | | |
| 8 | Auto-classify | daily | `market/auto_classify.py` | CORE | Keep. Writes primary_strategy, asset_class, sub_strategy, strategy, underlier_type. |
| 9 | CSV-rules overlay | daily inside Bloomberg chain | `market/rules.py` + 6 CSVs | **PROBLEMATIC** | Replace with single `classification_override` table (§8). |
| 10 | Bloomberg-chain post-steps | daily | `apply_fund_master.py`, `apply_underlier_overrides.py`, `apply_issuer_brands.py`, `apply_classification_sweep.py` | CORE but FAT | Consolidate to one script. |
| 11 | Morning classification sweep email | 09:00 ET weekdays | `scripts/classification_sweep.py --post-summary` | **REDUNDANT** | **Kill**. The same gap data shows up in the 18:30 preflight and the 20:15 pipeline summary. Three notifications for one signal is two too many. |
| **Database writes** | | | | | |
| 12 | `mkt_master_data` write | end of chain | `market/db_writer.py` | CORE | Keep. |
| 13 | `mkt_time_series` write | end of chain | same | CORE | Keep. |
| 14 | `rex_products` sync from filings | manual today, should be auto | `scripts/sync_rex_products_from_filings.py` | CORE but BUGGY | Fix Phase 3 (Bug 2 + Bug 4); add Phase 4 demotion (BMAXATCL case). |
| 15 | `capm_products` admin edits | manual via /admin | webapp + `capm_audit_log` | **REDUNDANT** | **Merge into `rex_products`** (§7). |
| **Surfaces** | | | | | |
| 16 | `/operations/products` | live | `webapp/routers/capm.py::_capm_index_impl` | CORE | Keep. After §7 merge, simpler. |
| 17 | `/operations/pipeline` | live | `webapp/routers/pipeline_calendar.py::_pipeline_products_impl` | CORE | Keep. **Add target_inception_date inline editing** so Ryu can input on new filings. |
| 18 | `/operations/calendar` | live | same | CORE | Keep. |
| 19 | Reports pre-bake | 17:25 ET | `scripts/prebake_reports.py` → `mkt_report_cache` | OPTIONAL | Keep for now; re-evaluate if Render performance allows on-demand. |
| **Send pipeline** | | | | | |
| 20 | Preflight + auto-GO | 18:30 ET | `scripts/preflight_check.py` (PR #16) | CORE | Keep. |
| 21 | Gate auto-open | 19:00 ET | systemd timer | CORE | Keep. |
| 22 | Daily pipeline + send | 19:30 ET | `scripts/run_daily.py` → `scripts/send_all.py --use-decision` | CORE | Keep. |
| 23 | Gate auto-close | 20:00 ET | systemd timer | CORE | Keep. |
| 24 | Pipeline summary | 20:15 ET | `scripts/pipeline_summary.py` (PR #17) crontab | CORE | Keep, **move to 08:00 ET** so it's the morning triage email (target architecture). |
| **Confirmations** | | | | | |
| 25 | DB backup | 23:00 ET | systemd timer | CORE | Keep + extend `sync_vps_to_d_drive.sh` to copy nightly. |
| 26 | Render webapp deploy | on push | Render auto | CORE | Keep. |
| 27 | DB upload to Render | end of daily | `scripts/run_daily.py` → `/api/v1/db/upload` | CORE | Keep. Add IP allowlist (only VPS IP). |

**Net change**: 27 workflows → 23 (kill #5, #11; merge #3 into #2, merge #15 into rex_products). Plus consolidations (#10 → 1 script).

---

## 5. Secrets and credentials inventory

| # | Name | Location | Purpose | Risk if leaked | Status |
|---|---|---|---|---|---|
| 1 | `AZURE_TENANT_ID` | .env (all hosts) | M365 tenant UUID (immutable) | LOW (UUID alone is useless) | OK |
| 2 | `AZURE_CLIENT_ID` | .env (all hosts) | App registration ID (immutable) | LOW (ID alone is useless) | OK |
| 3 | `AZURE_CLIENT_SECRET` | .env (all hosts) | Service principal for Graph API (Mail.Send + Files.ReadWrite.All + Sites.ReadWrite.All) | **CRITICAL** — enables email send + SharePoint pull | ⚠ **Expiry unknown — check Azure Portal** |
| 4 | `AZURE_SENDER` | .env (all hosts) | `relasmar@rexfin.com` | LOW | OK |
| 5 | `API_KEY` | .env (all hosts) | Auth for `/api/v1/db/upload` etc. | **CRITICAL** — overwrites production DB | ⚠ Unrotated since ≥2026-04-24 |
| 6 | `SESSION_SECRET` | .env (all hosts) | Session cookie signing | HIGH — session forgery → admin | ⚠ Unrotated since ≥2026-04-24 |
| 7 | `SITE_PASSWORD` | .env (all hosts) | Site-wide gate | HIGH | ⚠ Unrotated since ≥2026-04-24 |
| 8 | `ADMIN_PASSWORD` | .env (all hosts) | Admin login | **CRITICAL** | ⚠ **Was on GitHub until 2026-05-05; never rotated** |
| 9 | `ANTHROPIC_API_KEY` | .env (all hosts) | Claude API for filing analysis | HIGH (uncapped spend) | ⚠ Unrotated since ≥2026-04-24 |
| 10 | `SEC_USER_AGENT` | .env (all hosts) | SEC EDGAR identification | LOW | OK |
| 11 | `CBOE_SESSION_COOKIE` | .env (VPS only) | CBOE issuer portal session | MEDIUM (read-only CBOE) | OK (rotated monthly when stale) |
| 12 | `CBOE_CONCURRENCY` | .env (VPS only) | Operational tuning | n/a | OK |
| 13 | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` | .env (local + VPS, not Render) | SMTP fallback (dormant) | LOW-MED (dormant Gmail identity) | Consider removing if truly unused |
| 14 | `SMTP_PASSWORD` | .env (local + VPS, not Render) | Gmail app password | HIGH if password reuse | ⚠ Consider removing |
| 15 | `RENDER_UPLOAD_TOKEN` | .env (VPS only) | Possibly unused | MEDIUM if used | Investigate |
| 16 | `PIPELINE_SCHEDULE_HOUR` | .env (all) | Operational | n/a | OK |

### Five critical security items to address before anything else

1. **Rotate ADMIN_PASSWORD immediately**. Was public on GitHub until 2026-05-05. Assume compromised.
2. **Rotate API_KEY, SESSION_SECRET, SITE_PASSWORD, ANTHROPIC_API_KEY** as a batch (none rotated in >25 days).
3. **Check AZURE_CLIENT_SECRET expiry in Azure Portal** + set calendar reminder. If this expires, email + Bloomberg pull both die silently.
4. **Add IP allowlist to `/api/v1/db/upload`** — only VPS IP (46.224.126.196).
5. **Add CSRF middleware to FastAPI** + tighten cookie `SameSite=Strict` for admin paths.

None of these are architectural; all reversible. Should be Phase 0a (security hardening), before the architecture rebuild.

---

## 6. The four bugs — root causes + fixes (with BMAXATCL corrected)

### Bug 1: Bitcoin shows "0 competitors" on /operations/pipeline

**Where**: `webapp/routers/pipeline_calendar.py:852-874` — race-density aggregation does `UPPER()` + strip-suffix on `rex_products.underlier` AND `mkt_master_data.map_li_underlier/map_cc_underlier`, then dict-counts.

**Why it breaks**: REX product has `underlier="XBTUSD"`; competitor crypto-spot ETFs (IBIT, FBTC) have `map_li_underlier="Bitcoin"` or `"BTC"`. Different strings → different dict keys → counted as separate underliers.

**Same problem exists for**: any INDEX underlier. Including BMAXATCL Index (used as underlier for ATCL fund — see Bug 4 correction below).

**Quick fix (data-only, ~30 min)**: Normalize Bitcoin underliers across mkt_master_data to a single canonical symbol (whatever rex_products uses for crypto). UPDATE statement.

**Structural fix (Phase 4)**: polymorphic `underlier_master` table with `underlier_type` ENUM (equity / etp / index / crypto_pair / basket / commodity / fx / rate) + appropriate identifier per type (FIGI from OpenFIGI). All comparisons go through `underlier_id`, not strings.

### Bug 2: REX TSM Growth & Income (TSII) shows Listed but never traded

**Where**: `scripts/sync_rex_products_from_filings.py:539-548` — Phase 3 promotes rex_products from `status=Effective` → `status=Listed` by matching on ticker alone.

**Why it breaks**: SEC recycled the TSII ticker — first assigned to TSM Growth & Income (filed, never effective), then reassigned to TSLA Growth & Income (filed + effective). Ticker-only match in Phase 3 promoted the wrong row.

**Two layers — both ship together per your call**:

**Layer 1 — Fix the scraper**: Phase 3 adds fund-name cross-validation before promotion. Both `rex_products.name` and `mkt_master_data.fund_name` must token-overlap (drop boilerplate like "T-REX", "2X", "DAILY", "TARGET", "ETF") before the ticker match is accepted.

**Layer 2 — Independent duplicate-ticker audit**: nightly check runs:

```sql
SELECT ticker, COUNT(*), group_concat(name, ' | ') AS conflicting_names
FROM rex_products
WHERE ticker IS NOT NULL
GROUP BY ticker HAVING COUNT(*) > 1
```

Any duplicate ticker with conflicting names → flagged in the 08:00 morning email. You triage by asking me to investigate.

### Bug 3: 13 T-REX 2X products marked Listed with no real inception

**Tickers**: WLTU, SOUU, SPOU, FMCU, SRFU, STSU, UIU, BREU, DEFU, HIVU, HOLU, HSDU, NAKU, NXTU.

**Why it breaks**: Phase 3 accepts any parseable inception_date including bulk-seeded placeholders (multiple products with `inception_date=2026-02-18` simultaneously). With `mkt_master_data.market_status=ACTV` (which Bloomberg sets independent of actual trading), Phase 3 promotes to Listed.

**Quick fix**: inception_date must be **after** rex_products.initial_filing_date AND within the last 60 days. Otherwise leave status at `Effective` or `Target List`.

**Structural fix (Phase 5)**: status=Listed requires evidence from three sources — SEC effective + exchange Form 8-A + observed first trade print. Until all three exist, status stops at `Target List`.

### Bug 4: BMAXATCL — corrected per your feedback

**Original wrong diagnosis**: I said BMAXATCL was a delisted fund.

**Truth**: **BMAXATCL Index** is a Bloomberg INDEX, used as the underlier for the ATCL (Autocallable) fund. Not a fund itself.

**Separate issue you identified**: **BMAX US** was an actual fund that delisted. Bloomberg has completely removed the ticker from `mkt_master_data` (Bloomberg reclaimed it). rex_products still has BMAX as `status=Listed` because there's no signal from Bloomberg to demote it (the row vanished entirely instead of flipping to LIQU).

**Fix**: add a Phase 4 audit:

```sql
SELECT p.ticker, p.name, p.status
FROM rex_products p
WHERE p.status='Listed'
  AND p.ticker IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM mkt_master_data m
                  WHERE UPPER(m.ticker_clean) = UPPER(p.ticker))
```

Any rex_product marked Listed whose ticker is **completely absent** from mkt_master_data → flagged in morning email for confirmation (don't auto-demote — fund tickers occasionally drop out for a few hours before reappearing).

**Index-underlier handling**: BMAXATCL as an underlier needs polymorphic underlier support (Phase 4) — `underlier_type=index`, `index_provider=Bloomberg`, `index_code=BMAXATCL`. Same fix as Bug 1.

---

## 7. The capm_products / rex_products mess

You asked directly. Here's the truth:

| Table | Rows | What's in it | How it gets updated |
|---|---|---|---|
| `rex_products` | 552 | All REX-branded products across all statuses (Under Consideration → Filed → Effective → Target List → Listed → Delisted). Includes pre-launch filings. | SEC filing pipeline + admin edits |
| `capm_products` | 74 | Curated subset of rex_products that have additional fee/custodian/LMM/AP fields. | Admin panel inline edits + `capm_audit_log` for forensics |

**The redundancy**: capm_products columns (`fixed_fee`, `variable_fee`, `custodian`, `lmm`, `cu_size`, `cut_off`, `exchange`, `bb_ticker`, `category`, `direction`, `leverage`, `underlying_ticker`, `underlying_name`, `expense_ratio`, `competitor_products`, `bmo_suite`, `prospectus_link`, `notes`) should be columns on `rex_products`. capm_products only exists because it predates the unified rex_products table.

**Merge plan** (Phase 3 of the roadmap):
1. Add the ~18 capm columns to `rex_products` schema
2. For each of the 74 capm rows, match to rex_products by ticker AND name; copy the curated fields into rex_products
3. Repoint `/operations/products` to use only rex_products + mkt_master_data (no 3-way merge)
4. Repoint `/admin/rex-products` inline editor to write directly to rex_products
5. Migrate `capm_audit_log` schema to track edits on rex_products
6. Drop capm_products table

**Side effects**:
- `/operations/products` page logic simplifies dramatically (no 3-way merge with name-overlap fuzzy matching)
- "Same metric in different reports" inconsistencies become impossible — single source of truth for fee/custodian/LMM
- Admin panel becomes one form per rex_product

---

## 8. Classification redesign — kill the CSVs

You explicitly said: "I haven't touched anything on admin in a month." But the 6 rule CSVs in `config/rules/` are the part you'd touch if you wanted to override the classifier. They're the source of your pain.

### Today

| CSV | Purpose | Edit cost |
|---|---|---|
| `fund_mapping.csv` | ticker → etp_category override | Edit text file, save, wait for daily run |
| `issuer_mapping.csv` | (category, issuer) → display name | Same |
| `category_attributes.csv` | 20+ enrichment columns per fund | Same |
| `rex_funds.csv` | is_rex flag list | Same |
| `market_status.csv` | ACTV / INACTIVE / RESTRICTED override | Same |
| `exclusions.csv` | fund blacklist | Same |

### Target

**Single table**: `classification_override`

```sql
CREATE TABLE classification_override (
  product_id      UUID NOT NULL,    -- FK → product_master.canonical_id (Phase 4)
  field_name      TEXT NOT NULL,    -- 'etp_category', 'issuer_display', 'is_rex', etc.
  value           TEXT,             -- NULL = blacklist this field
  set_by          TEXT,             -- 'auto_classifier' | 'admin' | 'sec_filing' | 'manual'
  set_at          TIMESTAMPTZ DEFAULT NOW(),
  reason          TEXT,
  PRIMARY KEY (product_id, field_name)
);
```

**Resolution logic** (in `market/derive.py`):
```
final_value = (
  classification_override(product, field)  # explicit override wins
  ?? bloomberg_value                       # else trust Bloomberg
  ?? auto_classify(product, field)         # else derived heuristic
  ?? NULL
)
```

**Edit mechanism**: admin UI shows the current value + "Override" button. Click → form pre-populated → save writes one row to `classification_override`. Visible in audit log. Persists across daily runs.

**Migration**: walk each CSV, generate INSERT statements for the override table, drop the CSVs.

---

## 9. Ryu's ideal workday

Per your clarification — three touchpoints, all self-service via webapp:

### Touchpoint 1: CBOE cookie (anytime 09:00–17:30, when shown stale)

**Page**: `https://rexfinhub.com/admin/cboe-cookie`

**What it shows**:
- ✓ Last scan: 03:00 ET today, 475,148 tickers
- Cookie age: 23 days
- Banner only when stale: "CBOE session expired — paste fresh cookie below"

**What you do**: paste 32-char token, click "Update + Rescan".

**What happens behind the scenes**:
1. Web form POSTs to `/api/v1/cboe/rotate-cookie` on Render
2. Render endpoint forwards to VPS via authenticated webhook
3. VPS writes new cookie to `/home/jarvis/rexfinhub/config/.env`
4. VPS triggers `live_check(AAPL)` to verify auth
5. VPS kicks off full recovery sweep (~45 min) in background
6. VPS pushes in-progress DB to Render so banner flips from red ("expired") → blue ("refreshing — N tickers processed") within seconds
7. Page returns with success message + ETA

You spent 30 seconds. Done.

### Touchpoint 2: Bloomberg file (no action — runs itself)

Graph API pulls it from SharePoint at 17:15 + 21:00 ET. If your team updates the SharePoint file by 17:00, VPS has it by 17:15. You don't do anything.

**If your team's process changes** (Bloomberg file moves to a different drive/path), the config in `webapp/services/graph_files.py` needs updating. One-time engineering task, not daily.

### Touchpoint 3: New REX product target inception (when a filing happens)

**Page**: `/operations/pipeline`

When a new product appears as `Under Consideration` or `Target List`, you click the inline `target_inception_date` cell and input the date. Auto-saves.

**What happens after**:
- On the actual inception day, Bloomberg market_status flips to ACTV, first trade observed, status auto-promotes to Listed (3-source rule)
- Your input becomes the expected_inception for the lifecycle tracker
- If the actual launch slips past your target, that's logged as a delay event — useful for monitoring filing→launch lead times

### Morning routine: 08:00 ET email

`[PIPELINE]` summary lands in your inbox. Format:

```
OVERALL: PASS  (1 warn, 0 fail)

✓  1. Bloomberg sync (17:15 ET)        ok      File mtime 17:14
✓  2. Classification sweep             ok      0 unclassified
✓  3. Preflight audit (18:30 ET)       pass    8 audits run; auto-GO written
✓  4. GO/HOLD decision                 GO      auto-GO; warn audits OK with .autogo_on_warn flag
✓  5. Gate transitions                 ok      open=1, close=1
✓  6. Email sends                      sent    daily_filing@19:32

DATA QUALITY (25 assertions)
 ✓  24 passed
 ⚠   1 warn: duplicate ticker detected — TSII has 2 rex_products rows

ACTION ITEMS
 ⚠  TSII: 2 rex_products with different names (TSM vs TSLA). Ask Claude to investigate.
```

Your job: scan, triage the 1 warn, ask me to investigate. ~2 minutes.

---

## 10. Best practices reference

(See v1/v2 for full citations.)

**Canonical product ID + identifier crosswalk** — Intrinio's published architecture, BlackRock Aladdin Next-Gen Security Master. Synthetic UUID per product; ticker/CUSIP/ISIN/FIGI/CIK/series-ID/class-ID in a side table with valid_from/valid_to.

**Polymorphic underlier** — OpenFIGI free API supports stocks/ETPs/indices/crypto; Bloomberg+Kaiko issued first crypto FIGIs in 2023. Standard schema includes underlier_type enum + appropriate identifier per type.

**Bi-temporal status history (SCD Type-2)** — CRSP delisting code scheme; standard MDM pattern. Every status change is a new row with valid_from/valid_to + tx_from/tx_to. Current status is a view, never an in-place update.

**Survivorship rules** — Arcesium pattern. For each field, declare which sources win in which order. Deterministic, auditable.

**Daily ops = data-quality assertions** — dbt-expectations + Great Expectations. The morning email is "X passed / Y failed" not "did you do your checklist?"

**edgartools** (MIT-licensed Python) — typed EDGAR API, handles N-CEN/N-PORT/13F natively. Replaces ad-hoc scrapers.

**6-state lifecycle enum**: `filed` → `effective` → `trading` → `suspended` → `delisted` → `liquidated`. (CRSP uses 3-digit codes; overkill for our scale.)

---

## 11. Target architecture for v2

```
RAW (immutable append-only)
  sec_filings_raw       (every filing observed)
  bloomberg_daily_raw   (every BBG file with mtime)
  cboe_symbols_raw      (nightly snapshots)
  exchange_listings_raw (Form 8-A, Form 25, listing notices)
  thirteen_f_raw        (quarterly bulks)
        │ (data-quality assertions: ~25 dbt tests)
        ▼
CORE MASTER (bi-temporal, SCD-2)
  product_master            ← canonical_id (UUID) PK
  identifier_xref           ← FIGI/CUSIP/ISIN/ticker/CIK/series-id/class-id
  issuer_dim
  underlier_master          ← polymorphic (equity/etp/index/crypto_pair/basket/commodity/fx/rate)
  fund_underlier            ← M:N with weights + temporal validity
  classification            ← REX taxonomy
  classification_override   ← replaces all 6 CSVs
  listing_events            ← Form 8-A, Form 25, exchange listing notices
  status_history            ← bi-temporal lifecycle (SCD-2)
  filings                   ← typed (edgartools), FK to product_master
        │ (survivorship rules: deterministic source priority per field)
        ▼
SERVING
  /operations/products
  /operations/pipeline (inline target_inception_date editor for new filings)
  /operations/calendar
  /admin/cboe-cookie    (self-service rotation)
  /filings/symbols
  /intel/holdings
  Daily reports (8 bundles)
  Public API
```

---

## 12. Phased roadmap

### Phase 0a — Security hardening (2-3 days, MUST go first)

- Rotate ADMIN_PASSWORD, API_KEY, SESSION_SECRET, SITE_PASSWORD, ANTHROPIC_API_KEY (5 keys as a batch)
- Check AZURE_CLIENT_SECRET expiry in Azure Portal; set calendar reminder
- Add IP allowlist to `/api/v1/db/upload` (VPS IP only)
- Add CSRF middleware to FastAPI; SameSite=Strict for admin cookies

### Phase 0b — Triage patches (this week, 2-3 hours)

- Bug 1: canonicalize Bitcoin underliers in mkt_master_data (data fix)
- Bug 2: scraper fund-name validation + nightly duplicate-ticker audit
- Bug 3: inception-date sanity (must be after filing date, within last 60d)
- Bug 4: "fund vanished from Bloomberg" audit (BMAX case)

### Phase 1 — Cuts (1 week, pure deletions)

- Kill morning classification sweep (09:00 timer)
- Kill weekly trust universe sync (atom watcher covers it)
- Merge fresh-poller + 4×/day SEC scrape → single fresh-poller during market hours + daily fallback
- Consolidate 4 Bloomberg-chain post-steps → 1 script
- Extend `sync_vps_to_d_drive.sh` to copy nightly backups
- Schedule that sync from laptop Task Scheduler at 23:30 ET

### Phase 2 — Admin pages (1 week)

- `/admin/cboe-cookie` — paste + auto-rescan
- `/admin/pipeline` — inline `target_inception_date` editing on new filings

### Phase 3 — Merge `capm_products` → `rex_products` (1 week)

- Add 18 columns to rex_products schema
- Migrate 74 capm rows
- Repoint /operations/products to unified table
- Drop capm_products

### Phase 4 — Underlier master + canonical product ID (2-3 weeks)

- Build `underlier_master` polymorphic table
- Build `product_master.canonical_id` UUID
- Backfill from existing data
- Update race-density computation to use underlier_id
- Use OpenFIGI for crypto/index resolution

### Phase 5 — Lifecycle event table (2 weeks)

- Build `status_history` SCD-2 table
- 3-source rule for `Listed` promotion
- Auto-detect "fund vanished from Bloomberg" case

### Phase 6 — Classification override table + data-quality assertions (1-2 weeks)

- Replace 6 CSVs with classification_override table
- Write ~25 dbt-style data-quality assertions
- Wire failures into morning summary email
- Move pipeline summary from 20:15 → 08:00 ET

### Phase 7 — edgartools migration + cleanup (1 week)

- Drop in `edgartools`
- Retire ad-hoc SEC scraper
- Decommission `manually_edited_fields` JSON

**Total**: 11-14 weeks. Phases 0a + 0b deliver immediate wins in days. Phase 1-2 (2 weeks) gives meaningful UX improvements. Phases 3-7 are the structural rebuild.

---

## 13. What to kill — the brutal cuts

| Item | Why | Phase |
|---|---|---|
| `capm_products` table | Merge into rex_products | 3 |
| `manually_edited_fields` JSON column | Replaced by classification_override table | 6 |
| Morning classification sweep timer | Redundant with preflight | 1 |
| Weekly trust universe sync | Atom watcher already discovers new CIKs | 1 |
| 6 rule CSVs | One classification_override table | 6 |
| 4 separate Bloomberg-chain post-steps | One consolidated script | 1 |
| `mkt_report_cache` pre-bake | If Render performance allows on-demand | re-evaluate after Phase 4 |
| 5 separate state files (.preflight_token, .preflight_decision.json, .preflight_result.json, .send_enabled, .pipeline_stages.jsonl, .gate_state_log.jsonl) | Consolidate to 2-3 | Phase 6 |
| Windows Task Scheduler jobs mirroring VPS timers | VPS is truth — kill local schedulers | Phase 1 |
| `config/email_recipients.txt` text-file fallback | DB is truth | Phase 1 |
| SMTP fallback in code | Graph-only in production | Phase 1 |
| Empty `override.conf` drop-ins | Cosmetic | Phase 1 |

---

## 14. Open questions — your explicit decisions

These are the calls I need from you to proceed:

1. **Security hardening (Phase 0a) — do it now?** Five keys to rotate + Azure secret expiry check. Should be done before everything else. Reply "rotate now" to start.

2. **Trust universe sync — kill it?** I recommend killing (atom watcher covers it). Reply "kill" or "keep monthly metadata backfill."

3. **CBOE cookie workflow — webpage as I described?** Self-service `/admin/cboe-cookie` page. Reply "yes web page" to make it Phase 2 priority.

4. **`capm_products` merge — proceed?** Fold 18 columns into rex_products, drop the second table. Reply "yes merge."

5. **Phase order — start with security (0a) → triage patches (0b) → cuts (1) → admin pages (2)?** Or different order?

6. **Bloomberg file path on SharePoint — confirm path is correct?** `/Product Development/MasterFiles/MASTER Data/bloomberg_daily_file.xlsm`. If team is moving it, let me know.

7. **`/admin/bloomberg-upload` web page** — do we ever need a manual upload override (for when Graph API fails)? If yes, build as Phase 2. If "Graph API is fine, don't add manual fallback," skip.

Once you call these, I queue Phase 0a immediately.

---

## 15. Appendices

### A. Repository structure

```
C:\Projects\rexfinhub\
├── etp_tracker/          # SEC pipeline core
│   ├── run_pipeline.py
│   ├── trusts.py
│   ├── atom_watcher.py
│   ├── email_alerts.py
│   └── step2/3/4/5
├── market/               # Bloomberg + classification
│   ├── ingest.py
│   ├── transform.py
│   ├── auto_classify.py
│   ├── db_writer.py
│   └── rules.py
├── webapp/               # FastAPI app
│   ├── routers/
│   │   ├── operations.py
│   │   ├── capm.py       (TO MERGE)
│   │   ├── pipeline_calendar.py
│   │   ├── admin*.py
│   │   └── api.py
│   ├── services/
│   │   ├── graph_email.py
│   │   ├── graph_files.py
│   │   ├── cboe/
│   │   └── recipients.py
│   ├── models.py
│   └── auth.py
├── screener/             # L&I report engine
│   └── li_engine/
├── scripts/              # CLI utilities
│   ├── run_daily.py
│   ├── send_all.py
│   ├── send_email.py
│   ├── preflight_check.py
│   ├── pipeline_summary.py
│   ├── sync_rex_products_from_filings.py
│   ├── run_13f.py
│   ├── apply_classification_sweep.py
│   ├── apply_fund_master.py
│   ├── apply_underlier_overrides.py
│   ├── apply_issuer_brands.py
│   ├── poll_fresh_filings.py
│   └── pipeline_summary.py
├── deploy/systemd/       # VPS timer + service units
├── config/
│   ├── .env              # secrets
│   └── rules/            # 6 CSVs (to be eliminated)
└── data/                 # local DB (dev only)
```

### B. VPS systemd timers (verified active as of 2026-05-19)

```
rexfinhub-cboe.timer                  03:00 daily
rexfinhub-reconciler.timer            08:00 weekdays
rexfinhub-sec-scrape.timer            08:00/12:00/16:00/20:00 weekdays
rexfinhub-fresh-poller.timer          every 15 min market hours
rexfinhub-classification-sweep.timer  09:00 weekdays  (TO KILL)
rexfinhub-bloomberg.timer             17:15 + 21:00 weekdays
rexfinhub-preflight.timer             18:30 weekdays
rexfinhub-gate-open.timer             19:00 weekdays
rexfinhub-daily.timer                 19:30 weekdays
rexfinhub-gate-close.timer            20:00 weekdays
rexfinhub-db-backup.timer             23:00 daily
rexfinhub-13f-quarterly.timer         Feb 19, May 20, Aug 19, Nov 19 @ 06:00
rexfinhub-parquet-rebuild.timer       Fri 06:00
rexfinhub-bulk-sync.timer             Sun 07:00  (TO KILL or reduce to quarterly)
```

### C. Jarvis crontab (verified)

```
15 20 * * 1-5 /home/jarvis/venv/bin/python /home/jarvis/rexfinhub/scripts/pipeline_summary.py > /tmp/pipeline_summary.log 2>&1
```

### D. Override flag files (in /home/jarvis/rexfinhub/data/)

```
.autogo_on_warn          - present (enables auto-GO on WARN preflight)
.preflight_maintenance   - present (downgrades classification FAIL to WARN)
.send_paused             - ABSENT (presence would disable auto-GO entirely)
.summary_paused          - ABSENT (presence would suppress 20:15 summary)
```

### E. Key sources cited (full URLs in v2)

- Intrinio — Modern Security Master Architecture
- BlackRock Engineering — Domain-Driven Asset Management
- Arcesium — Security Master blog series
- CRSP — Delisting Codes documentation
- OpenFIGI API
- edgartools (GitHub)
- Cboe BZX ETP Listings Compliance Guide
- Nasdaq ETP Listing Guide
- dbt-expectations + Great Expectations

---

**End of v3 — ~8,000 words. The authoritative reference.**

Mark this as your design doc. Every Phase 0+ change references back here.

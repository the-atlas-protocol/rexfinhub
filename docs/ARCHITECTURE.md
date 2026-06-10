---
doc: architecture
status: canonical
updated: 2026-06-09
---

# REXFINHUB — MASTER SYSTEM ARCHITECTURE

> **The master file.** Everything the system is, where every piece lives, who owns every
> table, what runs when, and how data becomes a report. Verified against production
> 2026-06-09 (73-agent audit + live recon + same-day fixes — `docs/audit_2026-06-09/`).
> Routing: [`INDEX.md`](INDEX.md) · terms: [`GLOSSARY.md`](GLOSSARY.md) · design intent:
> [`DECISIONS/0011-engine-architecture.md`](DECISIONS/0011-engine-architecture.md) ·
> taxonomy: [`CLASSIFICATION.md`](CLASSIFICATION.md) · ops: [`RUNBOOK.md`](RUNBOOK.md)

---

## 0. What this system is

rexfinhub tracks the **ETP product landscape for REX Financial's L&I business**:
every SEC prospectus filing (who is filing what, when it becomes effective), every
live ETP's market data (AUM, flows, status, classification into REX's five proprietary
categories), and turns both into **decision products** — the T-REX launch pipeline,
whitespace/launch-race analysis, daily/weekly email reports, and the rexfinhub.com
web surface. One operator (Ryu), fully automated by design; humans handle only
genuinely ambiguous judgments.

```
                    ┌─────────────  SOURCES  ─────────────┐
                    SEC EDGAR          Bloomberg (SharePoint)   CBOE portal*
                        │                     │                     │
   ┌────────────────────▼─────────────────────▼─────────────────────▼──────┐
   │                      VPS  jarvis@46.224.126.196                       │
   │   etp_tracker/ scraper      market/ pipeline       cboe scanner       │
   │        │                        │                  (*dead: WAF)       │
   │        ▼                        ▼                                     │
   │   data/etp_tracker.db  ◄── single SQLite source of truth (~790MB)     │
   │        │                                                              │
   │   classify engine ── reconcilers ── assertions ── report bakers       │
   │        │                                              │               │
   │        ▼                                              ▼               │
   │   email sends (gated) ──────────────► uploads ──► Render replica      │
   └───────────────────────────────────────────────────────────────────────┘
                                                          │
                                              rexfinhub.com (read-only public)
   Local desktop/laptop (Syncthing): dev only, never authoritative.
   D:\sec-data: archive of backups/caches. Never queried live.
```

---

## 1. Environments & storage layout

| Environment | Path / host | Role | Authoritative for |
|---|---|---|---|
| **VPS** (Hetzner) | `jarvis@46.224.126.196:/home/jarvis/rexfinhub` | Production. All scheduled work runs here. | The database, the rules CSVs (`data/rules/`), runtime state |
| **Render** | rexfinhub.com (auto-deploys `main`) | Public read-only replica | Nothing — receives uploads |
| **GitHub** | `the-atlas-protocol/rexfinhub` (`main`) | Code truth. VPS deploys ONLY via `git pull` (ADR 0011 E4; the scp era ended 2026-06-09) | All code + `config/rules` mirror + docs |
| **Local** | `C:\Projects\rexfinhub` (+ `.claude/worktrees/*`) | Dev only; Syncthing desktop↔laptop | Nothing |
| **D: archive** | `D:\sec-data\{backups,databases,archives,cache,rexfinhub}` | Cold archive (nightly pulls when laptop online) | Nothing live |

**VPS repo top level:** `etp_tracker/` `market/` `screener/` `webapp/` `scripts/`
`tools/` `deploy/` `config/` `docs/` `tests/` + runtime dirs `data/` `logs/` `outputs/`
`reports/` `cache/` (runtime dirs are gitignored; the daily Bloomberg xlsm was untracked
2026-06-09 — data, not code).

**Databases (all SQLite, all on VPS):**

| File | Size | Contents | Replicated? |
|---|---|---|---|
| `data/etp_tracker.db` | ~790MB | EVERYTHING (≈60 tables, §3) | → Render 4-hourly upload |
| `data/live_feed.db` | small | realtime filing feed | separate, survives DB swaps |
| `data/13f_holdings.db` | — | 13F holdings (Q4'25 only; Q1'26 never ingested — accepted loss, Ryu 6/9) | partial |
| `data/backups/etp_tracker_YYYYMMDD.db` | ×7 | nightly `.backup` + integrity-check | → D: when laptop online |

---

## 2. The daily clock (every scheduled thing, verified live 2026-06-09)

All times ET. **systemd timers** (units in `deploy/systemd/`, installed to
`/etc/systemd/system`; every failure-prone unit now has
`OnFailure=rexfinhub-alert@%n.service` → critical-alert email):

| Time | Unit | What it does |
|---|---|---|
| every 15min (Mon-Fri 08:00-20:45) | fresh-poller | `poll_fresh_filings.py` — daily-index preflight scrape + `sync_rex_products --apply` (promote-only) |
| 60s daemon | atom-watcher | Tier-1 realtime: SEC atom feed → `filing_alerts` → live push to Render |
| 30s daemon | single-filing-worker | Tier-2: enriches alerts → `filings`/`fund_extractions`/`fund_status` |
| 03:00 | cboe | full symbol sweep — **dead since 5/13 (Cloudflare WAF blocks the VPS IP; cookie rotation cannot fix it; accepted for now, Ryu 6/9)** |
| 04:00/08:00 | reconciler | `etp_tracker.reconciler` — Tier-3 daily-index diff safety net + PEND→ACTV |
| 08:00 | morning-triage | 28 assertions (`run_assertions.py`) → triage email **always sends** (`ExecStart=-`); staleness guard flips subject if assertions crashed |
| 09:00 | classification-sweep | `classify_daily.py --apply` (autonomous classification engine, §6) → `classification_sweep.py` report |
| every 4h | sec-scrape | `run_all_pipelines.py` 12-step batch: scrape→sync→bake→upload-to-Render (+ post-step `sync_rex_products --apply`) |
| 17:15 | bloomberg(-chain) | SharePoint xlsm pull → `sync_market_data` → 8 post-steps incl. `status_reconciler --apply`, `ensure_canonical_identity`, `refresh_effective_dates` |
| 20:00 | gate-close | `send_enabled=False` (nightly lockdown) |
| 23:00 | db-backup | prune→`.backup`→`PRAGMA integrity_check` (order fixed 6/9) |
| Fri 06:00 | parquet-rebuild | analysis parquets (whitespace_v4, filing_race, …) |
| Sun 23:00 | grade-recommendations | L&I recommendation grading |
| quarterly | 13f-quarterly | currently a no-op backfill (13F deprioritized) |
| disabled | gate-open, preflight, intraday-refresh, daily, bulk-sync | present but untimered (send-resume machinery) |

**Crontab** (cleaned 6/9): `audit_duplicate_tickers` 02:35 · `li_engine run_v1` 22:30 wd
· `weekly_file_launch` Mon 07:00 · `weekly_system_report` Mon 07:05 · disk-hygiene
(`*_pre_*.db` >24h; nightly keep-last-10). Dead `pipeline_summary` + duplicate
`grade_recommendations` entries disabled 6/9.

---

## 3. Data architecture — table ownership (ADR 0011 E2: one writer per table)

| Table (group) | Sole writer | Readers | Notes |
|---|---|---|---|
| `trusts`, `filings`, `fund_extractions`, `fund_status`, `name_history` | scraper sync (`sync_service.py` + single-filing-worker) | everything | fund_status: 214,922 rows, 99.7% ETF-trusts (audit refuted the "184K mutual-fund pollution" claim) |
| `rex_products` (.status/.status_cached) | **status_reconciler** (corrections) + `sync_rex_products` (promote-only evidence) | reports, /operations | The 6/9 effectiveness model: Filed→Delayed(485BXT)→Effective(BPOS date≤today)→Listed(own ticker ACTV) |
| `status_history` (bi-temporal) | status_reconciler only | assertions, audit | dual-written with status_cached; drift assertion green |
| `product_master`, `identifier_xref` | canonical-identity step | reconciler | canonical_id coverage: 100% |
| `mkt_*` (master_data, time_series, rex_funds…) | market pipeline (`db_writer.py`, full refresh) | screener, reports, KPIs | from Bloomberg xlsm + rules CSVs |
| `system_flags` | `system_flags.set_flag` (set_by mandatory) | gates, preflight | THE gate store; dotfile layer retired for cleared flags; age-nag assertion live |
| `filing_alerts` | atom-watcher/reconciler | live feed, enricher | two-tier realtime design |
| `assertion_run` | run_assertions | triage email | 28 assertions |
| `ClassificationProposal` | classify_daily (queue tier) | review UI | now small = meaningful |
| `cboe_*` | CBOE scanner | /tools/tickers | data frozen 5/13 (WAF) |
| `capm_audit_log` | rule/product writers | audit | every rex_products UPDATE |

**Rules CSVs** (classification truth): `data/rules/` = runtime master on VPS,
`config/rules/` = git mirror — `classify_daily` mirrors after every apply (3-way drift
ended 6/9). Files: `fund_mapping.csv` (ticker→category), `attributes_{LI,CC,Crypto,Defined,Thematic}.csv`,
`issuer_mapping.csv` (+brand overrides), `exclusions.csv` (pair-undo vs full-ticker-Other),
`rex_funds.csv` (REX universe → `is_rex`).

---

## 4. The SEC filing pipeline (etp_tracker/)

Four detection tiers feeding one DB:

1. **Tier 1 — atom-watcher (60s):** SEC getcurrent atom feed, client-side form filter
   (485*/497*/N-1A/N-2) → `filing_alerts` + fire-and-forget Render live push.
2. **Tier 2 — single-filing-worker (30s):** enriches each alert: resolves/creates Trust,
   step3-class extraction, writes `filings` + `fund_extractions` + placeholder `fund_status`.
3. **Tier 3 — reconciler (daily):** previous-day `form.idx` diff vs alerts (safety net;
   938 catches to date) + REX lifecycle steps.
4. **Tier 4 — bulk_sync (weekly):** `company.idx` discovery → stub trusts for review.

**Batch path** (`run_pipeline.py`, in the 4-hourly unit): per-trust manifest-gated
step2 (submissions→CSVs) → step3 (strategy-routed extraction: header-only for
485BXT/497J, s1_metadata, full-body; effective-date confidence ladder HIGH/MEDIUM/HEADER/IXBRL;
ticker ladder SGML>TITLE-PAREN>LABEL-WINDOW) → step4 (rollup + status:
**485BPOS→EFFECTIVE only if date≤today, 485BXT→DELAYED, 485APOS→PENDING** — no more
auto-promotes, 6/9) → step5 (name history) → `sync_service` CSV→DB.

**Form semantics** (the 6/9 effectiveness overhaul, GLOSSARY has the full vocabulary):
registration-effective (1933 Act/Rule 485) ≠ listed/trading (8-A12B + ticker + first
trade). 485BXT *delays* a pending amendment to its stated date. Tickers appear at
listing; missing-ticker-on-pending is expected, not a defect.

**SEC etiquette:** sync client per-request pause + retry; async prewarm capped 8 req/s;
6h submissions TTL + If-Modified-Since; 5GB LRU web cache.

---

## 5. The market pipeline (market/ + Bloomberg)

17:15 chain: Graph API pulls the Bloomberg xlsm from SharePoint (hard-fail design —
no stale fallback) → `build_master_data` → 12-step transform (dedup guard; exclusions;
rules application; `is_rex` from `rex_funds.csv`; suite mapping) → full-refresh `mkt_*`
tables → 8 post-steps (canonical identity, status_reconciler --apply, effective-date
refresh, classification sweeps, chart data). MicroSectors AUM overlay reads the
`microsector_aum`/`microsector_sh` sheets (sheet-rename fix 6/8).

Bloomberg semantics: `market_status` ACTV/PEND/LIQU/DLST (PEND = competitor
launch-timing signal); inception_date = real first-trade date (99% populated).

---

## 6. The autonomous classification engine (built 2026-06-09, rebuilt to FULL SCALE 2026-06-10)

Contract: [`CLASSIFICATION.md`](CLASSIFICATION.md). TWO layers, ONE decision path:
the FULL-SCALE 3-axis taxonomy (asset_class x primary_strategy x sub_strategy +
~20 attributes; master = `config/rules/fund_master.csv`, full universe; DB home =
23 columns on mkt_master_data restamped by `apply_fund_master.py` after every
sync) and the LEGACY 5-category projection (etp_category + map_* — still what
all money pages/reports read, by design until consumer-by-consumer cutover).
The engine feeds BOTH from each decision and runs INSIDE the Bloomberg chain
(17:15/21:00, right after data lands — Ryu 2026-06-10) with 09:00 as catch-up +
report. The day's rules delta auto-commits to git (`commit_rules_delta.py`).

`scripts/classify_daily.py --apply` (09:00 daily, before the sweep report):

| Tier | Decider | Action |
|---|---|---|
| 0 | Standing rulings (Ryu, in code) | e.g. OBTC → excluded, never re-asked |
| 1 | Rule engine (deterministic Bloomberg fields) | HIGH candidates auto-applied |
| 2 | LLM (Haiku, cached taxonomy prompt) **+ independent critic pass** | AGREE+confident → auto-apply / exclude-Other |
| 3 | Queue (`ClassificationProposal`) | critic-DISAGREE or LOW only |

Plus gap-fillers: issuer_display registration (deterministic) and CC-attribute
derivation (the autocall tool's `attributes_CC.csv`). Newest-first ordering so the
daily limit always covers what the gap audit nags about. Every decision journaled to
`logs/auto_classify_YYYYMMDD.jsonl`. Day-one results: 64 gaps → 9 (true ambiguity);
~100 funds classified, ~40 excluded, 15 issuer fixes, 7 CC-attr rows; mirror clean.
Self-healing: newly-classified CC funds get attrs on the next run post-restamp.

---

## 7. The send pipeline + gates

**Reports inventory** (builders → recipient list_type; all sends via Graph API):

| Report | Builder | Cadence |
|---|---|---|
| Daily ETF digest | `etp_tracker/email_alerts.py` | daily (gated) |
| Weekly digest | `weekly_digest.py` | weekly |
| L&I report | `report_emails.py` (`li`) | weekly |
| Income / Flow / Autocall | `report_emails.py` | weekly |
| **T-REX Combined** (stock_recs slot) | `trex_combined_v9.build()` — rebuilt fresh at send | Mon |
| Blue Ocean overnight | `blue_ocean_report.main()` | Mon |
| Portfolio suite | `portfolio_suite_flow.py` | weekly |
| Morning triage / sweep summary | assertions + sweep | daily (bypass gate via `send_critical_alert`) |

**Gate architecture (single store):** `system_flags` rows — `send_enabled`
(THE gate; currently **False** = nothing sends; gate-close timer re-locks nightly at
20:00), `send_paused` (auto-GO override; cleared 6/9), `preflight_maintenance`
(strict-gating suppressor; cleared 6/9 after the backlog burn), `autogo_on_warn`.
Every flag carries set_by/set_at; the `restrictive_flag_age` assertion nags forgotten
flags. The dotfile layer is retired (`set_flag` cleans both stores). Recipients live
in the DB (`EmailRecipient` by list_type; unknown list_type must hard-error —
remaining slice item). **Sending to external recipients always requires Ryu's explicit
go** — the one permanent human gate.

---

## 8. The webapp (rexfinhub.com)

FastAPI app factory (`webapp/main.py`): Session → SecurityHeaders → DataFreshness →
CSRF (admin POSTs) → SiteAuth middlewares. ~50 routers, ~277 routes.

**Auth tiers (fixed 6/9):** whole site behind SITE_PASSWORD session; admin via
ADMIN_PASSWORD session flag; `/api/v1/*` — explicit M2M allowlist ONLY
(db/uploads/parquets/reports-upload/live/etp/returns/maintenance, each with key or
bearer auth); all other API paths need the session and return 401 JSON anonymous
(previously the whole tree was exposed — closed same day). Known remaining (medium):
13F intel routers documented admin-only but ungated; empty-ADMIN_PASSWORD fail-open;
35 of 36 template envs bypass StrictUndefined; v3 URL migration frozen mid-flight.

**Replication:** VPS → Render per 4-hourly run: DB via `.backup` + gzip →
`/api/v1/db/upload` (X-API-Key); 8 analysis parquets; screener cache via bearer
`RENDER_UPLOAD_TOKEN` (**must live as a Render env var** — disk copies die on deploy;
that outage 6/8-6/9 is why). Public site holds Bloomberg-derived per-fund data behind
the site password — boundary ADR still owed (audit M-finding).

---

## 9. Observability & integrity

- **28 morning assertions** (freshness, classification coverage, lifecycle sanity,
  KPI consistency, integrity, infra: backup-pattern freshness, restrictive-flag age,
  git-tree-clean deploy drift). Triage email **always** sends; subject flips to
  "ASSERTIONS DID NOT RUN" on staleness.
- **OnFailure → `rexfinhub-alert@.service`** on every failure-prone unit (emails the
  journal tail via `send_critical_alert`, which bypasses the send gate by design).
- **warn ≠ fail:** sweeps/assertion runs exit 0 with status in the email, not the
  unit state (the old conflation hid real failures and killed the watchdog email).
- **Audit trails:** `capm_audit_log` (product changes), `auto_classify_*.jsonl`
  (every classification decision), `ApiAuditLog` (M2M uploads), reconciler log,
  `engine_step_runs` (future, ADR 0011 E1).

---

## 10. Backup & recovery

Nightly 23:00: prune(7d) → `.backup` → `PRAGMA integrity_check` (delete + fail loudly
if not ok) → OnFailure alert. Rollback snapshots (`*_pre_*.db`, written before every
destructive --apply) now live ≥24h. D: pulls when the laptop is online
(`sync_vps_to_d*` — being repointed at snapshot files, never the live WAL DB).
**Accepted risk (Ryu 6/9):** no offsite/cloud leg — a VPS loss + >8-day laptop absence
loses backup history. **Still owed:** a tested RESTORE runbook + drill (no restore
procedure exists anywhere — audit HIGH).

---

## 11. Version control & deploy

- `main` on GitHub = the only code truth. Render auto-deploys `main`. VPS deploys via
  `git pull --ff-only` (classification-sweep + 13F pre-steps already pull daily).
- **No scp-to-prod.** Emergency hot-fixes must be rescue-committed same day — the
  `git_tree_clean` assertion nags any tracked modification on the VPS tree.
- Secrets: env only (VPS `config/.env` consumed via systemd `EnvironmentFile=`;
  Render dashboard env vars). Never on Render disk, never in git going forward
  (history hygiene = discreet slice, in progress).
- Worktrees under `.claude/worktrees/` for multi-agent work; session branches merge
  to main fast-forward after verification.

---

## 12. Known state & debt register (post-session truth, 2026-06-09 evening)

**Live and healthy:** filing detection (4 tiers) · effectiveness status model ·
autonomous classification · market pipeline · assertions/triage · backups(+integrity)
· public site (auth boundary fixed) · git-deploy loop.

**Parked by decision (Ryu 6/9):** CBOE scanner (WAF; data frozen 5/13) · 13F Q1'26
(accepted loss) · offsite backup leg (declined) · VPS deletion list (awaits explicit go;
~4GB redundant DB copies + repo-root clutter listed in `audit_2026-06-09/RYU_DECISIONS.md` §8).

**Open debt (ENGINE_PLAN slices, highest value first):** send-path exactly-once ledger
+ recipient misroute hard-error (Slice 3) · RESTORE runbook + drill (Slice 4) ·
`engine/tick.py` journaled DAG consolidating the timer fleet (Slice 5) · `kpi.py`
single source for every cross-report number (Slice 7) · legacy proposal-queue triage
(1,669 rows) · version-sprawl archive after proof-of-death (trex_combined v2-v8,
multi_angle v1-v3, generate_docx v1-v4) · 13F intel router gating · StrictUndefined
template migration · scripts/ lifecycle reorg (ops/admin/archive) · GLOSSARY additions
+ SYSTEM.md/TARGET.md refresh against this document.

---

## 13. Reading order for a fresh session

1. This file (the map of everything).
2. [`GLOSSARY.md`](GLOSSARY.md) for any unfamiliar term.
3. [`CLASSIFICATION.md`](CLASSIFICATION.md) before touching categories/rules.
4. [`RUNBOOK.md`](RUNBOOK.md) before operating (gates, sends, incidents).
5. [`audit_2026-06-09/`](audit_2026-06-09/) for evidence behind any claim here.
6. [`DECISIONS/`](DECISIONS/) before re-litigating any design.

---
doc: audit
title: Ultimate Fixup — production automation rhythm + report QC (2026-06-16)
status: canonical
updated: 2026-06-16
---

# Ultimate Fixup — automation rhythm, data pipeline, and report QC

The consolidated, evidence-grounded map of what production **actually runs**, the bugs
found in it, and the input→output verification that now guards the reports. Pairs with
[`SYSTEM_LEDGER.md`](../SYSTEM_LEDGER.md) (component purpose) and
[`lineage/index.html`](../lineage/index.html) (field lineage). Folds into `SYSTEM.md`.

## 1. Production timer / service map (VPS, verified 2026-06-16)

| Unit | Schedule (ET) | Does | State |
|---|---|---|---|
| atom-watcher (daemon) | continuous 60s | SEC filing discovery → filing_alerts | ✅ healthy |
| single-filing-worker (daemon) | continuous | Tier-2 enrich → trusts/fund_status | ✅ healthy |
| fresh-poller | Mon–Fri 08–20 /15min | daily-index scrape + promote-only | ✅ healthy |
| **bloomberg-chain** | Mon–Fri 17:15 + 21:00 | SharePoint pull + `sync_market_data` + **12 post-steps** | ✅ healthy (the good sync path; uses `db_writer.write_master_data` with dedup) |
| sec-scrape | Mon–Fri 08/12/16/20:00 | `run_all_pipelines → run_daily.main()` (runs market sync too) | ⚠️ ran the legacy heavy path; **crashed 08:00** (see §2.1) |
| reconciler | daily 08:00 | Tier-3 status safety net | ✅ |
| classification-sweep | Mon–Fri 09:00 | git pull + classify_daily + apply_fund_master + sweep | ✅ (failures ignored via `-` prefix) |
| morning-triage | Mon–Fri 08:00 | `run_assertions` + triage email | ❌ assertions exit 2 **and** email can't send (§2.4) |
| db-backup | daily 23:00 | nightly tarball | ✅ |
| parquet-rebuild | Mon+Fri 06:00 | L&I parquet chain | ✅ |
| grade-recommendations | Sun 23:00 | rec grading | ✅ |
| cboe | daily 03:00 | symbol-reservation sweep | ❌ 403 — cookie expired (routine rotation) |
| gate-close | Mon–Fri 20:00 | `send_enabled=False` | ✅ |
| **intraday-refresh** | (08/12/16/20):05 | `intraday_refresh.py` (fast `run_daily --skip-sec`) | ❌ **DISABLED — never enabled; last fired 2026-05-26** |
| **gate-open** | Mon–Fri 19:00 | `send_enabled=True` | ❌ DISABLED (gate never auto-opens; `send_enabled=false`) |
| **preflight** | Mon–Fri 18:30 | `preflight_check.py --post-summary` | ❌ DISABLED (pre-send safety check not running) |
| bulk-sync | Sun 07:00 | weekly full trust sync | ❌ DISABLED |
| daily (legacy) | Mon–Fri 19:30 | superseded `run_daily --skip-sec` | ❌ DISABLED (correctly off) |

Cron (jarvis): L&I daily engine 22:30; weekly L&I reports Mon 07:00/07:05; duplicate-ticker
audit 02:35; disk-hygiene `find … -delete`. No root crontab.

**ADR 0005 migration was never deployed on the box:** the intended fast artifact-refresh
heartbeat (`intraday-refresh`) is off, while its predecessor (`sec-scrape`) still runs the
slow legacy path it was meant to replace. The two write paths диverged (see §2.1).

## 2. Bugs found + fixed this session

### 2.1 Two market-write paths, inconsistent dedup → daily 08:00 crash  — FIXED
`market_sync._insert_master_data` had **no** `(ticker, etp_category)` dedup; `db_writer.write_master_data`
did. A transient duplicate sheet row (CMAY US / Corgi May Series — the sheet currently carries
**332** dup rows) aborted the entire sec-scrape sync with `IntegrityError`. The bloomberg-chain
(db_writer) survived because it dedups. **Fix:** added the identical guarded `drop_duplicates`
to `_insert_master_data` (commit a772358). Verified: re-sync dropped 332 dups, no crash.

### 2.2 `daily_classify` pipeline-runs never finalized → orphan leak  — FIXED
`run_daily.run_classification` created a `MktPipelineRun(status='running')` and never closed it —
33 orphaned rows since 2026-06-03. **Fix:** call `finish_pipeline_run(status='completed')` after
the writes (commit a772358).

### 2.3 Intraday path never restamped the curated taxonomy → full ACTV universe NULL  — FIXED
The 4×/day `run_daily` path restored issuer brands + the auto-sweep but never ran
`apply_fund_master`, so the curated 3-axis taxonomy (incl. all ~85 REX products) was NULL for
most of each day across **all 5,262 ACTV funds**. **Fix:** `apply_fund_master` now runs in
`run_market_sync` before the sweep (commit 7721712). Restored prod: NULL `primary_strategy`
5,262 → 15; REX 0 NULL.

### 2.4 Morning-triage failure alerts are themselves down  — OPEN
`run_assertions` exits 2 (real failures incl. `git_tree_clean` — dirty prod tree) **and** the
triage email dies on `graph_email not importable; install webapp deps`. The `rexfinhub-alert@…`
units are in `failed`. Net: failures happen silently. Needs the email-deps fix + a tree clean.

### 2.5 Send automation half-wired  — BY DESIGN (shadow-first) + OPEN
`gate-open` + `preflight` timers disabled, `send_enabled=false`. Sends are dark. Per the
shadow-first decision, real sends stay gated; but `preflight` should run (build + audit, post to
relasmar) so the system is send-ready. Re-enabling preflight is safe; gate-open stays off until
go-live.

### 2.6 Lower-severity warts (noted, not yet fixed)
- ETN override fails non-fatally: `Invalid value '…' for dtype 'str' … got 'float64'` — the
  MicroSectors AUM/flow override silently no-ops, so MicroSectors figures may fall back to raw
  Bloomberg. Affects the MicroSectors report.
- `Global ETP sync skipped: assets.csv not found`; `CSV export skipped: '>' not supported
  between 'str' and 'int'` — non-fatal but real code/data warts in the sync.
- CBOE cookie expired (routine rotation — Ryu pastes a new sessionid).
- Prod git tree dirty (`ticker_analyze.py` + rules CSVs + stale `.bak`/`.PAUSED`) → trips
  `git_tree_clean` every morning and risks `git pull --ff-only` failures.

## 3. Report QC — input→output verification (`scripts/qc_all_reports.py`)

Built from a per-report number-source audit of all 10 reports. Three layers, exit 2 on any hard
failure so it can gate the send:
- **Layer 1 — source invariants:** every category AUM + REX share recomputed from raw
  `mkt_master_data`; no duplicate keys; no liquidated-marked-active; ACTV REX completeness
  (issuer_display + primary_strategy); autocall union tagged; freshness; daily-history captured.
- **Layer 2 — builder vs source:** L&I / Income / Flow builders called and their headline AUM
  compared to an independent SQL recompute within tolerance.
- **Layer 3 — rendered scan:** built previews checked for a liquidated ticker, a literal
  None/nan/$0 in a headline, or a missing market-share chart.

Each report's purpose, headline numbers, source query, filters, and known fragility are catalogued
in the QC checklist that seeded this script (35 assertions).

## 4. Remaining work (prioritised)
1. Re-enable `preflight` timer (shadow-safe); fix triage-email `graph_email` import so failures alert.
2. Clean the prod git tree (commit `ticker_analyze.py` + rules CSVs; relocate `.bak`/`.PAUSED` — no autonomous delete).
3. ETN-override dtype fix (MicroSectors accuracy); assets.csv / CSV-export warts.
4. Full-system architecture visual (devices × timers × DBs × reports) + fold this audit into SYSTEM.md.
5. Shadow-send dry run to relasmar; LITU status decision; orphaned-run backfill cleanup.

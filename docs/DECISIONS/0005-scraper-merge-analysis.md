---
adr: 0005
title: Phase 1 Cut 3 — scraper role-overlap analysis (KEEP ALL THREE, rename one)
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0005 — Scraper role-overlap analysis (deferred from ADR 0003)

## Context

ADR 0003 (Phase 1 cuts round 1) deferred Cut 3 — "merge or retire one of {atom-watcher, fresh-poller, sec-scrape}" — pending a code-overlap analysis. The premise was that three SEC scraping pathways looked redundant. This ADR closes that question.

The three pathways:

| Unit | Cadence | Source | CIK universe | What it writes |
|---|---|---|---|---|
| `rexfinhub-atom-watcher.service` (daemon) | every 60 s, 24/7 | SEC EDGAR atom feed (`browse-edgar?action=getcurrent`) | ALL filers — no filter | `filing_alerts` table only; auto-creates `trusts` rows for unknown CIKs |
| `rexfinhub-fresh-poller.timer` | every 15 min, Mon-Fri 08:00-20:45 ET | SEC submissions JSON (per-CIK) with daily-index pre-flight | curated ~290 trusts (`Trust.source='curated'`) | `filings` + `rex_products` (via post-step sync) |
| `rexfinhub-sec-scrape.timer` | 4×/day, Mon-Fri 08:00/12:00/16:00/20:00 ET | SEC submissions JSON (per-CIK) | curated ~290 trusts | `filings` + `rex_products` + screener_cache + parquets + Render DB upload |

## What the code actually does

Three different jobs that **look** similar but aren't.

### atom-watcher — discovery layer

`etp_tracker/atom_watcher.py`:
- Polls 4 form-prefix queries (`485`, `497`, `N-1A`, `N-2`) against the SEC atom feed every 60 s
- Strict client-side form filter to reject S-11/N-1A/A drift
- Inserts new accessions into `filing_alerts` with `source='atom'`, `enrichment_status=0`
- For unknown CIKs: inserts into `trusts` with `source='watcher_atom', is_active=1`
- Tier 2 enrichment kicks off in-process via `etp_tracker/single_filing.py`
- **Latency**: ~1-3 min from SEC accept to local row

Live state observed at the time of writing: cycle #49,856; ~5 sec per cycle; "queried=4 fetched=4 parsed=147"; "new=0/1" most cycles. Working as designed.

### fresh-poller — incremental enrichment layer

`scripts/poll_fresh_filings.py`:
- Calls `run_pipeline(use_daily_index=True, since=today-2d, etf_only=True)` over curated CIKs
- Daily-index pre-flight skips trusts with no filing today — usually 95%+ of the curated set
- Runs `sync_rex_products_from_filings.py --apply --no-prompt` to promote filings into `rex_products`
- Uploads to Render only when row counts actually changed
- Holds an exclusive `flock` on `data/.poll_fresh_filings.lock`
- Refuses to start if `rexfinhub-sec-scrape.service` is `active` (via `Conflicts=` in the systemd unit)
- **Latency**: ~15-20 min worst case during business hours

Live state: 20 consecutive successful 15-min runs today; per-run 145-290 sec; two upload events when new filings landed (+4 at 13:30, +1 at 15:15).

### sec-scrape — intraday refresh (misnamed)

`scripts/run_all_pipelines.py --skip-email --skip-market`:
1. SEC pipeline (`run_daily.main()`) — full sweep, curated CIKs, no daily-index optimization
2. Classification sweep — `unified classify: 7390 funds classified`
3. Screener snapshot to `data/DASHBOARD/exports/screener_snapshots/<date>/`
4. Total returns scrape
5. Parquet rebuild + upload (`whitespace_v4.parquet`, `filing_race.parquet`, etc. — 8 files, ~12 MB)
6. DB compact (`668 MB → 665 MB`)
7. DB upload to Render

The "scrape" half is largely redundant with fresh-poller (same CIK universe, same submissions endpoint, just without the daily-index optimization). The other 6 steps are **not** done by fresh-poller — they're the value-add that justifies the 4×/day rhythm.

## Decision

**Keep all three. Retire none. But rename and refactor `sec-scrape` to reflect what it actually does.**

The three pathways serve different roles and the apparent redundancy is shallow:

| Role | Owner |
|---|---|
| Discovery of brand-new ETP issuers (unknown CIKs) | atom-watcher — only one that touches the broad universe |
| 15-min lag SLA on new filings from known issuers | fresh-poller — daily-index optimization makes this cheap |
| Periodic refresh of derived artifacts (classification, screener, parquets, Render DB) | sec-scrape — does the heavy artifact rebuild |

### What changes

**Rename + de-duplicate the scrape step** (deferred to implementation; ADR documents the path):

- Rename `rexfinhub-sec-scrape.timer` → `rexfinhub-intraday-refresh.timer`
- Rename `rexfinhub-sec-scrape.service` → `rexfinhub-intraday-refresh.service`
- ExecStart calls a new wrapper `scripts/intraday_refresh.py` that:
  - SKIPS the SEC scrape step (fresh-poller already covered it)
  - Runs classification sweep, screener snapshot, total returns, parquet rebuild, DB compact, Render upload
- Drop `Conflicts=rexfinhub-sec-scrape.service` from fresh-poller's unit (replaced with `Conflicts=rexfinhub-intraday-refresh.service` so the lock semantics stay intact)
- Update `docs/SYSTEM.md` workflow table + `docs/GLOSSARY.md` entries

**Estimated win**: each intraday run drops from ~15-20 min to ~5-8 min (the scrape was the slow part). 4 fewer redundant SEC API hits per day. Same artifact freshness.

### What does NOT change

- atom-watcher stays exactly as is. It's the only thing that catches new ETP issuers before they hit any curated list.
- fresh-poller stays exactly as is. The daily-index optimization + 15-min cadence is doing real work — today's log shows two upload events triggered by it that would otherwise have waited until the next 4-hour batch.
- The 4-hour rhythm of intraday-refresh stays — the parquets/screener/classification artifacts don't need refreshing more often than that.

## Consequences

**Wins**:
- ~50% reduction in intraday-refresh runtime (skip the scrape step)
- Eliminates the misleading "sec-scrape" name that suggested duplicate scraping
- The lock-conflict mechanism (`Conflicts=`) stays intact — both fresh-poller and intraday-refresh still serialize correctly
- Closes Cut 3 of Phase 1

**Trade-offs**:
- intraday-refresh now relies on fresh-poller having actually run successfully in the prior 15-min window. If fresh-poller is stuck or failing, intraday-refresh would skip the scrape and miss filings entirely.
- Mitigation: the 8:00/12:00/16:00/20:00 slots line up exactly with fresh-poller fires. If fresh-poller is down (no log line in the last 15 min), intraday-refresh should fall back to running the SEC scrape itself. The new wrapper script will check `data/.poll_fresh_filings.log` modification time and fall through to a full scrape if no run completed in the last 30 min.
- Naming change requires re-enabling the new timer + disabling the old one on VPS. One-time migration cost.

**Revert path**: rename back; remove `intraday_refresh.py`; restore the old `rexfinhub-sec-scrape.service` ExecStart.

## Alternatives considered

- **Retire `sec-scrape` entirely**. Rejected — the 6 non-scrape steps (classification sweep, screener, parquets, DB compact, Render upload) are real work that needs to happen on a 4-hour rhythm. Killing the timer would push these artifacts to once-daily, breaking the intraday parquet freshness contract.
- **Retire `fresh-poller`**. Rejected — atom-watcher writes to `filing_alerts` only, not `filings`/`rex_products`. Without fresh-poller's per-CIK enrichment + sync, new filings would sit in `filing_alerts` until the next 4-hour sec-scrape. The 15-min SLA disappears.
- **Retire `atom-watcher`**. Rejected — atom-watcher is the only path that catches brand-new ETP issuers (CIKs not in the curated list). Without it, a new issuer's first filing would never be picked up until they're manually added to the trust universe.
- **Merge fresh-poller and intraday-refresh into a single timer that branches on time-of-day**. Rejected — the cleanest separation is "filing detection cadence" (every 15 min) vs "artifact refresh cadence" (every 4 hours). Branching on time-of-day in one script makes the unit harder to reason about and harder to debug when a single role fails.
- **Decouple discovery and enrichment for fresh-poller** (atom-watcher feeds a queue, fresh-poller drains it). Rejected for now — current design works; introducing a queue adds operational surface area without measurable benefit at current filing volumes.

## Implementation (not done yet)

The rename + refactor is a follow-up commit. Acceptance criteria:

- [ ] New `scripts/intraday_refresh.py` skips SEC scrape when `fresh-poller` ran in the last 30 min; falls back to full scrape otherwise.
- [ ] New `deploy/systemd/rexfinhub-intraday-refresh.{service,timer}` units, with `Conflicts=` updated on fresh-poller.
- [ ] One-shot VPS migration: disable old units, enable new units, leave old unit files in place for revert.
- [ ] `docs/SYSTEM.md` workflow table updated.
- [ ] `docs/LOG.md` entry under Phase 1 Cut 3.
- [ ] Verify next 4-hour run completes faster than the prior baseline.

Tracking under task list "ADR 0005 implementation."

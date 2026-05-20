---
adr: 0010
title: Phase 7 — Migrate to edgartools + decommission manually_edited_fields
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0010 — Migrate SEC scraping to `edgartools`; consolidate state files

## Context

Today's SEC scraping stack is hand-rolled in `etp_tracker/`:
- `bulk_loader.py` — downloads + scans submissions.zip
- `single_filing.py` — fetches per-CIK submission JSONs
- `extractor.py` — parses prospectus form data into the schema
- `atom_watcher.py` — polls EDGAR atom feed
- ~3,500 lines of code, all maintained in-house

The open-source `edgartools` library (MIT-licensed, ~50k stars on GitHub, active maintainer) provides equivalent capabilities with significantly better:
- form-type parsing for the long tail (485APOS/485BPOS variants, N-1A/N-2/N-CSR variants)
- caching strategy
- error handling on SEC rate limits
- typed return values (Pydantic models)

Migrating eliminates the maintenance burden + closes the long tail of edge cases our hand-rolled extractor doesn't handle.

Separately, the codebase has accumulated **~7 state files** scattered across `data/`:
- `.send_enabled` — gate state
- `.send_paused` — emergency pause flag
- `.preflight_token` — preflight session ID
- `.preflight_result.json` — last preflight outcome
- `.preflight_decision.json` — auto-GO decision payload
- `.preflight_maintenance` — flag to escalate WARN→PASS
- `.autogo_on_warn` — flag to auto-GO on WARN
- `.pipeline_stages.jsonl` — per-stage timing log
- `.gate_state_log.jsonl` — gate transition log
- `.intraday_refresh.log` — Phase 1 wrapper log
- `.poll_fresh_filings.log` — fresh-poller log
- `.cboe_rotated_at` — Phase 2 cookie freshness stamp
- `.duplicate_tickers_audit.json` — Phase 0b audit output
- `.auto_demote_vanished` — Phase 0b feature flag

Each grew organically as new features landed. There's no single "what's the system state right now?" query — the answer is splayed across 14 files. ADR 0001 / TARGET.md `### cuts` calls for consolidation to 2-3 files.

This ADR designs both migrations. Phase 7 is the last phase in the rebuild roadmap.

## Decision

### Part A: `edgartools` migration

Use [`edgartools`](https://github.com/dgunning/edgartools) as the SEC scraping layer. Wrap with a thin compatibility shim during the dual-read window.

**Replace**:
- `etp_tracker/bulk_loader.py::download_submissions_zip` → `edgar.bulk.download_submissions()`
- `etp_tracker/single_filing.py::fetch_submissions_json` → `edgar.Filings(cik=...)`
- `etp_tracker/extractor.py::parse_485apos` → `edgar.Filing.obj()` returning typed model
- `etp_tracker/atom_watcher.py` → KEEP. edgartools doesn't have a near-realtime atom equivalent; our 60s-poll discovery layer remains in-house.

**Keep**:
- `webapp/services/market_sync.py` — Bloomberg sync, separate from SEC
- All Phase 0-6 work on `rex_products`, `product_master`, `classification_override`, etc.
- `atom_watcher` for ALL-universe discovery

### Part B: State-file consolidation

14 state files → **3 tables**:

```sql
-- All boolean/flag state in one row, one cell each
system_flags (
  flag_name      TEXT PRIMARY KEY,   -- 'send_enabled','send_paused','preflight_maintenance',
                                     -- 'autogo_on_warn','auto_demote_vanished', ...
  is_set         BOOLEAN NOT NULL,
  set_at         TIMESTAMPTZ NOT NULL,
  set_by         TEXT,
  notes          TEXT
);

-- Preflight + auto-GO + decision payloads
preflight_run (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at         TIMESTAMPTZ NOT NULL,
  result_status  TEXT NOT NULL,      -- 'pass' | 'warn' | 'fail'
  decision       TEXT,               -- 'go' | 'hold' | NULL (= no decision yet)
  decision_token TEXT,
  result_json    JSONB,
  decision_json  JSONB
);

-- Append-only event log (replaces .pipeline_stages.jsonl + .gate_state_log.jsonl
-- + .intraday_refresh.log + .poll_fresh_filings.log + .cboe_rotated_at)
system_event (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type     TEXT NOT NULL,      -- 'stage_completed','gate_opened','gate_closed',
                                     -- 'cboe_rotated','intraday_refresh_run',
                                     -- 'fresh_poller_run','sweep_dispatched', ...
  event_at       TIMESTAMPTZ NOT NULL,
  details        JSONB,
  INDEX (event_type, event_at)
);
```

After Phase 7:
- `system_flags` replaces `.send_enabled`, `.send_paused`, `.preflight_maintenance`, `.autogo_on_warn`, `.auto_demote_vanished` (5 → 1 table)
- `preflight_run` replaces `.preflight_token`, `.preflight_result.json`, `.preflight_decision.json` (3 → 1 table)
- `system_event` replaces `.pipeline_stages.jsonl`, `.gate_state_log.jsonl`, `.intraday_refresh.log`, `.poll_fresh_filings.log`, `.cboe_rotated_at`, `.duplicate_tickers_audit.json` (6 → 1 table)

One file remains: `temp/submissions.zip` (genuinely a binary cache, not state). That's fine.

Admin UI gains a `/admin/system-state` page surfacing all three tables for inspection.

### Stages

**Stage 1 — edgartools dependency + spike**.
- Add `edgartools` to `pyproject.toml` / `requirements.txt`.
- Spike: pick one CIK, fetch via edgartools, parse, compare output to existing pipeline. Verify field-level equivalence.

**Stage 2 — Wrap edgartools in compatibility shim**.
- New `etp_tracker/edgar_client.py` exposes the same API surface as `single_filing.py` + `extractor.py` but delegates to edgartools.
- Existing callers don't change.

**Stage 3 — Dual-run window (2 weeks)**.
- Fresh-poller runs BOTH old code and new shim; compares outputs in a diff log.
- Manual review of any divergence; bug-fix the shim or accept the difference.

**Stage 4 — Cut over fresh-poller + intraday-refresh to edgartools**.
- Atom-watcher stays on existing code (different role).
- Old `bulk_loader.py` / `single_filing.py` / `extractor.py` move to `etp_tracker/legacy/` for revert.

**Stage 5 — Drop legacy code after 30 days of clean operation**.

**Stage 6 — State consolidation**.
- New tables created.
- One-off migration script copies flag/log files into DB rows.
- Code updated to read/write via the new tables.
- Old files moved to `data/.legacy/` (not deleted).

**Stage 7 — Delete `data/.legacy/` after 30 days**.

## Consequences

**Wins**:
- ~3,500 lines of in-house scraping code retired.
- Better long-tail form parsing (edgartools handles more variants than our extractor).
- Better state observability via the admin UI.
- Closes TARGET.md `### cuts` items: "5+ state files → consolidate to 2-3" and "Migrate to edgartools".

**Trade-offs**:
- External dependency. edgartools is well-maintained but a single point of failure. Mitigated by keeping `etp_tracker/legacy/` available for 30-day revert.
- State migration touches every code path that reads a flag/log file. Wide blast radius; per-touchpoint testing required.
- The 2-week dual-run window defers the win. Worth it for confidence.

**Revert path**:
- Part A: `etp_tracker/legacy/` files restored; shim swapped out.
- Part B: legacy files in `data/.legacy/` swapped back in; code re-pointed.

## Dependencies

- Phase 6 must complete first. Phase 6's `classification_override` is what edgartools' typed outputs flow INTO. Cleaner cut.
- Phase 7 starts ≥ 2026-07-15.

## Alternatives considered

- **Don't migrate to edgartools; keep maintaining in-house**. Rejected — the maintenance burden is real (BUG-03 + BUG-04 + BUG-08 all touched extraction code). edgartools is actively maintained and has dozens of contributors fixing edge cases we'd never see in our scope.
- **Keep state files; just rename them for consistency**. Rejected — half-measure. The real cost is "where is the state of the system right now?" being unanswerable via SQL.
- **Different SEC library** (`sec-edgar-downloader`, etc.). Rejected after spike — edgartools is the most full-featured.

## Implementation timeline

- Part A: ~2 weeks (1 spike + 1 week shim + 2 week dual-run + cutover).
- Part B: ~1 week (schema + migration + read/write refactor).
- 30-day grace before legacy file deletion.

Total active engineering: ~3-4 weeks. Calendar: ~7-8 weeks including grace periods.

## Closing the rebuild

ADR 0010 is the last phase in the TARGET.md roadmap. After it ships (~2026-09):

- Phase 0a was deferred and remains so (security hardening — separate decision).
- All structural debt from the 2026-05-12 rebuild is addressed.
- Ryu's daily touchpoints are: (1) read the morning triage email; (2) approve any override decisions surfaced there; (3) paste a CBOE cookie when stale. The Bloomberg pull + classification + send + summary all run autonomously.

---
adr: 0011
title: Engine architecture — one tick, one writer, one gate, one truth
status: proposed
date: 2026-06-09
supersedes: parts of 0003 (timer fleet shape)
---

# ADR 0011 — Engine architecture

## Context

The 2026-06-09 full-system audit (73-agent sweep + live recon; `docs/audit_2026-06-09/`)
found 171 defects whose root causes collapse into five architectural diseases, each with a
production incident already on the books:

1. **Many writers, no authority.** rex_products.status had 3+ writers (sync, reconciler,
   admin, repair scripts) → the Delayed-status epidemic fixed on 6/9; status_cached drift;
   phantom Listed rows. fund_status likewise.
2. **Many flags, two stores each.** send_enabled / send_paused / preflight_maintenance /
   autogo_on_warn each exist as a DB row AND a dotfile → the 35-day send blackout, a month
   of suppressed classification gating, red-button runbook no-ops.
3. **Implicit orchestration.** The "sec-scrape" unit is a 12-step monolith; six maintenance
   steps ride as ExecStartPost of an unrelated Bloomberg unit; ordering lives in comments;
   one step's death silently skips the rest; warn exits conflate with failure and kill the
   triage email exactly when it matters.
4. **Deploy-by-scp.** Production code lived only on the VPS disk (nightly L&I engine, Monday
   reports, GAP-08 fix). git pull pre-steps fail silently on the dirty tree. Render deploys
   wipe disk-resident secrets (screener-cache 503 since 6/8).
5. **Backups that can't restore.** No restore procedure; same-volume backups pruned after
   write; rollback snapshots deleted by a 15-min cron; torn WAL hot-copies on D:; offsite leg
   depends on a laptop.

## Decision

### E1. One tick — an explicit, journaled DAG
A single orchestrator (`engine/tick.py`) owns the daily flow:

```
scrape → db-sync → market-sync → classify-gate → reconcile → bake-reports
       → upload-render → [send-gate] → send → verify → backup → offsite
```

- Each step: idempotent, declares its inputs/outputs (data contract), writes a
  `engine_step_runs` row (started/finished/status/error/artifacts).
- A step failure isolates: downstream steps that declare a dependency on it skip WITH a
  journal row; independent steps continue.
- Warn ≠ fail: steps return ok|warn|fail; only fail stops dependents; warn never breaks
  the unit exit code. The triage email **always** sends (it is itself a step).
- systemd keeps ONE timer per cadence (15-min poller, 4-hourly tick, nightly tick,
  weekly/quarterly ticks) — all ExecStart the orchestrator with a profile, never chains.

### E2. One writer per table
| Table | Sole writer | Everyone else |
|---|---|---|
| rex_products.status/status_cached | status_reconciler | sync = promote-only evidence (already shipped); admin via reconciler API |
| status_history | status_reconciler | read-only |
| fund_status | sync_service | read-only |
| mkt_* | market pipeline (db_writer) | read-only |
| system_flags | flags API (set_by mandatory) | read-only |

Enforced by morning assertions (writer fingerprint per table, e.g. set_by/source columns).

### E3. One gate store
`system_flags` is the only flag store. The dotfile layer (`data/.send_enabled`,
`.send_paused`, `.preflight_maintenance`, `.autogo_on_warn`) is deleted after a one-time
migration check; every reader goes through `system_flags.get_flag`. Every flag carries
set_by + set_at; morning triage alarms on any restrictive flag older than N days
(send_paused>3d, preflight_maintenance>7d). RUNBOOK red-buttons become `scripts/gate.py
open|close|pause|status` operating on the DB store.

### E4. Deploy = git, secrets = env
- VPS runs from main via `git pull --ff-only` in the tick's deploy step; scp-to-prod is for
  emergencies only and must be followed by a rescue commit within the day (assertion: dirty
  tracked files on VPS = warn).
- The daily Bloomberg xlsm is untracked (it is data, not code); artifacts (reports/, outputs/,
  caches, *.db) never live in git.
- All M2M tokens move to environment (Render env group; VPS systemd EnvironmentFile) — no
  secrets on Render disk, none tracked in git. (Repo-history hygiene handled as its own
  discreet slice.)

### E5. Backups that restore
- Order: prune → backup → verify (`PRAGMA integrity_check` on the snapshot) → gzip →
  offsite push (Hetzner Storage Box / B2, 30-day retention) — all steps of the nightly tick,
  each journaled, OnFailure=critical-alert.
- Rollback snapshots (`*_pre_*.db`) get a 24h minimum lifetime (hygiene cron exempts them;
  each script's keep-3 rotation is the authority).
- D: sync pulls only `.backup`-API snapshots, never the live WAL DB.
- `docs/RUNBOOK.md` gains a tested RESTORE procedure; restore drill quarterly (calendar'd).

### E6. One source of truth per number
A single `webapp/services/kpi.py` (or equivalent) owns: REX fund universe (count/AUM by
suite), ETP active universe, market-share series. Every report builder imports it; no
builder computes its own variant. Report freshness dependencies (parquets, caches) are
declared in the bake step's contract, not discovered at send time.

## Consequences
- The 12 systemd timers collapse to ~5 profiles of one orchestrator; ordering becomes
  testable code instead of unit-file archaeology.
- Single-writer + journaled steps make every number traceable: report KPI → kpi.py →
  table → writing step → upstream filing/Bloomberg row.
- The flag matrix (8 states) becomes 4 DB rows with alarms; the class of "forgotten flag"
  outages ends.
- Cost: migration must be incremental (slice plan in `docs/audit_2026-06-09/ENGINE_PLAN.md`);
  each slice ships with before/after verification, and old paths retire only after the
  3-gate proof of death (build-prove-retire).

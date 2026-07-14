---
adr: 0003
title: Phase 1 — Cuts (round 1)
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0003 — Phase 1 Cuts: round 1

## Context

`TARGET.md#phases` lists Phase 1 as "pure deletions and consolidations to remove redundant moving parts." Five concrete items were named there:

1. Kill the 09:00 ET morning classification sweep timer (redundant with 18:30 preflight + 20:15 summary)
2. Kill the weekly trust universe sync timer (atom watcher covers new-CIK discovery in 1-3 min)
3. Merge `rexfinhub-fresh-poller.timer` + `rexfinhub-sec-scrape.timer` (4×/day) into a single scraper schedule
4. Merge the 4 `ExecStartPost=` lines in `rexfinhub-bloomberg-chain.service` into a single consolidated wrapper script
5. Extend `sync_vps_to_d_drive.sh` to pull every nightly backup (not just the most recent), and schedule from laptop Task Scheduler at 23:30 ET

Items 1, 2, 4, 5 are mechanical — defensible without further investigation. Item 3 requires understanding the subtly different roles of `atom_watcher.py` (in-process every 60s for ALL filings), `poll_fresh_filings.py` (systemd every 15 min, curated CIKs only), and the 4×/day full-pipeline scrape (slow batch fallback). Premature merging risks missing filings.

This ADR documents items 1, 2, 4, 5. Item 3 deferred to ADR 0005 after a code-level analysis (re-numbered from 0004 once 0004 was taken by Phase 2 admin pages).

## Decision

### Cut 1 — Disable classification-sweep timer

Action taken on VPS:
```
sudo systemctl disable --now rexfinhub-classification-sweep.timer
```

Both unit files remain in `deploy/systemd/` for historical reference and easy revert. Service may still be invoked manually (`sudo systemctl start rexfinhub-classification-sweep.service`) — only the timer is gone.

### Cut 2 — Disable bulk-sync (weekly trust universe sync) timer

Action taken on VPS:
```
sudo systemctl disable --now rexfinhub-bulk-sync.timer
```

The `etp_tracker/atom_watcher.py` already auto-creates Trust rows for previously unknown CIKs within 1-3 min of any 485-series filing, per the prior workflow audit. The weekly bulk sync only added metadata enrichment (entity_type, regulatory_act, filing_count) — useful but not load-bearing.

If we ever want quarterly metadata backfill, the unit files remain in place — re-enable selectively when needed.

### Cut 3 (DEFERRED to ADR 0005)

Investigate role overlap between atom_watcher / fresh-poller / 4×/day scrape before merging. None disabled in this ADR.

### Cut 4 — Consolidate Bloomberg-chain post-steps

Before:
```
ExecStartPost=.../apply_fund_master.py
ExecStartPost=.../apply_underlier_overrides.py
ExecStartPost=.../apply_issuer_brands.py
ExecStartPost=.../apply_classification_sweep.py --apply --apply-medium
```

After:
```
ExecStartPost=.../apply_bloomberg_post_steps.py
```

New wrapper at `scripts/apply_bloomberg_post_steps.py` runs the 4 underlying scripts in order with timing logs. A non-zero exit from any step does NOT abort the chain — every step runs so partial successes still apply. Overall return code = max of individual return codes.

Same end behaviour; half the systemd-unit noise; single place to add timing / logging / skip-on-error logic if needed later.

### Cut 5 — Extend D-drive sync to all backups

`scripts/sync_vps_to_d_drive.sh` previously pulled only "today's daily + latest pre_sync" backup. Rewritten to enumerate every `.db` file under `data/backups/` on VPS and pull what we don't already have on D drive. Idempotent — `scp -p` won't re-transfer files we have.

Header updated to reflect Phase 1 scheduling guidance: "Schedule via Windows Task Scheduler at 23:30 ET daily" (after the VPS 23:00 backup completes). User to wire the Task Scheduler entry once on the laptop. Not automatable from this codebase.

## Operations

VPS state (verified):
- `rexfinhub-classification-sweep.timer` → disabled (was 09:00 weekdays)
- `rexfinhub-bulk-sync.timer` → disabled (was Sun 07:00)
- `rexfinhub-bloomberg-chain.service` → updated to use single ExecStartPost
- `scripts/apply_bloomberg_post_steps.py` → deployed

Repo state:
- `deploy/systemd/rexfinhub-bloomberg-chain.service` updated
- `scripts/apply_bloomberg_post_steps.py` added
- `scripts/sync_vps_to_d_drive.sh` updated (header + backup logic)
- `deploy/systemd/rexfinhub-classification-sweep.{service,timer}` kept (disabled, not deleted, for revert)
- `deploy/systemd/rexfinhub-bulk-sync.{service,timer}` kept (disabled, not deleted, for revert)

Manual user action (one-time):
- Wire Windows Task Scheduler on laptop:
  - Trigger: daily 23:30 ET
  - Action: `bash /c/Foundry/rexfinhub/scripts/sync_vps_to_d_drive.sh > /c/Foundry/rexfinhub/data/.sync_d_drive.log 2>&1`
  - Requires SSH key set up + D drive mounted

## Consequences

**Positive**:
- 09:00 ET inbox no longer interrupted by sweep email (the same gap data appears in 20:15 summary)
- Sunday morning bulk sync no longer eats network/CPU for no operational gain
- 4-line ExecStartPost block in bloomberg-chain.service replaced with 1 line; logging consolidated; future skip-on-error easier to add
- D drive becomes a true long-term backup archive (was only "current day + latest pre-sync")

**Negative**:
- If `atom_watcher.py` ever crashes silently, new-CIK discovery stops. Mitigation: monitor in 20:15 summary (TODO — add atom-watcher heartbeat assertion). Without weekly bulk sync as safety net, an atom_watcher outage is more visible.
- Disabled units remain in the repo — easy to mistake for active. The disabled state lives only on VPS systemd. Mitigation: `SYSTEM.md#workflows` updated to mark these timers as DISABLED.

## Alternatives considered

- **Delete the disabled unit files from repo**: rejected. Keeping them disabled-not-deleted preserves audit trail and one-command revert.
- **Inline the Bloomberg post-steps directly into the chain `ExecStart=` Python snippet**: rejected. Already too dense; separate wrapper is cleaner.
- **Move the D-drive sync to VPS-side push (rsync from VPS)**: rejected. Laptop pulling is preserved as the simpler default; SSH key direction already works.

## References

- `docs/SYSTEM.md#workflows` (timer states updated)
- `docs/TARGET.md#phases` (Phase 1 marked partially shipped; Cut 3 deferred to ADR 0005)

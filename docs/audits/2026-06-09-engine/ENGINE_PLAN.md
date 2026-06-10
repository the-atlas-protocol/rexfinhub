# ENGINE PLAN — execution slices

> Ordered by (breakage risk × L&I impact), each slice independently shippable with its own
> verification. Architecture: ADR 0011. Findings: MASTER_AUDIT.md + FINDINGS_DIGEST.md.
> Slices needing Ryu's hand are in RYU_DECISIONS.md and excluded from autonomous execution.

## Slice 0 — Stop the bleeding (DONE this session)
- [x] send_all stock_recs imports nonexistent `main` → `build` (C1). Verified by import test.
- [x] Rescue unversioned VPS production code into git (run_v1, Monday reports, GAP-08,
      db_writer dedup, rules CSVs) — commits 49b356a, 1542435.
- [x] status_cached drift: verified self-healed (0 rows, assertion SQL); no action.
- [x] Dormant-filing pipeline filter (live on VPS).

## Slice 1 — Observability first (small diffs, kills the silent-failure class)
1. Triage email always sends: run_assertions exits 0 always, writes status to its journal
   row; failure signal moves INTO the email + send_critical_alert on FAIL.
   (Alternative kept simple: `ExecStart=-` prefix + Post email; chosen: in-script.)
2. classification_sweep: warn → exit 0 (status in summary email, not exit code).
3. Backup unit: prune-before-backup reorder + `PRAGMA integrity_check` on snapshot +
   OnFailure=critical-alert template unit (reused by all rexfinhub-* units).
4. Fix backup_recent assertion: glob `etp_tracker_\d{8}\.db$`, FAIL when dir missing on VPS.
5. New assertions: (a) restrictive flag age (send_paused>3d, preflight_maintenance>7d warn);
   (b) VPS dirty-tracked-files count >0 for >24h = warn; (c) every systemd rexfinhub unit
   ran within its expected window (reads journal timestamps).
Verification: force a FAIL assertion in a sandbox run → email still arrives; unit shows green.

## Slice 2 — One gate store (C2+C3)
1. Migrate the 4 dotfile flags into system_flags once (values already there), then remove
   file-fallback branches from preflight_check.py / system_flags.py readers.
2. `scripts/gate.py open|close|pause|unpause|status` — the only sanctioned flag mutator
   (set_by mandatory, prints resulting state).
3. RUNBOOK red-button section rewritten around gate.py (C3).
4. Dotfile deletion on VPS = Ryu-approved step (no-deletions rule) — RYU_DECISIONS #4.
Verification: grep zero readers of dotfiles; gate.py round-trip on staging DB; runbook
walkthrough reproduces each state.

## Slice 3 — Send-path hardening
1. Recipient misroute guard: unknown list_type = hard error at build time (not default list).
2. Exactly-once: per-(report,day) send ledger row written BEFORE SMTP, marked sent after;
   resume skips ledger-marked; crash mid-send cannot double-send.
3. Stock_recs/blue_ocean fallback prints CRITICAL + includes bake timestamp in subject suffix
   `[STALE BAKE 2026-06-08]` if fallback used — stale can never ship invisibly.
Verification: dry-run send (preview mode) for all 9 reports; ledger rows present; kill -9
mid-send test on staging → no duplicate.

## Slice 4 — Backup/restore (E5)
1. Reorder unit + integrity_check + gzip (Slice 1.3 lands the unit shell).
2. Offsite leg: rclone to Storage Box/B2 — needs Ryu credential (RYU_DECISIONS #6).
3. RESTORE.md runbook + `scripts/restore_drill.py` (restores latest snapshot to /tmp,
   integrity_check, row-count sanity vs live) — wire as monthly timer step.
4. Exempt `*_pre_*.db` from the 15-min hygiene cron; raise to 24h (RYU_DECISIONS #3 cron edit).
5. Repoint sync_vps_to_d.ps1 step 1 at data/backups snapshots, not the live DB.
Verification: restore drill passes end-to-end; offsite object visible; torn-copy path gone.

## Slice 5 — Orchestration: the tick (E1)
1. Build `engine/tick.py` skeleton + `engine_step_runs` table; wrap EXISTING scripts as steps
   (no rewrites): scrape, sync, market, reconcile(status_reconciler --apply), bake, upload,
   backup. Profiles: poller/4h/nightly/weekly.
2. Migrate ONE timer first (nightly) → run both paths 3 days (shadow mode, tick in dry-run
   journal-only) → cut over → repeat for 4h unit → retire ExecStartPost chains.
3. Reconciler scheduling: status_reconciler --apply becomes an explicit nightly step
   (today it only runs ad-hoc; etp_tracker.reconciler stays the 08:00 SEC-index net).
Verification: 3-day shadow journal parity; step-failure injection isolates correctly.

## Slice 6 — Classification engine
1. Unbrick the proposal loop: triage the 1,669 pending (auto-expire >90d stale; batch-present
   the 81 L&I ones to Ryu via the existing review UI/queue).
2. Backlog: present the 37 unclassified + 20 issuer gaps + 7 CC attrs as one approval batch
   (queue extracted: audits/2026-06-09-engine; includes REX's own TLDR US; OBTC → exclusions per
   standing ruling). After approval lands → remove maintenance flag (RYU_DECISIONS #5).
3. Single rules truth: data/rules = master, config/rules = generated mirror (build step),
   VPS reads only config/rules from git — kills 3-way CSV drift.
Verification: sweep green with 0 gaps; flag removed; strict gating restored without send-day
breakage.

## Slice 7 — Report layer: one truth (E6)
1. `kpi.py`: REX universe (count/AUM by suite), ETP ACTV universe, market share — extracted
   from report_data.py; all builders import.
2. Bake-step contract: builders declare parquet/cache inputs + max ages; bake fails loudly on
   stale input instead of rendering wrong numbers.
3. Version sprawl: trex_combined v2–v8, multi_angle v1-v3, generate_docx v1-v4, expanded_panel
   v1-v2 → `archive/` after 3-gate proof of death (static refs, runtime refs 30d, equivalence).
Verification: every report rebuilt; KPI cross-report identity assertions (the L&I≡Flow≡Blue
Ocean count reconciliation that burned us 6/8) added to morning assertions.

## Slice 8 — Scraper precision (post-engine)
- step3 date-confidence ladder relabel (HEADER low-trust), 8-A12B ingestion as listing
  predicate (deferred from effectiveness overhaul), body_extractors dead-output removal,
  edgar shadow-client decision (ADR 0010 follow-through).

## Slice 9 — 13F + recommendation_history resurrection
- 13F: re-point quarterly timer at ingest mode; re-ingest Q1 2026; locate/rebuild holdings.db
  (D: archive may hold it — RYU_DECISIONS #7 for retrieval).
- recommendation_history: diagnose why grading writes don't land (0 rows); re-run backfill;
  add row-count assertion (>0 after first graded Sunday).

## Slice 10 — Repo + storage organization (E4 + folder plan)
- scripts/ split: scripts/ops/ (scheduled), scripts/admin/ (manual, supported),
  scripts/archive/ (one-offs, dated, frozen); enforcement assertion: nothing in archive/
  referenced by any unit/cron.
- Untrack bloomberg xlsm; .gitattributes (LF normalize); worktree DB hygiene policy;
  VPS top-level cleanup list (temp_cookies.txt, stray rexfinhub.db, temp/, archive/, outputs/)
  → RYU_DECISIONS #8 (deletions).
- D: layout doc in storage-architecture memory + repo docs.

## Slice 11 — Docs truth pass
- SYSTEM.md corrections (backup rotation 7d not 14d; gate architecture; tick architecture as
  it ships); RUNBOOK rewrite (gate.py, restore, CBOE reality); GLOSSARY adds (Delayed, 485BXT,
  registration-effective vs listed, ghost-Listed, promote-only, dormant filing, engine tick);
  TARGET.md: shipped items moved to SYSTEM; LOG.md entries for this session; ADR 0011 status
  flip to accepted after Ryu sign-off.

## Slice 12 — discreet item (last, per standing instruction)
- Handled separately; not detailed here.

## Sequencing
0 ✅ → 1 → 2 → 3 (these three kill the active-failure classes) → 4 → 6 (backlog burn) →
5 (tick migration, shadow-mode) → 7 → 9 → 10 → 11 → 8 → 12.
CBOE (high cluster) is gated on the WAF/IP decision — RYU_DECISIONS #9 — independent of slices.

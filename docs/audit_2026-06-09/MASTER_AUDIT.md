# MASTER AUDIT — Engine Session 2026-06-09

> The capstone of the full-system audit: 73-agent workflow sweep (14 subsystems, 171 defects,
> 53 adversarially-confirmed hot findings) + live VPS/D:/Render recon. Companion files:
> `FINDINGS_DIGEST.md` (every finding with evidence), `OPERATIONAL_RECON.md` (live-infra evidence).
> Verification discipline: every critical/high claim was independently re-checked by an
> adversarial verifier against code/data; 4 plausible findings were refuted and excluded.

## The four confirmed CRITICALS

### C1. The send pipeline will silently ship stale reports (FIXED this session)
`scripts/send_all.py:_build_stock_recs` imported `trex_combined_v9.main` — which does not exist
(entrypoint is `build()`). The ImportError fell into the "last good file" fallback on every
send, so the freshness rebuild NEVER ran; Tuesday's send worked only because the file had been
hand-baked minutes earlier. **Fixed: import `build`; entrypoints verified by import-test.**

### C2. Send-gate architecture: 4 flags × 2 stores = the standing outage machine
Live prod state: `send_enabled=0` (intended lock), **`send_paused=1` since 2026-05-26**
("Tue post-Memorial pause; not ready" — forgotten; kills preflight auto-GO whenever sends resume),
`preflight_maintenance=1` (BOTH as DB flag and as `data/.preflight_maintenance` dotfile, set
2026-05-11, suppressing strict classification gating for a month), `autogo_on_warn=1`.
Each flag exists in a DB table AND a legacy dotfile with priority fallback logic
(`preflight_check.py:771-778`). This double-bookkeeping caused the 35-day dark period
(file gate vs DB flag divergence) and the month of suppressed gating.
**Engine fix (slice 2): one gate store (system_flags), delete the dotfile layer, every flag gets
max-age alarm in morning triage. Flag flips themselves = Ryu decisions (see RYU_DECISIONS.md).**

### C3. RUNBOOK red-button procedures are no-ops
The emergency procedures (kill-switch / unpause) still instruct `touch`/`rm` of dotfiles that
nothing reads anymore after the DB-first system_flags cutover. In an incident, following the
runbook does nothing. **Fix in docs slice; pairs with C2's single-store cutover.**

### C4. Credential hygiene in the public repo
Live credential literals sit in HEAD of tracked files (5 files). Handling per standing
instruction: separately and discreetly, at the end of the execution plan, not detailed here.

## Confirmed HIGH clusters (37 findings — full list in FINDINGS_DIGEST.md)

**Backup/restore (5):** no restore procedure exists anywhere; all backups on the same VPS volume
as the live DB (prune chained AFTER backup so disk-full kills cleanup too); backup failure is
structurally silent (no OnFailure, watchdog email dies on failure, freshness assertion's glob
matches rollback snapshots and even partials); the 15-min disk-hygiene cron deletes the rollback
snapshots scripts deliberately create (the "restore is one cp away" layer has a ≤15-min lifetime);
both PowerShell D-drive sync scripts hot-copy the live WAL-mode DB via scp → torn copies in
D:\sec-data\backups that look authoritative. The off-VPS leg depends entirely on the laptop being
online; >8-day absence permanently loses dailies.

**CBOE (3+):** scanner dead 27+ days, data frozen at 2026-05-08/13; the 403 is Cloudflare WAF
(IP-level), misclassified as cookie expiry by error string, banner, AND runbook — the prescribed
fix (cookie rotation) cannot work; mkt_master_data join broken since flagged CRITICAL in the
2026-05-11 audit (F2), still unfixed.

**Classification (4):** the proposal review loop is dead — 1,669 pending proposals, exactly 1
ever approved; 81 L&I proposals stuck in the queue while live L&I competitors go untracked;
37 unclassified new launches accumulated behind the maintenance flag (incl. REX's own TLDR US);
20 NULL issuer_display; rules CSVs drift between data/rules, config/rules, and the VPS copies.

**13F (2):** the dataset has effectively vanished from every controlled location and Q1 2026 was
never ingested; the quarterly systemd timer runs `backfill` (re-tags, ingests nothing) — the
growth path was never wired.

**recommendation_history (3-4):** the table is EMPTY (0 rows) in prod despite the Sunday grading
loop and a "completed" backfill task — the L&I track-record feature (report section 9) is running
on air; grading writes are not landing.

**Scraper (3):** step3's effective-date/ticker body extraction fragility; async_client fragile
fallback; bulk_loader gaps. (Tier-1/2/3 watcher architecture itself verified sound.)

**Orchestration (4):** six maintenance steps exist only as ExecStartPost of the Mon-Fri Bloomberg
chain (skipped silently if ExecStart raises or overruns 1800s); warn-vs-fail exit-code conflation
marks healthy services failed (morning-triage, classification-sweep) AND suppresses the triage
email exactly when it matters; the 4-hourly "sec-scrape" unit is a misnamed 12-step monolith
(scrape→bake→upload) with no per-step failure isolation; pipeline_summary cron dead since 5/26.

**Send (5):** beyond C1/C2 — recipient misroute risk on unknown list_types, no exactly-once
guarantee around mid-send crashes, duplicate-send guard granularity.

**Webapp/Render:** RENDER_UPLOAD_TOKEN lives in gitignored config/.env → every Render deploy
breaks the screener-cache upload (503 since 6/8 16:14, public screener stale ~2 days). DB upload
died mid-compress at 12:06 today (watch 16:00 run). [Webapp mapper re-running; findings to be
appended.]

**Docs (4):** SYSTEM.md claims a 14-day backup rotation (actual: 7); RUNBOOK gate procedures
no-op (C3); GLOSSARY missing the new effectiveness vocabulary (Delayed, 485BXT, registration-
effective vs listed, ghost-Listed, promote-only sync); TARGET.md still lists shipped items as
future.

**Version control (closed this session):** the entire deployed surface was unversioned — the
nightly L&I engine (run_v1.py), Monday report builders, GAP-08 disk fix, db_writer dedup guard,
and current rules CSVs existed ONLY on the VPS disk. **All rescued into branch
`worktree-fix-rex-family-2026-06-08` (commits 49b356a + 1542435); branch now supersedes every
dirty tracked file on the VPS except the daily Bloomberg xlsm (binary, to be untracked).**

## Refuted by adversarial verification (excluded — important negatives)
1. "Tier-3 reconciler diffs against a permanently-stale cached index" — SEC publishes the daily
   index once, post-close; the no-TTL cache is semantically correct here; 938 reconciler-sourced
   alerts prove the net works.
2. "253 DELAYED rows missing the 485BXT date" — 252/253 are 485APOS-with-delaying-amendment,
   where NULL is the CORRECT value; not a defect.
3. "7 Listed T-REX funds have is_rex=0" — 6 of 7 are competitor funds correctly flagged
   (Direxion/GraniteShares/Hartford); "fixing" them would inject ~$204M of competitor AUM into
   REX KPIs. Real residual: 6 phantom 'Listed' rex_products rows whose tickers collide with
   competitor funds (separate medium finding), plus the AXTU-class new-launch gap already
   patched in 15c63e8.
4. "Reconciler parasitic on Bloomberg pull success" — the Graph download never raises; post-steps
   run even on pull failure. Surviving kernel (low): a raising ExecStart or 1800s overrun skips
   six maintenance steps for a day.

## Already verified healthy (don't fix what isn't broken)
Two-tier realtime detection (atom_watcher 60s + single_filing_worker 30s) + Tier-3 daily
reconciler + Tier-4 weekly discovery; manifest-gated step3 with retry caps; SEC rate limiting
(sync pause + async 8 req/s limiter); the 2026-06-09 effectiveness overhaul (re-verified end to
end by fresh agents); db-backup timer running; send gate CLOSED as intended; Render site up.

## Where everything lands
- Fix execution: `ENGINE_PLAN.md` (slices, owners, verification per slice)
- Ryu-only decisions: `RYU_DECISIONS.md`
- Architecture: `docs/DECISIONS/0011-engine-architecture.md` (proposed)

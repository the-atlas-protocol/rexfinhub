# Stage 2 — Full-Scale Fix Execution Plan

Generated: 2026-05-11 20:41 ET
Source: 18 Stage 1 audits, ~200 findings, 32 criticals, 9 architectural root causes

## Wave 1 — Parallel fix execution (~75 min)

Each implementer fix runs in an isolated git worktree. VPS-side fixes run via SSH. No two streams edit the same file. Each writes a fix doc to `docs/audit_2026-05-11/fix_<id>.md` with: diff summary, verification, rollback.

| Stream | Type | Surface | Worktree branch |
|---|---|---|---|
| **R1** | VPS sysadmin (ssh) | Swap active systemd: bloomberg.service → bloomberg-chain.service. Install missing units (13f-quarterly, parquet-rebuild, classification-sweep ExecStartPost). | n/a |
| **R2** | Implementer (worktree) | Extend `write_classifications()` to write primary_strategy/asset_class/sub_strategy to mkt_master_data. Add 3-axis fields to Classification dataclass. | `audit-fix-R2-writer` |
| **R3** | Implementer (worktree) | Tighten body-extractor ticker regex; expand `_BAD_TICKERS`; add validation against ticker universe; add manifest "successful=non-zero" check. | `audit-fix-R3-extractor` |
| **R5** | Implementer (worktree) | Fix `mkt_report_cache` staleness (pass pipeline_run_id everywhere); add writer_model to FilingAnalysis UNIQUE; add staleness banner. **Cache flush itself happens in Wave 2 after upstream fixes land.** | `audit-fix-R5-cache` |
| **R6** | Implementer (worktree) | Flip `classify_engine.py` RULES_DIR to config/rules/; merge 60+ orphan tickers; delete data/rules/; add issuer-aware joining to step6_apply_category_attributes. | `audit-fix-R6-csv` |
| **R7** | Implementer (worktree) | Pin `Environment=TZ=America/New_York` on every systemd unit; patch send_email.py to capture TZ-aware today once; fix `market_sync.py:537` to pass as_of_date; add TZ labels to template renders. | `audit-fix-R7-tz` |
| **R8** | Implementer (worktree) | Rotate ADMIN_PASSWORD (Ryu sets); add CSRF middleware to admin POSTs; add rate limit + IP allowlist + audit log on `/api/v1/db/upload`. | `audit-fix-R8-auth` |
| **R9** | VPS sysadmin (ssh) | Install sqlite3, fix db-backup script (verify backup files actually create), rotate CBOE cookie (manual touchpoint), enable fail2ban, restore VPS recipients .txt fallback files, tune preflight thresholds to allow GO when expected categories pass. | n/a |

## Wave 2 — Integration (~45 min, sequential, coordinator-led)

1. Merge each Wave 1 branch to main; resolve conflicts (likely R5/R7 vs main on settings)
2. Push to main → VPS auto-pulls on next service start (or trigger immediately)
3. SSH VPS: restart bloomberg-chain.service, run apply_classification_sweep + apply_issuer_brands + apply_fund_master
4. **Flush `mkt_report_cache` and rebuild from current pipeline_run** (R5's data step)
5. Re-run preflight; verify NULL primary_strategy down + NULL issuer_display down

## Wave 3 — Independent verification (~20 min, parallel)

3 reviewer agents validate the 6 code-changing fixes (R2, R3, R5, R6, R7, R8) for correctness, regression risk, rollback completeness.

## Wave 4 — Send (~15 min, sequential)

1. Re-run preflight on VPS
2. If overall_status=pass → trigger send_all manually (bypass GO/HOLD requirement for one-off rebuild)
3. Verify recipients received with correct numbers + correct subject date
4. Report deliverables to Ryu

## Deferred (off-cycle, not tonight)

- **R4 — Schema UNIQUE migration**: drop trust_id from `uq_fund_status`. Requires dedup of 55,694 cross-trust replicated rows + migration script + rollback. Too risky to land mid-flight; needs proper planning and a staging cycle.

## Hard constraints (binding on every agent)

1. **No edits to main directly.** Every code change goes through a worktree branch.
2. **No destructive ops without an explicit reversibility note.** No DROP TABLE, no DELETE without WHERE, no schema migration without rollback.
3. **Every fix has a verification step.** SQL query showing the bad value before, then after; or template render check; or systemd `systemctl status` confirmation.
4. **No new features.** Fix only.
5. **Stay in your worktree.** No editing files assigned to other streams.

## Critical path for tonight's send

R1 + R2 + R6 + R7 must land cleanly before Wave 2 step 3 (sweep). R5 cache flush must run AFTER sweep. Then preflight should pass.

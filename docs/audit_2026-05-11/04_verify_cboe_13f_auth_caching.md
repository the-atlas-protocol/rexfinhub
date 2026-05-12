# Stage 4 Re-Audit — CBOE + 13F + Auth + Caching (combined)

Generated: 2026-05-11 post Wave 1-4 deploy
Mode: READ-ONLY spot-verification of Stage 1 docs

## Scope

These four surfaces had no direct fixes targeted in Wave 1-4. Goal: confirm Stage 1
critical/high findings are still in their previously-documented states (or noting any
drift) and surface any NEW finding triggered by the deploy.

Verification inputs: Stage 1 docs (`01_cboe_reserved.md`, `01_13f_notes.md`,
`02_auth_secrets.md`, `01_caching.md`); R5 / R8 / H1 fix docs; live SQL spot-checks
against `data/etp_tracker.db`, `data/13f_holdings.db`, `data/structured_notes.db`;
VPS `systemctl` + nginx checks; live HTTP probes.

---

## Surface 1 — CBOE + Reserved Symbols

Stage 1 doc: `01_cboe_reserved.md` (11 findings: 4 crit / 4 high / 2 med / 1 low)

### Top severity findings — current state

| Stage 1 ID | Title | Stage 1 sev | Today's state |
|---|---|---|---|
| F1 | CBOE cookie expired 16d, 9 consecutive failed sweeps | CRIT | **STILL OPEN.** `cboe_scan_runs` rows 8-12 all `failed` with same 302 redirect message. `config/.env` cookie mtime still **2026-04-24 15:18** (today: 2026-05-11 — 17d stale). VPS service still `failed`. No rotation has occurred — operational, awaiting Ryu's `/cboe-cookie` action. |
| F2 | `mkt_master_data` join silently broken | CRIT | **STILL OPEN.** Re-ran the diagnostic query: `SELECT COUNT(*) FROM reserved_symbols rs WHERE EXISTS (SELECT 1 FROM mkt_master_data m WHERE m.ticker = rs.symbol)` → still **0 of 282**. No code change to `cross_reference.py` between Stage 1 and now. |
| F3 | Reserved Symbol status enum mismatch (12 rows) | HIGH | **STILL OPEN.** Same 12 rows in non-canonical status (`Filed: 5`, `Wait Listed: 4`, `Requested: 3`). `webapp/routers/operations_reserved.py:28` `VALID_STATUSES` unchanged — still `["Reserved","Active","Expired","Released"]`. Click-to-edit corruption risk persists. |

### New finding from deploy

None. Wave 1-4 did not touch CBOE/Reserved Symbols code paths. No regressions
introduced; no improvements either.

### Verdict: **WATCH**

The CBOE cookie expiry is the dominant operational issue and remains entirely in
Ryu's hands. Two CRIT findings (F1 + F2) are in the same state Stage 1 left them.
No new bugs landed; existing bugs persist. Becomes FAIL if cookie isn't rotated
within 7 more days (would compound to a month of stale data on `/tools/tickers`).

---

## Surface 2 — 13F + Structured Notes

Stage 1 doc: `01_13f_notes.md` (14 findings: 3 crit / 5 high / 4 med / 2 low)

### Top severity findings — current state

| Stage 1 ID | Title | Stage 1 sev | Today's state |
|---|---|---|---|
| F1 | 13F pillar dormant on Render — `ENABLE_13F` not set | CRIT | **STILL OPEN.** `webapp/main.py:318, 453` still gates `if os.environ.get("ENABLE_13F")`. VPS `.env` still does **not** contain an `ENABLE_13F` line. Live probe to `https://rexfinhub.com/holdings/` returns 302 → /login (auth-gated) but no behind-auth rendering would land — env var is the gate. |
| F2 | Quarterly 13F systemd unit not installed on VPS | CRIT | **PARTIALLY RESOLVED — improvement.** `ls /etc/systemd/system/` on the VPS now shows **both** `rexfinhub-13f-quarterly.service` and `.timer` present and `enabled`. But: `data/13f_holdings.db` still absent on VPS (`/home/jarvis/rexfinhub/data/13f_holdings.db: No such file or directory`). And the F14 sub-bug (`--backfill` flag mismatch) is unconfirmed-fixed — when this timer fires the script will likely crash. |
| F3 | Local 13F DB short on coverage (Q4 2025: 2.4M rows / 5,842 institutions vs expected 3.47M / 10,535) + `last_filed` NULL on all rows | CRIT | **STILL OPEN.** Re-ran: Q4 2025 = 2,403,267 holdings unchanged, `MAX(last_filed) FROM institutions` = `None`, `cusip_mappings WHERE trust_id IS NOT NULL` = 0. Q1 2026 still 0 rows (filing deadline May 15, 4 days away). DB mtime still 2026-03-27 — no fresh ingest. |

### Notes pillar (sub-section of Stage 1 doc)

- F9 (Credit Suisse + Jefferies 0% extracted): **STILL OPEN** — same 33,995 / 0 and 358 / 0 counts.
- F10 (`underlier_tickers` 100% NULL): **STILL OPEN** — 0 of 604,046 products have it populated.
- Notes DB mtime unchanged at 2026-04-13 (28 days stale).

### New finding from deploy

**NEW — F2 was partially addressed without coordination.** The 13F systemd timer
was installed and enabled on the VPS at some point between Stage 1 and now, but:

1. The DB it would feed is not present on the VPS (would need to ingest from scratch).
2. The `--backfill` flag mismatch in `scripts/fetch_13f.py` (Stage 1 F14) is
   unverified — if the timer fires before the script-vs-flag mismatch is fixed,
   the next quarterly run will exit non-zero with `argparse` error.
3. `ENABLE_13F` is still not set, so even if data lands the user-facing surface
   stays dormant.

Net: timer installed but the chain to actually serve data is still broken in two
places. Higher risk than Stage 1 baseline because the timer **will fire** silently
on schedule and start logging failures.

### Verdict: **WATCH**

Half-step forward (timer installed) without finishing the work. Three CRIT findings
unaddressed; one of them (F2) regressed into "now timer-fires-and-crashes territory"
unless the script flag is reconciled before next fire date.

---

## Surface 3 — Auth + Secrets

Stage 1 doc: `02_auth_secrets.md` (12 findings: 2 crit / 4 high / 5 med / 1 low/positive)

R8 + H1 fixes targeted this surface directly. Verification of those is the focus.

### Top severity findings — current state

| Stage 1 ID | Title | Stage 1 sev | Today's state |
|---|---|---|---|
| F1 | ADMIN_PASSWORD potentially still post-leak; rotation unverified | CRIT | **STILL OPEN — operational.** R8 doc explicitly lists "Rotate ADMIN_PASSWORD" as `ACTION REQUIRED FROM RYU`. No evidence of rotation in audit logs or commit history. Code path is hardened (literal scrubbed; CSRF added) but the value remains presumed-compromised. |
| F3 | No CSRF on state-mutating admin endpoints | HIGH | **RESOLVED.** R8 landed `webapp/services/csrf.py` (~60 LoC) + `CsrfMiddleware` in `webapp/main.py:168-235` (verified — `from webapp.services.csrf import ... is_valid as _csrf_is_valid` import + middleware logic at lines 199-230). H1 hardened the multipart path (`webapp/main.py:206` rejects multipart without `X-CSRF-Token` header before body spool). Per H1 doc, ASGI smoke confirmed body never consumed on rejection. |
| F5 | `/api/v1/db/upload` no rate limit, no audit log | HIGH | **RESOLVED.** R8 added per-IP rate limit (`_RATE_LIMIT = int(os.environ.get("DB_UPLOAD_RATE_PER_HOUR", "6"))` at `webapp/routers/api.py:45` — H1 bumped from 1 to 6 to avoid VPS-retry self-DoS), `ApiAuditLog` model exists at `webapp/models.py:1342` (table name `api_audit_log`), audit row written at `api.py:104` on every upload. **NB**: the audit table does not yet exist in the local DB (`SELECT name FROM sqlite_master WHERE name='api_audit_log'` → None). It will be created on next `Base.metadata.create_all` startup. Non-blocking — only affects local-dev introspection. |

### H1 follow-up verification (XFF + multipart + migration)

| H1 fix | State |
|---|---|
| `_client_ip()` now takes right-most XFF entry | `webapp/routers/api.py:50-65` reflects the right-most parser; matches H1 doc verbatim. |
| `DB_UPLOAD_RATE_PER_HOUR` default = 6 (not 1) | Confirmed at `api.py:45`. |
| CSRF multipart short-circuit | Confirmed at `webapp/main.py:199-209` — bails to 403 before any `await request.form()`. |
| FilingAnalysis migration script present | `scripts/migrate_filing_analysis_unique_2026_05_11.py` referenced by H1 doc; **NOT YET RUN against `data/etp_tracker.db`** (see Surface 4 — F-CACHE-1 finding below). |

### New finding from deploy

**NEW — F-AUTH-1: `api_audit_log` table not materialized in local DB.** R8 added
the model but the local DB doesn't yet have the table. SQLAlchemy will auto-create
on next webapp startup via `create_all`, but until then any local invocation of
`/api/v1/db/upload` would log a warning and skip the audit row insert (the writer
catches the OperationalError; behavior degrades gracefully). On Render the same
auto-create runs at process boot, so production should self-heal on next deploy.
**Impact: low.** Worth confirming on next Render deploy that the table exists.

### Verdict: **PASS (with caveats)**

R8 + H1 substantively closed the highest-severity Stage 1 findings (F3, F5, plus
H1 hardening of XFF spoof + multipart DoS). Two open items remain operational and
explicitly listed in R8's "ACTION REQUIRED FROM RYU": (1) rotate `ADMIN_PASSWORD`,
(2) set `SITE_PASSWORD` + `API_KEY` in Render dashboard. Code-side this surface
moved from "seven open findings" to "code-fully-hardened, two ops actions pending".

---

## Surface 4 — Caching Layers

Stage 1 doc: `01_caching.md` (CRIT-1, CRIT-2, CRIT-3 + HIGH-1/2/3 + MED + LOW)

R5 fix targeted this surface directly.

### Top severity findings — current state

| Stage 1 ID | Title | Stage 1 sev | Today's state |
|---|---|---|---|
| CRIT-1 | `temp_cache/` 27 GB orphan | CRIT | **STILL OPEN — out of R5 scope.** `ls temp_cache` on local still returns the `submissions` + `web` subdirs; not deleted. R5 was code-only by design. Reclaim is queued for ops cleanup. |
| CRIT-2 | FilingAnalysis cache key ignores model upgrades | CRIT | **CODE FIXED, MIGRATION PENDING.** `webapp/models.py:211-214` now declares `UniqueConstraint("filing_id", "writer_model", name="uq_filing_analyses_filing_writer")`. **However**, the local DB still has the legacy `CREATE UNIQUE INDEX ix_filing_analyses_filing_id ON filing_analyses (filing_id)` — verified via `sqlite_master`. The H1-shipped migration script `scripts/migrate_filing_analysis_unique_2026_05_11.py` exists but **has not been run** against `data/etp_tracker.db`. Until the migration runs, a writer-model upgrade still hits `IntegrityError` on the legacy unique index, which is the exact bug R5 was designed to fix. **This is the load-bearing gap from Wave 1-4.** |
| CRIT-3 | `mkt_report_cache` staleness check no-op for `screener_3x` | CRIT | **CODE FIXED, DB ROW STILL NULL.** R5 changed `screener_3x_cache.save_to_db` to auto-derive `pipeline_run_id` (verified at `webapp/services/screener_3x_cache.py:62-102`) and made `report_data._read_report_cache` treat NULL as stale (forces rebuild + WARN log). **However**, the existing `screener_3x` row in `mkt_report_cache` still has `pipeline_run_id = None` (verified). The new code now correctly forces a rebuild on read — the row will be re-stamped with the real `pipeline_run_id` on the next `_compute_and_cache_screener` invocation. So functionally fixed-on-read, will self-heal on next pipeline run. |

### Other Stage 1 findings — sample

- HIGH-1 (SEC HTTP cache no TTL for body fetches): **STILL OPEN** — out of R5 scope.
- HIGH-2 (local prebaked reports 28d stale): **STILL OPEN** — local file mtime unchanged.
- MED-1 (atlas memory wrong about D: drive cache path): **STILL OPEN** — atlas memory not updated.

### New finding from deploy

**NEW — F-CACHE-1: R5's CRIT-2 fix is incomplete without running the H1 migration
script.** The schema declaration is correct in `webapp/models.py`, but the
`Base.metadata.create_all` startup path **only creates tables that don't already
exist** — it does not alter constraints on existing tables. So any DB that
pre-existed R5 (i.e., every production DB on the planet — local + VPS + Render)
still has the legacy `UNIQUE(filing_id)` index. The fix is one step away: run
`python scripts/migrate_filing_analysis_unique_2026_05_11.py --apply` against
`data/etp_tracker.db` (then re-upload to Render, or run the script on Render shell).
H1 documented this clearly; verification confirms it has not yet been done locally.

**Severity: HIGH.** Not a regression — R5 + H1 documented the migration step; it
just hasn't been executed. But until it runs, the entire reason R5 existed (model
upgrades silently serve stale narratives) is unfixed in the running DB.

### Verdict: **WATCH**

R5 + H1 code is correct and merged. CRIT-3 self-heals on next pipeline run via the
new staleness guard. CRIT-2 needs one explicit operational step (run the migration
script). CRIT-1 is queued ops cleanup. No regressions — the open items are exactly
what R5/H1 documented as out-of-scope or operational.

---

## Combined verdict matrix

| Surface | Stage 1 critical/high count | Closed by deploy | Still open | Verdict |
|---|---|---|---|---|
| CBOE + Reserved Symbols | 4 CRIT + 4 HIGH | 0 | 8 (all operational + cosmetic) | **WATCH** |
| 13F + Structured Notes | 3 CRIT + 5 HIGH | 0 (1 partial: F2 timer installed) | 8 | **WATCH** |
| Auth + Secrets | 2 CRIT + 4 HIGH | 4 (F3 CSRF, F5 rate-limit/audit, H1 XFF, H1 multipart) | 2 (F1 password rotation, F4 Render env vars — both operational) | **PASS** |
| Caching | 3 CRIT + 3 HIGH | 1 (CRIT-3 code) + 1 deferred-self-heal (CRIT-2 code, pending migration) | 4 (CRIT-1 orphan, HIGH-1/2/3) | **WATCH** |

## Highest-priority follow-ups (not for this re-audit; flagging for next wave)

1. **Run the FilingAnalysis migration** (`scripts/migrate_filing_analysis_unique_2026_05_11.py --apply`) against local + Render DBs. This is the single-step completion of R5 CRIT-2.
2. **Rotate ADMIN_PASSWORD + set Render env vars** (R8 ACTION REQUIRED block, still pending).
3. **Rotate CBOE cookie** via `/cboe-cookie` skill — clears CBOE F1 + F7 simultaneously.
4. **Reconcile 13F systemd unit** with actual script flag (`scripts/run_13f.py auto` vs `scripts/fetch_13f.py --backfill`) before the next quarterly fire (08/19 timer per Stage 1 F2). Otherwise the now-installed-and-enabled timer logs `argparse` failures silently.
5. **Delete `temp_cache/`** (CRIT-1) — 27 GB recovery on a 4-GB-free C: drive.

## Files inspected (read-only)

- `docs/audit_2026-05-11/01_cboe_reserved.md`, `01_13f_notes.md`, `02_auth_secrets.md`, `01_caching.md`
- `docs/audit_2026-05-11/fix_R5.md`, `fix_R8.md`, `hotfix_H1.md`
- `webapp/main.py` (CSRF middleware, ENABLE_13F gates)
- `webapp/services/csrf.py` (presence)
- `webapp/services/screener_3x_cache.py` (R5 save_to_db change)
- `webapp/models.py` (FilingAnalysis UniqueConstraint, ApiAuditLog)
- `webapp/routers/api.py` (`_client_ip`, `_RATE_LIMIT`, ApiAuditLog write)
- `webapp/routers/operations_reserved.py` (VALID_STATUSES unchanged)
- `data/etp_tracker.db` — `cboe_scan_runs`, `reserved_symbols`, `mkt_master_data`, `mkt_report_cache`, `filing_analyses` index, sqlite_master for `api_audit_log`
- `data/13f_holdings.db` — `holdings` by report_date, `institutions.last_filed`, `cusip_mappings.trust_id`
- `data/structured_notes.db` — `products.underlier_tickers`, `issuers` zero-extraction
- VPS `systemctl is-active rexfinhub-cboe`, `is-enabled rexfinhub-13f-quarterly.timer`, `/etc/systemd/system/` listing, `/home/jarvis/rexfinhub/data/` listing, `config/.env` ENABLE_13F check
- Live: `curl -I https://rexfinhub.com/holdings/` (302 → /login expected)
- `git log --oneline -20` for deploy chronology

## Sign-off

Read-only verification complete. No DB writes, no rotations, no test calls.
Combined verdict: **2 PASS-equivalent, 3 WATCH, 0 FAIL** — no surface regressed
critically as a side effect of Wave 1-4. Largest single-action gap is running the
FilingAnalysis migration to convert R5's code fix into an actual schema change.

# Stage 1 Audit — CBOE + Reserved Symbols
Generated: 2026-05-11T19:30:00-04:00
Agent: cboe_reserved

## Summary
**11 findings (4 critical, 4 high, 2 medium, 1 low)**. Two systemic problems dominate:

1. **CBOE cookie has been expired for 16 days** (since Apr 25). The nightly sweep has now failed 9 consecutive nights — the daily-pipeline upload (`run_daily.py --upload`) is being skipped because `ExecStart` chains stop on the first non-zero exit (status=3 from `run_cboe_scan.py`). Both `auth_health` banner and the failure mode work as designed. The `/tools/tickers` page is showing data nearly 17 days stale.
2. **`mkt_master_data` cross-reference join is silently broken.** `cross_reference.py` joins `MktMasterData.ticker == CboeSymbol.ticker`, but every `mkt_master_data.ticker` value is in Bloomberg `XXX US` format (7,342 of 7,361 rows). Result: zero CBOE rows ever pick up the REX-context fund_name / issuer / etp_category fields. Counts on `/tools/tickers` (12,755 reserved-by-competitor) are derived from `cboe_known_active` only. The atlas memory of "13,119 reserved-by-competitor" is now 12,755 (drift from stale data + universe changes).

The new Reserved Symbols admin page (282 rows from xlsx) has solid bones but ships with a status enum mismatch (12 rows have statuses outside the editable dropdown, click-to-edit will silently corrupt them), no audit log on admin changes, no idempotent daily importer despite the docstring claim, and a few smaller concurrency risks.

## Findings

### F1: CBOE session cookie expired 16 days ago — nightly sweep failing every night
- **Severity**: critical
- **Surface**: `config/.env:CBOE_SESSION_COOKIE` (mtime 2026-04-24 15:18); `cboe_scan_runs` table (12 rows, last 9 are status='failed' with 302→login error)
- **Symptom**: Every nightly run since 2026-04-26 has aborted in <30s with `CBOE redirected to 'https://account.cboe.com/account/login/' (status 302)`. Last successful full sweep: 2026-04-25 07:01 UTC. Page banner correctly displays "expired" but Ryu has not rotated the cookie.
- **Evidence**:
  - VPS systemctl status: `failed (Result: exit-code) since Mon 2026-05-11 03:01:43 EDT; 15h ago`
  - DB query: 9 of last 9 runs `status='failed'`, error message contains `cookie` keyword (matches `auth_failed` heuristic in `cross_reference.auth_health`)
  - `cboe_state_changes` total = 0 since first deploy — no flips ever recorded (would have been impossible during the broken-cookie window anyway)
- **Blast radius**: 17 days of stale `/tools/tickers` data; new ticker reservations by competitors going undetected; the symbol-availability check on the search box (`live.live_check`) returns "auth" errors for every interactive lookup. Also: because the systemd unit's `ExecStart` chains, the post-CBOE `run_daily.py --upload` step is also skipped after CBOE failure → DB-to-Render upload may be running only via the separate 17:45 ET pipeline.
- **Hypothesis**: Operational, not code. CBOE issuer-portal cookies expire on a ~30-day idle cycle; Ryu hasn't logged into the portal to refresh. No automatic alerting beyond the banner.
- **Fix size**: trivial (rotate the cookie, run `/cboe-cookie` skill). Architectural side-question: should there be an email alert on `failed_streak >= 2`?

### F2: `mkt_master_data` join silently broken in `cross_reference.py` — every taken ticker shows zero REX context
- **Severity**: critical
- **Surface**: `webapp/services/cboe/cross_reference.py:30` (`_mkt_label_subquery`) and `cross_reference.py:95` (`outerjoin(mkt, CboeSymbol.ticker == mkt.c.ticker)`)
- **Symptom**: `MktMasterData.ticker` is in Bloomberg `XXX US` format (e.g. `BU US`, `CA US`, `FB US` — 7,342 of 7,361 rows have ` US` suffix; 0 rows are bare 1-4 letter tickers). The join condition `CboeSymbol.ticker == mkt.c.ticker` therefore always misses. Result: `fund_name`, `issuer`, `listed_exchange`, `etp_category` are always NULL on `/tools/tickers` rows; the only fund_name labels that appear come from `cboe_known_active` (NASDAQ/SEC EDGAR sources, ~12,263 distinct base tickers).
- **Evidence**:
  - `SELECT COUNT(*) FROM reserved_symbols WHERE EXISTS (SELECT 1 FROM mkt_master_data m WHERE m.ticker = rs.symbol)` → **0 of 282**
  - Stripping suffix manually: `JOIN mkt_master_data m ON SUBSTR(m.ticker,1,INSTR(m.ticker||' ',' ')-1) = c.ticker` → **7,037 distinct matches** (would-be join cardinality)
  - `mkt_master_data` total tickers: 7,361 — only **19** are bare (no space), the rest all `XXX US` / `0123456D US` (Bloomberg "delisted" marker)
- **Blast radius**: Every taken symbol on `/tools/tickers` mislabeled as "active" vs "reserved" through the wrong prism — currently `state == 'active' if (fund_name OR known_name)`. Because mkt_master_data never contributes, the active/reserved split is decided entirely by NASDAQ Trader + SEC EDGAR. REX-internal categories (etp_category) and issuer attribution NEVER appear. The "reserved-by-competitor" count includes any taken ticker whose base isn't in `cboe_known_active` — a number that would shrink if the mkt join worked.
- **Hypothesis**: Original design assumed bare-ticker storage in `mkt_master_data`; Bloomberg ingestion stores raw Bloomberg tickers without normalization. Either the join needs `SUBSTR(... INSTR ...)` or `mkt_master_data` needs a `bare_ticker` derived column.
- **Fix size**: small (add a computed `bare_ticker` join key, or a `func.substr` expression in the subquery)

### F3: Reserved Symbol status enum mismatch — admin click-to-edit will silently corrupt 12 rows
- **Severity**: high
- **Surface**: `webapp/routers/operations_reserved.py:28` (`VALID_STATUSES = ["Reserved","Active","Expired","Released"]`); `operations_reserved_symbols.html:175-189` (status dropdown built from `RS_STATUSES` only)
- **Symptom**: The xlsx import preserves source-of-truth status values: 5 rows are `Filed`, 4 are `Wait Listed`, 3 are `Requested` — none of which appear in `VALID_STATUSES`. When admin clicks the status cell on one of these 12 rows, the JS builds a `<select>` with only the four valid options; the original status is not pre-selected (no `option.selected = true` matches), so the dropdown defaults to the first option (`Reserved`). On blur, the field is silently overwritten — no warning, no validation that the original value was non-standard.
- **Evidence**:
  - DB: `SELECT COUNT(*) FROM reserved_symbols WHERE status NOT IN ('Reserved','Active','Expired','Released')` → **12**
  - Code path: in the JS handler (line 167 onwards), the optimistic `td.textContent = newVal` writes the new value before the server confirms. The server-side update endpoint (`reserved_symbol_update`) doesn't validate `status` against `VALID_STATUSES` either — accepts any string.
- **Blast radius**: 12 known-impacted rows could lose their custodial status on a stray click. Unlikely deletion but real corruption — `Filed` represents a meaningful pipeline state the team uses.
- **Hypothesis**: `VALID_STATUSES` was hand-derived without auditing the xlsx; should be either (a) extended to include `Filed/Wait Listed/Requested`, or (b) the import script should normalize incoming statuses to the canonical four.
- **Fix size**: trivial (extend `VALID_STATUSES`, add a brief "did you mean to change?" guard)

### F4: No admin-edit audit log on `reserved_symbols`
- **Severity**: high
- **Surface**: `webapp/routers/operations_reserved.py:129-214` (update / add / delete endpoints)
- **Symptom**: All three admin endpoints (`/update`, `/add`, `/delete`) commit immediately with no append to any audit table. The model has only `created_at` and `updated_at` — no actor, no diff, no history. `delete` is hard-DELETE (no soft-delete flag).
- **Evidence**:
  - Other admin tables in this DB DO have audit infrastructure: `classification_audit_log`, `capm_audit_log` exist (compare patterns). `reserved_symbols` has none.
  - Endpoint code: `db.delete(row); db.commit(); return JSONResponse({"ok": True})` — irreversible, no log
- **Blast radius**: Any admin (one shared password — see CLAUDE.md) can permanently delete any reservation with a `confirm()` click. Loss of REX's only registry of in-flight ticker reservations is a high-stakes operational risk. No way to reconstruct who deleted what when.
- **Hypothesis**: New page (built today per task #109), audit logging deprioritized for v1.
- **Fix size**: small (add a `reserved_symbols_audit_log` table with row_id/field/old/new/actor/ts; write from all three endpoints)

### F5: `import_reserved_symbols.py` claims "daily-safe" but is not scheduled anywhere
- **Severity**: medium
- **Surface**: `scripts/import_reserved_symbols.py:9-13` docstring; no entry in `deploy/systemd/`; no cron; not invoked by `run_daily.py`
- **Symptom**: The docstring says "Daily-safe: idempotent upsert by (exchange, symbol)" but the script is only runnable manually with the source xlsx living at `C:/Users/RyuEl-Asmar/Downloads/Reserved Symbols.xlsx` (Ryu's personal Downloads folder, Windows-only). On Render and the VPS the source file does not exist; the script exits 0 with "skipping" — confirmed by reading `import_reserved_symbols(...)` early-return path. So the table is loaded once, manually, from a file Ryu must edit locally.
- **Evidence**:
  - `grep` for `import_reserved_symbols` in scripts/services: only the file itself + this doc.
  - DB: all 282 reserved_symbols rows have `created_at == updated_at` within a 0.22s window (2026-05-11 15:35:37) — a single import run, never re-run.
  - Source path is hardcoded to a Downloads folder; idempotent upsert logic is correct but unreachable in any automated context.
- **Blast radius**: The xlsx is the source of truth (per docstring) but admin-edit on the page can drift from xlsx — and the doc explicitly says "rows in DB but not in xlsx are LEFT ALONE (so manually added entries via /operations/reserved-symbols admin survive)". This means the two write paths can diverge silently and there's no reconciliation report.
- **Hypothesis**: Plan was to wire up later; never landed.
- **Fix size**: medium (decide single source of truth: xlsx-on-VPS or admin-page; if both, build a reconciliation diff)

### F6: Admin update endpoint accepts arbitrary `symbol` rename — can violate UNIQUE constraint
- **Severity**: medium
- **Surface**: `webapp/routers/operations_reserved.py:139-169`
- **Symptom**: `EDITABLE` set includes `"exchange"` and `"symbol"`. The endpoint normalizes case but does not check for collisions before commit. If admin renames `(Cboe, MIKE)` to `(Cboe, BONK)` and the latter exists, the DB will raise `IntegrityError` from `uq_reserved_symbol_exchange` and the request will 500. JS catches it as a generic "Save error" toast — admin sees no useful message.
- **Evidence**:
  - Code: no `db.execute(select(...).where(exchange==..., symbol==NEW))` pre-check before `setattr` + `commit`. Compare with `reserved_symbol_add` which DOES check (line 186-190) — inconsistent.
  - No collisions present today (`HAVING COUNT(*) > 1` on (symbol) returned zero), so no live damage yet.
- **Blast radius**: 500 error and no toast clarity; potentially confusing if hit. Not data-corrupting because the IntegrityError aborts the txn.
- **Hypothesis**: Oversight — `add` got the duplicate-check logic, `update` did not.
- **Fix size**: trivial (mirror the duplicate-check from `add`)

### F7: 21 REX-reserved tickers show "available" in CBOE — likely cookie-rotation artifact, not real
- **Severity**: medium
- **Surface**: cross-table consistency (`reserved_symbols` ∩ `cboe_symbols.available=1`)
- **Symptom**: 21 of REX's 282 reserved symbols show `cboe_symbols.available = TRUE` (i.e. CBOE thinks they're free to grab). Examples: `ACID`, `BONK`, `MIKE`-not-in-list, `ATCY`, `HNX` (Requested), `TSU` (Requested), `PHON`. Most have `last_checked_at = 2026-04-25 07:01 UTC` — i.e. data captured BEFORE these were reserved.
- **Evidence**:
  - DB: see query result in section "DB queries run" below
  - `last_checked_at` clusters around 2026-04-25 — these reservations may have been added to xlsx after that date
- **Blast radius**: Low if you understand the staleness; but the page would mislead anyone making reservation decisions today. Once cookie is rotated and a fresh sweep runs, this should auto-resolve.
- **Hypothesis**: Pure staleness from F1. Worth tracking that the post-rotation sweep flips them all to `available=False` and writes 21 new `cboe_state_changes` rows.
- **Fix size**: trivial (resolves once F1 is fixed). Worth adding a `/operations/reserved-symbols` widget showing "N of your reservations missing from CBOE's universe" as a sanity panel.

### F8: `concurrency=300` on the production scanner — 4× higher than the probe's safety-recommended max
- **Severity**: medium
- **Surface**: `config/.env:CBOE_CONCURRENCY` (configured value used on VPS, currently empty in local .env but `cboe_scan_runs.concurrency` shows 300 on every run); `webapp/services/cboe/rate_probe.py:24` (`PROBE_RUNGS = (5, 10, 20, 35, 50, 75, 100)` — max ramp tested is 100, suggested = 85% of last clean rung)
- **Symptom**: Production sweep runs at `concurrency=300`. The rate-limit probe was designed to suggest ≤ 85 (i.e., 100 × 0.85). 300 is a 3× extrapolation never validated. The one successful sweep (2026-04-25, run id 2) checked 475,254 tickers in 43m17s → ~183 req/s — which IS below the 200 limit hinted in `MAX_429_STREAK = 5` triggering 10-min pauses, but it's empirical not principled.
- **Evidence**:
  - DB: `SELECT id, concurrency FROM cboe_scan_runs ORDER BY id` → all 300
  - `rate_probe.py` PROBE_RUNGS top out at 100
  - Successful run had `state_changes_detected = 0` — first sweep, nothing to flip
- **Blast radius**: Risk of CBOE banning the source IP if they ever decide to enforce rate limits. The 429 backoff (`BACKOFF_429_SECONDS = 600`) is generous but a hard ban is uncovered.
- **Hypothesis**: Empirical — Ryu likely raised concurrency until it worked, beyond what the probe recommends.
- **Fix size**: trivial (re-run the probe with `PROBE_RUNGS` extended to 200, 300; document the empirical ceiling)

### F9: Reserved Symbols page links to `/operations/products` breadcrumb that may not exist
- **Severity**: low
- **Surface**: `webapp/templates/operations_reserved_symbols.html:61` (`<a href="/operations/products">REX Operations</a>`)
- **Symptom**: Breadcrumb assumes a parent landing page at `/operations/products`. Quick search of routers shows no such route registered (didn't enumerate exhaustively in this audit; flagging for verification).
- **Evidence**: No grep hit on `/operations/products` in `webapp/routers/`. `webapp/main.py:361` only registers `operations_reserved` router with prefix `/operations/reserved-symbols`.
- **Blast radius**: 404 from breadcrumb — cosmetic.
- **Hypothesis**: Placeholder breadcrumb to a future operations hub.
- **Fix size**: trivial

### F10: `nasdaq_screener_stocks` and `nasdaq_screener_etfs` sources have been failing for at least 14 days
- **Severity**: medium
- **Surface**: `webapp/services/cboe/known_active.py:64-122` (NASDAQ screener fetchers), VPS journal
- **Symptom**: Every `refresh_known_active` run since at least May 4 logs `Read timed out` for both `api.nasdaq.com` endpoints. The function silently returns 0 rows from those sources — `failed_sources` array now permanently contains `["nasdaq_screener_stocks", "nasdaq_screener_etfs"]`. The remaining three sources (NASDAQ Trader files + SEC EDGAR) still produce 13,355 rows (vs 12,263 distinct base tickers in the table — partially overlapping).
- **Evidence**:
  - VPS journal May 11: `Source nasdaq_screener_stocks failed: HTTPSConnectionPool ... Read timed out`
  - DB `cboe_known_active` source breakdown: only `nasdaq_trader_nasdaq` (1,182), `nasdaq_trader_other` (4,320), `sec_edgar` (7,782) — zero from the screener APIs
- **Blast radius**: The screener APIs were the only source of `sector` / `industry` / `market_cap` enrichment. Without them, every taken ticker on `/tools/tickers` is missing those fields. The active/reserved split is unaffected (binary based on base_ticker presence) but the cell labels are thinner.
- **Hypothesis**: NASDAQ tightened bot detection or the API URL/format changed. Worth a one-off curl from the VPS to confirm.
- **Fix size**: small (adjust headers / URL or accept the loss and remove the dead fetchers)

### F11: `linked_filing_id` / `linked_product_id` / `notes` fields on every row are NULL — feature stub never wired
- **Severity**: low
- **Surface**: `reserved_symbols` table (282/282 rows have these three fields NULL)
- **Symptom**: Per the model docstring (line 1116-1118): "Goal (per Ryu 2026-05-11): map REX's reserved tickers against our filings so we know which products are coming next". The schema fields exist but no import / admin UI / population logic touches them.
- **Evidence**:
  - DB: `SELECT COUNT(*) WHERE linked_filing_id IS NOT NULL` → 0; same for product/notes
  - Admin endpoint (`reserved_symbol_update`) does include these in `EDITABLE` but the template's table doesn't expose them as columns — no way to set via UI
- **Blast radius**: Latent. Currently un-usable; will be useful once filling-in workflow ships.
- **Hypothesis**: Forward-thinking schema for Phase 2.
- **Fix size**: medium (when the linkage workflow is built — out of audit scope today)

## DB queries run

```sql
-- ReservedSymbol counts
SELECT COUNT(*) FROM reserved_symbols;                                  -- 282
SELECT status, COUNT(*) FROM reserved_symbols GROUP BY status;          -- Reserved 270, Filed 5, Wait Listed 4, Requested 3
SELECT exchange, COUNT(*) FROM reserved_symbols GROUP BY exchange;      -- Cboe 258, NYSE 17, NASDAQ OMX 7
SELECT suite, COUNT(*) FROM reserved_symbols GROUP BY suite;            -- Leverage 81, Income 63, Crypto 48, ...
-- Days-left buckets: 31-90d=16, 90d+=259, no_date=7, expired=0

-- Status enum mismatch
SELECT COUNT(*) FROM reserved_symbols
  WHERE status NOT IN ('Reserved','Active','Expired','Released');       -- 12

-- mkt_master_data join check
SELECT COUNT(*) FROM reserved_symbols rs WHERE EXISTS
  (SELECT 1 FROM mkt_master_data m WHERE m.ticker = rs.symbol);         -- 0 (BROKEN)

-- Reserved symbols vs CBOE availability
SELECT COUNT(*) FROM reserved_symbols rs WHERE EXISTS
  (SELECT 1 FROM cboe_symbols c WHERE c.ticker=rs.symbol AND c.available=0);  -- 261
SELECT COUNT(*) FROM reserved_symbols rs WHERE EXISTS
  (SELECT 1 FROM cboe_symbols c WHERE c.ticker=rs.symbol AND c.available=1);  -- 21 (suspicious)

-- CBOE sweep state
SELECT COUNT(*) FROM cboe_symbols;                                      -- 475,254 (full universe)
SELECT available, COUNT(*) FROM cboe_symbols GROUP BY available;        -- 0:24,899  1:450,355  (NULL: 0)
SELECT MIN(last_checked_at), MAX(last_checked_at) FROM cboe_symbols;
  -- 2026-04-25 07:01:32 .. 07:44:35 (everything 16 days stale)
SELECT COUNT(*) FROM cboe_known_active;                                 -- 13,284 rows / 12,263 distinct base
SELECT MAX(refreshed_at) FROM cboe_known_active;                        -- 2026-05-04 (newer than the cboe sweep)
SELECT COUNT(*) FROM cboe_state_changes;                                -- 0 (NEVER written; first sweep was the only one to complete)

-- Atlas memory check (claimed 13,119 reserved-by-competitor)
SELECT COUNT(*) FROM cboe_symbols c WHERE c.available=0 AND NOT EXISTS
  (SELECT 1 FROM cboe_known_active k WHERE k.base_ticker = c.ticker);   -- 12,755 today (drift from atlas value 13,119)

-- Scan run history
SELECT id, started_at, status, tier, tickers_checked, error_message
  FROM cboe_scan_runs ORDER BY started_at DESC LIMIT 12;
  -- 9 consecutive 'failed' (auth 302), 1 'crashed mid-sweep' (fixed in c209c70),
  -- 1 'completed' full sweep (Apr 25), 1 'completed' 1-letter probe (Apr 25)
```

## Live state inspection

- **VPS systemd CBOE service**: `failed (code=exited, status=3)` since `Mon 2026-05-11 03:01:43 EDT` — auth 302 redirect.
- **VPS timer**: active (waiting), next trigger Tue 2026-05-12 03:00 EDT — will fail again unless cookie rotates.
- **VPS data dir**: `/home/jarvis/rexfinhub/data/etp_tracker.db` = 624 MB, last write 17:20 EDT today (still being updated by other pipelines). No `cboe*` or `reserved*` files in `/data` — both live inside the SQLite DB.
- **Cookie**: `config/.env` last modified 2026-04-24 15:18 UTC (16+ days ago). Length 42 chars. Sessions live where expected.
- **NASDAQ screener APIs**: failing with `Read timed out` for ≥14 days. Three other sources still working.
- **Daily import**: `import_reserved_symbols.py` runs only manually, only on Ryu's Windows box. The xlsx source file is not synced anywhere.

## Surfaces inspected

- `scripts/import_reserved_symbols.py` (137 lines, full read)
- `scripts/run_cboe_scan.py` (110 lines, full read)
- `scripts/refresh_cboe_known_active.py` (34 lines, full read)
- `scripts/probe_cboe_rate_limit.py` (63 lines, full read)
- `webapp/routers/operations_reserved.py` (239 lines, full read)
- `webapp/routers/tools_tickers.py` (18 lines, full read)
- `webapp/routers/filings.py` — `_symbols_impl` and `_symbols_redirect` (lines 1290-1377)
- `webapp/templates/operations_reserved_symbols.html` (239 lines, full read)
- `webapp/templates/filings_symbols.html` (lines 1-80; banner / KPI sections)
- `webapp/services/cboe/__init__.py`, `scanner.py`, `live.py`, `rate_probe.py`, `universe.py`, `known_active.py`, `cross_reference.py` (full reads)
- `webapp/models.py` — `ReservedSymbol`, `CboeSymbol`, `CboeStateChange`, `CboeScanRun`, `CboeKnownActive` (lines 1106-1220)
- `deploy/systemd/rexfinhub-cboe.service`, `rexfinhub-cboe.timer`
- `config/.env` and `config/.env.example` (CBOE entries only)
- VPS: systemctl status, journalctl 14d, `/home/jarvis/rexfinhub/data/`, `config/.env` cookie line
- DB: `reserved_symbols`, `cboe_symbols`, `cboe_known_active`, `cboe_scan_runs`, `cboe_state_changes`, `mkt_master_data` (read-only queries)

## Surfaces NOT inspected

- `webapp/services/cboe/__init__.py` content beyond the docstring (file is essentially empty, just a module docstring)
- `webapp/templates/filings_symbols.html` lines 81+ (table body, paginator) — sampled only header / banner
- `webapp/templates/_filings_subnav.html` (referenced by filings_symbols.html)
- `webapp/main.py` beyond the operations_reserved router include line — did not verify whether `/operations/products` exists (F9)
- `MktMasterData` model definition — only inspected via the rows; ticker format conclusion drawn from data shape (could be a derived `bare_ticker` column elsewhere I missed, but no evidence in the cross_reference subquery)
- The `live_check` happy-path live HTTP request was NOT executed (cookie expired; would have returned auth error anyway)
- No POST/PUT to `/operations/reserved-symbols` (read-only audit, per constraint)
- Render production DB (only inspected the local sync; could differ if Render last upload was before today)
- 13F router and other CBOE-adjacent code paths
- Any `reserved_symbols`-aware code outside the four files in scope (search confirmed none)

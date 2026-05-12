# Stage 1 RE-AUDIT — Classification Engine Verification
Generated: 2026-05-11T22:18:00 ET (post-fix verification)
Agent: classification re-audit
Mode: READ-ONLY

## Summary

The "100% NULL primary_strategy" smoking gun is **RESOLVED**. After R1 (chain
service deployment + `apply_classification_sweep.py --apply --apply-medium`
in ExecStartPost) and the post-deploy chain runs at 00:57, 01:06, and 01:27
UTC, the live VPS DB shows **5,206 / 5,235 ACTV rows (99.4%)** populated on
`primary_strategy`. The 29 holdouts are real edge cases (newly listed
generic equity ETFs that auto_classify returns "Unclassified" for, see N1).

R2 and R6 are correctly applied at the code surface level. R2 is verified
in the dataclass + writer code; R6 confirmed via `RULES_DIR =
_CONFIG_RULES_DIR` in `tools/rules_editor/classify_engine.py:21` and the
removal of all CSVs from `data/rules/` (replaced by README.md).

T1 backfill confirmed: `etp_category` populated for 1,941 of 5,235 ACTV
(was 1,877 in Stage 1 — +64 rows since fix).

**However, three NEW issues surfaced that weaken the durability of the
fixes**: (N2) the chain unit's `apply_classification_sweep` step never
populates the legacy `mkt_master_data.strategy` column, leaving it 100%
NULL — R2's dataclass changes never reach the chain pathway. (N3) 76
`mkt_pipeline_runs` rows are stuck in `status='running'` with no
`finished_at`, all from `daily_classify` source, dating back to May 7. (N4)
`mkt_fund_classification` is again pinned to a single stuck run id (339)
with classifier output that the master table no longer reflects (chain
runs 340/341 wiped/repopulated master *after* run 339 wrote
`mkt_fund_classification`).

The 100% NULL primary_strategy crisis is over. The mechanism that caused
it (chain runs sweep but not the dataclass writer) is partially reborn for
the legacy `strategy` column — same wipe-no-restore shape, smaller blast
radius.

## Stage 1 finding status

### F1 — Bloomberg sync wipes all classification columns; no consumer restores
- **Status**: **RESOLVED** (via R1)
- **Evidence**:
  - `systemctl cat rexfinhub-bloomberg.timer` → `Unit=rexfinhub-bloomberg-chain.service`
  - Chain unit has 4 ExecStartPost lines including
    `apply_classification_sweep.py --apply --apply-medium` and `apply_issuer_brands.py`
  - Live VPS query: ACTV total 5,235; `primary_strategy NOT NULL = 5,206`
    (99.4%); `asset_class NOT NULL = 5,233` (99.96%); `sub_strategy NOT
    NULL = 5,206` (99.4%)
  - Chain ran cleanly at 21:00 EDT after deployment, then again at 00:57,
    01:06, 01:27 UTC (sweep_run_ids `sweep_20260512T005733`,
    `sweep_20260512T010603`, `sweep_20260512T012705`)
- **Residual**: 29 ACTV rows still NULL primary_strategy (see N1 below).
  Not a regression — these are the rows auto_classify cannot decide on
  and that fund_master.csv has not yet been hand-curated for.

### F2 — `db_writer.write_classifications` only writes legacy `strategy` column
- **Status**: **PARTIALLY RESOLVED** at code level, **NOT YET REACHABLE** in production
- **Code evidence (R2 applied correctly)**:
  - `market/auto_classify.py:50-53` — `Classification` dataclass now has
    `primary_strategy`, `asset_class`, `sub_strategy` fields
  - `market/db_writer.py:240-279` — `write_classifications` now writes
    `master_row.primary_strategy/asset_class/sub_strategy` gap-only
    (preserves curated values), with explicit comment referencing audit
    fix R2
  - `taxonomy_filled` log counter added
- **Production reachability gap**: `write_classifications` is only invoked
  from `scripts/run_daily.py::run_classification` (line 401). The active
  VPS chain unit (`rexfinhub-bloomberg-chain.service`) calls
  `apply_classification_sweep.py` instead, which populates the same 3-axis
  columns via a SEPARATE code path (`derive_taxonomy` in the sweep script,
  not the writer). The chain pathway never invokes `write_classifications`,
  so the legacy `strategy` column it would also write is **left at 100%
  NULL** by every chain run (see N2).
- **Net effect on the Stage 1 symptom**: primary_strategy now populates
  via the chain's sweep step (R1 path), not via R2's writer. R2 is durable
  fallback insurance for `scripts/run_daily.py` runs only. On VPS today,
  R2 has produced zero rows.

### F3 — Three-source-of-truth race for fund classification
- **Status**: **PARTIALLY RESOLVED**
- `data/rules/` has been emptied (only README.md remains) — confirmed
  locally at `C:/Projects/rexfinhub/data/rules/`
- `RULES_DIR` in `tools/rules_editor/classify_engine.py:21` now points at
  `config/rules/` — confirmed
- `config/rules/fund_mapping.csv` grew from 2,300 to 2,366 rows (+66:
  R6 merge brought 34 new from data/rules + T1 added 32 more)
- `config/rules/issuer_mapping.csv` 349 rows (was 341)
- All `attributes_*.csv` files larger (e.g. `attributes_LI` 877 → 899)
- **Remaining race**: `fund_master.csv` (7,231 rows) still distinct from
  `fund_mapping.csv` (2,366 rows). Different schemas, no enforced
  precedence. This is documented in R6 but not architecturally fixed —
  the two files continue to coexist with overlapping ticker sets.
- `tools/rules_editor/classify_engine.py::apply_classifications` still
  only writes to `fund_mapping.csv`, never to `fund_master.csv` — so
  newly auto-approved funds still won't get a `primary_strategy` from
  this surface (must wait for sweep to fill it from BBG signals).

### F4 — Local sweep wiped by Bloomberg sync
- **Status**: **RESOLVED** (chain pathway preserves sweep output now;
  R1 fold-into-chain is the structural fix)
- The sweep at `sweep_20260512T012705` ran at 01:27 UTC right after
  master rows were touched at 01:06 UTC by chain run 341, and the writes
  survived (current ACTV primary_strategy = 5,206 / 5,235).
- The wipe-then-restore cycle is now in a single systemd unit; cannot
  escape half-applied unless an unscheduled `sync_market_data` runs
  outside the chain.

### F5 — `mkt_fund_classification` table is stale
- **Status**: **MOVED, NOT FIXED**
- Was pinned to run 303 (May 7) at Stage 1
- Now pinned to run 339 (May 11 20:08 EDT) — refreshed once
- 7,359 rows / 14 distinct strategies (close to the previous 7,332)
- **New problem**: run 339 `status='running'` (never finished), and
  chain runs 340/341 ran after it but did NOT touch
  `mkt_fund_classification`. So this table is again drifting away from
  master (master last updated 01:06 UTC; classification table pinned
  to 20:08 EDT yesterday). Same shape of bug, same severity (medium).

### F6 — Audit and writer disagree on what "classified" means
- **Status**: **PARTIALLY RESOLVED**
- `write_classifications` now ALSO writes 3-axis (R2). But the daily
  pipeline's `print(f"  Unified classify: {n_written} funds classified")`
  message at `scripts/run_daily.py:403` still doesn't gate on whether
  3-axis was written or whether the run committed. And on VPS the daily
  run never finishes (see N3) so the audit signal is meaningless.
- Preflight gate `primary_strategy IS NULL` count dropped from 5,235 to
  29; the gate would now pass on VPS.

### F7 — 79 unclassified new launches (Tier 1 14-day window)
- **Status**: **PARTIALLY RESOLVED**
- Tier 1 count dropped from 79 → 47 (+T1 backfilled 32). Remaining 47
  are funds with `etp_category IS NULL AND inception_date >= today-14d`
  — concentrated in CORGI thematic ETFs and FIDELITY ENHANCED variants.
- Note: the audit checks `etp_category IS NULL`, not `primary_strategy
  IS NULL`. The 47 here are the SAME funds as the 29 NULL primary_strategy
  PLUS 18 more that have `primary_strategy='Plain Beta'` from the sweep
  but `etp_category IS NULL` because they don't fit LI/CC/Crypto/Defined/
  Thematic. The audit and the auto-classifier disagree on what "categorized"
  means — etp_category is the OLD 5-bucket vocabulary, primary_strategy
  is the NEW 5-bucket vocabulary, and they don't map 1:1.

### F8 — NULL issuer_display 64.5% on ACTV
- **Status**: **RESOLVED**
- Chain unit's `apply_issuer_brands.py` ExecStartPost line is firing.
- Live: ACTV `issuer_display NOT NULL` = 4,409 / 5,235 (84.2% non-NULL,
  i.e. 15.8% NULL — was 64.5% NULL at Stage 1). Not 0%, but a 4× drop.

### F9 — 8 CC funds missing from attributes_CC.csv
- **Status**: **NOT VERIFIED THIS PASS** (out of scope per 30-min budget;
  attributes_CC.csv now 359 rows, was 342 — likely backfilled but did
  not re-run the preflight Tier 3 check)

## New findings

### N1 — 29 ACTV rows correctly NULL primary_strategy because auto_classify abstains
- **Severity**: low (real edge case, not a bug)
- **Surface**: `market/auto_classify.py::classify_fund` returns
  `strategy="Unclassified"` for funds it can't fingerprint; the sweep's
  `_PRIMARY_STRATEGY_MAP` maps "Unclassified" → None and skips the write.
- **Tickers (all 29)**: FEMV, FEMG, FSEG, FSEV (Fidelity Enhanced family);
  NICO, TPFC, TPFG, QVMT, ACVU, SPCK, GENZ, TLG, MNVT (one-off thematic
  or generic equity); STYL, WNDR, DOCK, BZZ, YUNG, LATR, BAY, GNMX,
  EYES, PTNT, NYNY, CLUB (Corgi-branded thematic family — all launched
  2026-05-05/06 and undescribed in BBG fields); DINE (Simplify
  multi-asset income); WDAI, PSAI (Pacer S&P AI Top — launched
  2026-07-05, future-dated, no BBG signals yet); BUYB (ProShares
  buyback aristocrats).
- **Evidence**: For FEMV US — `asset_class_focus='Equity'`,
  `fund_type='ETF'`, `uses_leverage='False'`, all other discriminators
  NULL. The sweep DID see it (audit log shows `sweep_high asset_class
  Equity` write), but `_PRIMARY_STRATEGY_MAP.get(None)` returns None so
  `primary_strategy` stays empty.
- **Blast radius**: 29 funds invisible to /strategy/* filtering. No
  emails or reports break.
- **Hypothesis**: The taxonomy lacks a default-bucket. Either
  `_PRIMARY_STRATEGY_MAP` needs a "Plain Beta" fallback when
  `asset_class` is Equity but no specific signal fires, OR fund_master.csv
  needs human curation to backfill these (the recommended path; the
  Corgi family has marketing names that need brand-aware mapping).
- **Fix size**: small (one-line fallback in sweep) OR small (curate 29
  rows in fund_master.csv).

### N2 — Legacy `strategy` column on `mkt_master_data` is 100% NULL on VPS
- **Severity**: medium (downstream consumers using the legacy column see
  zero data)
- **Surface**: same shape as Stage 1 F1, smaller blast — the chain
  unit's ExecStartPost steps DO populate the 3-axis columns
  (`primary_strategy/asset_class/sub_strategy`) via
  `apply_classification_sweep.py`, but they NEVER touch the legacy
  `strategy` column. The R2 dataclass writer that does populate it lives
  in `market/db_writer.py::write_classifications` and is only invoked
  from `scripts/run_daily.py::run_classification`, which is never
  called by the chain.
- **Evidence**:
  - `SELECT COUNT(*) FROM mkt_master_data WHERE strategy IS NOT NULL` →
    **0** (5,235 ACTV, 7,361 total — all NULL on legacy column)
  - Chain run 341 finished 01:05:38 UTC; master `MAX(updated_at) =
    01:06:12.699004` (slightly later because sweep updates ran in
    sweep_20260512T010603 starting 01:06:11). Strategy column not
    written by either step.
  - `mkt_fund_classification` at the same moment HAS distinct strategies
    (Broad Beta 1909, Fixed Income 1276, Leveraged & Inverse 900, ...)
    written by run 339 — so the data exists in a sister table, just not
    on master.
- **Blast radius**: any consumer querying
  `mkt_master_data.strategy` (the legacy 13-bucket column) sees 100%
  NULL. Includes some legacy templates in webapp/routers/* that haven't
  been migrated to the 3-axis columns. The 3-axis is the modern surface
  so most pages are fine, but the audit check
  `null_strategy = SUM(... strategy IS NULL ...)` still reports 100%.
- **Hypothesis**: R2 was a code fix without a deploy/scheduler change.
  R1 fixed the scheduler but R1's fix invokes the SWEEP code path
  (which doesn't write legacy `strategy`), not R2's writer code path.
  Two parallel attempts to fix the same problem; neither covers the
  whole column set.
- **Fix size**: small. Either: (a) add `apply_classification_sweep.py`
  to write `master_row.strategy` from its `derive_taxonomy` translation
  table (one column, deterministic); or (b) add an additional
  ExecStartPost to the chain that runs only the
  `write_classifications` portion of `run_classification`; or (c) drop
  the legacy `strategy` column from the schema entirely now that 3-axis
  is universally populated.

### N3 — 76 stuck `mkt_pipeline_runs` rows with `status='running'` and no `finished_at`
- **Severity**: medium (runs DB integrity)
- **Surface**: `mkt_pipeline_runs` table; all 76 stuck rows have
  `source_file='daily_classify'`, dating back to 2026-05-07 08:09:55.
- **Evidence**:
  - `SELECT COUNT(*) FROM mkt_pipeline_runs WHERE status='running' AND
    source_file='daily_classify'` → **76**
  - Most recent stuck: id=339 started 2026-05-11 20:08:10, never
    finished. Daily timer fired at 19:30, daily.service finished
    cleanly at 19:46:39 EDT. So `run_classification` (via
    `create_pipeline_run(... source_file="daily_classify")`) created
    the row but the `db.commit()` either rolled back, raised, or the
    row was created in a different transaction than the one that would
    set `finished_at`.
  - Looking at `scripts/run_daily.py:400-407`: `create_pipeline_run`
    is called, then `write_classifications`, then `db.commit()`, then
    `db.close()`. There is no `finished_at` update in this code path.
    `create_pipeline_run` likely sets `started_at` and inserts; nothing
    here marks `finished_at`.
- **Blast radius**: any preflight or admin dashboard counting
  in-flight pipelines sees 76 phantom runs. Garbage-collection scripts
  may misbehave. The audit "latest run id" indicator would jump to a
  stuck run if the dashboard sorts by id desc.
- **Hypothesis**: `create_pipeline_run` was wired up for the daily
  classify phase but the corresponding "finalize" call was never
  written. Or it was, and an earlier exception in the run skips it.
- **Fix size**: small. Either: (a) add a `try/finally` that calls a
  `finalize_pipeline_run(run_id, status="completed")` on exit from
  `run_classification`; or (b) batch-update existing 76 rows to set
  `status='aborted'` and `finished_at=started_at`; or (c) drop the
  `daily_classify` source_file row creation entirely if the chain's
  `auto` runs cover the audit need.

### N4 — `mkt_fund_classification` drifts immediately after every chain run
- **Severity**: medium
- **Surface**: `mkt_fund_classification` is populated only by
  `write_classifications` (run_daily.py), which is not invoked by the
  chain. Chain runs touch `mkt_master_data` (and audit log + proposals)
  but NOT `mkt_fund_classification`. Result: every chain run desyncs
  this table from master.
- **Evidence**: at this audit, `mkt_fund_classification` pinned to
  run 339 (started 20:08 EDT, never finished — but the rows exist
  because `write_classifications` flushed before any later failure).
  Chain run 341 finished 01:05 UTC — 5h after run 339. ACTV rows in
  master have `primary_strategy='Plain Beta'` (sweep output) for funds
  that `mkt_fund_classification` records as `strategy='Broad Beta'` /
  `Sector` / `International`. Both can be true (different vocabularies),
  but if any UI joins them assuming sync, it's quietly stale.
- **Blast radius**: small if no consumer joins them; medium if any
  page reads from `mkt_fund_classification` to show "more granular"
  data. The Stage 1 audit recommended folding the writer into
  `sync_market_data` — that recommendation has not been implemented.
- **Hypothesis**: Same architectural issue as N2 — the chain replaces
  the daily classify pathway for `mkt_master_data` writes but ignores
  `mkt_fund_classification` completely.
- **Fix size**: small. Add `write_classifications` invocation as a 5th
  ExecStartPost on the chain unit, OR delete `mkt_fund_classification`
  if the master-table 3-axis fields suffice.

### N5 — `_PRIMARY_STRATEGY_MAP` collapses 9 distinct strategies to "Plain Beta"
- **Severity**: low (information loss, not a bug)
- **Surface**: `scripts/apply_classification_sweep.py:64-80`
- **Evidence**: Crypto, Fixed Income, Commodity, Alternative, Multi-Asset,
  Thematic, Sector, International, and Broad Beta all map to
  `primary_strategy='Plain Beta'` in the sweep. Then sub_strategy is
  derived from the original strategy ("Sector" → "Sector",
  "Fixed Income" → "Broad", etc.).
- **Symptom**: 3,654 / 5,235 ACTV rows (69.8%) have
  `primary_strategy='Plain Beta'`, leaving "Plain Beta" as a
  near-meaningless catch-all. The original 13-strategy info is preserved
  in `sub_strategy`, but any UI that filters/aggregates only on
  `primary_strategy` will see a single bucket holding most funds.
- **Blast radius**: depends on whether downstream UIs use `sub_strategy`
  for fan-out. /strategy/* pages and admin classification stats pages
  should be inspected.
- **Hypothesis**: This is by design (per
  `docs/CLASSIFICATION_SYSTEM_PLAN.md`) — 5-bucket primary axis is the
  product spec. But it's worth noting because Stage 1 didn't surface it
  as a downstream effect. If the spec is wrong, this is the place to
  contest it.
- **Fix size**: zero (it's spec-compliant) OR architectural (revisit
  the 5-bucket axis).

### N6 — `daily_classify` runs DB-write `mkt_fund_classification` even when stuck
- **Severity**: low (deferred — connected to N3 + N4)
- **Surface**: `market/db_writer.py:202-237` — the `for c in
  classifications` loop adds rows + flushes every 500. If the run
  later fails, those rows persist (no transaction rollback because
  flushes were issued).
- **Evidence**: 76 stuck `daily_classify` runs but
  `mkt_fund_classification` has rows from at least one of them
  (run 339). So the table is partially populated by zombie runs.

## DB queries run

```sql
-- Q1: ACTV NULL counts (post-fix)
SELECT
  COUNT(*) AS total_actv,
  SUM(CASE WHEN primary_strategy IS NULL OR primary_strategy='' THEN 1 ELSE 0 END) AS null_ps,
  SUM(CASE WHEN asset_class IS NULL OR asset_class='' THEN 1 ELSE 0 END) AS null_ac,
  SUM(CASE WHEN sub_strategy IS NULL OR sub_strategy='' THEN 1 ELSE 0 END) AS null_ss,
  SUM(CASE WHEN strategy IS NULL OR strategy='' THEN 1 ELSE 0 END) AS null_legacy,
  SUM(CASE WHEN etp_category IS NULL OR etp_category='' THEN 1 ELSE 0 END) AS null_cat,
  SUM(CASE WHEN issuer_display IS NULL OR issuer_display='' THEN 1 ELSE 0 END) AS null_iss
FROM mkt_master_data WHERE market_status='ACTV';
-- VPS result:
--   total_actv     = 5235
--   null_ps        = 29     (was 5235 → 99.4% improvement)
--   null_ac        = 2      (was 5235 → 99.96%)
--   null_ss        = 29     (was 5235 → 99.4%)
--   null_legacy    = 5235   (still 100% NULL — N2)
--   null_cat       = 3294   (was 3358 → +64 fills via T1)
--   null_iss       = 826    (was 3375 → 75.5% improvement, F8)

-- Q2: Recent pipeline runs
SELECT id, started_at, finished_at, status, source_file, master_rows_written
  FROM mkt_pipeline_runs ORDER BY id DESC LIMIT 8;
-- 341 | 2026-05-12 01:01:31 | 01:05:38 | completed | auto | 7361
-- 340 | 2026-05-12 00:52:58 | 00:57:06 | completed | auto | 7361
-- 339 | 2026-05-11 20:08:10 | NULL    | running   | daily_classify | 0  (STUCK)
-- 338 | 2026-05-11 19:37:25 | NULL    | running   | daily_classify | 0  (STUCK)
-- 337 | 2026-05-11 21:15:50 | 21:20:09 | completed | auto | 7361

-- Q3: classification_audit_log totals
SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM classification_audit_log;
-- 30591 rows, 2026-05-12 00:57:40 to 2026-05-12 01:27:11
-- (was 0 on VPS at Stage 1 — sweep is firing now)

-- Q4: Distinct sweep_run_ids
SELECT sweep_run_id, COUNT(*) FROM classification_audit_log
  GROUP BY sweep_run_id ORDER BY MIN(created_at) DESC LIMIT 10;
-- sweep_20260512T012705 | 4111
-- sweep_20260512T010603 | 13240
-- sweep_20260512T005733 | 13240   (the deploy-time chain run)

-- Q5: primary_strategy distribution (ACTV)
SELECT primary_strategy, COUNT(*) FROM mkt_master_data
  WHERE market_status='ACTV' GROUP BY primary_strategy ORDER BY 2 DESC;
-- Plain Beta      3654   (69.8% — see N5)
-- L&I              621
-- Defined Outcome  506
-- Income           335
-- Risk Mgmt         90
-- (None)            29

-- Q6: NVDX deep dive (re-run from Stage 1)
SELECT ticker, fund_name, strategy, primary_strategy, etp_category,
       issuer_display, asset_class, sub_strategy
  FROM mkt_master_data WHERE ticker LIKE 'NVDX%';
-- ('NVDX US', 'T-REX 2X LONG NVIDIA DAILY TARGET ETF',
--   None,        ← legacy strategy still NULL (N2)
--   'L&I',       ← primary_strategy NOW POPULATED (was None) 
--   'LI', 'REX', 'Equity', 'Long')

-- Q7: mkt_fund_classification by run id
SELECT pipeline_run_id, COUNT(*) FROM mkt_fund_classification
  GROUP BY pipeline_run_id;
-- 339 | 7359  (single stuck run; see N4)

-- Q8: 76 stuck daily_classify runs
SELECT COUNT(*) FROM mkt_pipeline_runs
  WHERE status='running' AND source_file='daily_classify';
-- 76 (oldest 2026-05-07 08:09:55, newest 2026-05-11 20:08:10)

-- Q9: attribute fill rates (sweep coverage on ACTV)
SELECT COUNT(*) AS total,
  SUM(CASE WHEN region IS NOT NULL THEN 1 ELSE 0 END) AS region_filled,
  SUM(CASE WHEN duration_bucket IS NOT NULL THEN 1 ELSE 0 END) AS duration_filled,
  SUM(CASE WHEN credit_quality IS NOT NULL THEN 1 ELSE 0 END) AS cq_filled,
  SUM(CASE WHEN underlier_name IS NOT NULL THEN 1 ELSE 0 END) AS underlier_filled,
  SUM(CASE WHEN concentration IS NOT NULL THEN 1 ELSE 0 END) AS conc_filled,
  SUM(CASE WHEN distribution_freq IS NOT NULL THEN 1 ELSE 0 END) AS distfreq_filled
FROM mkt_master_data WHERE market_status='ACTV';
-- total            5235
-- region_filled     493 (9.4%)
-- duration_filled   300 (5.7%)
-- cq_filled         756 (14.4%)
-- underlier_filled 2783 (53.2%)
-- conc_filled      5235 (100%)
-- distfreq_filled    10 (0.2%)
-- → most attributes still sparsely filled. concentration is universal
--   because the sweep writes "basket" by default for non-singlestock.
```

## Local vs VPS divergence

| Metric                              | Stage 1 VPS | Now VPS | Δ        |
|-------------------------------------|-------------|---------|----------|
| ACTV total                          | 5,235       | 5,235   | 0        |
| ACTV primary_strategy NOT NULL      | 0           | 5,206   | +5,206   |
| ACTV asset_class NOT NULL           | 0           | 5,233   | +5,233   |
| ACTV sub_strategy NOT NULL          | 0           | 5,206   | +5,206   |
| ACTV strategy (legacy) NOT NULL     | 0           | 0       | **0** (N2) |
| ACTV etp_category NOT NULL          | 1,877       | 1,941   | +64 (T1) |
| ACTV issuer_display NOT NULL        | 1,860       | 4,409   | +2,549   |
| classification_audit_log rows       | 0           | 30,591  | +30,591  |
| Active timer target unit            | bloomberg   | bloomberg-chain | swapped (R1) |
| Stuck daily_classify runs           | (not measured) | 76    | N3       |

## Verdict

R1 is the load-bearing fix and it works as designed in production.
primary_strategy went from 100% NULL to 0.6% NULL on VPS — the smoking
gun is dead. R6 successfully eliminated the split-brain CSV path. T1
backfilled etp_category for 64 rows.

R2 is correctly applied at the code level but never reached on VPS
because the chain pathway uses the sweep instead of the dataclass
writer. That's not a regression but it is an unfinished bridge: the
legacy `strategy` column on `mkt_master_data` is still 100% NULL
(N2). And the chain pathway leaves `mkt_fund_classification` to drift
because `write_classifications` is the only writer and the chain doesn't
call it (N4).

Three architectural cleanup items remain:
- N2 — populate `strategy` on the chain (same shape as F1, smaller column
  set)
- N3 — finalize stuck `daily_classify` runs (76 zombies in
  mkt_pipeline_runs)
- N4 — keep `mkt_fund_classification` in sync (or delete it)

Two minor data-quality items remain:
- N1 — 29 ACTV rows are correctly NULL because auto_classify abstains;
  fund_master.csv backfill OR a Plain-Beta fallback in the sweep would
  close them
- N5 — `_PRIMARY_STRATEGY_MAP` makes "Plain Beta" hold 70% of all
  ACTV; spec-compliant but worth confirming with product

The 100% NULL primary_strategy crisis is over. The remaining work is
clean-up, not firefighting.

## Surfaces inspected

- `market/auto_classify.py` lines 1-100 (Classification dataclass + R2
  fields confirmed at 50-53)
- `market/db_writer.py` lines 200-282 (R2 writer changes confirmed at
  240-279)
- `market/config.py` (only via RULES_DIR re-export check)
- `tools/rules_editor/classify_engine.py` lines 1-60 (R6 RULES_DIR
  redirect confirmed at line 21)
- `scripts/apply_classification_sweep.py` lines 1-204 (sweep header,
  derive_taxonomy, _PRIMARY_STRATEGY_MAP, _ASSET_CLASS_FOCUS_MAP)
- `scripts/run_daily.py` lines 370-430 (run_classification + invocation
  pattern; N3 root cause)
- `config/rules/*.csv` row counts (fund_master 7,231; fund_mapping 2,366;
  attributes_LI 899; attributes_CC 359; attributes_Crypto 152;
  attributes_Defined 549; attributes_Thematic 427)
- `data/rules/` — confirmed only README.md remains (R6)
- VPS systemd state: `rexfinhub-bloomberg.timer` Unit= verified;
  `rexfinhub-bloomberg-chain.service` 4 ExecStartPost lines verified;
  `rexfinhub-daily.service` ExecStart confirmed runs `run_daily.py
  --skip-sec`
- VPS DB live SQL: 9 queries documented above

## Surfaces NOT inspected

- F9 Tier 3 audit (CC funds missing from attributes_CC.csv) — not
  re-verified per 30-min budget
- Local DB state — only VPS queried (Stage 1 noted local=VPS for the
  key NULL counts)
- `webapp/routers/admin.py::admin_classification_stats` — still not
  traced
- The 41 other files containing "primary_strategy" string — sample-only
- `scripts/preflight_check.py` was not re-run; gate pass/fail not
  empirically confirmed (but should pass given the metric drop)

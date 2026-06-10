# Stage 1 Audit — Classification Engine
Generated: 2026-05-11T18:55:00 ET
Agent: classification

## Summary

The "100% NULL primary_strategy" smoking gun is fully reproduced and explained.
There are TWO disjoint classification surfaces on `mkt_master_data` and they
write through DIFFERENT code paths:

1. **Legacy 1-axis surface**: columns `strategy`, `strategy_confidence`,
   `underlier_type` (lines 456-458 of `webapp/models.py`). Written by
   `market/db_writer.py::write_classifications` line 250 — but that function
   is ONLY invoked from `scripts/run_daily.py::run_classification` (Phase 5
   of the local daily pipeline). It is never called by the VPS Bloomberg
   timer.

2. **3-axis taxonomy surface**: columns `asset_class`, `primary_strategy`,
   `sub_strategy`, plus ~14 attribute columns (lines 460-482 of
   `webapp/models.py`). These columns are NEVER written by `db_writer.py`.
   They are populated by either:
   - `scripts/apply_fund_master.py` (reads `config/rules/fund_master.csv`,
     7,231 rows, all with primary_strategy filled), OR
   - `scripts/apply_classification_sweep.py` (auto-derives from BBG via
     `market/auto_classify.py` + `derive_taxonomy`).

`webapp/services/market_sync.py::sync_market_data` performs a full
`delete(MktMasterData)` then bulk-insert (lines 244, 257). EVERY Bloomberg
sync therefore wipes BOTH surfaces to NULL. Nothing in `sync_market_data`
restores them.

The active VPS systemd service `rexfinhub-bloomberg.service`
(timer-triggered every cycle) calls `sync_market_data` and stops. It does
NOT call `apply_fund_master.py` or `apply_classification_sweep.py`. The
companion `rexfinhub-bloomberg-chain.service` (which DOES chain
`apply_fund_master.py` via ExecStartPost) exists in
`deploy/systemd/rexfinhub-bloomberg-chain.service` but is not the timer
target. Hence: every VPS Bloomberg cycle leaves
`primary_strategy/asset_class/sub_strategy/strategy` at 100% NULL. This
matches the preflight number exactly.

The daily classifier line "Unified classify: 7,359 funds classified" is
TRUE in the technical sense (the in-memory DataFrame got Classification
objects appended) and it DOES write `mkt_fund_classification` (a separate
table — currently has 7,332 rows pinned to `pipeline_run_id=303` from May
7) AND writes back to `mkt_master_data.strategy` (the legacy column on
the master table). But the Bloomberg sync (which runs AFTER, at 21:15
EDT today on VPS / 22:15 EDT local) wipes the master table and the
daily run does not refire its classification phase afterward. The audit
gate checks `primary_strategy` (line 446 of `scripts/preflight_check.py`),
which is the new 3-axis column nothing has ever populated on VPS.

## Findings

### F1: Bloomberg sync wipes all classification columns; no consumer restores them on VPS
- **Severity**: critical
- **Surface**: `webapp/services/market_sync.py:244` (the wipe);
  `deploy/systemd/rexfinhub-bloomberg.service:11` (the active VPS service
  that triggers the wipe and never re-fills)
- **Symptom**: 100% NULL `primary_strategy`, `asset_class`, `sub_strategy`,
  `strategy` on every ACTV fund right after a Bloomberg sync.
- **Evidence**:
  ```python
  # webapp/services/market_sync.py
  239    # Step 3: Clear existing data (full snapshot replace)
  240    log.info("Clearing existing market data...")
  ...
  244    db.execute(delete(MktMasterData).where(True))
  245    db.flush()
  ...
  257    master_rows = _insert_master_data(db, master_df, run_id)
  ```
  ```ini
  # deploy/systemd/rexfinhub-bloomberg.service (the ACTIVE VPS service)
  ExecStart=/home/jarvis/venv/bin/python -c "...sync_market_data(db); db.close(); print('Bloomberg pull + sync complete')"
  # No ExecStartPost. No apply_fund_master.py. No apply_classification_sweep.py.
  ```
  Live VPS query (this audit): ACTV total 5,235; primary_strategy NOT
  NULL = 0; asset_class NOT NULL = 0; sub_strategy NOT NULL = 0;
  strategy NOT NULL = 0.
- **Blast radius**: every report and page that filters on
  `primary_strategy` / `asset_class` / `strategy` (admin classification
  stats, market/fund pages, /strategy/*, attribution sections of email
  digests). Effectively ALL multi-axis category filtering across the
  market intelligence side of the site.
- **Hypothesis**: The OLD systemd unit `rexfinhub-bloomberg.service` was
  never replaced with `rexfinhub-bloomberg-chain.service` on the VPS.
  Stage 2 fix: either swap the timer target to the chain unit, OR add
  `ExecStartPost=apply_fund_master.py` + `ExecStartPost=apply_classification_sweep.py
  --apply --apply-medium` to the active unit, OR fold the restore logic
  into `sync_market_data` itself so it can never escape into prod
  half-applied.
- **Fix size**: small (one systemd unit edit + reload, OR three
  ExecStartPost lines)

### F2: `db_writer.write_classifications` only writes the LEGACY column family — never `primary_strategy`
- **Severity**: critical
- **Surface**: `market/db_writer.py:240-258`
- **Symptom**: Even when `run_daily.py::run_classification` runs and
  succeeds end-to-end, `primary_strategy` stays NULL because
  `write_classifications` writes `master_row.strategy = ...` (line 250)
  not `master_row.primary_strategy = ...`. The daily classifier output
  "Unified classify: 7,359 funds classified" is therefore misleading —
  the function fills `mkt_fund_classification` (separate table) and the
  legacy `strategy` column, but does NOT populate the 3-axis columns
  the audit and most modern UI pages care about.
- **Evidence**:
  ```python
  # market/db_writer.py
  240    # Sync classification columns back to mkt_master_data
  241    # Build a lookup: ticker -> (strategy, confidence, underlier_type)
  242    class_lookup = {
  243        c.ticker: (c.strategy, c.confidence, c.underlier_type)
  244        for c in classifications
  245    }
  246
  247    updated = 0
  248    for master_row in session.query(MktMasterData).all():
  249        if master_row.ticker in class_lookup:
  250            strategy, confidence, underlier_type = class_lookup[master_row.ticker]
  251            master_row.strategy = strategy           # LEGACY column
  252            master_row.strategy_confidence = confidence
  253            master_row.underlier_type = underlier_type
  254            updated += 1
  ```
  Note: nothing here references `primary_strategy`, `asset_class`, or
  `sub_strategy`.
- **Blast radius**: same as F1 — but this means even FIXING F1 by
  re-running `run_classification` will not restore `primary_strategy`.
  The 3-axis columns require `apply_fund_master.py` OR
  `apply_classification_sweep.py`. Two distinct write paths exist for
  what is conceptually the same data.
- **Hypothesis**: `db_writer.write_classifications` was written for the
  old 1-axis schema and was never extended when the 3-axis columns
  landed. The Classification dataclass in `market/auto_classify.py:30-37`
  also has no `primary_strategy` field — its `strategy` field is the
  legacy 13-strategy string ("Leveraged & Inverse" etc), and
  `apply_classification_sweep.py:65-80` is the only place that maps it
  to the new vocabulary ("L&I" / "Income" / "Plain Beta" / etc).
- **Fix size**: medium. Either: (a) extend Classification to carry
  `primary_strategy` + `asset_class` + `sub_strategy` and have
  `db_writer.write_classifications` write them; or (b) make
  `db_writer.write_classifications` invoke `derive_taxonomy` from
  `apply_classification_sweep.py` and write all 3-axis columns +
  attributes; or (c) deprecate the legacy `strategy` column and route
  ALL classification writes through `apply_fund_master.py` + the sweep.

### F3: Three-source-of-truth race for fund classification
- **Severity**: high
- **Surface**: `config/rules/fund_master.csv` (7,231 rows) vs
  `config/rules/fund_mapping.csv` (2,300 rows) vs the auto-derive in
  `apply_classification_sweep.py::derive_taxonomy`
- **Symptom**: Three different files contain three different views of
  what each ticker's classification should be. fund_master.csv has the
  full 3-axis taxonomy. fund_mapping.csv has only the legacy
  etp_category (LI/CC/Crypto/Defined/Thematic). The sweep derives both
  from BBG signals. There is no documented precedence.
- **Evidence**:
  - `config/rules/fund_mapping.csv` columns: `ticker, etp_category,
    is_primary, source` — ONLY etp_category. No primary_strategy field.
  - `config/rules/fund_master.csv` columns: 28 columns including the
    full 3-axis taxonomy + 20 attributes.
  - `data/rules/fund_mapping.csv` (mirror, 2,328 rows — DIFFERENT count
    from config/rules/fund_mapping.csv which has 2,300!) is the write
    target of `tools/rules_editor/classify_engine.py:18`. config/rules
    is the read target of `market/auto_classify.py::apply_csv_overrides`
    (line 356). Ryu's daily classify writes to data/rules but the
    pipeline reads from config/rules — they have already drifted by 28
    rows.
  - `tools/rules_editor/classify_engine.py:464-535::apply_classifications`
    only writes `etp_category` to fund_mapping.csv. It NEVER writes to
    fund_master.csv. So Phase 2 of `run_classification` (which calls
    `apply_classifications`) cannot fill 3-axis columns either —
    even if it auto-approves dozens of HIGH-confidence new funds, none
    of them get a `primary_strategy` assignment for the audit.
- **Blast radius**: every NEW fund launched after the May 6 fund_master
  snapshot will be classified by the daily pipeline as etp_category=LI
  (or CC, etc.) but with NULL primary_strategy. The 79 unclassified new
  launches in the preflight Tier 1 are funds Bloomberg gave us recently
  that the daily classify_engine did not auto-approve (LOW confidence)
  — these stay both `etp_category=NULL` AND `primary_strategy=NULL`.
- **Hypothesis**: the 3-axis migration shipped fund_master.csv as the
  manual-curation surface but never wired the daily classifier
  (`apply_classifications` in tools/rules_editor) to extend it. The
  classifier can only ever extend the legacy fund_mapping.csv.
- **Fix size**: large. Either consolidate fund_master.csv +
  fund_mapping.csv into a single authoritative file, or extend
  `apply_classifications` to write a fund_master row alongside the
  fund_mapping row.

### F4: The local daily pipeline DID call sweep at 13:35 today, but Bloomberg sync at 22:15 wiped it
- **Severity**: high (already evidenced; demonstrates the F1 mechanism on
  desktop too, not just VPS)
- **Surface**: `scripts/run_daily.py:332-336` (calls sweep) and
  `webapp/services/market_sync.py:244` (wipes)
- **Symptom**: Local sweep_20260511T133535 wrote 16,238 rows to
  classification_audit_log including 2,698 primary_strategy fills and
  5,487 asset_class fills. None of them survived to the live DB.
- **Evidence** (from this audit's live SQL):
  ```
  classification_audit_log (sweep_high source):
    asset_class: 5487 fills logged
    primary_strategy: 2698 fills logged
    sub_strategy: 2698 fills logged
    + 11 other attribute columns
  Sweep run timestamp: 2026-05-11 13:35:38 UTC
  Manual fix run:      2026-05-11 14:04:46 UTC (14 manual_polish_fix entries)
  Manual approve run:  2026-05-11 13:57:42 UTC (2,710 manual_batch_approval)
  ----- THEN -----
  mkt_pipeline_runs.id=304: 2026-05-11 22:15:16 .. 22:18:10
    source_file='auto', master_rows_written=7361
  mkt_master_data.MAX(updated_at)=2026-05-11 22:15:35 (run 304 insertion)
  Current state: 0 rows have primary_strategy.
  ```
- **Blast radius**: confirms F1 — also explains why running the sweep
  manually does not appear to "stick" if a Bloomberg watcher fires
  later in the same window.
- **Hypothesis**: order-of-operations. `_market_synced_today` in
  `run_daily.py:48-73` lets a daily run skip market-sync if a Bloomberg
  watcher already refreshed today, but the converse does NOT exist —
  there is no detector for "Bloomberg watcher fired AFTER our sweep"
  that would re-trigger the sweep.
- **Fix size**: small (have `sync_market_data` invoke
  `apply_fund_master.py` + sweep before commit), or trivial (add a
  systemd path-watcher on `data/etp_tracker.db` that fires the sweep
  whenever the master table is touched).

### F5: `mkt_fund_classification` table is stale (pinned to May 7 run)
- **Severity**: medium
- **Surface**: `mkt_fund_classification` table; written by
  `db_writer.write_classifications`
- **Symptom**: the only row population in mkt_fund_classification is
  `pipeline_run_id=303` (May 7). No rows for run 304 (today). The full
  refresh in `write_classifications:202` deletes everything before
  insert, but insertion only happens when `run_classification` is
  called, which only happens via the local `run_daily.py`.
- **Evidence**:
  ```
  Live: SELECT pipeline_run_id, COUNT(*) FROM mkt_fund_classification
        GROUP BY pipeline_run_id;
    303 → 7332 rows
    (no other run_ids)
  ```
- **Blast radius**: any UI / report that joins mkt_fund_classification
  for "more granular than mkt_master_data" data is using 4-day stale
  values. New funds launched after May 7 are missing entirely.
- **Hypothesis**: same root cause — `sync_market_data` does not invoke
  the classification phase, and the local daily pipeline has not
  re-run since May 7's run 303.
- **Fix size**: small (fold classification call into sync_market_data,
  or make run_classification idempotent and trigger it after every
  sync).

### F6: Audit and writer disagree on what "classified" means
- **Severity**: medium
- **Surface**: `scripts/preflight_check.py:446` (queries
  `primary_strategy IS NULL`) vs `scripts/run_daily.py:403` (prints
  "Unified classify: N funds classified" referring to
  `n_written = write_classifications(...)` which writes the legacy
  `strategy` column).
- **Symptom**: contradictory signal. The daily run claims success; the
  preflight one hour later screams 100% NULL.
- **Evidence**:
  ```python
  # scripts/preflight_check.py:441-450
  total = db.execute(text("SELECT count(*) FROM mkt_master_data WHERE market_status='ACTV'")).scalar()
  null_strat = db.execute(
      text("SELECT count(*) FROM mkt_master_data WHERE market_status='ACTV' AND primary_strategy IS NULL")
  ).scalar()
  ```
  ```python
  # scripts/run_daily.py:401-403
  n_written = write_classifications(db, classifications, run_id=run_id)
  db.commit()
  print(f"  Unified classify: {n_written} funds classified")
  ```
  but `write_classifications` writes only `strategy`, not
  `primary_strategy`.
- **Blast radius**: operators trust the daily success message, ignore
  the cause. Real data quality is invisible until the preflight fires.
- **Hypothesis**: the preflight gate was added as part of the 3-axis
  migration without aligning the daily pipeline output messaging.
- **Fix size**: trivial (rename the daily print, OR add a real
  primary_strategy NULL check inside run_classification before printing
  "classified").

### F7: 79 unclassified new launches — definition
- **Severity**: medium (downstream of F1/F3)
- **Surface**: `scripts/preflight_check.py:148-159` — Tier 1 of
  `audit_classification`
- **Definition**:
  ```sql
  SELECT ticker FROM mkt_master_data
  WHERE market_status='ACTV'
    AND etp_category IS NULL
    AND date(inception_date) >= date('now','-14 days')
  ```
- **Why they aren't being classified**: `tools/rules_editor/classify_engine.py::scan_unmapped`
  + `apply_classifications` only auto-approves HIGH/MEDIUM confidence
  candidates and only into the 5 tracked categories
  (LI/CC/Crypto/Defined/Thematic). Funds outside those categories
  (Plain Beta, sector, international, fixed income, etc.) end up in
  `outside` (line 152) and are never written back. So a freshly listed
  generic equity ETF stays etp_category=NULL forever unless a human
  edits fund_mapping.csv.
- **Blast radius**: the 79 funds will keep showing as gaps every day
  until they age out of the 14-day window.
- **Fix size**: medium. Decide whether to (a) extend etp_category to
  include "Other" / "Plain Beta" so every fund has a value, (b) drop
  the etp_category gap check from the audit and rely on
  primary_strategy instead (which fund_master.csv covers more
  comprehensively), or (c) keep the gap check but exempt the asset
  classes the taxonomy does not currently target.

### F8: NULL issuer_display 64.5% on ACTV — same wipe-no-restore mechanism
- **Severity**: high
- **Surface**: `webapp/services/market_sync.py:244` (wipe) +
  `scripts/apply_issuer_brands.py` (only run from
  `run_daily.py:309-312`, NOT from active VPS systemd unit)
- **Symptom**: 64.5% NULL issuer_display on ACTV (3,375 of 5,235).
  Almost identical to local (3,375 NULL out of 5,235).
- **Evidence**:
  ```
  # Local: 1,860 NOT NULL of 5,235 ACTV → 64.5% NULL
  # VPS:   1,860 NOT NULL of 5,235 ACTV → 64.5% NULL  (identical)
  ```
  `apply_issuer_brands.py` is invoked from `run_daily.py:309-312` only.
  The VPS active unit `rexfinhub-bloomberg.service` does not call it.
  The companion `rexfinhub-bloomberg-chain.service` does (ExecStartPost
  line in chain.service references `apply_issuer_brands.py`) but is
  not the active timer target.
- **Blast radius**: 3,375 funds show as NULL issuer brand on /issuers/,
  /market/issuer, every email digest grouped by issuer.
- **Hypothesis**: same as F1 — wrong systemd unit is active on VPS.
  Local matches because `_market_synced_today` short-circuited the
  apply_issuer_brands call when run 304 fired from a separate process.
- **Fix size**: trivial when bundled with F1 fix.

### F9: 8 CC funds missing from attributes_CC.csv
- **Severity**: low (well-bounded)
- **Surface**: `scripts/preflight_check.py:171-188` — Tier 3 of
  `audit_classification`
- **Definition**: ACTV funds with `etp_category='CC'` whose ticker is
  not in `config/rules/attributes_CC.csv`.
- **Why**: `tools/rules_editor/classify_engine.py::apply_classifications:511`
  has a "skip if already in attributes" guard (line 512). When a
  fund is already in fund_mapping.csv with category CC but the
  attributes_CC.csv row was lost in a prior CSV desync (the same
  desync that produced fund_master.csv "db-export-2026-05-01"
  recovery rows), there is no automatic catch-up.
- **Fix size**: small (one-time backfill script, or extend
  `apply_classifications` to ALWAYS append to attributes when missing
  even if fund_mapping has the ticker).

## DB schema findings

`mkt_master_data` (111 columns total) carries BOTH classification
families:

- Legacy 1-axis (lines 456-458 of webapp/models.py):
  - `strategy` VARCHAR(50)
  - `strategy_confidence` VARCHAR(10)
  - `underlier_type` VARCHAR(50)

- 3-axis taxonomy (lines 460-482 of webapp/models.py):
  - `asset_class` VARCHAR(30)
  - `primary_strategy` VARCHAR(40)
  - `sub_strategy` VARCHAR(80)
  - `concentration` VARCHAR(10)
  - `underlier_name` VARCHAR(60)
  - `underlier_is_wrapper` BOOL
  - `root_underlier_name` VARCHAR(60)
  - `wrapper_type` VARCHAR(20)
  - `mechanism` VARCHAR(20)
  - `leverage_ratio` FLOAT
  - `direction` VARCHAR(10)
  - `reset_period` VARCHAR(15)
  - `distribution_freq` VARCHAR(15)
  - `outcome_period_months` INT
  - `cap_pct` FLOAT
  - `buffer_pct` FLOAT
  - `accelerator_multiplier` FLOAT
  - `barrier_pct` FLOAT
  - `region` VARCHAR(30)
  - `duration_bucket` VARCHAR(20)
  - `credit_quality` VARCHAR(20)
  - `tax_structure` VARCHAR(20)
  - `qualified_dividends` BOOL

Plus the legacy etp_category surface (line 431):
  - `etp_category` VARCHAR(20)
  - `category_display` VARCHAR(100)
  - `issuer_display` VARCHAR(200)
  - `issuer_nickname` VARCHAR(200)
  - `primary_category` VARCHAR(20)

`mkt_fund_classification` does NOT have `primary_strategy` /
`asset_class` / `sub_strategy` columns. Its `strategy` column is the
legacy 13-strategy string. So even if you joined this table back into
master, it would not give you primary_strategy.

`mkt_fund_mapping` (DB) and `fund_mapping.csv` both only have
`etp_category`, no primary_strategy.

`rex_products` table has neither classification surface.

`classification_proposals` queue (2,762 rows) tracks `proposed_strategy`
which uses 3-axis vocabulary (Plain Beta / Income / Defined Outcome /
etc.). This is the correct vocabulary but the proposals table is not
where the live UI reads from.

## DB queries run

```sql
-- Q1: Tables with classification-related names
SELECT name FROM sqlite_master WHERE type='table' AND
  (name LIKE '%classif%' OR name LIKE '%mapping%' OR name LIKE '%mkt%'
   OR name LIKE '%rex%' OR name LIKE '%fund%')
ORDER BY name;
-- Result: classification_audit_log, classification_proposals,
--   fund_distributions, fund_extractions, fund_status,
--   mkt_category_attributes, mkt_exclusions, mkt_fund_classification,
--   mkt_fund_mapping, mkt_global_etp, mkt_issuer_mapping,
--   mkt_market_status, mkt_master_data, mkt_pipeline_runs,
--   mkt_report_cache, mkt_rex_funds, mkt_stock_data, mkt_time_series,
--   rex_products

-- Q2: which tables have primary_strategy?
PRAGMA table_info(mkt_master_data);          -- HAS primary_strategy (line 461 of model)
PRAGMA table_info(mkt_fund_classification);  -- NO primary_strategy
PRAGMA table_info(rex_products);             -- NO primary_strategy
PRAGMA table_info(mkt_fund_mapping);         -- NO (only etp_category)

-- Q3: NULL counts on ACTV
-- Local:
--   ACTV total = 5,235
--   primary_strategy NOT NULL = 0  (100% NULL)
--   strategy NOT NULL = 0          (100% NULL)
--   asset_class NOT NULL = 0       (100% NULL)
--   sub_strategy NOT NULL = 0      (100% NULL)
--   etp_category NOT NULL = 1,877  (35.9%)
--   issuer_display NOT NULL = 1,860 (35.5% — i.e. 64.5% NULL)

-- Q4: mkt_fund_classification distinct strategies
SELECT strategy, COUNT(*) FROM mkt_fund_classification
  GROUP BY strategy ORDER BY 2 DESC;
-- Result: 14 distinct strategies, total 7332 rows, ALL pinned to
--   pipeline_run_id=303 (May 7) — table is stale.

-- Q5: classification_proposals
SELECT proposed_strategy, status, COUNT(*) FROM classification_proposals
  GROUP BY proposed_strategy, status;
-- Plain Beta approved=2455, Income approved=187, Defined Outcome
--   approved=69, Leveraged & Inverse pending=15, Defined Outcome
--   pending=14, Income pending=14, Thematic pending=4, Crypto pending=3,
--   Defined Outcome rejected=1.
-- Total: 2,762

-- Q6: NVDX deep dive (a known L&I fund)
SELECT ticker, fund_name, strategy, primary_strategy, etp_category,
       issuer_display, asset_class, sub_strategy
  FROM mkt_master_data WHERE ticker LIKE 'NVDX%';
-- ('NVDX US', 'T-REX 2X LONG NVIDIA DAILY TARGET ETF',
--   None, None, 'LI', 'REX', None, None)
-- → etp_category populated (came from a survival mechanism we have not
--   yet identified — possibly fund_mapping.csv inserted via mkt_fund_mapping
--   table seeding); ALL 3-axis + legacy strategy columns NULL.

-- Q7: Recent pipeline runs and the wipe chronology
SELECT id, started_at, finished_at, status, source_file, master_rows_written
  FROM mkt_pipeline_runs ORDER BY id DESC LIMIT 5;
-- 304 | 2026-05-11 22:15:16 | 22:18:10 | completed | auto | 7361
-- 303 | 2026-05-07 11:59:18 | 11:59:47 | completed | bloomberg_daily_file.xlsm | 7486
-- 302 | 2026-05-04 08:15:43 | NULL    | running   | daily_classify | 0
-- 301 | 2026-05-04 12:07:51 | 12:12:09 | completed | auto | 7247

-- Q8: Audit log shows sweep DID write but BBG sync wiped after
SELECT sweep_run_id, source, COUNT(*)
  FROM classification_audit_log
  WHERE sweep_run_id='sweep_20260511T133535'
  GROUP BY source, column_name ORDER BY 3 DESC LIMIT 25;
-- sweep_high asset_class: 5487
-- sweep_high primary_strategy: 2698
-- sweep_high sub_strategy: 2698
-- ...
-- created_at range: 2026-05-11 13:35:38 (BEFORE the 22:15 wipe)

-- mkt_master_data updated_at confirms post-wipe state
SELECT MAX(updated_at), MIN(updated_at) FROM mkt_master_data;
-- ('2026-05-11 22:15:35', '2026-05-11 22:15:33')
-- All rows last touched by run 304's bulk-insert. Sweep changes are gone.
```

## Local vs VPS divergence

| Metric | Local | VPS | Match? |
|---|---|---|---|
| ACTV total | 5,235 | 5,235 | YES |
| ACTV primary_strategy NOT NULL | 0 (0%) | 0 (0%) | YES |
| ACTV strategy NOT NULL | 0 | 0 | YES |
| ACTV asset_class NOT NULL | 0 | 0 | YES |
| ACTV etp_category NOT NULL | 1,877 (35.9%) | 1,877 (35.9%) | YES |
| ACTV issuer_display NOT NULL | 1,860 (35.5%) | 1,860 (35.5%) | YES |
| Latest run id | 304 (today, auto, 7361) | 337 (today, auto, 7361) | both wipes |
| classification_audit_log rows | 18,983 | 0 | DIFFERS |
| Recent sweep_run_id | sweep_20260511T133535 | (none ever) | DIFFERS |
| fund_master.csv exists | YES (7231 rows) | YES (7232 lines, May 6) | YES |
| apply_classification_sweep.py | exists | exists | YES |
| Active systemd unit | n/a (Windows) | rexfinhub-bloomberg.service | n/a |

The crucial divergence: VPS has NEVER run apply_classification_sweep.py
(zero classification_audit_log rows). Local has run it many times but
the most recent sweep was wiped by a subsequent Bloomberg sync. Net
result: identical 100% NULL on the column the audit checks.

The `rexfinhub-bloomberg.timer` was last triggered Mon 2026-05-11
17:15:00 EDT (1h31m ago at audit time) and is next scheduled for Mon
2026-05-11 21:00:00 EDT. Each trigger fires
`rexfinhub-bloomberg.service` which calls only sync_market_data with
no post-step. The "chain" service shipped in
`deploy/systemd/rexfinhub-bloomberg-chain.service` is sitting unused
in the repo.

## Surfaces inspected

- `market/auto_classify.py` (820 lines) — full read
- `market/db_writer.py` (424 lines) — full read; F2 located at lines 240-258
- `market/config.py` — referenced via STRATEGIES import (not deeply read)
- `tools/rules_editor/classify_engine.py` (579 lines) — full read; F3 located at line 18 (RULES_DIR points to data/rules, not config/rules)
- `tools/rules_editor/categorize.py` — not in repo at given path
- `tools/rules_editor/ai_classify.py` — not in repo at given path
- `webapp/models.py` — read lines 361-600 (MktMasterData + MktFundClassification + indexes)
- `webapp/services/market_sync.py` — read lines 200-300 (the wipe at line 244)
- `scripts/run_daily.py` — read lines 1-470, 700-1029 (run_classification at line 376, run_market_sync at line 282, classification sweep at line 320-340)
- `scripts/preflight_check.py` — full read; audit_attribution_completeness at line 424; primary_strategy query at line 446
- `scripts/apply_classification_sweep.py` — full read; primary_strategy mapping at line 65-80, write logic at line 540-603
- `scripts/apply_fund_master.py` — read lines 1-285 (full); writes primary_strategy via line 250 SQL UPDATE
- `scripts/apply_issuer_brands.py` — header comment confirms it must run AFTER every sync_market_data
- `config/rules/fund_mapping.csv` — schema confirmed: ticker, etp_category, is_primary, source (NO primary_strategy column)
- `config/rules/fund_master.csv` — schema confirmed: 28 columns including full 3-axis taxonomy, 7,231 data rows
- `data/rules/fund_mapping.csv` — 2,328 rows (vs config/rules with 2,300 — drift of 28 rows)
- `deploy/systemd/rexfinhub-bloomberg.service` — ACTIVE on VPS, calls only sync_market_data
- `deploy/systemd/rexfinhub-bloomberg-chain.service` — exists in repo, NOT the active timer target

## Surfaces NOT inspected

- `market/config.py` (only confirmed STRATEGIES import is satisfied)
- `tools/rules_editor/categorize.py` and `ai_classify.py` (path not found in repo — may have been moved or deleted; not relevant to the smoking gun anyway)
- `webapp/routers/admin.py::admin_classification_stats` — not traced; could give yet another view
- The 41 other files containing "primary_strategy" string — sampled enough to be confident the mystery is the write path, not the read path.
- VPS systemd journal — would confirm whether `rexfinhub-bloomberg.service` actually completes successfully on every cycle (assumption: yes, because mkt_pipeline_runs.id=337 has status='completed' and master_rows_written=7361). Worth verifying in Stage 2.
- `webapp/services/screener_3x_cache.py` — touched by the daily run after sweep but not relevant to primary_strategy population.

# Fix R5 — Cache desync code fix

**Branch**: `audit-fix-R5-cache`
**Date**: 2026-05-11
**Owner**: implementer (worktree `agent-a83fdb0b8e39c126b`)

## Context

Stage 1 financial-numbers audit discovered that `mkt_report_cache.flow_report`
ships a JSON blob to recipients that is sign-flipped vs the DB it claims to
mirror (REX 1W flow shown as +$206.3M while DB recomputes to -$64.7M for
`pipeline_run_id=304`). Grand AUM off by $336B (~2.2%); per-suite KPIs shifted
between 5% and 700%.

The caching audit traced the desync to three independent code-level defects:

1. `screener_3x_cache.save_to_db` does not set `pipeline_run_id` on the cache
   row — the column was always `NULL`.
2. `report_data._read_report_cache` had a freshness guard that only ran when
   `row.pipeline_run_id` was truthy. Combined with (1), the staleness check
   was a permanent no-op for the screener row, AND a broad `except Exception`
   silently swallowed the `None < int` `TypeError` thrown when comparing
   `latest_run` against a NULL `row.pipeline_run_id` for any other future
   row that hits the same condition.
3. `FilingAnalysis` had `UNIQUE(filing_id)` — `writer_model` is stored on the
   row but never participated in the uniqueness key, so a writer-model upgrade
   (e.g. Sonnet -> Opus) silently served the stale narrative forever.

A fourth defect was flagged in the audit (`_capm_seed_if_empty` rewriting
timestamps on every cold start, bypassing the audit log) and is also fixed
here as the optional follow-up.

This change is **code-only**. No cache rows are flushed; that is Wave 2 work.

## Changes

### 1. `webapp/services/screener_3x_cache.py` — `save_to_db` now stamps `pipeline_run_id`

`save_to_db(db, data)` gains an optional `pipeline_run_id: int | None = None`
parameter. When the caller does not supply a value, the function auto-derives
the latest `MktPipelineRun.id` so the row is never persisted with `NULL`.

```python
def save_to_db(db, data: dict, pipeline_run_id: int | None = None) -> None:
    ...
    if pipeline_run_id is None:
        try:
            pipeline_run_id = db.execute(
                select(MktPipelineRun.id)
                .order_by(MktPipelineRun.id.desc())
                .limit(1)
            ).scalar()
            if pipeline_run_id is None:
                log.warning("save_to_db: no MktPipelineRun rows exist; ...")
            else:
                log.info("save_to_db: pipeline_run_id auto-derived to %d ...",
                         pipeline_run_id)
        except Exception as e:
            log.warning("save_to_db: failed to auto-derive pipeline_run_id: %s", e)
            pipeline_run_id = None

    db.execute(delete(MktReportCache).where(MktReportCache.report_key == "screener_3x"))
    row = MktReportCache(
        pipeline_run_id=pipeline_run_id,   # <-- previously omitted -> NULL
        report_key="screener_3x",
        data_json=json.dumps(data, default=str),
        data_as_of=data.get("data_date", ""),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    log.info("Screener cache saved to DB (pipeline_run_id=%s)", pipeline_run_id)
```

The internal `warm_cache` caller relies on the auto-derive path (no `run_id`
in scope on the warm path).

### 2. `webapp/services/report_data.py` — explicit handling, NULL = stale

The broad `except Exception` is gone. Errors are caught at narrow boundaries
(`SQLAlchemyError` for DB ops, `ValueError`/`TypeError` for JSON parse).
A `NULL` `pipeline_run_id` on a cache row is now treated as **stale** and
forces a rebuild, with a `WARNING`-level log so the upstream writer can be
identified.

Diff (essential):

```python
# OLD
if latest_run and row.pipeline_run_id and row.pipeline_run_id < latest_run:
    log.info(...stale...)
    return None
return json.loads(row.data_json)
# ...
except Exception as e:
    log.debug("Report cache read failed for %s: %s", key, e)
return None

# NEW
if row.pipeline_run_id is None:
    log.warning(
        "Report cache '%s' has pipeline_run_id=NULL; treating as stale "
        "and forcing rebuild (latest_run=%s). ...",
        key, latest_run,
    )
    return None

if latest_run is not None and row.pipeline_run_id < latest_run:
    log.info("Report cache '%s' is stale (run %d vs latest %d), rebuilding",
             key, row.pipeline_run_id, latest_run)
    return None

try:
    return json.loads(row.data_json)
except (ValueError, TypeError) as e:
    log.warning("Report cache '%s' has malformed JSON, rebuilding: %s", key, e)
    return None
```

Imports updated to add `from sqlalchemy.exc import SQLAlchemyError`.

### 3. `webapp/models.py` — `FilingAnalysis` UNIQUE includes `writer_model`

```python
class FilingAnalysis(Base):
    __tablename__ = "filing_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("filings.id"), nullable=False, index=True,
        # NOTE: 'unique=True' removed; uniqueness now lives on the composite
        # constraint below.
    )
    ...
    writer_model: Mapped[str | None] = mapped_column(String)
    ...

    __table_args__ = (
        UniqueConstraint(
            "filing_id", "writer_model",
            name="uq_filing_analyses_filing_writer",
        ),
    )
```

### 4. `webapp/database.py` — `_capm_seed_if_empty` respects audit log

The seeder now refuses to populate `capm_products` / `capm_trust_aps` when
`capm_audit_log` has any entries — a non-empty audit trail means an admin
made manual edits that we must not silently overwrite.

```python
# Guard: if capm_audit_log has any entries, do NOT reseed silently.
_cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
             "AND name='capm_audit_log'")
if _cur.fetchone():
    _cur.execute("SELECT COUNT(*) FROM capm_audit_log")
    _audit_count = (_cur.fetchone() or [0])[0]
    if _audit_count > 0:
        _l.info("CapM seed skipped: capm_audit_log has %d entries — "
                "refusing to overwrite admin edits. ...", _audit_count)
        return
```

## FilingAnalysis migration script (apply manually)

The project has no `migrations/` directory. Apply by hand against
`data/etp_tracker.db` (and the Render copy on next sync). The schema change
must run AFTER the new `webapp.database._migrate_missing_columns()` runs at
startup so `writer_model` exists as a column.

SQLite cannot drop a `UNIQUE` index that was declared inline as
`UNIQUE` on the column itself (no name was assigned). The legacy DDL
generated by SQLAlchemy created an autoincremented index name like
`sqlite_autoindex_filing_analyses_1`. We need to identify it, drop it,
add the new composite unique index, and add a non-unique index on
`filing_id` for lookup speed.

### Forward migration

```sql
-- 1. Identify the autoindex protecting filing_id (unique). Confirm name:
SELECT name FROM sqlite_master
WHERE type='index' AND tbl_name='filing_analyses' AND sql IS NULL;
-- Expected: 'sqlite_autoindex_filing_analyses_1'

-- 2. Sanity check: any existing duplicate (filing_id, writer_model) pairs?
SELECT filing_id, writer_model, COUNT(*) AS n
FROM filing_analyses
GROUP BY filing_id, writer_model
HAVING n > 1;
-- Expected: zero rows. If non-zero, deduplicate first (keep latest analyzed_at).

-- 3. Rebuild table to drop the inline UNIQUE on filing_id.
--    SQLite cannot ALTER away an inline column UNIQUE without a table rebuild.
BEGIN TRANSACTION;

CREATE TABLE filing_analyses_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id INTEGER NOT NULL REFERENCES filings(id),
    analyzed_at DATETIME NOT NULL,
    prospectus_url VARCHAR,
    objective_excerpt TEXT,
    strategy_excerpt TEXT,
    filing_title VARCHAR,
    strategy_type VARCHAR,
    underlying VARCHAR,
    structure VARCHAR,
    portfolio_holding VARCHAR,
    distribution VARCHAR,
    narrative TEXT,
    interestingness FLOAT,
    selector_reason VARCHAR,
    selector_model VARCHAR,
    writer_model VARCHAR,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd FLOAT,
    CONSTRAINT uq_filing_analyses_filing_writer
        UNIQUE (filing_id, writer_model)
);

INSERT INTO filing_analyses_new
SELECT id, filing_id, analyzed_at, prospectus_url, objective_excerpt,
       strategy_excerpt, filing_title, strategy_type, underlying, structure,
       portfolio_holding, distribution, narrative, interestingness,
       selector_reason, selector_model, writer_model, tokens_in, tokens_out,
       cost_usd
FROM filing_analyses;

DROP TABLE filing_analyses;
ALTER TABLE filing_analyses_new RENAME TO filing_analyses;

CREATE INDEX ix_filing_analyses_filing_id ON filing_analyses(filing_id);

COMMIT;
```

### Rollback (reversible)

```sql
BEGIN TRANSACTION;

CREATE TABLE filing_analyses_old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id INTEGER NOT NULL UNIQUE REFERENCES filings(id),
    analyzed_at DATETIME NOT NULL,
    prospectus_url VARCHAR,
    objective_excerpt TEXT,
    strategy_excerpt TEXT,
    filing_title VARCHAR,
    strategy_type VARCHAR,
    underlying VARCHAR,
    structure VARCHAR,
    portfolio_holding VARCHAR,
    distribution VARCHAR,
    narrative TEXT,
    interestingness FLOAT,
    selector_reason VARCHAR,
    selector_model VARCHAR,
    writer_model VARCHAR,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd FLOAT
);

-- Rollback requires that NO duplicate filing_id rows exist. If they do,
-- the writer-model upgrade has produced multiple narratives and you must
-- decide which one wins before reverting:
--   DELETE FROM filing_analyses WHERE id NOT IN
--     (SELECT MIN(id) FROM filing_analyses GROUP BY filing_id);

INSERT INTO filing_analyses_old
SELECT id, filing_id, analyzed_at, prospectus_url, objective_excerpt,
       strategy_excerpt, filing_title, strategy_type, underlying, structure,
       portfolio_holding, distribution, narrative, interestingness,
       selector_reason, selector_model, writer_model, tokens_in, tokens_out,
       cost_usd
FROM filing_analyses;

DROP TABLE filing_analyses;
ALTER TABLE filing_analyses_old RENAME TO filing_analyses;

CREATE INDEX ix_filing_analyses_filing_id ON filing_analyses(filing_id);

COMMIT;
```

## Caller wiring (out of this worktree's scope)

`save_to_db` has two callers:

1. `webapp/services/screener_3x_cache.warm_cache` (in this worktree) — left
   on the auto-derive path.
2. `webapp/services/market_sync._compute_and_cache_screener` (line 671) —
   `run_id` is in scope two frames up (line 238). The follow-up implementer
   should change the signature to `_compute_and_cache_screener(db, run_id)`
   and pass `pipeline_run_id=run_id` through. Until then, the auto-derive
   safety net protects the row.

Documenting here so it does not get lost: this file (`market_sync.py`) is
NOT owned by this worktree per the assignment scope. Patch suggested:

```python
# webapp/services/market_sync.py
# line 271 (call site)
-        _compute_and_cache_screener(db)
+        _compute_and_cache_screener(db, run_id)

# line 671 (function def)
-def _compute_and_cache_screener(db: Session) -> None:
+def _compute_and_cache_screener(db: Session, run_id: int) -> None:
     ...
-    save_to_db(db, result)
+    save_to_db(db, result, pipeline_run_id=run_id)
```

## Verification

### Before — production DB state

```text
$ python -c "import sqlite3; ..."
('li_report', 304)
('cc_report', 304)
('flow_report', 304)
('screener_3x', None)        <-- the bug
```

### After — `save_to_db` correctly stamps the run id

Test harness (against a non-destructive copy of the prod DB):

| Step                               | `pipeline_run_id` written |
|------------------------------------|---------------------------|
| Initial (legacy bug)               | `None`                    |
| `save_to_db(db, data, pipeline_run_id=304)` | `304`            |
| `save_to_db(db, data)` (auto-derive) | `304` (= latest in `mkt_pipeline_runs`) |

### After — `_read_report_cache` correctly handles NULL / fresh / stale

| Cache state                                  | Behaviour                               | Log                                                                                            |
|----------------------------------------------|-----------------------------------------|------------------------------------------------------------------------------------------------|
| `pipeline_run_id IS NULL` (`screener_3x`)    | returns `None` -> rebuild forced         | `WARNING ... has pipeline_run_id=NULL; treating as stale and forcing rebuild (latest_run=304)` |
| `pipeline_run_id = latest_run` (`flow_report`) | returns `dict (7 keys)` -> served       | (no warn)                                                                                      |
| `pipeline_run_id = 1`, `latest_run = 304`    | returns `None` -> rebuild forced         | `INFO ... is stale (run 1 vs latest 304), rebuilding`                                          |

### Syntax check

```text
webapp/services/screener_3x_cache.py OK
webapp/services/report_data.py OK
webapp/models.py OK
webapp/database.py OK
```

## Rollback

Per-file revert is sufficient — these are additive code changes. No data
mutations were performed by the fix itself.

1. `git revert <commit-sha>` on branch `audit-fix-R5-cache`.
2. The schema migration (FilingAnalysis UNIQUE -> composite) is the only
   change with persistent data effect; reverse with the rollback SQL block
   above. Make sure no duplicate `filing_id` rows exist before reverting.
3. `mkt_report_cache.screener_3x` rows that get written with non-NULL
   `pipeline_run_id` after this fix lands are still valid against the legacy
   `_read_report_cache`; no flush required to roll back.

## Out of scope (deferred)

- Flushing `mkt_report_cache` rows that currently hold sign-flipped
  `flow_report` JSON. Wave 2 handles this after R1 + R2 + R6 land.
- Wiring `run_id` through `market_sync._compute_and_cache_screener`. See
  "Caller wiring" above.
- Backfilling `pipeline_run_id` on the existing `screener_3x` cache row.
  Not needed: the next pipeline run will overwrite it with a stamped row.
- `etp_tracker/filing_analysis.py:167` builds `cached_by_fid = {r.filing_id: r for r in cached_rows}`
  which collapses multiple writer-model rows to whichever appears last in
  the result set. This is now a real possibility under the new composite
  unique constraint (a filing can legitimately have a Sonnet row AND an
  Opus row). The reader needs an `ORDER BY analyzed_at DESC` (or a
  preferred-writer policy) so the freshest model wins. Out of this fix's
  scope; flag for the next pass on `filing_analysis.py`.

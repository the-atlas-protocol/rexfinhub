# Audit Fix R2 — Writer Denormalization (3-Axis Taxonomy)

**Branch**: `audit-fix-R2-writer`
**Date**: 2026-05-11
**Owner**: implementer agent

## Problem

Stage 1 classification audit found that `market/db_writer.py::write_classifications`
wrote `master_row.strategy` (legacy column) but never touched the audit-checked
3-axis taxonomy columns on `mkt_master_data`:

- `primary_strategy`
- `asset_class`
- `sub_strategy`

Result: even after the daily pipeline logged `"Unified classify: 7,359 funds
classified"`, those three columns stayed 100% NULL across all 7,361 rows. The
only path that populated them was `scripts/apply_classification_sweep.py`,
which is not invoked by the active VPS systemd unit.

The `Classification` dataclass in `market/auto_classify.py` had no fields for
the 3-axis taxonomy, so even if the writer wanted to populate them, the data
was not flowing through.

## Fix Summary

Made the writer durable by extending the data flow at two layers:

1. **`market/auto_classify.py`** — Extended `Classification` dataclass with
   three new optional fields (`primary_strategy`, `asset_class`,
   `sub_strategy`) and added `_derive_three_axis_taxonomy()` /
   `_derive_sub_strategy_for_taxonomy()` helpers that translate the legacy
   13-strategy classifier output into the 3-axis taxonomy. `classify_all()`
   now invokes these helpers per row after CSV overrides apply.
2. **`market/db_writer.py`** — Extended `write_classifications` to also write
   the three new fields onto `master_row.primary_strategy`,
   `master_row.asset_class`, `master_row.sub_strategy`. The legacy
   `master_row.strategy` write is preserved exactly as-is (additive only).

## Design Choice — Duplicate vs Import

The `derive_taxonomy` mapping logic exists in
`scripts/apply_classification_sweep.py`. We **duplicated** the mapping rather
than importing for three reasons:

1. `scripts/` has no `__init__.py` and is not on `sys.path` in normal
   webapp/pipeline flow.
2. `apply_classification_sweep` imports `webapp.database` and `webapp.models`
   at module top — importing it would break `auto_classify.py`'s top-of-file
   guarantee of "no webapp dependencies".
3. We only need the 3 audit-checked columns (primary_strategy, asset_class,
   sub_strategy), not the full 16-column sweep output.

The duplicated mapping tables (`_PRIMARY_STRATEGY_MAP`, `_ASSET_CLASS_FOCUS_MAP`)
and the sub-strategy logic are documented as needing to stay in sync with
`scripts/apply_classification_sweep.py` if that file's mapping evolves.

## Code Diff Summary

### `market/auto_classify.py`
- `Classification` dataclass gains three optional fields: `primary_strategy`,
  `asset_class`, `sub_strategy` (all `str | None = None`).
- `classify_all()` builds a `row_lookup: ticker -> pd.Series` while iterating,
  then after CSV overrides loops the results and calls
  `_derive_three_axis_taxonomy(c, row)` to populate the new fields in-place.
- New private helpers: `_derive_three_axis_taxonomy()` and
  `_derive_sub_strategy_for_taxonomy()`. New module-level dicts:
  `_PRIMARY_STRATEGY_MAP`, `_ASSET_CLASS_FOCUS_MAP`.

### `market/db_writer.py`
- `write_classifications()` final block (sync to mkt_master_data) rewritten:
  - Replaces tuple-based lookup with `class_lookup: ticker -> Classification`.
  - Preserves legacy writes: `master_row.strategy`, `strategy_confidence`,
    `underlier_type`.
  - Adds gap-only writes (only when classifier produced a value) for:
    `master_row.primary_strategy`, `master_row.asset_class`,
    `master_row.sub_strategy`.
  - New log message includes a `taxonomy_filled` count alongside the
    existing `updated` count.

## Before / After Verification

### Before fix (BASELINE on production DB)

```sql
SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NOT NULL;
-- Expected: 0 (per Stage 1 audit finding)
```

### After fix (smoke test on isolated worktree DB)

Seeded 5 master rows with realistic Bloomberg metadata (TQQQ, JEPI, BITO,
PJUL, SPY), ran `classify_all` → `write_classifications`, then queried the DB:

```
BEFORE primary_strategy non-NULL: 0 / 5
AFTER  primary_strategy non-NULL: 5 / 5
  BITO US    primary=Plain Beta       asset=Crypto       sub=Single-Access
  JEPI US    primary=Income           asset=Equity       sub=Derivative Income > Covered Call
  PJUL US    primary=Defined Outcome  asset=Equity       sub=Buffer
  SPY US     primary=Plain Beta       asset=Equity       sub=Broad
  TQQQ US    primary=L&I              asset=Equity       sub=Long
```

All 5 rows correctly populated. Existing `test_write_classifications` test
also replayed and passed (backward compatible — old call sites that build
Classification with no taxonomy fields still work).

## Rollback

```bash
git checkout main -- market/db_writer.py market/auto_classify.py
```

## Constraints Honoured

- Only edited `market/db_writer.py` and `market/auto_classify.py`.
- Additive only — legacy `master_row.strategy` write is preserved unchanged.
- No schema migrations (the three columns already exist on `MktMasterData`).
- 3-axis writes are gap-friendly (only set when classifier produces a value),
  so a curated row will never be null-out by the classifier abstaining.

## Interaction with R1

R1 separately fixes the systemd unit so the sweep runs. R2 is the durable
fallback: even if R1 is reverted (or someone removes the sweep from cron),
the daily classify step will keep the audit-checked columns populated.

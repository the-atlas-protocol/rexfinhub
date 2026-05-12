# Fix R6 — CSV rules split-brain merge

**Date:** 2026-05-11
**Branch:** `audit-fix-R6-csv`
**Owner:** implementer agent

## Problem (Stage 1 audit recap)

`tools/rules_editor/classify_engine.py` wrote classifier-approved rules to
`data/rules/`, but every other consumer (`market/config.py`,
`webapp/routers/admin.py`, `tools/rules_editor/app.py`,
`scripts/preflight_check.py`, all tests, the `mkt_*` DB) read from
`config/rules/`. The DB matched `config/rules/` byte-for-byte. Result: every
classifier-approved fund since the migration was invisible to the live site.

Additionally, `step6_apply_category_attributes` joined attribute rows on
`ticker` only — so a ticker that legitimately appears in
`attributes_Crypto.csv` (because it is a Crypto fund) would also pull in any
stale `attributes_Thematic.csv` row that shared the same ticker.

## Diff counts (before merge)

| File                      | config/rules/ rows | data/rules/ rows | Delta |
|---------------------------|--------------------|------------------|-------|
| fund_mapping.csv          | 2300               | 2327             | +34 (data has 34 keys cfg lacks) |
| issuer_mapping.csv        | 341                | 342              | +2  |
| attributes_LI.csv         | 877                | 895              | +18 |
| attributes_CC.csv         | 342                | 348              | +10 (cfg also had 4 unique to it) |
| attributes_Crypto.csv     | 146                | 149              | +3  |
| attributes_Defined.csv    | 527                | 529              | +2  |
| attributes_Thematic.csv   | 420                | 421              | +1  |

Note: the audit estimated "27 extra rows" in fund_mapping; the actual figure
came in at **34** new (ticker, etp_category) keys after de-dup.

## Merge strategy

Primary keys per file:

| File                      | Primary key                  |
|---------------------------|------------------------------|
| fund_mapping.csv          | `(ticker, etp_category)`     |
| issuer_mapping.csv        | `(etp_category, issuer)`     |
| attributes_*.csv          | `ticker`                     |

Conflict resolution: **`data/rules/` wins on conflict**, since classifier
output is the newer version (the manual `config/rules/` snapshot pre-dated
the migration). Conflict observations:

- **fund_mapping**: 6 conflicts, all of the form `source = manual` in cfg
  vs `source = atlas` in data. Cosmetic; atlas wins.
- **attributes_LI**: 1 conflict, value-cell change. Data wins.
- **attributes_CC**: 2 conflicts, value-cell changes. Data wins.
- All other files: 0 conflicts.

After merge, all rows present in either source survive. `data/rules/` was
git-tracked (verified via `git ls-files data/rules/`); files were `git rm`-d
and replaced with a `README.md` pointing future contributors at
`config/rules/`.

## Cross-category leakage (18 tickers)

Tickers that have an attribute row in one category file but whose
`fund_mapping.csv` entry says they belong to a different category. These are
now **harmless at runtime** because the new `step6_apply_category_attributes`
joins on `(ticker, etp_category)`, but the orphan rows in the per-category
attribute CSVs should still be deleted manually.

| Ticker     | Attribute file says | fund_mapping says |
|------------|---------------------|--------------------|
| KQQQ US    | CC                  | Thematic           |
| TESL US    | CC                  | Thematic           |
| QBUL US    | CC                  | Defined            |
| OOSB US    | Crypto              | LI                 |
| OOQB US    | Crypto              | LI                 |
| ACEI US    | Defined             | CC                 |
| ACII US    | Defined             | CC                 |
| SPYH US    | Defined             | CC                 |
| NDIV US    | Thematic            | CC                 |
| BITQ US    | Thematic            | Crypto             |
| WGMI US    | Thematic            | Crypto             |
| FDIG US    | Thematic            | Crypto             |
| CRPT US    | Thematic            | Crypto             |
| SATO US    | Thematic            | Crypto             |
| DECO US    | Thematic            | Crypto             |
| HECO US    | Thematic            | Crypto             |
| STCE US    | Thematic            | Crypto             |
| NODE US    | Thematic            | Crypto             |

**Recommended action (Stage 2):** review each row. If the attribute row was
created in error, delete it from the per-category CSV. If the fund actually
spans both categories (e.g. a crypto fund that pays an income coupon),
either add a second `fund_mapping.csv` row for the secondary category or
move the attribute row to the correct file. The audit asked for "11
tickers"; the merged state surfaced **18** — 7 more than expected, all in
the Thematic-vs-Crypto pair.

## Code diff

### `tools/rules_editor/classify_engine.py`

```diff
-# Write to data/rules/ (source of truth), not config/rules/ (git-tracked copy)
-RULES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "rules"
+# Write to config/rules/ — the single source of truth consumed by the live site,
+# market/config.py, webapp/routers/admin.py, and the mkt_* DB tables.
+# (Previously wrote to data/rules/ which was a split-brain copy invisible to
+# every other consumer; see docs/audit_2026-05-11/fix_R6.md.)
+RULES_DIR = _CONFIG_RULES_DIR
```

### `market/transform.py`

`step6_apply_category_attributes` now re-loads each per-category attribute
CSV at call time, tags every row with its source `etp_category`, and joins
on `(ticker, etp_category)` rather than `ticker` alone. The unified
`category_attributes` DataFrame supplied by `rules.load_category_attributes`
is still accepted for signature compatibility and short-circuit detection,
but is no longer used for the join itself. A defensive fallback to the old
ticker-only join is kept for the case where `etp_category` is missing from
the input frame (should not occur in practice — step3 always sets it).

### `data/rules/`

- All CSVs and `_queues_report.json` removed via `git rm`.
- Replaced with a single `README.md` redirecting future contributors to
  `config/rules/`.

## Verification

```text
$ python -c "from market.config import RULES_DIR; print(RULES_DIR)"
C:\Projects\rexfinhub\.claude\worktrees\agent-aabc420f8badcfbf2\config\rules

$ python -c "from tools.rules_editor.classify_engine import RULES_DIR; print(RULES_DIR)"
C:\Projects\rexfinhub\.claude\worktrees\agent-aabc420f8badcfbf2\config\rules
```

Synthetic write test (added a `TESTFIX_R6 US` candidate via
`apply_classifications`, confirmed it landed in
`config/rules/fund_mapping.csv` and `config/rules/attributes_Crypto.csv`,
then deleted it).

Cross-category fix unit test: ran step6 on a frame containing
`('BITQ US', 'Crypto')` and confirmed only `q_category_attributes.map_crypto_*`
columns are populated; `q_category_attributes.map_thematic_category` is
NaN, even though `attributes_Thematic.csv` has a `BITQ US` row.

## Final row counts (after merge)

| File                      | Rows |
|---------------------------|------|
| fund_mapping.csv          | 2334 |
| issuer_mapping.csv        | 344  |
| attributes_LI.csv         | 895  |
| attributes_CC.csv         | 352  |
| attributes_Crypto.csv     | 149  |
| attributes_Defined.csv    | 529  |
| attributes_Thematic.csv   | 421  |

## Rollback

```bash
# 1. Restore the pre-merge config/rules/ snapshot
cp -r config/rules.bak_2026-05-11/* config/rules/

# 2. Restore data/rules/ from git
git checkout HEAD~1 -- data/rules/

# 3. Revert classify_engine.py
git checkout HEAD~1 -- tools/rules_editor/classify_engine.py

# 4. Revert transform.py
git checkout HEAD~1 -- market/transform.py
```

The backup directory `config/rules.bak_2026-05-11/` is gitignored (it lives
under `config/rules.bak_*` which is not currently in `.gitignore`; if the
backup needs to survive across worktrees, add `config/rules.bak_*` to
`.gitignore` and copy it to the project root before merging).

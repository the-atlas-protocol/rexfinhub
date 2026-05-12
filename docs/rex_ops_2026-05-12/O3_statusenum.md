# O3 — rex_products status enum collapse (15 → 6)

**Owner**: O3 — status enum
**Branch**: `rexops-O3-statusenum`
**Worktree**: `C:/Projects/rexfinhub-O3`
**Date**: 2026-05-12

---

## 1. Goal

Collapse the bloated 15-value `rex_products.status` enum back to the 6 values that the REX ops team actually uses day-to-day, per Ryu's May 2026 ops review. Only 6 of 15 enum values are populated in production today; the granular Counsel/Board/485A/485B splits are noise in the primary status column and live more naturally in the audit log (for stage history) and `latest_form` (for SEC form-type detail).

---

## 2. Target 6-value lifecycle (left-to-right)

| # | Status                | Meaning                                                                  |
|---|-----------------------|--------------------------------------------------------------------------|
| 1 | Under Consideration   | Researched, in counsel review, in board pipeline. Not yet a Target List. |
| 2 | Target List           | Formally targeted for build but not yet under counsel review.            |
| 3 | Filed                 | SEC filing in (485A or 485B); effective date NOT yet set.                |
| 4 | Effective             | SEC effective. Pre-launch / awaiting LMM seed.                           |
| 5 | Listed                | Live on exchange.                                                        |
| 6 | Delisted              | Off exchange (liquidated, expired, dropped).                             |

485A vs 485B distinction is preserved in `rex_products.latest_form`, not in status.

---

## 3. Mapping table (old → new)

| Old status              | New status            | Notes                                            |
|-------------------------|-----------------------|--------------------------------------------------|
| Research                | Under Consideration   | (Ryu: NOT Target List)                           |
| Counsel Review          | Under Consideration   |                                                  |
| Counsel Approved        | Under Consideration   |                                                  |
| Counsel Withdrawn       | Under Consideration   |                                                  |
| Pending Board           | Under Consideration   |                                                  |
| Board Approved          | Under Consideration   |                                                  |
| Not Approved by Board   | Under Consideration   |                                                  |
| Board (legacy)          | Under Consideration   |                                                  |
| Counsel (legacy)        | Under Consideration   |                                                  |
| PEND (legacy code)      | Under Consideration   |                                                  |
| Target List             | Target List           | Pass-through.                                    |
| Target (legacy)         | Target List           |                                                  |
| Filed                   | Filed                 | Pass-through.                                    |
| Filed (485A)            | Filed                 | 485A flag preserved in `latest_form`.            |
| Filed (485B)            | Filed                 | 485B flag preserved in `latest_form`.            |
| Awaiting Effective      | **Filed** OR **Effective** | Conditional: if `estimated_effective_date` IS NULL → Filed; if NOT NULL → Effective. |
| Effective               | Effective             | Pass-through.                                    |
| Listed                  | Listed                | Pass-through.                                    |
| ACTV (legacy code)      | Listed                |                                                  |
| Delisted                | Delisted              | Pass-through.                                    |
| LIQU (legacy code)      | Delisted              |                                                  |
| INAC (legacy code)      | Delisted              |                                                  |
| EXPD (legacy code)      | Delisted              |                                                  |
| DLST (legacy code)      | Delisted              |                                                  |

The Awaiting Effective branch is the only conditional case (depends on `estimated_effective_date`).

---

## 4. Before / After distribution (production data, 2026-05-12)

### BEFORE

| Status               | Count | %     |
|----------------------|-------|-------|
| Awaiting Effective   | 327   | 45.2% |
| Filed                | 273   | 37.8% |
| Listed               | 85    | 11.8% |
| Filed (485A)         | 21    | 2.9%  |
| Delisted             | 11    | 1.5%  |
| Research             | 6     | 0.8%  |
| **TOTAL**            | **723** | 100% |

### AFTER (projected = actual on test DB)

| Status               | Count | %     |
|----------------------|-------|-------|
| Effective            | 325   | 45.0% |
| Filed                | 296   | 40.9% |
| Listed               | 85    | 11.8% |
| Delisted             | 11    | 1.5%  |
| Under Consideration  | 6     | 0.8%  |
| Target List          | 0     | 0.0%  |
| **TOTAL**            | **723** | 100% |

The 2 "Awaiting Effective with NO est_effective_date" rows correctly fall back to Filed. The remaining 325 Awaiting rows have a date and become Effective.

Target List shows 0 — production has no rows in that bucket today. The status remains in the enum because Ryu wants it as a deliberate-build-pipeline slot upstream of Filed.

---

## 5. Migration script

**Path**: `scripts/migrate_rex_status_2026-05-12.py`

### Flags

| Flag             | Purpose                                                              |
|------------------|----------------------------------------------------------------------|
| `--db PATH`      | SQLite path. Default `data/etp_tracker.db`.                          |
| `--apply`        | Actually mutate. Default is dry-run.                                 |
| `--i-agree-prod` | Non-interactive consent for production DB writes (CI escape hatch).  |

### Safety rails

- Default is dry-run (no `--apply` → no writes).
- If `--apply` AND `--db` points at the production filename (`etp_tracker.db`) AND `--i-agree-prod` is NOT passed: the script prompts for the literal string "I AGREE" via stdin before mutating. Any other answer aborts.
- All writes go through `UPDATE ... WHERE id = ?` with a parallel `INSERT INTO capm_audit_log`. Either both succeed or the transaction rolls back (single `con.commit()` at end).
- Any row whose `old_status` is not in the mapping table aborts the script with a list of unmapped values — no partial writes.

### Audit log

Writes to existing `capm_audit_log` table (generic audit shape). One row per migrated rex_products row, with:

```
action       = "update"
table_name   = "rex_products"
row_id       = rex_products.id
field_name   = "status"
old_value    = "<old status string>"
new_value    = "<new status string>"
row_label    = "<ticker> | <name>" (truncated to 200 chars)
changed_by   = "migrate_rex_status_2026-05-12"
changed_at   = UTC ISO timestamp
```

This makes the migration fully reversible: a single `UPDATE rex_products SET status = audit.old_value FROM capm_audit_log audit WHERE rex_products.id = audit.row_id AND audit.changed_by = 'migrate_rex_status_2026-05-12'` rolls it back.

### Test DB validation (already run by O3)

```
$ cp data/etp_tracker.db data/etp_tracker.test.db
$ python scripts/migrate_rex_status_2026-05-12.py --apply \
      --db C:/Projects/rexfinhub/data/etp_tracker.test.db
```

Result: 354 rows updated, 354 audit log entries written, before/after counts match the projection above.

### Recommended production rollout (coordinator runs)

```bash
# 1. Backup
cp data/etp_tracker.db data/etp_tracker.backup_2026-05-12.db

# 2. Dry-run against prod (read-only)
python scripts/migrate_rex_status_2026-05-12.py

# 3. If output matches the table above, apply (will prompt "I AGREE"):
python scripts/migrate_rex_status_2026-05-12.py --apply

# 4. Verify
sqlite3 data/etp_tracker.db \
  "SELECT status, COUNT(*) FROM rex_products GROUP BY 1 ORDER BY 2 DESC"
```

---

## 6. Dry-run output (verbatim, against test DB)

```
DB:        C:\Projects\rexfinhub\data\etp_tracker.test.db
Mode:      DRY-RUN
Prod DB:   no (test/dev copy)

--- BEFORE ---
  'Awaiting Effective'               327  ( 45.2%)
  'Filed'                            273  ( 37.8%)
  'Listed'                            85  ( 11.8%)
  'Filed (485A)'                      21  (  2.9%)
  'Delisted'                          11  (  1.5%)
  'Research'                           6  (  0.8%)
  TOTAL                              723

--- PROJECTED AFTER ---
  'Effective'                        325  ( 45.0%)
  'Filed'                            296  ( 40.9%)
  'Listed'                            85  ( 11.8%)
  'Delisted'                          11  (  1.5%)
  'Under Consideration'                6  (  0.8%)
  TOTAL                              723

--- MAPPING PLAN  (354 row updates) ---
  'Awaiting Effective'             -> 'Effective'              : 325 rows
  'Filed (485A)'                   -> 'Filed'                  : 21 rows
  'Research'                       -> 'Under Consideration'    : 6 rows
  'Awaiting Effective'             -> 'Filed'                  : 2 rows

Dry run — no writes. Re-run with --apply to mutate.
```

---

## 7. Code changes in this PR

| File                                       | Change                                                                                                          |
|--------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| `scripts/migrate_rex_status_2026-05-12.py` | NEW — migration script (dry-run by default, audit-logged, prod safety prompt).                                  |
| `webapp/routers/pipeline_calendar.py`      | `VALID_STATUSES`, `STATUS_COLORS`, `PENDING_EFFECTIVE_STATUSES` collapsed to the 6-value enum. Funnel order + KPI queries rewritten to match. |
| `webapp/models.py`                         | Docstring + inline status comment on `RexProduct` updated to reflect 6-value enum.                              |

NOT touched (per task constraints):
- `webapp/templates/pipeline_products.html` (O1 owns)
- Days-in-stage logic (O2 owns)

---

## 8. Follow-ups for downstream tickets

1. **Template pass (O1)** — the `awaiting` KPI variable in `_pipeline_products_impl` is currently populated with the `Effective` count to keep the existing template wiring intact. O1 should rename the variable / label on the page to "Effective" (and ideally remove the deprecated name).
2. **Status history table** — when `rex_product_status_history` lands, restore the granular Counsel/Board stages there for audit/timeline views, rather than re-bloating the primary status column.
3. **Production migration** — coordinator runs `--apply` after reviewing this doc. The backup step is non-optional.

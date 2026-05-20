---
adr: 0009
title: Phase 6 — Single classification_override table + ops-as-assertions
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0009 — Single `classification_override` table + ops-as-assertions

## Context

Today the classification pipeline reads from **6 rule CSVs** in `config/rules/`:
- `fund_mapping.csv` — ticker → etp_category
- `issuer_mapping.csv` — issuer_name → etp_category
- `attributes_LI.csv`, `attributes_CC.csv`, `attributes_Crypto.csv`, `attributes_Defined.csv`, `attributes_Thematic.csv` — per-category attribute overrides
- `issuer_brand_overrides.csv` — issuer_display canonicalization

Each CSV is hand-edited by Ryu and shipped via `config/rules/` (git-tracked) ↔ `data/rules/` (working copy). Problems:

1. **Three copies of the same rule** sometimes exist — local `data/rules/`, git-tracked `config/rules/`, VPS-deployed `/home/jarvis/rexfinhub/config/rules/`. The classifier reads from `config/rules/`. When they drift (which happens), classifications silently differ between dev and prod.
2. **Hand-editing CSVs is the third explicit touchpoint** in Ryu's daily ops — the very thing the rebuild is trying to eliminate (TARGET.md `### ideal-day`).
3. **No audit trail of override edits**. Git tracks them but per-row history is lost in noisy CSV diffs.
4. **CSV encoding bugs** keep recurring (BUG-08 — cp1252 em-dash byte 0x97 broke CI today). Text-file rules with no schema validation.
5. **Cross-CSV consistency is unenforced**. A ticker can be classified `LI` in `fund_mapping` but have entries in `attributes_Crypto`; nothing catches the contradiction until a downstream report crashes.

ADR 0001 / TARGET.md `### principles` calls for: **one `classification_override(product_id, field_name, value, set_by, reason)` table** replacing all 6 CSVs, and **~25 dbt-style data-quality assertions** run daily, with failures surfaced in the 08:00 ET triage email. This ADR designs both.

## Decision

### 1. `classification_override` table

```sql
classification_override (
  canonical_id   UUID NOT NULL REFERENCES product_master(canonical_id),
  field_name     TEXT NOT NULL,    -- 'etp_category','issuer_display','is_rex',
                                   -- 'primary_strategy','asset_class','sub_strategy',
                                   -- 'mechanism','direction','leverage_ratio',
                                   -- 'reset_period','cap_pct','buffer_pct','barrier_pct',
                                   -- 'concentration','region','duration_bucket',
                                   -- 'credit_quality','underlier_id','expense_ratio_override'
  value          TEXT,             -- NULL is meaningful: "explicitly blacklist this product
                                   --   from auto-classification on this field"
  set_by         TEXT NOT NULL,    -- 'admin:<user>' | 'auto_classifier_v<N>' | 'csv_import:<file>'
  set_at         TIMESTAMPTZ NOT NULL,
  reason         TEXT,             -- free text the admin entered when overriding
  PRIMARY KEY (canonical_id, field_name)
);
```

Resolution at read time, per field:

```python
def resolve(canonical_id, field):
    # 1. Admin/manual override wins absolutely
    row = classification_override.get(canonical_id, field)
    if row:
        return row.value   # may be NULL = explicit blacklist
    # 2. Bloomberg classification (when present)
    if bloomberg_has(canonical_id, field):
        return bloomberg_value(canonical_id, field)
    # 3. Auto-classifier (rule-based on fund name + Bloomberg attrs)
    return auto_classify(canonical_id, field)
```

The 6 CSVs migrate as follows:
- `fund_mapping.csv` → `classification_override` rows with `field_name='etp_category'`
- `issuer_mapping.csv` → no longer needed; auto_classifier reads issuer from `product_master` and applies rules in code
- `attributes_*.csv` → `classification_override` rows with `field_name=<attribute>`
- `issuer_brand_overrides.csv` → `classification_override` rows with `field_name='issuer_display'`

Admin write path: new `/admin/products/{canonical_id}/classify` form. POST writes one row to `classification_override` with `set_by='admin:<user>'`, capm_audit_log gets a parallel entry.

### 2. Ops-as-assertions (~25 tests)

Replaces hand-eyeballed CSV review with a daily test suite. Each test returns pass / fail / count + sample failures. Run after every Bloomberg sync (17:30 ET) + reported in the 08:00 ET triage email.

Asserts (per TARGET.md `### ops-as-assertions`, formalized here):

**Freshness (5)**:
- Bloomberg file mtime ≥ today 17:00 ET
- `mkt_master_data` row count delta < 5% day-over-day
- `rex_products` updated_at within last 24h for ≥ 95% of Listed rows
- atom-watcher last cycle within 5 min
- `submissions.zip` mtime < 24h

**Classification coverage (5)**:
- Every active REX product has `primary_strategy` populated
- Every active product has resolved `underlier_id` (post Phase 4)
- Every active product has `etp_category` (LI / CC / Crypto / Defined / Thematic / Plain Beta)
- Every Listed product has `mkt_master_data.market_status = 'ACTV'`
- No active product has classification_override conflicting with auto_classify rule (manual review item)

**Lifecycle integrity (5)** (post Phase 5):
- No `status='listed'` without 3-source evidence
- No date inversion: `inception_date >= initial_filing_date`
- No ticker appearing in >1 active `rex_products` row
- Every `Filed` rex_product has matching `filings` row in DB
- Every Listed product's `latest_form` is one of {485BPOS, 497, N-1A}

**Send-pipeline health (5)**:
- Yesterday's send-log has expected number of report entries (5-8 depending on weekday)
- No SMTP fallback in send-log (Graph-only in production)
- Preflight passed (or warn-with-flag) within last 24h
- Auto-GO decision file age < 24h
- No subscriber list has 0 active recipients

**Reports KPI consistency (3)**:
- Flow report REX 1W KPI matches issuer-table REX row sum (closes BUG-05 detection — though doesn't fix root cause until Phase 6 survivorship rules also land)
- LI report row count delta day-over-day < 10%
- Distribution calendar event count >0 for Mon-Thu

**Secrets (2)**:
- `AZURE_CLIENT_SECRET` Azure-Portal-expiry > 30 days away
- `API_KEY` rotation age < 90 days

Test framework: thin wrapper inspired by `dbt test` / `Great Expectations`. Each test is a Python function returning `(passed: bool, count: int, sample: list[dict])`. Results land in a new `assertion_run` table (timestamped) for trend analysis.

### Daily summary email format

The 08:00 ET pipeline summary email (moved from 20:15) leads with:

```
=== REX FINHUB | Daily Triage | 2026-05-19 ===
Overall: 23 / 25 passed   (2 attention items)

▼ Classification coverage
  ✗ etp_category missing       (3 funds: ABCD, EFGH, IJKL)
  ✓ primary_strategy populated
  ✓ underlier_id resolved
  ...
▼ Lifecycle integrity
  ✗ Date inversion             (1 fund: MNOP — inception 2026-04-01, filing 2026-04-15)
  ...
```

The link on each failure goes to `/admin/triage/{assertion_id}` which lets Ryu inspect the failing rows + apply an override (=> writes `classification_override` row with `set_by='admin:<user>', reason=<text>`).

### Stages

**Stage 1 — Schema** (additive).
- `classification_override` + `assertion_run` tables.
- New SQLAlchemy models.

**Stage 2 — Migrate the 6 CSVs into `classification_override`**.
- One-off script `scripts/migrate_csv_rules_to_db.py` reads every row and inserts with `set_by='csv_import:<filename>', reason='Initial migration from CSV'`.
- Validation: row counts match (CSV row count == DB row count per field).

**Stage 3 — Resolver code + auto-classifier reads override-first**.
- `webapp/services/classify_engine.py` becomes a thin wrapper that delegates to `webapp/services/classification_resolver.py`.
- The new resolver follows the 3-step priority (override → Bloomberg → auto_classify).
- Auto-classifier itself becomes pure-Python rules (no CSV reads), invoked only as a fallback.

**Stage 4 — Admin write surface**.
- `/admin/products/{canonical_id}/classify` form (HTMX-style inline edit on `/operations/products`).
- Writes go through `classification_override`; audit log via `capm_audit_log`.
- The 6 CSVs become read-only (kept in `config/rules/` for revert; classifier no longer reads them).

**Stage 5 — Build the assertion runner**.
- `scripts/run_assertions.py` invoked from the new daily 08:00 ET timer.
- 25 assertions implemented per the categories above.
- 08:00 email template rendering the summary.

**Stage 6 — Move the pipeline summary email from 20:15 to 08:00**.
- New timer `rexfinhub-morning-summary.timer` at 08:00 ET.
- Old `rexfinhub-pipeline-summary` timer at 20:15 disabled.

**Stage 7 — Delete the CSV files**.
- After ≥7 days of clean operation with classification_override as truth.
- `config/rules/*.csv` removed from git.

## Consequences

**Wins**:
- Eliminates the third (and largest) of Ryu's daily touchpoints — no more CSV editing.
- One source of truth for classification overrides; cross-CSV inconsistencies become impossible.
- Audit trail per override (who, when, why) replaces git blame on CSV files.
- Encoding bugs (BUG-08 class) impossible — DB stores text correctly via SQLAlchemy.
- Daily test failures become triage items, not silent regressions.
- Assertion run history enables trend analysis (e.g. "classification coverage dropped from 99% to 92% over 5 days — why?").

**Trade-offs**:
- CSV-editing-workflow muscle memory dies. Admin UI must be at least as fast as opening a CSV in Excel — design priority.
- 25 assertions add ~30 sec to the daily sync. Acceptable.
- The morning email becomes a TODO list. If 5 days in a row arrive with "ALL GREEN" the email becomes noise — but the failure case is the value.

**Revert path**: keep CSV files in git through Stage 7 dual-read; resolver can be flag-switched back to CSV-first.

## Dependencies

- **Phase 4 must complete first**. `classification_override.canonical_id` references `product_master(canonical_id)`.
- Phase 6 starts ≥ 2026-06-25 (after Phase 5 reconciler dry-run completes).

## Alternatives considered

- **Keep CSVs but add a hash-validation step in CI**. Rejected — solves the syntax bug class but not the multiplicity, audit, or admin-UX problems.
- **YAML rules instead of DB rows**. Rejected — YAML is editable but still file-based with all the cross-source drift problems.
- **Use the existing `capm_audit_log` for overrides**. Rejected — that's a write log, not a resolver source. Would conflate audit with data.

## Implementation timeline

- Stage 1 (schema): half day.
- Stage 2 (CSV migration): 1 day.
- Stage 3 (resolver): 2 days.
- Stage 4 (admin write surface): 2-3 days.
- Stage 5 (assertion runner + 25 tests): 3-4 days.
- Stage 6 (timer move): half day.
- Stage 7 (CSV delete): half day + 7-day grace.

Total active engineering: ~10 days. Calendar: ~3 weeks including grace.

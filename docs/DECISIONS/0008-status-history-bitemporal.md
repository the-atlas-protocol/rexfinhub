---
adr: 0008
title: Phase 5 — Bi-temporal status_history + 3-source Listed rule
status: proposed
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0008 — Bi-temporal `status_history` + 3-source rule for `Listed` promotion

## Context

Today `rex_products.status` is a single in-place string column ranging over six values: `Under Consideration → Filed → Effective → Target List → Listed → Delisted`. When a product moves from Filed to Effective, the value gets `UPDATE`d. The previous value is lost.

This causes four problems:

1. **No audit trail of lifecycle transitions**. A row's history of stage changes — who flipped it, when, why — exists only in `capm_audit_log` and only for admin edits. The daily auto-classifier can promote `Filed → Effective` silently with no record.
2. **No "as of date X" queries**. "How many funds were in Filed status on 2026-04-01?" requires reading capm_audit_log + replaying state. Cumbersome and unreliable.
3. **Promotions to `Listed` are not gated**. The reconciler today flips `status = 'Listed'` whenever it sees evidence (Bloomberg ACTV, exchange notice, etc.). BUG-04 (BMAX US Listed despite Bloomberg vanish) and BUG-03 (placeholder inception dates) are the BUG-class symptoms: single-source evidence is insufficient.
4. **Knowledge time vs. reality time confusion**. "When did we *know* the fund went Effective?" is a different question from "When did the fund *actually* become Effective?" — but today we only have one timestamp, and it conflates them.

ADR 0001 / TARGET.md `### principles` calls for a bi-temporal lifecycle table (SCD-2 / SQL:2011 style with separate reality time / knowledge time columns) and a deterministic "3-source rule" for `Listed` promotion. This ADR designs both.

## Decision

Add a new `status_history` table keyed by `canonical_id` (the Phase 4 product master key). Every status transition appends a row; nothing updates in place. `rex_products.status` becomes a denormalized cache derived from the latest `status_history.status` per canonical_id — useful for fast reads but no longer authoritative.

The "3-source rule" gates promotion to `Listed`: at least three independent evidence sources must agree before the reconciler appends a `status='Listed'` row.

### Schema

```sql
status_history (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_id   UUID NOT NULL REFERENCES product_master(canonical_id),

  status         TEXT NOT NULL,   -- enum: 'under_consideration','filed','effective',
                                  --       'target_list','listed','suspended',
                                  --       'delisted','liquidated'

  -- Reality time: when the transition actually happened in the world.
  valid_from     TIMESTAMPTZ NOT NULL,
  valid_to       TIMESTAMPTZ,     -- NULL = currently in this status

  -- Knowledge time: when WE first knew about the transition.
  tx_from        TIMESTAMPTZ NOT NULL,  -- when we inserted this row
  tx_to          TIMESTAMPTZ,     -- when we superseded this row (e.g. corrected the date)

  -- Evidence
  source         TEXT NOT NULL,   -- 'sec_485APOS','sec_485BPOS','sec_class_contract',
                                  -- 'cboe_listing_notice','bloomberg_actv_first_seen',
                                  -- 'bloomberg_first_trade','exchange_form_8a',
                                  -- 'manual_admin','reconciler_auto'
  evidence       JSONB,           -- per-source structured payload:
                                  --   sec: {form, accession_no, filing_date}
                                  --   bloomberg: {market_status, observed_at}
                                  --   cboe: {listing_notice_url, effective_date}
                                  --   manual: {admin_user, reason}

  -- Audit
  set_by         TEXT NOT NULL,   -- 'reconciler' | 'admin:<user>' | 'sec_pipeline' | 'bloomberg_sync'
  created_at     TIMESTAMPTZ NOT NULL,

  INDEX (canonical_id, valid_from),
  INDEX (status, valid_to)  -- WHERE valid_to IS NULL for "current state" queries
);

-- Materialized view for fast "current state" reads. Refreshed after every
-- status_history insert (trigger or app-level).
rex_products.status_cached  -- existing column, becomes a cache of:
   SELECT status FROM status_history sh
   WHERE sh.canonical_id = rex_products.canonical_id
     AND sh.valid_to IS NULL
   ORDER BY sh.valid_from DESC LIMIT 1
```

### The 3-source rule for `Listed`

The reconciler may only append `status='listed'` when at least 3 independent evidence sources concur, where independence is defined by source category:

| Category | Source values |
|---|---|
| **SEC** | `sec_485BPOS`, `sec_class_contract`, `exchange_form_8a` (any one) |
| **Bloomberg** | `bloomberg_actv_first_seen` (single source) |
| **Exchange** | `cboe_listing_notice`, `bloomberg_first_trade` (any one) |

To promote: need evidence from **3 different categories**. Specifically:
- An SEC filing demonstrating the registration is Effective (485BPOS + effective date or 8-A)
- AND Bloomberg `mkt_master_data.market_status = 'ACTV'`
- AND exchange-side observation: either a CBOE listing notice for the ticker OR Bloomberg's `first_trade_date` is set

If only 2 of 3 categories have evidence, status stays `effective` (or `target_list` if exchange but no Bloomberg ACTV). The dashboard `### ops-as-assertions` (Phase 6) surfaces "ready for Listed but 1 source missing" as a triage item.

### Reconciler invariants

```python
# Pseudocode for the Phase 5 reconciler
def reconcile_status(canonical_id):
    evidence = collect_evidence(canonical_id)
    current = get_current_status(canonical_id)
    proposed = derive_proposed_status(evidence)

    if proposed == 'listed' and not has_three_categories(evidence):
        proposed = 'effective'  # downgrade — not enough evidence

    if proposed != current:
        # Append new row; close out the previous valid_to.
        close_valid_to(canonical_id, now)
        append_status_row(canonical_id, proposed, evidence, source='reconciler')
        update_status_cached(canonical_id, proposed)
```

The reconciler runs after every Bloomberg sync (17:30 ET) AND after every fresh-poller fire (every 15 min during business hours).

### Backfill

Stage 2 of Phase 5 backfills `status_history` from existing data:
- One row per current `rex_products.status`, with `valid_from = COALESCE(initial_filing_date, created_at)`, `tx_from = created_at`, `source = 'backfill'`.
- For rows currently `Listed`: also synthesize an `effective` row dated 75 days before `inception_date` (SEC 485APOS default rule).
- For rows currently `Delisted`: synthesize a closing transition.

After backfill, every active row has at least one history record.

### Stages

**Stage 1 — Schema** (additive, no breakage).
- `scripts/migrate_status_history_schema.py` — CREATE TABLE.
- Doesn't replace `rex_products.status` column yet; both coexist.

**Stage 2 — Backfill from current state**.
- `scripts/backfill_status_history.py` — synthesize one history row per active product.
- Validation: every canonical_id has exactly one `valid_to=NULL` row.

**Stage 3 — Reconciler implementation + 3-source gating**.
- `webapp/services/status_reconciler.py` (new) implements the rule.
- Hooked into the bloomberg-chain ExecStartPost (after `apply_classification_sweep`).
- Pre-Stage-3, the reconciler runs in `--dry-run` mode for 3 days. Outputs a diff: "would promote X, would demote Y". Manual review.
- Stage-3 cutover flips to `--apply`.

**Stage 4 — Survivorship reads use the cache**.
- Routes/services that need "current status of product X" read `rex_products.status_cached` (the denormalized column).
- Routes that need historical state ("on date X, what was the status?") read `status_history` directly.

**Stage 5 — Deprecate `rex_products.status` direct writes**.
- Any code that does `rex_products.status = 'X'` must instead go through the reconciler (or `append_status_row()` for admin overrides).
- Admin route `/admin/rex-products/update/{id}` continues to allow status edits — but they route through `append_status_row(set_by='admin:<user>', source='manual_admin')` instead of an in-place update.

## Consequences

**Wins**:
- Full lifecycle audit trail per product, queryable in SQL.
- BUG-class problems become structurally impossible:
  - BMAX-class (single-source ghost Listed) — fails the 3-source gate.
  - Placeholder inception dates — `valid_from` is now first-class data with evidence, not just a date column.
- "As of date X" queries (e.g. "how many funds were active on the 2026-04-15 freeze day?") become trivial.
- Sets up Phase 6 `classification_override` with the same bi-temporal pattern.

**Trade-offs**:
- Every status change becomes a multi-row write (close existing row + insert new). 10× the write volume for status — but status changes are rare (~10/day) so absolute cost is negligible.
- `rex_products.status_cached` denormalization requires either a trigger or an app-level refresh. App-level (called inside `append_status_row()`) is simpler and chosen.
- The reconciler dry-run period (3 days) defers actual Phase 5 utility. Worth it — single-source Listed promotion is exactly the bug class we're trying to prevent, so we want to verify the rule before turning it on.

**Revert path**: drop `status_history` table; revert `webapp/services/status_reconciler.py` deletion would restore the previous in-place UPDATE path. Code dependency on `status_cached` falls back to reading `status` directly.

## Dependencies

- **Phase 4 must complete first**. `status_history.canonical_id` references `product_master(canonical_id)` — needs the canonical_id column populated on rex_products.
- Phase 5 starts no earlier than ~2026-06-10 (end of Phase 4 grace period).

## Alternatives considered

- **Append-only `rex_products` history table without bi-temporal columns**. Rejected — losing the `tx_from`/`tx_to` distinction makes corrections impossible to model (e.g. "we learned on 2026-05-10 that the fund actually went Listed on 2026-04-15, not 2026-04-22").
- **Use SQLite trigger instead of app-level cache update**. Rejected — SQLite triggers are second-class and harder to test. App-level is more portable for a future Postgres migration.
- **3-source rule with weights** (e.g. SEC counts 2, Bloomberg 1). Rejected — the categorical version is easier to reason about and matches the spec in TARGET.md.

## Implementation timeline

- Stage 1 (schema): half day.
- Stage 2 (backfill): 1 day.
- Stage 3 (reconciler + 3-day dry-run): 3 days code + 3 days observation.
- Stage 4 (cache reads): 1-2 days.
- Stage 5 (deprecate direct writes): 1-2 days.

Total active engineering: ~6-8 days. Calendar: ~2 weeks including dry-run grace.

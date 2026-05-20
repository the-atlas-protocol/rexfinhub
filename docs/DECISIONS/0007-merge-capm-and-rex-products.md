---
adr: 0007
title: Phase 3 — Merge capm_products into rex_products
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0007 — Merge `capm_products` into `rex_products`

## Context

**Audit data (2026-05-19)**:
- `capm_products`: **74 rows**
- `rex_products`: **541 rows**
- Overlap: **100% of CapM tickers exist in `rex_products`** — every merge target is an UPDATE, not an INSERT.
- `capm_audit_log`: 1,263 rows. The `table_name` column already supports `'rex_products'` (verified during Phase 2 work) — same log serves both tables.
- No FK constraints point at `capm_products.id`. No unique constraint conflicts on merge.
- **Critical asymmetry**: CapM has better `direction` data (concrete "Long"/"Short"/"Both") than Rex (mostly NULL). Migration must NOT silently overwrite CapM direction with Rex's NULL.

The database has two parallel products tables that should be one:

- **`rex_products`** — the lifecycle tracker. Source of truth for `Under Consideration → Filed → Effective → Target List → Listed → Delisted` status. SEC-derived (CIK, series_id, class_contract_id, filing dates). Drives `/operations/pipeline`.
- **`capm_products`** — the Capital Markets operational ledger. Source of truth for fee schedule, custodian, suite source, classification axes (`product_type`, `category`, `sub_category`), competitor mappings. Drives `/operations/products`.

Originally split because the two roles were managed by different stakeholders (Counsel/Compliance owned the lifecycle status; Capital Markets owned the fee/custodian/competitor mapping). The 2026-05-12 rebuild collapsed the lifecycle enum from 15 values to 6 and consolidated most admin flows under `/operations/pipeline` — the structural reason for the split mostly disappeared. Today the split causes:

1. **3-way merge in `/operations/products`** (`_capm_index_impl`) — joins `rex_products` + `capm_products` + `mkt_master_data` to compute the live universe. Fragile; flagged as GAP-07 in `SYSTEM.md`.
2. **Dual audit logs** — both tables write to `capm_audit_log` (after the `webapp/routers/admin_rex_products.py::rex_product_update` work in Phase 2, both tables now log there). The naming is misleading.
3. **Dual classification overrides** — `CapMProduct.manually_edited_fields` and `RexProduct.manually_edited_fields` serve identical semantic purposes.
4. **Field-name drift** — `CapMProduct.fund_name` vs `RexProduct.name`, `CapMProduct.suite_source` vs `RexProduct.product_suite`, `CapMProduct.prospectus_link` vs `RexProduct.latest_prospectus_link`. Same data, different column names.
5. **Type drift** — `CapMProduct.cu_size` is `String(20)`; `RexProduct.cu_size` is `Integer`. Joining requires casts.

## Decision

Add CapM-specific columns to `rex_products`, backfill from `capm_products`, repoint code, keep `capm_products` as a read-only shadow for one week, then drop. **Five stages**, each independently revertable.

### Schema target

`rex_products` gains the following columns (all nullable):

| Column | Type | Source | Notes |
|---|---|---|---|
| `bb_ticker` | `String(30)` | CapM | Bloomberg-specific ticker variant (`AAPL US`). |
| `inception_date` | `Date` | CapM | First trade date. Distinct from `official_listed_date` (which sometimes carries SEC effective date). Phase 5 status_history will reconcile. |
| `issuer` | `String(200)` | CapM | Issuer display name, distinct from `trust` (legal entity) and `product_suite` (REX brand). |
| `fixed_fee` | `String(20)` | CapM | Fee schedule. Kept as String for currency formatting (`$0.0001`). |
| `variable_fee` | `String(50)` | CapM | |
| `cut_off` | `String(20)` | CapM | Order cut-off time. |
| `custodian` | `String(100)` | CapM | |
| `suite_source` | `String(30)` | CapM | DEPRECATED on merge — gets folded into `product_suite`. Migration sets `product_suite = COALESCE(product_suite, suite_source)`. |
| `our_category` | `String(50)` | CapM | First classification axis. |
| `product_type` | `String(50)` | CapM | Second classification axis. |
| `category` | `String(50)` | CapM | Third classification axis. |
| `sub_category` | `String(50)` | CapM | Fourth classification axis. Will fold into `classification_override` in Phase 6. |
| `leverage` | `String(10)` | CapM | Leverage ratio as string ("2", "-3", "1.5"). Phase 5 underlier_master normalizes. |
| `underlying_ticker` | `String(50)` | CapM | Distinct from `underlier` (which is a freeform string). Phase 4 underlier_master makes this typed. |
| `underlying_name` | `String(300)` | CapM | |
| `expense_ratio` | `Float` | CapM | Currently a CapM column. `rex_products.mgt_fee` is the parallel SEC-derived value. KEEP BOTH for now; survivorship resolution comes in Phase 5. |
| `competitor_products` | `Text` | CapM | DEPRECATED on merge — `rex_products.competitors` already serves this role with stricter format (pipe-separated tickers). Migration sets `competitors = COALESCE(competitors, competitor_products)`. |
| `bmo_suite` | `String(50)` | CapM | BMO MicroSectors ETN suite tag. |

Field-name reconciliation (no schema change; resolved at read time):
- `RexProduct.name` ← preferred over `CapMProduct.fund_name`. On merge, set `name = COALESCE(name, fund_name)` for rows added from CapM.
- `RexProduct.latest_prospectus_link` ← preferred over `CapMProduct.prospectus_link`.
- `RexProduct.product_suite` ← preferred over `CapMProduct.suite_source`.

Type reconciliation:
- `cu_size`: keep `Integer` on `rex_products`. Migration parses CapM's `String(20)` value (`"50,000"` → `50000`); raises on parse failure (no silent data loss).

### Stages

**Stage 1 — Schema migration (additive, no breakage)**.
- New SQLAlchemy columns added to `RexProduct`.
- New ALTER TABLE migration script `scripts/migrate_capm_to_rex_schema.py`. Idempotent; checks for column existence before adding.
- After stage 1: `rex_products` has the new columns (NULL for all existing rows); `capm_products` unchanged; no code reads the new columns yet.

**Stage 2 — Data backfill**.
- New script `scripts/migrate_capm_data_to_rex.py`. Given the 100% overlap, every CapM row maps to an existing Rex row via ticker. For each `capm_products` row:
  1. Find matching `rex_products` row by `ticker` (case-insensitive, trim).
  2. **Survivorship rules** (per-field):
     - `direction`: **CapM wins when CapM is non-NULL** (Rex's NULLs get filled). This is the one exception to "never overwrite Rex" because Rex's direction is empirically incomplete and CapM's is hand-curated.
     - `name` ← `fund_name` only if Rex's `name` is empty.
     - `product_suite` ← `suite_source` only if Rex's `product_suite` is empty.
     - `latest_prospectus_link` ← `prospectus_link` only if Rex's is empty.
     - `competitors` ← `competitor_products` only if Rex's is empty.
     - All new CapM-only columns (`bb_ticker`, `inception_date`, `issuer`, fee fields, `our_category`, `product_type`, `category`, `sub_category`, `leverage`, `underlying_*`, `expense_ratio`, `bmo_suite`) populated unconditionally.
     - `manually_edited_fields`: union the two sets — admin edits on either side are preserved.
  3. If no matching ticker (should not happen per audit, but defensive): log + skip.
- Reports: rows updated / rows where direction was filled / rows skipped (with reason).
- Idempotent — running twice is a no-op after the first run.

**Stage 3 — Repoint routes + services to read `rex_products`**.
- `webapp/routers/capm.py::_capm_index_impl` (lines 155-463) 3-way merge becomes a 2-way merge (`rex_products` + `mkt_master_data`). 210 lines of JOIN logic simplifies considerably.
- `webapp/routers/capm.py::_capm_export_impl` (CSV export at `/operations/products/export.csv`) queries CapM directly today — switches to `rex_products`.
- `webapp/routers/capm.py::_capm_update_impl` writes to CapM today. After merge, it writes to `rex_products` (or delegate to the existing `/admin/rex-products/update/{id}` route from Phase 2).
- `webapp/routers/dashboard.py:221-222` uses `CapMProduct.count()` for a sidebar KPI — flip to RexProduct.
- `webapp/routers/admin_products.py::update_product` already writes `rex_products` (verified during Phase 2 work) — no change needed.
- `webapp/templates/capm.html` (529 lines) — most rendering logic unchanged, but field names update where they were ambiguous (`fund_name` → `name`, etc.).
- Flow report builders that read `capm_products` for issuer/competitor mappings switch to `rex_products`.
- `scripts/import_capm.py` (the source-of-truth reconciler for CapM xlsx imports) gets a `--target rex_products` flag; default stays `capm_products` during the dual-write window.
- `webapp/database.py::_capm_seed_if_empty()` is replaced by `_rex_seed_capm_columns_if_empty()` — same safety guard (skip if `capm_audit_log` has entries), reads from a NEW seed file `webapp/data_static/rex_products_capm_overlay.csv` containing only the 18 CapM-derived columns keyed by ticker. The original `capm_products.csv` is kept under `webapp/data_static/` for revert.

**Stage 4 — Dual-write grace period (1 week)**.
- `capm_products` table NOT dropped.
- `import_capm.py` writes to BOTH tables (capm_products for revert, rex_products for live reads).
- Read traffic comes from `rex_products` only.
- Monitor `data/.pipeline_stages.jsonl` + the 20:15 ET summary for any anomaly.

**Stage 5 — Drop `capm_products`**.
- After ≥7 calendar days of clean dual-write operation.
- Migration script `scripts/drop_capm_products.py`:
  - Final reconciliation diff: any row in `capm_products` not reflected in `rex_products` aborts the drop.
  - `DROP TABLE capm_products`.
  - `capm_audit_log` retained — it already serves both tables.
  - `import_capm.py` switches to single-write (`rex_products` only).

Revert path at any stage: re-enable the previous stage's reads. Schema columns are additive so revert is non-destructive until Stage 5. After Stage 5, revert requires restoring `capm_products` from the previous nightly backup (D-drive sync covers ~14 days).

### What does NOT change in Phase 3

- The 4-axis classification (`our_category`, `product_type`, `category`, `sub_category`) moves with the data but its replacement (single `classification_override` table) is Phase 6 work.
- `RexProduct.expense_ratio` (from CapM) and `RexProduct.mgt_fee` (SEC) coexist with NULL semantics until Phase 5 survivorship rules.
- The `capm_audit_log` table is NOT renamed yet — the table_name column already supports `'rex_products'` rows (verified during Phase 2 work; admin_rex_products.py writes there with `table_name='rex_products'`). The existing 1,263 historic CapM rows stay as-is with `table_name='capm_products'` for historical traceability. A future rename to `product_audit_log` is a cosmetic Phase 6+ change.
- `capm_trust_aps` (40 rows, separate table, no FK to capm_products) is **OUT OF SCOPE** for Phase 3. Its merge into a unified `trust_aps` table is a future smaller ADR if needed.

## Consequences

**Wins**:
- One canonical products table; `/operations/products` becomes a 2-way join instead of 3-way (closes GAP-07).
- One classification override surface (after Phase 6 lands).
- Removes the field-name drift (`name` vs `fund_name`, etc.) — same data, one column.
- Cleaner Phase 4 (canonical_id) starting state — only one source table to assign IDs to.

**Trade-offs**:
- `rex_products` grows from 30 to ~48 columns. Schema width is a smell; mitigated by Phase 4 normalization (canonical_id + side tables).
- `CapMProduct` import path in code persists during Stage 3/4 (`import_capm.py` writes both). Some "two places to look" pain remains until Stage 5.
- `expense_ratio` vs `mgt_fee` ambiguity persists until Phase 5. Documented; not load-bearing.

**Revert path**:
- Stages 1-4: re-disable reads from new columns; data sits dormant.
- Stage 5: restore `capm_products` from previous nightly backup; flip reads back.

## Alternatives considered

- **Drop `rex_products`, keep `capm_products`**. Rejected — `rex_products` carries the lifecycle status enum + SEC identifiers (CIK, series_id, class_contract_id) which `capm_products` lacks. The two were schema-designed around their distinct purposes; merging into the SEC-heavy side is the right direction since SEC identifiers are immutable while CapM operational data is mutable.
- **Keep both tables; add a foreign key**. Rejected — adds join complexity without removing the dual-write-and-read pain. Doesn't simplify Phase 4.
- **Skip Phase 3; go straight to Phase 4 canonical_id**. Rejected — canonical_id assignment is much cleaner against one source table. Doing Phase 3 first reduces Phase 4 scope by ~half.
- **Big-bang single-stage migration**. Rejected — no revert path. Per user's safety preference, multi-stage with dual-write window is the appropriate trade-off between speed and risk.

## Implementation timeline

- Stage 1 (schema migration): ~half day. Pure additive ALTER TABLE.
- Stage 2 (data backfill): ~half day. Includes diff verification.
- Stage 3 (repoint code): ~2 days. Code surface is concentrated in `capm.py` + `admin_products.py` + the flow report builders.
- Stage 4 (grace period): 7 calendar days. No code work.
- Stage 5 (drop): ~half day. After validation diff passes.

Total active engineering: ~3 days. Calendar: ~10 days. ADR 0006 (canonical-product-id) is the next phase and can be drafted during the Stage 4 grace period.

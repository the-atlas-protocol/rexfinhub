---
adr: 0006
title: Phase 4 — Canonical product identifier + polymorphic underlier
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0006 — Canonical product identifier + polymorphic underlier master

## Context

After Phase 3 (ADR 0007) merges `capm_products` into `rex_products`, there's one products table. Good. But the table still uses `id INTEGER AUTOINCREMENT` as primary key and lets SEC identifiers like `ticker`, `cik`, `series_id`, `class_contract_id` drift over time. Three concrete problems this causes today:

1. **Recycled tickers reassign product identity**. The TSII bug class (BUG-02 in `SYSTEM.md`, mitigated 2026-05-19) is structurally caused by code keying off ticker. When SEC recycles a placeholder ticker between a delisted product and a new pre-launch filing, the database has no stable way to tell them apart.
2. **Underliers are typed as freeform strings**. `rex_products.underlier`, `underlying_ticker`, `underlying_name`, and `mkt_master_data.map_li_underlier` are all `VARCHAR`. The same Bitcoin product appears variously as `"BTC"`, `"BTC USD"`, `"BTCUSD"`, `"BTC-USD"`, `"XBTUSD"`, `"BITCOIN"`. BUG-01 was caused by this — competitors on a product appear to be zero because the canonical key differs across rows. Phase 0b's `scripts/canonicalize_crypto_underliers.py` papers over a few common cases nightly; doesn't address the structural issue.
3. **No path to attribute fund_underlier relationships with weights or effective periods**. A basket-strategy fund holds N underliers; today we have no way to express that without a freeform comma-separated string field.

ADR 0001 / TARGET.md `### principles` calls for a **canonical product identifier** (synthetic UUID per fund/filing, with all SEC and exchange identifiers mapping to it via a side table with `valid_from`/`valid_to`) and a **polymorphic underlier reference** (typed enum — equity / etp / index / crypto_pair / basket / commodity / fx / rate — with resolution via OpenFIGI where applicable).

This ADR designs the schema and migration plan. Implementation lands in Phase 4 after the Phase 3 grace period closes (≥ 2026-05-26).

## Decision

Introduce three new tables — `product_master`, `identifier_xref`, `underlier_master` — plus a join table `fund_underlier`. `rex_products` gains a `canonical_id` foreign key column (nullable during migration; required after Stage 5).

### Schema

```sql
-- The single canonical row per product. ID is a synthetic UUID — never
-- derived from any external identifier, so ticker recycling cannot
-- re-point an existing row.
product_master (
  canonical_id   UUID PRIMARY KEY,
  created_at     TIMESTAMPTZ NOT NULL,
  fund_name      TEXT,            -- denormalized for convenience reads;
                                  -- authoritative copy lives on rex_products.name
  status_current TEXT,            -- cached from status_history.latest (Phase 5)
  is_rex         BOOLEAN NOT NULL DEFAULT FALSE
);

-- Bi-temporal identifier mapping. Every ticker / CUSIP / ISIN / FIGI /
-- CIK / SEC series_id / class_contract_id maps here. valid_to=NULL means
-- "current". Multiple rows per canonical_id are normal (ticker history,
-- multiple share classes).
identifier_xref (
  canonical_id   UUID NOT NULL REFERENCES product_master(canonical_id),
  id_type        TEXT NOT NULL,   -- enum: 'ticker','cusip','isin','figi','cik','series_id','class_contract_id','bloomberg'
  id_value       TEXT NOT NULL,
  valid_from     TIMESTAMPTZ NOT NULL,
  valid_to       TIMESTAMPTZ,     -- NULL = current
  source         TEXT NOT NULL,   -- 'sec_485APOS','sec_class','bloomberg_actv','cboe_listing','manual'
  PRIMARY KEY (canonical_id, id_type, id_value, valid_from),
  INDEX (id_type, id_value)
);

-- Polymorphic underlier reference. Different fields populated per type.
underlier_master (
  underlier_id   UUID PRIMARY KEY,
  underlier_type TEXT NOT NULL,   -- enum: 'equity','etp','index','crypto_pair','basket','commodity','fx','rate'
  -- Equity / ETP / Crypto (FIGI-resolvable)
  primary_figi   TEXT,
  ticker         TEXT,
  -- Index
  index_provider TEXT,            -- 'S&P','MSCI','Bloomberg','CBOE'
  index_code     TEXT,            -- 'SPX','BMAXATCL'
  -- Crypto pair
  crypto_base    TEXT,            -- 'BTC'
  crypto_quote   TEXT,            -- 'USD'
  -- Display
  display_symbol TEXT NOT NULL,   -- canonical user-facing string ('XBTUSD','SPX','AAPL')
  -- Metadata
  created_at     TIMESTAMPTZ NOT NULL,
  resolved_via   TEXT,            -- 'openfigi','manual','derived'
  CONSTRAINT one_identity_per_type CHECK (
    (underlier_type = 'equity'     AND ticker IS NOT NULL) OR
    (underlier_type = 'etp'        AND ticker IS NOT NULL) OR
    (underlier_type = 'index'      AND index_provider IS NOT NULL AND index_code IS NOT NULL) OR
    (underlier_type = 'crypto_pair' AND crypto_base IS NOT NULL AND crypto_quote IS NOT NULL) OR
    (underlier_type IN ('basket','commodity','fx','rate'))
  )
);

-- Many-to-many: a basket fund may reference multiple underliers.
fund_underlier (
  canonical_id   UUID NOT NULL REFERENCES product_master(canonical_id),
  underlier_id   UUID NOT NULL REFERENCES underlier_master(underlier_id),
  weight         NUMERIC,          -- NULL = sole underlier
  effective_from TIMESTAMPTZ NOT NULL,
  effective_to   TIMESTAMPTZ,      -- NULL = current
  PRIMARY KEY (canonical_id, underlier_id, effective_from)
);
```

`rex_products` gets one new column:

```sql
ALTER TABLE rex_products ADD COLUMN canonical_id UUID;
```

Nullable during Stages 1-3. Made NOT NULL in Stage 5 after every row is backfilled.

### UUID generation

Python `uuid.uuid4()` — random V4 UUIDs. Reasons over V5 (name-based) and DB-side `gen_random_uuid()`:
- Independent generation in scripts and admin UI (no DB round-trip required to claim an ID)
- No risk of two different products colliding because they happened to share a name token at one point in time
- SQLite stores as `TEXT` (no native UUID type); PostgreSQL would store as `uuid`. Either way the value is opaque.

### Stages

**Stage 1 — Create empty tables, add `canonical_id` column** (additive; no breakage).
- `scripts/migrate_canonical_id_schema.py` — idempotent ALTER + CREATE.
- New tables are empty; new column is NULL on every rex_products row.
- No code uses any of this yet.

**Stage 2 — Backfill `product_master` from `rex_products`**.
- One row in `product_master` per `rex_products.id`. Generate UUID, set `fund_name = rex.name`, `is_rex` derived from `rex.product_suite` membership in REX-branded suites.
- Set `rex_products.canonical_id = <new UUID>` for every row.
- Validate: count match `product_master == rex_products`.

**Stage 3 — Backfill `identifier_xref`**.
- For each `rex_products` row:
  - Insert ticker (if any) with `valid_from = initial_filing_date or created_at`.
  - Insert CIK, series_id, class_contract_id (each with their source).
  - Insert BB ticker (id_type='bloomberg') with `valid_from=inception_date`.
  - All `valid_to = NULL` (current).
- For each `mkt_master_data` row not yet mapped:
  - Match by ticker → canonical_id; insert FIGI (if Bloomberg has it).
- Validate: no orphaned ticker queries should miss.

**Stage 4 — Build `underlier_master` from observed underlier strings**.
- Distinct values from `rex_products.underlier`, `underlying_ticker`, `underlying_name`, `mkt_master_data.map_li_underlier`, `map_cc_underlier`, `map_crypto_underlier`.
- Classify each into a type:
  - Matches `^[A-Z]{1,5}$` and exists in mkt_master_data → `equity` or `etp` (look up FIGI via OpenFIGI batch).
  - Crypto patterns (`BTC`, `BTC USD`, `BTCUSD`, `XBTUSD`, `BITCOIN`, etc.) → `crypto_pair` with base/quote inferred.
  - Index provider patterns (`SPX`, `BMAXATCL Index`) → `index`.
  - Multiple comma-separated tickers → `basket` with N entries in fund_underlier.
  - Everything else → manual review queue.
- Validation report: how many underliers auto-classified vs. needing manual review.

**Stage 5 — Populate `fund_underlier`, drop the freeform columns**.
- Per `rex_products` row, insert into `fund_underlier` referencing the new `underlier_id`.
- Code refactor: routes/services stop reading `rex_products.underlier` / `underlying_ticker` etc.; read via `fund_underlier` join.
- Drop the freeform columns AFTER one week of dual-read validation.

### What does NOT change

- `mkt_master_data` keeps its freeform underlier columns through Phase 4. Phase 5 (status history) and Phase 6 (classification overrides) are where mkt_master_data gets restructured.
- `rex_products` retains its `Integer` primary key. `canonical_id` is added alongside; both coexist until external API consumers (none today) might depend on the integer ID.
- The `identifier_xref` table accepts new id types via the `id_type` enum being string-typed. No schema change needed to support a new identifier type.

## Consequences

**Wins**:
- TSII-class ticker-recycling bugs become structurally impossible. Recycled SEC tickers produce a new `identifier_xref` row with `valid_from=<recycle date>` on a different `canonical_id`; the old product keeps its history.
- Bitcoin / crypto underliers normalize once via the openfigi/heuristic classifier and never need a nightly canonicalization cron again.
- Phase 5 `status_history` and Phase 6 `classification_override` both key off `canonical_id`. Each phase reuses Phase 4's identity model.
- OpenFIGI integration lands once; reusable across underlier resolution and any future SEC FIGI-linkage work.

**Trade-offs**:
- Five tables instead of one. Increase in schema complexity. Mitigated by the side tables being immutable-append (no UPDATE statements on identifier_xref, only INSERT).
- Every query that wants the current ticker for a product needs to filter `identifier_xref WHERE id_type='ticker' AND valid_to IS NULL`. A view (`product_current_ticker`) hides this from most callers.
- UUIDs are 36-char strings in SQLite; 38 bytes per row vs 4 bytes for an integer. At ~1,000 rex_products rows, this is negligible.

**Revert path**:
- Stages 1-3: tables and column are additive; revert removes them without data loss.
- Stages 4-5: keep the freeform columns through Stage 5 dual-read; revert restores the old read paths.

## Dependencies

- **Phase 3 must complete first.** Specifically, the Stage 5 drop of `capm_products` (≥ 2026-05-26). Phase 4 needs one source-of-truth products table to assign canonical_ids to; running it before Phase 3 doubles the work.
- **OpenFIGI API account.** Free tier permits 25 requests per 6-second window; sufficient for backfilling ~1,000 ticker/figi lookups in one batch run. Requires a registered API key (free) for higher rate limits if needed.

## Alternatives considered

- **Use SEC CIK + series_id as primary key**. Rejected — pre-launch products often have a CIK but no series_id yet; the key would be nullable.
- **Use ticker as primary key with composite uniqueness**. Rejected — recycled tickers (the original problem) make ticker non-unique over time.
- **Skip `underlier_master`; keep freeform strings forever**. Rejected — BUG-01 (Bitcoin competitors) will keep recurring.
- **Use Python's `uuid.uuid5()` (namespace-derived UUIDs)**. Rejected — namespace would need to be a stable input (name or CIK), but both can change. V4 random is correct here.

## Implementation timeline

- Stage 1 (schema): ~1 day. Pure additive.
- Stage 2 (backfill product_master): ~half day.
- Stage 3 (identifier_xref): ~1 day. Includes spot-checking historical SEC accession data.
- Stage 4 (underlier_master + OpenFIGI): ~3-5 days. Includes the OpenFIGI integration + manual review of unclassifiable underliers.
- Stage 5 (fund_underlier + drop freeform columns): ~2 days code + 1 week dual-read grace.

Total active engineering: ~7-10 days. Calendar: ~2-3 weeks including grace periods.

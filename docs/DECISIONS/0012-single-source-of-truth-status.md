---
adr: 0012
title: Single source of truth for product status — fund_status (feed) → status_history (record) → status_cached (derived)
status: accepted
date: 2026-06-15
deciders: Ryu El-Asmar
supersedes: none
extends: 0008
---

# ADR 0012 — Single source of truth for status: feed → record → derived cache

## Context

ADR 0008 made `status_history` the bi-temporal authority and `rex_products.status_cached`
a derived cache. But the *three-table* relationship was never written down in one place, and
the Ultimate-Fixup discovery (2026-06-15) found three separate places that encode "status":

- **`fund_status`** (215K rows) — SEC filings-universe lifecycle, one row per *series*
  (`PENDING` / `EFFECTIVE` / `DELAYED`). This is an upstream **feed**, not a decision.
- **`status_history`** (1,086 rows) — the bi-temporal **record**: every REX-product status
  transition, with evidence and reality/knowledge time. THE authority (ADR 0008).
- **`rex_products.status` / `status_cached`** — the **derived** current status, materialized
  from the latest `status_history` row by `status_reconciler --apply`.
- **`rex_product_status_history`** (0 rows) — an empty duplicate of `status_history` that was
  never populated. A fourth encoding that exists only to confuse.

The risk this ADR closes: any reader (a report, a route, a new script) treating `fund_status`
as the product's status, or writing `rex_products.status` in place, silently diverges the system
from its own authority — and nothing catches it.

## Decision

There is exactly one authority chain for "what status is this REX product":

```
fund_status            status_history              rex_products.status_cached
(SEC feed,      ──►     (bi-temporal record,  ──►   (derived cache, fast reads)
 per series)            THE authority)               written ONLY by the reconciler
   evidence in            appended by
   reconciler             status_reconciler --apply
```

1. **`fund_status` is a FEED, never an authority.** It is one evidence input the reconciler
   reads. No report, route, or script may read `fund_status` to answer "is this REX product
   Listed/Effective/Delisted." That question is answered by `status_cached` (current) or
   `status_history` (as-of-date).

2. **`status_history` is the record/authority** (ratifies ADR 0008). Every transition appends;
   nothing updates in place.

3. **`rex_products.status_cached` is derived** and written ONLY by `status_reconciler`. The
   invariant is exact:

   > For every `rex_products` row, `status_cached` equals the `status` of its single
   > `status_history` row with `valid_to IS NULL`.

   Verified on prod 2026-06-15: 586 products, **0 divergent**.

4. **`rex_product_status_history` is retired** — empty duplicate, no writer, no reader. (Drop
   gated on Ryu's go-ahead per the no-autonomous-deletions rule; until then it is asserted to
   stay empty so nothing starts writing a second record table.)

## Enforcement (auto-heal assertion)

`scripts/run_assertions.py` gains `assert_status_single_source`:

- **Divergence:** count `rex_products` whose `status_cached` ≠ latest `status_history.status`
  (`valid_to IS NULL`). Expected 0. Non-zero ⇒ the reconciler missed a sync (or something wrote
  `status` directly) ⇒ **fail** (a send tripwire) + the next reconciler run is the heal.
- **Empty-dup guard:** `rex_product_status_history` must stay at 0 rows. Non-zero ⇒ a second
  record table is being written ⇒ **fail**.

This assertion is one of the daily 08:00 tripwires and a `--apply`-chain post-check, so the
invariant can never silently rot.

## Consequences

**Wins:** one documented chain; a divergence is a loud failure, not a silent drift; reports that
need "alive?" have one correct column to read (`status_cached` / Bloomberg `market_status` for
the wider ETP universe — see note); the empty dup stops being a trap.

**Note on `market_status`:** for the *full ETP universe* (not just REX products), the alive/dead
signal is Bloomberg `mkt_master_data.market_status` (`ACTV` vs `LIQU`/`DLST`). That is a separate
authority for a separate population (every ETP, not REX lifecycle). The two never conflict:
`status_history`/`status_cached` govern the 586 REX products; `market_status` governs the ~5,368
ACTV ETP universe the reports screen against. ADR 0008's 3-source `Listed` rule already consumes
`market_status` as one of its evidence categories, so they compose rather than compete.

**Trade-offs:** the assertion adds one query to the 08:00 run (negligible). Retiring
`rex_product_status_history` is deferred to a human-confirmed drop.

**Revert path:** delete the assertion; the chain still works (it documents existing behavior).

## Alternatives considered

- **Collapse `status_cached` and read `status_history` everywhere.** Rejected — the cache exists
  for read speed on hot paths (every report, every /operations render). ADR 0008 already chose it.
- **Make `fund_status` the authority for REX products.** Rejected — `fund_status` is per-series
  SEC lifecycle for the whole filings universe; it has no concept of REX canonical product or the
  3-source `Listed` gate, and it carries no Bloomberg/exchange evidence.
- **A brand-new unified status table.** Rejected — `status_history` already is that table; a new
  one is the fragmentation this whole fixup is fighting.

---
adr: 0002
title: Phase 0b triage patches for Bugs 1-4
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0002 — Phase 0b: Triage Patches for Bugs 1-4

## Context

Four data-correctness bugs surfaced during the 2026-05-18/19 review of `/operations/products` and `/operations/pipeline`:

- **BUG-01** — Bitcoin underlier shows 0 competitors. Cause: string-based underlier comparison; REX uses "XBTUSD", competitors use "Bitcoin"/"BTC".
- **BUG-02** — REX TSM Growth & Income (TSII) shows Listed despite never trading. Cause: Phase 3 of `sync_rex_products_from_filings.py` matches on ticker only; SEC recycled TSII from TSM to TSLA Growth & Income.
- **BUG-03** — 13+ T-REX 2X products marked Listed with placeholder inception dates. Cause: Phase 3 accepts any parseable inception_date including bulk-seeded values.
- **BUG-04** — BMAX (delisted fund) still shows Listed in rex_products despite vanishing from Bloomberg. Cause: sync logic only promotes; never demotes.

The structural fixes for these live in TARGET.md Phases 4-5 (canonical product ID, polymorphic underlier_master, bi-temporal status_history). Those phases are weeks of work. The triage patches in this ADR are the minimal code/data changes that prevent each bug from recurring while the structural rebuild is in flight.

## Decision

Ship four patches as a single Phase 0b PR:

### Patch 1 (BUG-01) — Bitcoin underlier canonicalization (data-only)

New one-shot script `scripts/canonicalize_crypto_underliers.py`. Idempotent — safe to re-run. Maps every Bitcoin variant ("Bitcoin", "BTC", "BTC/USD", "BTC-USD", "XBT") in `mkt_master_data.map_li_underlier` / `map_cc_underlier` / `map_crypto_underlier` to canonical `"XBTUSD"`. Same for Ethereum variants → `"XETUSD"`.

Run once on VPS after deploy; future Bloomberg sync may reintroduce variants, so this script also runs nightly via cron (see Operations below) until Phase 4 lands.

### Patch 2 (BUG-02) — Fund-name validation in Phase 3 + nightly duplicate-ticker audit

Two layers per Ryu's explicit request:

**Layer 1 — Scraper fix**: `sync_rex_products_from_filings.py::phase3_activate_from_market()` adds `_names_overlap()` cross-check. Both `rex_products.name` and `mkt_master_data.fund_name` must share at least one non-boilerplate token (after stripping "REX", "T-REX", "2X", "DAILY", "TARGET", etc.) before the ticker match is accepted.

**Layer 2 — Independent audit**: new script `scripts/audit_duplicate_tickers.py` runs nightly. Finds every ticker appearing in >1 `rex_products` row. Writes `data/.duplicate_tickers_audit.json`. `scripts/pipeline_summary.py` reads it and adds a row to the morning email (`warn` status if duplicates exist).

### Patch 3 (BUG-03) — Inception-date sanity gates in Phase 3

Added to `phase3_activate_from_market()`. Before promoting Effective → Listed:

- Parsed `inception_date` must be on or after `rex_products.initial_filing_date` (filing date is the floor)
- Parsed `inception_date` must be within last 60 calendar days

Either check failing → skip promotion + log warning. Stale/bulk-seeded dates can't sneak through.

### Patch 4 (BUG-04) — Phase 4 demotion audit for vanished tickers

New function `phase4_demote_vanished_from_market()` in `sync_rex_products_from_filings.py`. For every `status=Listed` rex_product, check whether its ticker is absent from `mkt_master_data` entirely (BMAX case — Bloomberg reclaimed the ticker).

Default behaviour: log a warning and surface in morning email. Auto-demotion to `Delisted` is gated behind an opt-in flag (`data/.auto_demote_vanished`) to avoid false positives from transient Bloomberg drop-outs.

## Operations

Files added:
- `scripts/canonicalize_crypto_underliers.py`
- `scripts/audit_duplicate_tickers.py`

Files modified:
- `scripts/sync_rex_products_from_filings.py` — Phase 3 sanity gates + new Phase 4 + `_names_overlap()` helper + `SyncStats.vanished_count` field
- `scripts/pipeline_summary.py` — new "Duplicate-ticker audit" row

Cron additions (jarvis crontab on VPS):
```
30 02 * * * /home/jarvis/venv/bin/python /home/jarvis/rexfinhub/scripts/canonicalize_crypto_underliers.py > /tmp/canonicalize_crypto.log 2>&1
35 02 * * * /home/jarvis/venv/bin/python /home/jarvis/rexfinhub/scripts/audit_duplicate_tickers.py > /tmp/audit_dup_tickers.log 2>&1
```

Both run at 02:30/02:35 ET nightly so they're complete before the 18:30 preflight reads `data/.duplicate_tickers_audit.json`.

## Consequences

**Positive**:
- BUG-01 fixed for Bitcoin/Ethereum/extensible crypto variants today; no waiting for Phase 4
- BUG-02 fixed at the scraper level (won't recur) and surfaced via audit if it does (safety net)
- BUG-03 fixed at the scraper level; rejected dates are logged so we can spot pipeline regressions
- BUG-04 surfaced for human review; no risk of auto-demoting funds that briefly drop out of Bloomberg

**Negative**:
- Bloomberg may keep introducing new Bitcoin variants we haven't mapped; the script needs an occasional pattern update
- Audit-only Phase 4 still requires Ryu's eyes to act on the morning email warning. Auto-demote behind flag is opt-in for safety
- `_names_overlap()` uses simple token intersection. If a future fund name shares zero tokens with its Bloomberg `fund_name` (e.g., a rebrand mid-filing), the legitimate match will fail. Mitigation: log the skip clearly so it's easy to spot

## Alternatives considered

- **Skip the audits, ship structural rebuild only** — rejected. Phases 4-5 are 4+ weeks out; production needs correctness today.
- **Auto-demote BUG-04 candidates immediately** — rejected. Transient Bloomberg drop-outs are real (saw one on 2026-05-08). Auto-demoting risks false negatives.
- **Build a Bitcoin underlier mapping table in the DB** — rejected for Phase 0b. That's part of Phase 4 (polymorphic underlier_master).

## References

- `docs/SYSTEM.md#known-bugs` (BUG-01 through BUG-04)
- `docs/TARGET.md#phases` (Phase 0b shipping criteria)
- `docs/raw/REXFINHUB_ARCHITECTURE_v3_2026-05-19.md` §6 (full root-cause analysis)

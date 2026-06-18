---
doc: decision
id: 0013
title: Self-correcting, AI-enabled rexfinhub (fix at source · understand context · stay silent)
status: accepted
date: 2026-06-18
---

# ADR 0013 — Self-correcting, AI-enabled rexfinhub

## Context

Ryu kept catching the *same class* of bug by hand: a report shows stale/wrong data
(Tidal Trust II brand, inflated MicroSectors AUM, a raw "Pending" status, a KPI that
doesn't match REX's actual fund count). Each was patched individually. The reframe
that drove this work:

- **Timing is not the disease.** "Did the script run" checks are worthless — they
  always pass. What was missing is *contextual* quality.
- **Fix at the source.** A reconciler that silently backfills what the SEC scrape
  missed is secondary work papering over a source defect — surface it instead.
- **Resolve ambiguity with AI.** If a strategy/brand is unclear: rules → the
  Bloomberg description in the daily file → a web search — before a human is asked.
- **Stay silent.** No per-event alert clutter. The system fixes and logs; only the
  genuinely-undecidable reaches Ryu, once, consolidated.

The one principle the prior design got right — **one source per fact** — is kept; what
changed is *how it is enforced and repaired*: from string-scans/leak-lists to AI that
understands meaning, and from secondary reconcilers to correctness at ingest.

## Decision

### Stage B — self-healing resolution cascade (`market/resolve.py`)
Every unresolved fact (category, brand, underlier, strategy) flows through ONE ordered
escalation: **rules → Bloomberg `fund_description` (AI) → AI web search → human queue**.
Journaled (`logs/ai_resolve_*.jsonl`), idempotent, offline-safe (AI rungs degrade to
no-ops without a key). Powered by `claude_service.resolve_fact()` (incl. the Anthropic
`web_search` tool). `derive_issuer_brands` now guarantees a non-NULL brand via
regex → AI → deterministic name-fallback; the "Tidal Trust II" leak class self-resolves.
`ai_classify_unmapped` now reads the Bloomberg description (previously ignored).

### Stage C — gates that BLOCK (`preflight_check.py`, `run_chain.py`, `send_all.py`)
The `preflight_maintenance` escape hatch is permanently disabled. New cheap pre-filters
(forbidden-status preview scan; staged `PREVIEW_DIR`) plus an **AI semantic reviewer**
(brand-vs-legal-entity, KPI-vs-lineup) that judges correctness, not format. `run_chain`
builds to a staging dir and **promotes to live previews only on green**; `send_all`
refuses on a red preflight for both `--use-decision` and manual `--send`.

### Stage A — reconcilers become health-probes (`etp_tracker/reconciler.py`, `status_reconciler.py`)
The SEC filing reconciler gains a read-only `--probe` (`probe_day`/`probe_recent`) that
computes how many index filings the live atom watcher MISSED, writes nothing, exits
non-zero on a gap. `run_recent` still backfills (transitional safety net — the atom feed
is inherently lossy and the watcher can have downtime), but now fires ONE consolidated,
day-rate-limited ingest-gap alert so the scrape gets fixed at source. `status_reconciler
--assert-noop` derives status read-only and FAILs + escalates if any transition would
apply (assert status was already correct at the source). Writers retire only once the
probe reports 0 misses for N days (proof-of-death).

### Stage D — silence by default
Routine nag emails removed: `classification-sweep --post-summary` dropped from its unit;
`preflight --post-summary` emails only on FAIL (HOLD). `run_chain` sends at most one
message per refresh: **"reports ready"** (green) with a **"needs your call"** section
only when the cascade left something unresolved.

### Stage E — one ordered chain + auto-trigger
The six L&I-engine parquet modules are folded into `apply_bloomberg_post_steps.STEPS`
(no more separate Mon/Fri clock). `run_chain`'s git step hard-aborts + alerts on a non-ff
divergence (never build on stale code). `watch_bloomberg.py` now drives `run_chain` (the
one chain) instead of a bespoke parallel sequence.

### Stage F — prune (proof-of-death) + docs
Deleted the second market write path with airtight 3-gate evidence (below).

### Autonomy hardening (Stage 5 + wiring the guards)
- **Heartbeat** (`scripts/healthcheck.py` + `rexfinhub-healthcheck.{service,timer}`):
  daily self-check that every chain stage ran AND succeeded today and that the DB +
  Bloomberg file are fresh — one consolidated, rate-limited alert on a silent failure.
  `run_chain` now appends every step outcome to `data/.pipeline_stages.jsonl` so the
  chain is observable.
- **Guards run on their own**: `rexfinhub-reconciler.service` runs `--probe` (ingest-gap
  detection + alert) before the transitional writer.
- **Cascade completed for categories**: `ai_classify_unmapped` adds a bounded web-search
  rung for funds the name+description can't place — finishing rules → description → web
  for `etp_category`, matching brands.
- **CI enforces the invariants**: `pr-checks.yml` runs the offline gate/heartbeat tests
  as a hard step (no failure-swallowing) so the guardrails cannot silently regress.

## Kill-list (proof-of-death ledger)

| Artifact | Static refs | Runtime use | Equivalent | Verdict |
|---|---|---|---|---|
| `scripts/run_market_pipeline.py` | only `run_all_pipelines.py` (Windows-legacy) + 1 HTML string | not a VPS systemd unit; prod = `sync_market_data`/`run_chain` | `sync_market_data` supersedes (adds MS override + post-steps it lacked) | **DELETED** |
| `market/derive.py` (`derive_dim_fund_category` + the `issuer_mapping` brand branch) | only `run_market_pipeline.py` | dead after the above | live brand path is `derive_issuer_brands` | **DELETED** |
| `market/transform.py::run_transform` | only `run_market_pipeline.py` | dead after the above | n/a | left in place (large module; flagged dead) |
| `issuer_mapping.csv` (brand use) | was read only by `derive_dim_fund_category` | neutralized by the deletions above | `issuer_brand_overrides.csv` | brand drift origin removed |
| `trex_combined_v2`–`v8` | — | — | — | already archived (`archive/retired-2026-06-16/`) |

## Known-gaps / deferred

- **Stage A part 2**: derive status *inside* sync (not just the assert guard); harden
  watcher uptime so `--probe` → 0. Needs VPS/DB validation.
- **Timer retirement** (Stage E): `systemctl disable rexfinhub-parquet-rebuild.timer`
  (folded into the chain) and `rexfinhub-classification-sweep.timer` once the chain
  proves green — VPS ops, see RUNBOOK.
- `market/transform.py` is now dead (only the deleted path used it); delete in a focused
  follow-up.

## Consequences

A fact that is wrong can no longer ship silently: it is resolved by the cascade, blocked
by the gate, or surfaced once for Ryu. The send checkpoint is hard-coupled to a green
build. Ryu's interaction budget per refresh is one message.

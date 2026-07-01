---
doc: revamp-roadmap
title: rexfinhub Revamp Roadmap
status: proposed
date: 2026-06-24
authors: Ryu + ATLAS (4-domain investigation)
---

# rexfinhub Revamp Roadmap

Durable output of the 2026-06-24 full-system investigation (four parallel read-only
domain audits: data model, pipeline/ingestion/AI, reports/send/web, architecture/code-health).
This is the plan a subsequent `/goal` executes from. Nothing here is built until each phase is
approved. Build-prove-retire applies throughout: nothing old is deleted until a 3-gate proof of
death (static grep / runtime / equivalence) is green.

---

## TL;DR — the diagnosis

rexfinhub is **architecturally sound but carrying three compounding debts**. It does not need a
rewrite — it needs to *finish what was designed, restore its instruments, and delete the
scaffolding*.

1. **Unfinished migrations.** The best designs are half-built. The **canonical-id identity spine**
   (`product_master` / `identifier_xref`) — the correct model — was wired only to REX's 593 funds,
   never extended to the 7,697-row universe. **ADR-0014** (one effective date) and the **3-axis
   classification** cutover are likewise declared-but-partial. The system therefore runs *three
   generations of classification in parallel*, and product identity is bound to the mutable ticker.
   Every data-quality incident this week (dup tickers, orphaned old-ticker rows, null-names,
   time-series drift, ticker-bleed) is one face of that single flaw.
2. **Numb instrumentation.** The chain **exits 1 on every run**; preflight is **never green** (a
   cry-wolf AI audit forces every send through an `autogo_on_warn` override); several "disabled"
   timers are **still firing**; a stale `.send_log.json` sits next to the live `send_log` table.
   The signals that should report "healthy" are broken, so real failures hide.
3. **Band-aids holding it together.** Nightly crons (`audit_duplicate_tickers`, `restamp_time_series`,
   `canonicalize_crypto_underliers`, `drop_duplicates(keep='first')`) are the *only* reason the
   counts are zero. The send path is guards-around-guards. Four of ten report builders re-implement
   the same helpers.

**The revamp = finish the migrations + restore the signals + delete the band-aids.** Success is
not "make the counts zero" (they already are) — it is **"delete the band-aids and keep the counts
zero,"** which only the structural fixes achieve.

---

## Already shipped (2026-06-24, context)

- **SEC watcher source-fix** (`6f6e3a2`). Root cause: the SEC `getcurrent` atom feed is hard-capped
  at **100 entries/query**; the broad `type=497` query lumped 497/497J/497K into one 100-window, so
  during a filing rush a 497K scrolled off the bottom before the next poll (silent overflow, no
  error). Fix: split the flooded queries into per-subform (`485B`/`485A`, `497K`/`497J`/`497`) so
  each window spans far more wall-clock. Verified live; accession-dedup prevents double-ingest.
  Out-of-scope note: `getcurrent` returns 0 entries for bare `N-1A` — those only arrive via the
  reconciler/EFTS path (pre-existing, not changed).
- **Public "CC" → "Income" labels** on the Downloads page (`e470490`); API `value="CC"` preserved.

---

## Per-domain findings

### A. Data model & classification (the structural core)

**Root flaw: no stable product identity in the live path, and the identity used folds
classification into the key.** `mkt_master_data` (7,697 rows, ~110 cols) carries three generations
of classification, none retired:

- Legacy 5-bucket (what reports still read): `etp_category` (blank on 4,927 rows), `category_display`,
  `primary_category`, `map_li_*`, `map_cc_*`, `cc_category`, `issuer_nickname`.
- Mid-gen (orphaned): `strategy`, `strategy_confidence`, `underlier_type`.
- 3-axis (the convergence target): `asset_class`, `primary_strategy`, `sub_strategy` + 21 attrs.

Conflicts (live): `primary_category` 0 divergent vs `etp_category` (dead dup); `category_display`
is a 1:1 projection; `issuer_nickname` vs `issuer_display` diverge on **5,218 rows**;
`mkt_fund_classification` is a complete THIRD encoding with **0 readers** (already drifted); the
3-axis `primary_strategy` is partly **back-derived from legacy `etp_category`** (`apply_fund_master.py`
:280-291) — circularly coupled, not independent. 77 files read legacy `etp_category`; 47 read the
3-axis — the cutover was never completed.

Schema reality: PK is an autoincrement `id`; business key is `UNIQUE(ticker, etp_category)` —
**ticker is not unique, ticker+category is** (calibrated backwards: two categories = two legal rows;
same category = sync IntegrityError crash, the CMAY 2026-06-16 crash). Population is **full-snapshot
DELETE+reinsert** every sync — any fix not re-derivable from the Bloomberg sheet is destroyed each
run. The real identity layer (`product_master` 593 + `identifier_xref` 1,912) is clean but covers
only 593 funds; **7,694 of 7,697 master tickers have no `canonical_id`**, and `identifier_xref` has
recorded **0 ticker retirements** (the bitemporal machinery exists, never used for a ticker change).

Status model: `status_history` (1,108, bitemporal, the authority) is clean; `rex_products.status`
(legacy) lossily collapses `delayed→Filed` (**210 funds conflict**) and is only neutralized because
every reader wraps in `canonical_status()`. Effective dates: three stores still independently
populated (ADR-0014 declared one source but it is largely intent).

**Recs (impact / effort):**
1. Drop `UNIQUE(ticker, etp_category)`; classification = mutable attributes; key on `canonical_id`. (VHigh / L)
2. Extend `canonical_id` + `identifier_xref` to the full 7,697-row universe. (VHigh / L)
3. Sync: DELETE+reinsert → **upsert on `canonical_id`**. (High / M-L)
4. Time-series/snapshot reference master by id (stop copying labels → retire `restamp_time_series`). (High / M)
5. Drop dead encodings: `mkt_fund_classification` table, `primary_category` col, `rex_product_status_history` (0 rows). (Med / S)
6. Collapse `strategy`/`strategy_confidence`/`underlier_type` + `category_display` + `issuer_nickname` into 3-axis + `issuer_display`. (Med / M, gated on 77→47 reader cutover)
7. Cut the `apply_fund_master` back-derivation of `primary_strategy` from `etp_category`. (Med / S-M)
8. Replace stored `rex_products.status` with a `canonical_status(status_cached)` view. (Med / M)
9. Make ADR-0014 real: one effective-date column-of-record, schema-enforced. (Med / M)
10. Fix ticker-bleed at source: dedup step3 extraction by `(CIK + series_id)`. (Med / M)

Spine: #2 → #1/#3 (everything depends on canonical-id reaching the universe). #5 is free subtraction.

### B. Pipeline, ingestion & AI (the numb instruments)

The on-demand chain (`run_chain.py`) runs **~9–12 min**, six steps; the **staging→promote gate**
(17 audits, promote only on non-fail, `.preflight_red` hard-blocks send) is the **strongest part of
the design**. But:

- **`post_steps_classify_ai_enrich` returns rc=1 every run** (one chronically-failing sub-step among
  22; orchestrator reports `max()` so the chain's exit code is a dead signal).
- **Preflight is never green — only `warn`** because `audit_ai_semantic_review` false-positives every
  run (flagged the real count 79 as "implausibly low," once flagged real issuer "Corgi" as a test
  value). Every promotion/send rides the `autogo_on_warn` override = cry-wolf.
- 22 post-steps are **comment-ordered, not a DAG**; an early failure runs later steps on stale state.
- **Redundant timers still enabled** (`classification-sweep`, `parquet-rebuild`, `reconciler`) though
  the chain does this inline — second-writer drift risk.
- **SEC extraction (`step3.py`)** swallows fetch/parse errors silently + `record_success()` on a
  0-row extraction → transient errors become permanent omissions. ADR-0014's `robust_election` is
  wired **only for 485APOS**; 485BPOS/BXT still use the fragile regex cascade; the divergence
  tripwire is **not deployed**.
- **AI middlemen** (`ai_classify_unmapped`, `ai_underlier_intel`, `ai_source_ipo`) are auxiliary,
  idempotent via journal dedup, total cost **<$0.10/day**; but all parse JSON by find-brackets +
  silent fallback to `[]` (no schema validation), hardcode model IDs, no backoff; `ai_source_ipo`
  has knowledge-cutoff drift (judges "still private" from training data).
- **DB/perf is solid for scale** (976MB, 150GB disk @ 35%, baked-HTML cache works, gz upload 7–36MB).
  Hygiene: ~6GB of redundant `pre_*.db` / render-staging copies on disk; no scheduled `PRAGMA
  optimize`/VACUUM.

**Recs:** restore signals first — (1) make chain exit-code meaningful, (2) kill the cry-wolf WARN,
(3) disable redundant timers, (4) finish ADR-0014 (485BPOS/BXT + tripwire); then harden silent
failures — (5) schema-validate LLM JSON, (6) centralize model IDs + backoff, (7) stop step3 error
swallowing, (8) DB-back the file markers; structural — (9) journaled DAG (ADR-0011 `engine/tick.py`),
(10) cost/health rollup in notify email, (11) cron `pre_*.db` cleanup + monthly optimize.

### C. Reports, send & web (accumulated complexity)

10 builders, **5 construction styles**, no shared library across them: `report_emails.py` (2,184 LOC)
has a real shared layer (`_wrap_email`, `_kpi_row`, `_table`, `_flow_bars`) reused by li/income/flow/
autocall — but **trex (1,517 LOC), blue_ocean, portfolio_suite, microsectors each re-implement colors/
escaping/formatters/tables from scratch**, and daily/weekly live in a third world (`email_alerts.py`).
Measured sizes: li 1.30MB, microsectors 1.15MB, income 1.14MB — **base64 PNGs + 2 embedded Gotham
fonts (~400-500KB) + inline-CSS-per-cell**. Gmail clips at ~102KB → these clip.

**Send system fragility (the week's pain, honestly):** exactly-once dedup IS a real DB `send_log`
table (good) — but a stale `data/.send_log.json` (9 code refs, not written since 2026-04-27) sits
beside it = "did it send?" confusion. `--force` skips dedup **entirely, unguarded** = the double-send
vector. `system_flags.set_flag` swallows a DB error with a warning then writes the dotfile anyway →
**gate dual-write can silently desync** (stuck-open/closed). `get_recipients` returns `[]` on unknown
`list_type` and `_load_recipients` falls through to a static file / `SMTP_TO` on any DB exception →
**silent misroute**. Gate is checked twice (email_alerts + graph_email). Render staging leaves ~1GB
files between the coarse 1h sweeps.

Contract layer (`report_numbers.yaml` + `contracts.py`) is genuinely self-correcting for ~5 numbers
but **7 of 10 reports have no entry**, and recipients live in 3 sources (YAML + DB table +
`expected_recipients.json` stale snapshot). Web: startup eager-loads `mkt_time_series` (280K) +
master with no TTL (blocks health-check); parquets die on Render deploy (not staged in
`render_build.sh`); dead surfaces = 13F (/holdings, /intel — DB absent on prod) + capm redirect.

**Recs:** send — (1) retire `.send_log.json`, (2) guard `--force` (typed confirm + "last sent at X"),
(3) `set_flag` fail-loud (or retire the dotfile), (4) hard-error on unknown `list_type`, (5) tighten
Render-staging cleanup, (6) collapse the double gate check; reports — (7) extract `report_components.py`
+ migrate the 4 standalone builders, (8) inline-CSS → `<style>` block, (9) stop base64 fonts/full-res
PNGs (host as URLs), (10) unify chart engine; contract — (11) single recipient source (DB table),
(12) extend contract to all 10 reports; web — (13) lazy-load caches + TTL, (14) stage parquets on
Render, (15) park/admin-gate the dead 13F surfaces.

### D. Architecture & code health (partial — agent rate-limited)

The dedicated deep-dive was rate-limited before its final report; its territory is covered by the
existing finalize plan and the other three domains: **version sprawl** (`trex_combined_v2..v8` dead,
v9 live; one-time `migrate_*`/`backfill_*` scripts; experimental generators; `*.bak`), the **two
market-write paths** (`run_market_pipeline` vs `market_sync`/`run_daily` — confirm which is
authoritative post-consolidation), and **doc sprawl** (stale docstrings — `apply_bloomberg_post_steps`
says "4 steps", has 22). **Action: re-run a focused dead-code/prune inventory** to produce the exact
git-rm kill-list before Phase 3.

---

## Phased roadmap

| Phase | Theme | Effort | Why |
|---|---|---|---|
| **0** | **Restore the signals** | S | Can't trust "green" until instruments are real. Day of low-risk work. |
| **1** | **Harden the send** | S–M | Closes every send fragility from this week. |
| **2** | **Finish the data-model migration (canonical-id)** | L | The structural prize — kills the whole data-quality bug class; lets us delete the band-aids. |
| **3** | **Subtract the dead weight** | S–M | Prove-dead-then-drop the redundant encodings, version sprawl, doc sprawl. |
| **4** | **Finish ADR-0014 + extraction hardening** | M | Closes the most-patched bug class (effective dates) + silent-failure surfaces. |
| **5** | **Reports & web cleanup** | M | Shared component library, lighter payloads (un-clip Gmail), web perf. |

**Phase 0 (do first):** fix the chain exit-code (split fatal vs advisory sub-steps; find the
every-run failure); fix/demote `audit_ai_semantic_review` so green means green; disable the redundant
timers; delete stale `.send_log.json` + the ~6GB orphaned DB copies.

**Phase 1:** one dedup source (DB); guard `--force`; `set_flag` fail-loud; hard-error on unknown
`list_type`.

**Phase 2 (the prize):** extend canonical-id to the full universe; key master on it; classification →
mutable attributes; sync → upsert; time-series → reference-by-id.

---

## Recommended sequencing

**Phase 0 + Phase 1 first** (~2–3 days, low-risk, behind the locked gate, validated with a `--to`
shadow send) — they fix what bit us this week and restore the signals needed to judge everything
after. **Then Phase 2** (the identity spine). Phases 3–5 follow once the migration is in and the
signals are trustworthy.

## Success criteria (for the /goal)
- Chain exits 0 on a healthy run; preflight reports **green** (not perpetual warn).
- The band-aid crons (`audit_duplicate_tickers`, `restamp_time_series`, `drop_duplicates`,
  `canonicalize_*`) can be **disabled and the counts stay zero** (proves the structural fix).
- One identity (`canonical_id`), one classification of record (3-axis), one status (view over
  `status_history`), one effective date — legacy columns dropped after 3-gate proof.
- Send path: one dedup source, `--force` guarded, gate single-sourced + fail-loud, no silent misroute.
- No report clipped by Gmail; one shared report component library.

## Caveats
- Live DB is currently clean on every symptom *because* of the band-aids — the revamp must remove the
  band-aid and keep it clean, not just observe zero.
- Build-prove-retire: nothing legacy dropped without static-grep + runtime + equivalence proof.
- Send gate stays locked; no external send during revamp work without explicit go.
- Re-run the architecture dead-code inventory (the one rate-limited domain) before Phase 3's prune.

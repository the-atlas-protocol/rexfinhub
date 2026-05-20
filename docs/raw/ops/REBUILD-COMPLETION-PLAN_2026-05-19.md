---
title: REX FinHub — Rebuild Completion Plan
created: 2026-05-19
owner: Ryu El-Asmar
derived-from: docs/TARGET.md v4, ADRs 0001–0010
status: active execution plan
---

# REX FinHub — Rebuild Completion Plan

> The 7-phase structural rebuild is ~85% built. This document is the finish line:
> every remaining item, in dependency order, with an explicit proof gate on every
> retirement. Completing this plan *realizes* the target architecture — it makes
> the new data model the **only** path, with no legacy escape hatches left open.

---

## 0. How to use this document

- **Section 1–4** = the destination and the rules. Read once.
- **Section 5** = where we stand today, verified against VPS + origin/main.
- **Section 6** = the work, organized into 7 tracks (Track 0–6). Each track has
  numbered steps, dependencies, and proof gates.
- **Section 7** = the order to execute in.
- **Section 11** = the definition of done. When every box there is checked, the
  architecture is realized.
- There are **no calendar deadlines** in this plan. Gates are *proof conditions*
  (clean diff, zero-access observation, identical output), not dates.

---

## 1. The Goal

**The three-touchpoint workday.** Ryu's entire daily interaction with REX FinHub
should be exactly three things:

1. Read the 08:00 ET triage email (~2 min).
2. Paste a CBOE cookie at `/admin/cboe-cookie` — only when the banner shows stale.
3. Enter a target inception date on `/operations/pipeline` when a new REX filing lands.

Anything else — editing a CSV, clicking sync, force-opening the gate, SSH-ing to
the VPS — is a **bug in the system**. The rebuild exists to delete every reason
those fourth, fifth, and sixth touchpoints still exist.

---

## 2. The Target Architecture — 7 Invariants

| # | Invariant | What it kills |
|---|---|---|
| I-1 | **One canonical UUID per product** (`product_master.canonical_id`); every ticker/CUSIP/CIK/FIGI maps in via bi-temporal `identifier_xref`. | SEC ticker recycling silently re-pointing a live product (BUG-02 class). |
| I-2 | **Polymorphic typed underliers** (`underlier_master`, 8 types) — never a bare string. | Crypto/underlier string-mismatch bugs (BUG-01 class). |
| I-3 | **Bi-temporal lifecycle** (`status_history`) — every status change appends a row; reality-time + knowledge-time; nothing updates in place. | Silent in-place status flips with no audit trail (GAP-09). |
| I-4 | **Deterministic survivorship** — declared per-field source priority resolves all source conflicts. | Ad-hoc winner-picking when SEC/Bloomberg/CBOE disagree. |
| I-5 | **Ops-as-assertions** — 25 dbt-style checks run every morning; failures become triage items in the 08:00 email. | Ryu re-running pipeline steps to hunt for what broke. |
| I-6 | **One `classification_override` table** replaces all 6 rule CSVs. | Hand-editing CSV files — Ryu's single largest daily touchpoint. |
| I-7 | **Self-service admin** — cookie, inception, send-pause, system-state all via `/admin/*`. | Chat-in-the-middle, SSH-edit-config, Syncthing-from-laptop. |

---

## 2b. Status Lifecycle State Machine

The 8-state product lifecycle (`status_history.status`, per ADR 0008):

| State | Meaning | Trigger |
|---|---|---|
| `under_consideration` | REX is planning the product; **no SEC filing exists yet** | manual — operator creates a pre-filing record (admin surface, Track 5B) |
| `filed` | An SEC 485-series filing exists for the product | auto — filing matcher (Track 5B); a product is born here directly if it was never pre-entered |
| `effective` | 485BPOS registration is legally effective | auto — SEC evidence (effective date reached) |
| `target_list` | REX has committed to listing the product | manual — operator enters a target inception date on `/operations/pipeline` |
| `listed` | Trading on the exchange | auto — reconciler, 3-source rule satisfied (~15 min) |
| `suspended` | Trading halted | auto — exchange / Bloomberg signal |
| `delisted` | Removed from the exchange | auto — vanished-from-market logic (BUG-04 fix) |
| `liquidated` | Fund wound down | auto / manual — liquidation notice |

**The duplicate problem (raised by Ryu, 2026-05-20).** Because `under_consideration`
is *pre-filing*, a product record exists before its SEC filing does. Without
intervention, the incoming filing would create a *second*, duplicate record. This
is precisely what the canonical-identity model (I-1) was built to prevent: the
pre-filing product already owns a `canonical_id`, and the **filing matcher**
(Track 5B) attaches the incoming filing's identifiers to that existing
`canonical_id` rather than minting a new product. Ambiguous matches are surfaced
in the morning triage for a one-click operator merge.

**`target_list → listed` (Ryu's question).** It auto-switches — but **not on
Bloomberg ACTV alone.** The 3-source rule (the structural fix for BUG-04
ghost-listings) requires SEC + Bloomberg ACTV + Exchange evidence to concur. In
practice ACTV is the *last* of the three to land, so it is the de facto trigger
and the reconciler promotes to `listed` within ~15 min. If ACTV lands while SEC
or Exchange evidence is still missing, the product stays at `target_list` and the
morning triage flags it — deliberate, so a fund is never Listed when it is not
actually trading.

---

## 3. Realization Map — what delivers each invariant

Most invariants are **already built**. The gap between "built" and "realized" is
that the *old paths still exist alongside the new ones*. The architecture is
realized only when each legacy path is provably dead and removed.

| Invariant | Built? | What "realized" still requires |
|---|---|---|
| I-1 canonical UUID | ✅ tables + backfill shipped | Track 4a — drop `capm_products` (the competing identity table). |
| I-2 typed underliers | ✅ tables + backfill shipped | Track 3 — resolve 12 unknown underliers + OpenFIGI FIGIs. Track 4b — drop freeform underlier columns from `rex_products`. |
| I-3 bi-temporal status | ✅ Stages 1–4 shipped | Track 5 — Stage 5: deprecate direct `rex_products.status` writes so `status_history` is the *only* mutation path. |
| I-4 survivorship | ✅ `survivorship.py` shipped | Track 1 — reconcile doc drift so the rule is unambiguous. |
| I-5 ops-as-assertions | ✅ 25 assertions + 08:00 triage shipped | Nothing — keep it green. |
| I-6 one override table | ✅ Stages 1–6 shipped | Track 4c — delete the 6 rule CSVs (the escape hatch). |
| I-7 self-service admin | ✅ Phase 2 + `/admin/system-state` + classify-override UI shipped | Track 4d — delete the 14 flag files; Track 0 — close the security holes that still force SSH. |

**Thesis:** the new architecture is built. *Realizing* it = proving every legacy
path dead, then removing it — plus building the one genuinely new system that
remains (Track 6, edgartools).

---

## 4. Governing Principle — Build · Prove · Retire

> **"Everything we want, we build. We remove nothing that exists until we have
> absolutely determined it has no use."** — Ryu, 2026-05-19

Every retirement in this plan (drop a table, delete a CSV, delete a flag file,
retire legacy code, drop a column) passes a **three-gate proof of death** before
the irreversible step fires:

- **Gate A — Static proof.** A codebase grep proves *zero code paths* read the
  artifact. Documented in the track.
- **Gate B — Runtime proof.** Where feasible, the artifact is instrumented (access
  logging) and observed across real traffic; the count of accesses is **zero**.
- **Gate C — Equivalence proof.** A final reconciliation diff proves the
  replacement holds **100%** of the artifact's data / behavior.

All three green → the retirement is *armed*. It fires only on Ryu's explicit go
(Decision D2). Nothing is ever deleted autonomously.

The new artifact is **built and run in parallel** with the old one for the entire
proving period. We are never without a working path.

---

## 5. Verified Current State (2026-05-19, vs VPS + origin/main)

| Phase | State | Remaining |
|---|---|---|
| 0a Security | DEFERRED (project-scale hardening) | Acute items pulled into **Track 0**. |
| 0b Bug patches | ✅ SHIPPED | BUG-01…08 all resolved. |
| 1 Cuts | ✅ Cuts 1/2/4/5 shipped; Cut 3 wrapper verified working tonight (rc=2, send correctly gate-blocked) | **Track 2** closeout. |
| 2 Admin pages | ✅ SHIPPED | — |
| 3 capm merge | 🟡 Stages 1–3 shipped | **Track 4a** — drop `capm_products`. |
| 4 canonical id | 🟡 Stages 1–5 shipped (12 underliers unresolved) | **Track 3** + **Track 4b**. |
| 5 status_history | 🟡 Stages 1–4 shipped; reconciler in dry-run | **Track 5**. |
| 6 classification_override | 🟡 Stages 1–6 shipped; 25 assertions live | **Track 4c** — delete 6 CSVs. |
| 7B state consolidation | 🟡 Stages 1–2 shipped | **Track 4d** — delete 14 flag files. |
| 7A edgartools | 📐 Designed (ADR 0010), not built | **Track 6**. |

**Production facts verified:** VPS DB carries all 10 new tables; `rex_products` =
48 columns; 25/25 assertions PASS; Render serving 200s; all systemd timers
healthy including the fixed `intraday-refresh` at :05; this worktree functionally
current with origin/main (`f8efb41`).

**Also found — 17 documentation defects** (stale TARGET.md lines, undefined
"Phase 4b", schema drift vs ADRs, ADRs 0006–0010 still marked "proposed").
Catalogued in **Appendix A**, fixed in **Track 1**.

---

## 6. The Plan — Tracks 0 → 6

Each track is independently shippable as one or more PRs to `main`, deployed to
the VPS, and verified. Legend: **[BUILD]** new work · **[PROVE]** verification ·
**[RETIRE]** armed removal, fires on Ryu's go · **[DOC]** documentation.

---

### Track 0 — Acute Security  *(default-IN; D4 unanswered)*

**Objective:** close the two security holes that are acute incidents, not
project-scale work. Broad Phase 0a hardening stays deferred.

| # | Step | Type |
|---|---|---|
| 0.1 | Rotate `ADMIN_PASSWORD` — it was public on GitHub until 2026-05-05 and has never been rotated since exposure. New value to VPS `.env` + Render env. | [BUILD] |
| 0.2 | Check `AZURE_CLIENT_SECRET` expiry in Azure Portal. If it lapses, **both** email send and Bloomberg pull fail silently. Record the expiry date; set a calendar reminder. | [PROVE] |
| 0.3 | Inventory the remaining unrotated secrets (`API_KEY`, `SESSION_SECRET`, `SITE_PASSWORD`, `ANTHROPIC_API_KEY`) into the deferred Phase 0a scope — do **not** rotate now (rotating `SESSION_SECRET` logs everyone out; `API_KEY` must change in lockstep on VPS + Render). | [DOC] |

**Dependencies:** none. **Verification:** admin login works with the new
password on Render; Azure expiry date is known and noted in `SYSTEM.md` GAP-01.

---

### Track 1 — Documentation Reconciliation

**Objective:** make the canonical docs trustworthy. Every later track and every
future Claude session rests on these docs; right now they contradict themselves.

| # | Step | Type |
|---|---|---|
| 1.1 | `TARGET.md` — delete the stale "planned / starts after Phase N" lines that sit next to the struck-through "SHIPPED" lines for Phases 4/5/6 (defect C-2). | [DOC] |
| 1.2 | `TARGET.md` — add a real **Phase 4b** entry to `### phases` (defect C-3): scope = resolve remaining unknown underliers + drop freeform underlier columns. | [DOC] |
| 1.3 | `TARGET.md` — fix `### cuts`: remove the obsolete "retire one scraper" line (ADR 0005 kept all three; C-8); correct "state files → (Phase 6)" to "(Phase 7B)" (C-7). | [DOC] |
| 1.4 | `TARGET.md` — correct assertion count "15" → "25" (C-4). | [DOC] |
| 1.5 | `TARGET.md` ↔ ADR 0006 — reconcile `fund_underlier` schema to the **shipped** form (`effective_from`/`effective_to` columns, not `TSRANGE`); add `product_master.is_rex` and the `bloomberg` `id_type` (C-11, C-12). | [DOC] |
| 1.6 | `TARGET.md` ↔ ADR 0008 — reconcile `status_history.status` enum to the shipped 8-value set (`under_consideration→filed→effective→target_list→listed`, +`suspended/delisted/liquidated`); delete the erroneous `trading` value (C-13). | [DOC] |
| 1.7 | Settle the classification route name to the shipped `/admin/classify-override/{canonical_id}` across `TARGET.md` + ADR 0009 (C-14). | [DOC] |
| 1.8 | Close `TARGET.md` `### known-gaps` GAP-01/02/03 — the ADRs they reference now exist; GAP-03's survivorship ADR is folded into ADR 0008 (C-5, C-6). | [DOC] |
| 1.9 | Flip ADRs 0006–0010 status `proposed` → `accepted` — all are shipped or actively executing (C — ADR register). | [DOC] |
| 1.10 | `SYSTEM.md` / `RUNBOOK.md` / `GLOSSARY.md` — clear stale gaps: RUNBOOK GAP-01 ("Phase 2 doesn't exist" — it shipped), GLOSSARY `cboe-cookie` "proposed Phase 2", SYSTEM GAP-04/05/06 (resolved by ADR 0003/0005). Verify the 13F timer date (May 19 vs 20; C — minor). | [DOC] |
| 1.11 | Code-doc drift: confirm `rex_products.status_cached` exists as a DB column and add it to the `RexProduct` ORM model if missing. | [BUILD] |

**Dependencies:** none. **Verification:** a fresh read of `docs/INDEX.md` →
`TARGET.md` → `SYSTEM.md` produces zero contradictions; `grep` for each closed
gap returns nothing stale. Full checklist in **Appendix A**.

---

### Track 2 — Phase 1 Cut 3 Closeout

**Objective:** formally close ADR 0005. The `intraday-refresh` wrapper was
verified working tonight (FULL run, rc=2, send correctly gate-blocked).

| # | Step | Type |
|---|---|---|
| 2.1 | Investigate the two non-fatal upload errors from tonight's verification run: (a) Render HTTP 503 on screener-cache upload — transient, confirm retry covers it; (b) **missing `etp_tracker_render.db.upload.gz`** at DB-upload time — the real one: trace whether the compression step failed or a concurrent process removed the file. | [PROVE] |
| 2.2 | Fix the root cause of 2.1(b) if it is a sequencing bug in `run_daily.py`'s compress→upload ordering. | [BUILD] |
| 2.3 | Mark ADR 0005's acceptance-criteria checklist complete; confirm `SYSTEM.md` describes the three-scraper model (atom-watcher / fresh-poller / intraday-refresh) accurately. | [DOC] |
| 2.4 | Add `LOG.md` entry covering the SIGTERM fix (PR #61) and Cut 3 closeout. | [DOC] |

**Dependencies:** none. **Verification:** next scheduled `intraday-refresh`
(08:05 ET) completes rc=0 with a successful Render upload.

---

### Track 3 — Phase 4b: Underlier Completion  *(this worktree's named purpose)*

**Objective:** every Listed REX product resolves to a typed (non-unknown)
underlier. The freeform-column drop is Track 4b.

**Audit (2026-05-20) — replanned.** `underlier_master` had **15** rows typed
`unknown`, not 12. Only **2** of the 15 actually affect REX products; the other
13 are orphan rows that entered via the full competitor universe in
`mkt_master_data` and no REX fund links them.

| # | Step | Type | Status |
|---|---|---|---|
| 3.1 | Repoint the **5 MicroSectors 3X ETNs** (FNGU, NRGU, NRGD, BNKU, BNKD) off the junk `0` underlier onto the correct Solactive/NYSE index underliers. `fix_microsectors_underliers.py` had skipped them — it only handled funds with *no* link; these had a *wrong* link. | [BUILD] | `fix_underlier_classification.py` |
| 3.2 | Reclassify the **13 orphan `unknown` rows** + the `-` row (ULTI) to their correct polymorphic types — `commodity` (XAU/XAG), `crypto_pair` (the alt-coins), `basket` (multi-crypto, ULTI's option-strategy). | [BUILD] | same script |
| 3.3 | Strengthen the `underlier_id_coverage` assertion to flag a Listed fund linked to an `unknown`-type underlier, not just a missing link — catches the junk-underlier class permanently. | [BUILD] | `run_assertions.py` |
| 3.4 | OpenFIGI `primary_figi` enrichment — **descoped from the critical path** (audit replan). The equities are already correctly typed; `primary_figi` has no current consumer. Optional future enrichment. | [DESCOPED] | — |

**Dependencies:** none. **Verification:** after `fix_underlier_classification.py`,
`underlier_master` has exactly 1 `unknown` row (the literal junk `0`, which no
fund references), and 0 Listed REX funds link to an unknown-type underlier.

---

### Track 4 — Arm & Prove the Four Retirements

**Objective:** retire the four legacy artifacts that keep the architecture from
being the *only* path. Per the Governing Principle, each passes all three proof
gates, then waits *armed* for Ryu's go (Decision D2 = arm + prove now, fire on go).

> **D2 is set:** I build every drop script and reconciliation check and run the
> proof-diffs now. The instant a diff is clean, the drop is armed; it fires on
> your explicit go — possibly today, possibly before the ADR's 7-day mark.
> **Nothing fires autonomously.**

#### Track 4a — Retire `capm_products` (Phase 3 Stages 4–5)

| # | Step | Type |
|---|---|---|
| 4a.1 | **Gate A:** grep-prove no code reads `capm_products` (the `/operations/products` merge was refactored 3-way→2-way in Phase 3 Stage 3 — confirm `capm.py` no longer references the table for reads). | [PROVE] |
| 4a.2 | **Gate B:** add access logging to any residual `capm_products` reference for one observation window; confirm zero reads under real traffic. | [PROVE] |
| 4a.3 | **Gate C:** write `drop_capm_products.py` with a built-in final reconciliation diff — every `capm_products` row's fields must be present and equal on the merged `rex_products` row, else the script aborts. Run it in `--check` mode now. | [BUILD][PROVE] |
| 4a.4 | All three gates green → **armed.** Fire `DROP TABLE capm_products` on Ryu's go. `capm_audit_log` is retained. | [RETIRE] |

#### Track 4b — Retire freeform underlier columns on `rex_products`

| # | Step | Type |
|---|---|---|
| 4b.1 | **Gate A:** grep-prove no code reads `rex_products.underlier` / `underlying_ticker` / `underlying_name`; all reads go through `underlier_master` + `fund_underlier`. | [PROVE] |
| 4b.2 | **Gate C:** prove `fund_underlier` + `underlier_master` hold 100% of the data the freeform columns held (depends on Track 3 = 0 unknowns). | [PROVE] |
| 4b.3 | All gates green → **armed.** Drop the freeform columns on Ryu's go. | [RETIRE] |

> **Ordering:** 4b fires *after* 4a — `capm_products` carries its own underlier
> columns; dropping `rex_products`' first would force a second rewrite of the
> merge code (resolved item #1 from the grill).

#### Track 4c — Retire the 6 rule CSVs (Phase 6 Stage 7)

| # | Step | Type |
|---|---|---|
| 4c.1 | **Gate A:** grep-prove no code path loads the 6 rule CSVs (`fund_mapping`, `issuer_mapping`, `attributes_LI/CC/Crypto/Defined/Thematic` — the *rule* CSVs; the other ~12 CSVs in `config/rules/` are not in scope). Resolver reads `classification_override` first. | [PROVE] |
| 4c.2 | **Gate C:** diff — every row across the 6 CSVs has a matching `classification_override` row (486 migrated; re-verify count parity). | [PROVE] |
| 4c.3 | All gates green → **armed.** `git rm` the 6 CSVs on Ryu's go. | [RETIRE] |

#### Track 4d — Retire the 14 flag files (Phase 7B Stage 3)

| # | Step | Type |
|---|---|---|
| 4d.1 | **Gate A:** grep-prove every flag-file read site goes through `system_flags.py` (5 sites migrated in Stage 2 — confirm no direct file reads remain). | [PROVE] |
| 4d.2 | **Gate B:** the `system_flags` helper currently dual-writes (DB + file). Flip reads fully to DB; observe one window with zero file reads. | [BUILD][PROVE] |
| 4d.3 | **Gate C:** confirm `system_flags` / `preflight_run` / `system_event` hold the full state. `temp/submissions.zip` is **kept** (genuine binary cache — not a flag file). | [PROVE] |
| 4d.4 | All gates green → **armed.** Delete the 14 flag files on Ryu's go. | [RETIRE] |

**Track 4 dependencies:** 4b after 4a. 4a/4c/4d independent of each other.
**Verification:** after each fire, the assertion suite stays 25/25; Render
upload + daily send unaffected.

---

### Track 5 — Phase 5 Stage 5: `status_history` as sole authority

**Objective:** make `status_history` the *only* way a product's status can
change — fully realizing invariant I-3.

| # | Step | Type |
|---|---|---|
| 5.1 | Generate the reconciler `--dry-run` diff and capture the full **175 promote / 39 demote** breakdown — ticker, old→new status, the 3-source evidence behind each. (D3 default: this breakdown goes to Ryu for review before `--apply`.) | [PROVE] |
| 5.2 | Ryu reviews the diff. 40% status churn on 541 products demands eyes-on before it goes live. | [PROVE] |
| 5.3 | On approval, flip the reconciler to `--apply`. It now writes real `status_history` transitions after every Bloomberg sync + fresh-poller fire. | [RETIRE-of-dry-run] |
| 5.4 | **[BUILD]** Route every remaining direct `rex_products.status = …` write through `append_status_row()`. Grep-prove zero direct writes remain outside the reconciler. | [BUILD][PROVE] |
| 5.5 | Add an assertion: "no `status_history` gaps / overlaps; every `canonical_id` has exactly one open (`valid_to IS NULL`) row." | [BUILD] |

**Dependencies:** Track 5.4 is cleaner after Track 4a (one products table).
5.1–5.3 can run independently. **Verification:** a manual status change made
anywhere in the app produces a new `status_history` row; `status_cached` matches.

#### Track 5B — Pre-filing lifecycle & filing matcher  *(raised by Ryu, 2026-05-20)*

**Objective:** make `under_consideration` (pre-filing) usable without creating
duplicate products when the SEC filing later arrives. See Section 2b.

| # | Step | Type |
|---|---|---|
| 5B.1 | Admin surface to create a pre-filing product: `product_master` row + `canonical_id` + working `fund_name` + optional planned ticker + target trust CIK; `status_history` row `under_consideration`. | [BUILD] |
| 5B.2 | Filing matcher — when a new 485-series filing is detected, *before* creating a new `product_master`, match it against open `under_consideration` products by: planned ticker (via `identifier_xref`), fund-name overlap (reuse `_names_overlap()`, BUG-02), target trust CIK. | [BUILD] |
| 5B.3 | Confident match → attach the filing's identifiers (CIK, series_id, class_contract_id, ticker) to the existing `canonical_id`; append `status_history` `filed`. No new product. | [BUILD] |
| 5B.4 | Ambiguous match → create the filing-derived product but raise a morning-triage item: "new filing X resembles under_consideration product Y — merge?" with a one-click merge affordance. | [BUILD] |
| 5B.5 | Add an assertion: no two `product_master` rows share a ticker/CIK/series across overlapping `identifier_xref` validity windows — catches duplicates the matcher missed. | [BUILD] |

**Dependencies:** Phase 4 canonical identity (shipped) + `_names_overlap()`
(shipped, BUG-02). Independent of Track 5A. **Verification:** create an
`under_consideration` product with a planned ticker, then ingest a filing
carrying that ticker — exactly one `product_master` row results, now at `filed`.

---

### Track 6 — Phase 7A: edgartools Migration

**Objective:** replace the ~3,500-line in-house SEC extraction stack
(`bulk_loader.py`, `single_filing.py`, `extractor.py`) with the MIT-licensed
`edgartools` library behind a compatibility shim. `atom_watcher.py` is **kept**
(no edgartools atom equivalent).

This is the one track that is mostly genuinely *new code*. It still follows
Build · Prove · Retire — and per Ryu's principle, **the legacy code is not
removed until absolutely proven to have no use.**

| # | Step | Type |
|---|---|---|
| 6.1 | Add `edgartools` dependency. Spike: run one CIK through it, compare field-level output to the in-house extractor. | [BUILD] |
| 6.2 | Build the compatibility shim `etp_tracker/edgar_client.py` — same function signatures the pipeline already calls, edgartools underneath. | [BUILD] |
| 6.3 | **Prove, fast path:** replay the last several weeks of *already-captured* filings (from the submissions cache + `filings` table) through **both** extractors; diff field-by-field. This converts ADR 0010's 2-week live wait into a one-shot batch over real historical data. | [PROVE] |
| 6.4 | **Prove, live path:** run both extractors in parallel on live filings with **diff-alerting** — any field divergence raises a morning-triage item. This runs continuously and is *not* time-boxed; it runs until divergences are zero across a meaningful sample of live filings. | [PROVE] |
| 6.5 | Cut `fresh-poller` + `intraday-refresh` over to the edgartools shim as the primary extractor. The legacy code **stays in place**, still runnable, diff-alerting still on. | [BUILD] |
| 6.6 | **Gate A+B+C combined:** legacy code has zero callers (grep), zero runtime invocations (instrumented), and the new path has produced identical output across the full proving sample. Only then is legacy retirement *armed*. | [PROVE] |
| 6.7 | Move legacy extractors to `etp_tracker/legacy/`; delete after the proving window, on Ryu's go. | [RETIRE] |

**Dependencies:** none blocking — ADR 0010's "Phase 6 first" is already
satisfied (`classification_override` exists; edgartools' typed output flows into
it regardless of the pending CSV deletion). Best sequenced last because it is the
largest single build. **Verification:** field-level diff = 0 across the proving
sample; SEC extraction metrics (filings/day, fields populated) unchanged after
cutover.

---

## 7. Dependency Graph & Execution Order

```
Track 0  Acute Security ─────────────┐  (independent, do early — security)
Track 1  Doc Reconciliation ─────────┤  (independent — do first, everything rests on it)
Track 2  Cut 3 Closeout ─────────────┤  (independent)
Track 3  Underlier Completion ───────┤  (independent — BUILD)
                                     │
Track 4a Retire capm_products ───────┤  (independent)
Track 4b Retire freeform columns ────┴──► requires 4a fired + Track 3 done
Track 4c Retire 6 CSVs ──────────────┐  (independent)
Track 4d Retire 14 flag files ───────┤  (independent)
                                     │
Track 5  status_history sole authority ─► 5.4 cleaner after 4a
Track 6  edgartools migration ───────────► largest build; sequence last
```

**Recommended order:** Track 1 (docs sound first) → Track 0 (security) →
Track 2 → Track 3 → Track 4a → Track 4c → Track 4d → Track 4b (after 4a) →
Track 5 → Track 6. Tracks 0/1/2/3/4c/4d have no inter-dependencies and can be
built in parallel where convenient.

---

## 8. Decisions of Record

| ID | Decision | Resolution |
|---|---|---|
| D1 | Phase 7A scope | **Build everything; remove nothing until absolutely proven unused.** Governs every track — see Section 4. Phase 7A is in scope (Track 6); legacy SEC code retires only after the combined proof gate. |
| D2 | Retirement timing | **Arm + prove now, fire on Ryu's go.** Drop scripts + reconciliation checks built immediately; irreversible step waits for explicit go, not a calendar date. |
| D3 | Reconciler `--apply` | *Default (unanswered):* the 175/39 diff goes into this plan / a review artifact; Ryu reviews before `--apply`. Track 5.1–5.2. |
| D4 | Acute security | *Default (unanswered):* in scope as **Track 0** — a known-public admin password cannot stay live. Broad Phase 0a stays deferred. |

ADR calendar gates (Phase 3 ≥2026-05-26, etc.) are **superseded by D2** — the
proof gate, not the date, governs.

---

## 9. Operator Touchpoints During Execution

Ryu's input is needed at exactly these points — nowhere else:

1. **Track 0:** supply / confirm the new `ADMIN_PASSWORD`; read the Azure secret expiry.
2. **Track 4 (×4):** one "go" per retirement once its proof gates are green.
3. **Track 5.2:** review the 175/39 reconciler diff; approve `--apply`.
4. **Track 6.7:** one "go" to delete the legacy SEC code once proven dead.
5. **Ongoing:** the existing CBOE cookie touchpoint (one is due now — `cboe.service` failed 403 at 03:00 ET).

Everything else executes without interrupting Ryu.

---

## 10. Risks & Watch Items

| Risk | Mitigation |
|---|---|
| Render DB upload flakiness (HTTP 503 / incomplete read — hit twice 2026-05-19). | Retry-with-backoff shipped (PR #54). Track 2.1 confirms it covers the screener-cache path too. Add an assertion if all retries fail. |
| Missing `etp_tracker_render.db.upload.gz` in tonight's run. | Track 2.1–2.2 root-causes before it recurs on a scheduled run. |
| Reconciler `--apply` churns 40% of products. | D3 — eyes-on diff review before going live (Track 5.2). |
| OpenFIGI rate limit (25 req / 6 s free tier). | Track 3.2 batches with backoff; only equity/etp underliers need it (~520 rows). |
| edgartools field-coverage gaps vs the in-house extractor. | Track 6.3 historical replay surfaces every gap before cutover; 6.4 live diff-alerting catches new filing formats. Legacy never removed until 6.6 proves zero divergence. |
| CBOE cookie expired (403 at 03:00 ET). | Operator touchpoint — rotate via `/admin/cboe-cookie`. Working as designed. |
| `AZURE_CLIENT_SECRET` silent expiry → email + Bloomberg both fail. | Track 0.2 — check expiry, set reminder. |

---

## 11. Definition of Done — Architecture Realized

The rebuild is complete when **every** box below is checked:

- [ ] **I-1** `capm_products` dropped; `product_master.canonical_id` is the sole product identity.
- [ ] **I-2** 0 unknown underliers; freeform underlier columns dropped from `rex_products`; every product resolves through `underlier_master`.
- [ ] **I-3** reconciler on `--apply`; **zero** direct `rex_products.status` writes outside `append_status_row()`.
- [ ] **I-4** survivorship rule documented unambiguously; `survivorship.py` is the only resolver.
- [ ] **I-5** 25/25 assertions green in the 08:00 triage email; new assertions added for underliers + status_history integrity.
- [ ] **I-6** 6 rule CSVs deleted; `classification_override` is the sole classification source.
- [ ] **I-7** 14 flag files deleted; `system_flags`/`preflight_run`/`system_event` are the sole state store; `ADMIN_PASSWORD` rotated.
- [ ] **Phase 7A** edgartools is the primary SEC extractor; legacy stack retired after proven dead.
- [ ] **Docs** `INDEX → SYSTEM → TARGET → RUNBOOK → GLOSSARY` are internally consistent; ADRs 0006–0010 marked `accepted`; all `known-gaps` current.
- [ ] **The three-touchpoint workday is real** — a full week passes with Ryu touching only the triage email, the CBOE cookie (when stale), and target inception dates.

When the last box is checked, every structural debt from the 2026-05-12 rebuild
is gone, the new data model is the only path, and the system runs itself.

---

## Appendix A — The 17 Documentation Defects (Track 1 checklist)

From the architecture review of `TARGET.md` + ADRs 0001–0010:

- [ ] **C-1** Phase 4 shipped before its stated Phase-3 prerequisite — reconcile the dependency wording.
- [ ] **C-2** `TARGET.md` `### phases` has both "SHIPPED" and "planned" lines for Phases 4/5/6 — delete the stale planned lines.
- [ ] **C-3** "Phase 4b" referenced but never defined — add it (Track 1.2).
- [ ] **C-4** Assertion count says 15, actual is 25 — correct it.
- [ ] **C-5** GAP-03's survivorship ADR never written — close GAP-03 as folded into ADR 0008.
- [ ] **C-6** GAP-01 cites wrong ADR number (`0002` vs actual `0007`) — close it.
- [ ] **C-7** `### cuts` assigns state-file consolidation to Phase 6 — it is Phase 7B.
- [ ] **C-8** `### cuts` still says retire one scraper — ADR 0005 kept all three.
- [ ] **C-9** ADR 0010 prose says "~7 state files" then lists 14 — fix to 14.
- [ ] **C-10** Phase 7 Part B stage numbering ("Part B Stage 1" = ADR 0010 Stage 6) — clarify.
- [ ] **C-11** `fund_underlier` schema drift — `TSRANGE` (TARGET.md) vs `effective_from/to` columns (ADR 0006, shipped). Use the shipped form.
- [ ] **C-12** `product_master.is_rex` and `identifier_xref` `bloomberg` id_type missing from TARGET.md.
- [ ] **C-13** `status_history.status` enum mismatch — 6 values incl. `trading` (TARGET.md) vs 8 values (ADR 0008, shipped). Use ADR 0008's set.
- [ ] **C-14** Classification route named 3 ways — settle on `/admin/classify-override/{canonical_id}`.
- [ ] **C-15** Phase 0b uses "Patches", Phase 1 uses "Cuts", Phases 3–7 use "Stages" — note the terminology, no change needed.
- [ ] **C-16** ADR 0010 title mentions `manually_edited_fields` decommission but the body doesn't — confirm that work is tracked under Phase 6.
- [ ] **C-17** Phase 0a entirely under-specified — mark explicitly as deferred-by-decision, out of rebuild scope.

Plus: ADRs 0006–0010 status `proposed` → `accepted`; RUNBOOK GAP-01, GLOSSARY
`cboe-cookie` "proposed Phase 2", SYSTEM GAP-04/05/06 — all stale, clear them.

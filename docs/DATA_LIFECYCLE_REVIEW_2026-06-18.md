# Data Lifecycle Review — what rexfinhub should be (2026-06-18)

> A whole-system review, not a list of patches. Five investigators mapped the data
> lifecycle end-to-end (ingestion → categorization → storage → rendering → backup),
> with file:line evidence. This doc summarizes what we found, walks each issue in
> plain English, lays out three *whole-system* directions, and recommends one — from
> the perspective of the whole, because changing one layer without the others is how
> we got here.

---

## The one-paragraph thesis (the disease)

rexfinhub has **good bones** — after PR #89 there is genuinely one market write path,
one suite-derivation function (`attach_rex_suite`), blocking preflight gates, and a
sound nightly DB backup. But every layer shares one disease: **each stage trusts that
the previous stage got it right, and when it didn't, the fix was bolted on downstream
instead of at the source.** Bad or missing data enters silently (stale Bloomberg
fallback, dropped SEC entries); "what is true and what each number means" lives in
human prose, not in anything the machine reads; correctness is re-established
independently in three different report functions; and recovery has never been
tested. The reconcilers, the `use_cache=False` flags, the re-stamp scripts — these are
all **symptoms of an implicit contract**. The system has no shared, machine-readable
notion of truth, so corrections accrete as patches and the same class of bug returns.

**What the system should be: a data refinery governed by one machine-readable contract,
where every value is established once at its source with provenance, and every
downstream consumer — the AI resolver, the gates, the reports, the docs — reads that
same contract instead of re-deriving truth on its own.**

---

## How real data teams solve this (so we're not reinventing)

Mature data shops don't fix this with willpower; they install four things. We don't
need the heavy platforms — we need the lightweight, solo-operator embodiment of each:

| Real-team pattern | Heavyweight tool | Our lightweight equivalent |
|---|---|---|
| **Data contracts / expectations** — every field & metric has a declared meaning + test | dbt tests, Great Expectations, Soda | One `contracts` artifact (source + filter + intent + expected + failure-signature), read by resolver + gate + reports |
| **Provenance / lineage** — every value records where it came from | OpenLineage, Marquez | A `provenance` stamp per resolved fact (rung, confidence, citation) — we already half-have this in `logs/ai_resolve_*.jsonl` |
| **Orchestration with observability** — the pipeline is a DAG that reports its own health | Dagster, Airflow | `run_chain` + `.pipeline_stages.jsonl` + the heartbeat (ADR 0013 already built this) |
| **Replicated managed store + tested DR** — no single file, restore is rehearsed | Postgres + PITR, RDS | Tested restore drill + offsite leg (cheap; not built yet) |

The recommendation below is exactly these four patterns at solo-operator weight.

---

## The five issues, in plain English

### 1. Data enters silently wrong (ingestion)
**What:** When a source fails, the system keeps going quietly instead of stopping.
- The Bloomberg pull falls back to a **stale local file** with only a log line — this is
  the literal root cause of the 2026-06-16 MicroSectors bug. `market/config.py` even
  says "hard-fail design — no stale fallback," but the fallback is still there.
- SEC atom entries with an unparseable CIK or accession are **dropped with a debug log**;
  485APOS effective-date extraction returns empty on a missed anchor; the Tier-2
  enrichment worker may not even have a systemd unit running it.
- 13F partial ZIPs **commit without rollback**; Q1 2026 was never ingested at all.

**Why it bites:** A number can be wrong or missing and *nothing tells you*. You find it
by eye, weeks later — exactly the loop you're trying to end.

### 2. Intent lives in prose, not in the machine (categorization & definition)
**What:** The AI that classifies funds is told the field name (`etp_category`) and the
allowed labels — but **not what they mean**. It is never told "CC = income via options,
NOT bond income," so a Treasury fund gets classified CC and is "correct" by the only
rule it was given. The brand resolver *does* carry intent ("…Trust isn't a brand") —
because the Tidal leak hurt enough to hand-code it once. Intent is wired in reactively,
one bug at a time. Meanwhile the intent itself is duplicated across `definitions.py`,
`REPORT_NUMBERS.md`, and `DEFINITIONS.md`, and **none of them is read at decision time.**

Worse, the "single source" isn't fully single: `attach_rex_suite` still falls back to
the stale `rex_suite_mapping.csv` (which GOAL.md said to delete), and there are ~18
overlapping rule CSVs (`rex_funds`, `rex_suite_mapping`, `fund_mapping`, `fund_master`
all encode REX identity differently).

**Why it bites:** A "self-correcting" system can only correct toward rules it already
has. It cannot correct toward an intent it was never given. The loop is open exactly
where it matters most.

### 3. Truth is established once but copied, then drifts (storage)
**What:** One write path now (good). But `mkt_time_series` bakes its category/issuer
labels **at insert time**, and the post-steps that enrich the master table afterward
**never re-stamp the time-series** — no guard, no assertion. So the chart data drifts
from the master. And ~105 columns carry legacy fields (`strategy`, `underlier_type`)
that are still *written* but no longer *read*.

**Why it bites:** The market-position card and charts can show a different category than
the report next to them, from the same pipeline run.

### 4. The same number is computed three different ways (rendering)
**What:** The headline L&I/income/flow counts are filtered by **hand-written logic in
three separate functions** (`get_li_report`, `get_cc_report`, `get_flow_report`) — and
the dedup is inconsistent: CC and flow dedup by ticker, **LI does not**. It ties out
today by luck of the current data, not by construction. Separately, the website/admin
can serve a **stale cache with no age shown**, while emails read live — so two surfaces
can disagree.

**Why it bites:** This is the GOAL.md "four definitions, four numbers" problem in
miniature, still alive. The next multi-category fund silently makes LI over-count.

### 5. Backup is sound; recovery is theoretical (durability)
**What:** The nightly VPS backup is genuinely good (atomic `.backup()`, integrity check,
prune-before-backup, failure alert). But **there is no tested restore procedure
anywhere.** The D: drive copies are *hot copies* of a live WAL-mode DB — not
consistency-guaranteed, possibly torn. 13F exists only as a partial copy on Render. The
AI decision journals and send history (`logs/*.jsonl`, `.send_log.json`) are **not
backed up** — restore the DB and you lose the audit trail written since the snapshot.

**Why it bites:** "We think we can restore" is not a backup. A corruption event today is
~2 hours of improvisation under pressure, with real loss risk.

---

## The common root (why these are one problem, not five)

Every issue above is the same shape: **a value is produced by one stage and consumed by
the next with no shared, enforceable statement of what it should be or where it came
from.** Ingestion doesn't assert completeness because nothing declares "complete." The
resolver guesses because nothing declares intent. The reports re-derive because nothing
is the canonical computation. Recovery is untested because nothing declares the restore
contract. **Fix them one at a time and you add five more bespoke patches. Fix the root
and each lane's fix becomes "wire it to the contract."** That is why this has to be
decided as a whole.

---

## Three whole-system directions

### Posture 1 — Patch in place (minimal, ~2–3 weeks)
Keep the architecture exactly as-is. Fix the worst silent-fails (make stale-Bloomberg
fail loud, validate extracted tickers/dates), add the time-series re-stamp + an
assertion, collapse the three report filters into one shared function, and write +
drill a restore runbook.

- **Pro:** Cheap, fast, no new concepts, ships before go-live noise settles.
- **Con:** The disease remains. Intent is still prose; correctness is still implicit;
  the next bug class still accretes a new patch. **This is the posture that created the
  current state** — it's local fixes without a spine. You will be back here in a quarter.

### Posture 2 — Contract + Provenance spine (recommended, ~6–8 weeks, incremental)
Build **one machine-readable contract layer** and route all five lanes through it.

- **Contracts:** one artifact keyed by fact/metric, each entry carrying `source`,
  `filter`, `intent` (one sentence), `expected` (where a canonical value exists), and
  `failure_signature` (what wrong looks like in words: "a 'Trust' in the issuer name,"
  "PEND counted in a current KPI," "AUM nonzero past delist").
- **Three consumers, no copies:** the AI resolver injects `intent` + `failure_signature`
  into its prompt; the preflight gate asserts observed-vs-`expected`; the report layer
  computes each headline number from **one** filter function derived from the contract;
  `REPORT_NUMBERS.md`/`DEFINITIONS.md` are *generated from* the contract, not kept beside
  it.
- **Provenance:** every resolved fact stamps where it came from (rung, confidence,
  citation) — extend the `logs/ai_resolve_*.jsonl` pattern into the DB.
- **Fail-loud at the source:** ingestion asserts freshness + completeness against the
  contract; no silent fallback. Reconciler *writers* retire on a dated proof, keeping
  only the read-only probes (honoring GOAL.md #4).
- **Storage/rendering fixes fall out for free:** the re-stamp becomes a contract
  assertion; the three filters collapse because the contract defines the one filter.
- **Durability:** add the tested restore drill + offsite leg + journal backup as the
  contract for recovery.

- **Pro:** Makes "self-correcting" actually true. Each future bug class is closed by a
  contract entry, not a patch. It's the four real-team patterns at solo weight.
- **Con:** Real up-front work; touches resolver, gate, reports, docs. Must be sequenced
  so it doesn't collide with ADR 0013 go-live.

### Posture 3 — Re-platform (heavy, ~3–4 months)
Move off single-file SQLite to managed Postgres with replication + PITR, formal schema
migrations, an orchestrator (Dagster/Airflow) with native observability, and an object
store for 13F.

- **Pro:** Solves durability and observability structurally; this is where a funded data
  team would land.
- **Con:** Months of migration for a one-operator shop; most of ADR 0013's value
  (gates, heartbeat, cascade) would be rebuilt on a new substrate. Over-engineered for
  the current scale. **Wrong altitude today** — revisit only if rexfinhub grows past one
  operator or the data outgrows SQLite.

---

## Recommendation (from the whole-plan perspective): **Posture 2**, sequenced

Posture 1 is a trap — it's the very pattern that produced today's state, and it leaves
the root untouched. Posture 3 is the right answer for a bigger org and the wrong answer
for you now. **Posture 2 is the only one that treats the five issues as the single
problem they are**, and it embodies exactly how real teams solve this without the
platform tax.

Sequence it so nothing collides and the cheap-but-catastrophic gaps move first:

1. **Ship ADR 0013 go-live first** (`bash scripts/golive.sh`). It's built, merged,
   tested. Get the gates + heartbeat + cascade running on live data — they're the
   observability substrate the contract spine plugs into.
2. **Pull two durability fixes forward immediately** (they're cheap and the downside is
   catastrophic, independent of everything else): make the **stale-Bloomberg fallback
   fail loud**, and write + run a **tested restore drill**. Days, not weeks.
3. **Build the contract layer** (ADR 0014): the artifact + the three wirings (resolver
   prompt, preflight expected-values, one report-filter function) + doc generation.
   This closes issues 2 and 4 and the open half of "self-correcting."
4. **Provenance + fail-loud ingestion**: stamp resolution provenance into the DB;
   convert silent ingestion fallbacks to contract-asserted fail-loud; retire reconciler
   writers on a dated probe-zero proof. Closes issue 1 and the reconciler-redundancy
   tension.
5. **Storage/rendering cleanups fall out of the contract**: time-series re-stamp as a
   contract assertion; collapse the three filters; quarantine legacy columns on a
   3-gate proof-of-death. Closes issues 3 and 4's remainder.
6. **Durability spine**: offsite backup leg + journal backup + 13F recovery. Closes
   issue 5 fully.

Each step is independently shippable and leaves the system better, but they share one
spine — so we never again fix one layer in a way the others don't know about.

---

## What I need from you

1. **Pick the posture** (recommend **2**). If 2, I'll formalize this as **ADR 0014** with
   the contract schema and the three wiring points specified concretely, for your review
   before any code.
2. **Confirm the sequence** — specifically that **go-live (ADR 0013) ships before** the
   contract work, and that the two durability fixes (stale-Bloomberg fail-loud + restore
   drill) are allowed to jump the queue.
3. **One scope call:** do you want the contract to cover **every** report number on day
   one, or start with the L&I/income/suite headline numbers (the ones that have burned
   you) and expand? I'd start narrow and expand — a full-coverage contract on day one is
   its own over-build.

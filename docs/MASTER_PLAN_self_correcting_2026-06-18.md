# Master Plan — the self-correcting rexfinhub (2026-06-18)

> The single plan that encompasses everything we discussed: take the already-built
> autonomous system live, write the rules down where the computer can read them, wire
> those rules into the three places that matter, make launches explain themselves, and
> close the silent-failure and backup gaps. Written to be read top-to-bottom. Companion
> docs: `docs/DATA_LIFECYCLE_REVIEW_2026-06-18.md` (the why, with evidence),
> `config/contracts/report_numbers.draft.yaml` (the rules, already approved),
> `docs/DECISIONS/0013-self-correcting-rexfinhub.md` (the built system).

---

## The goal in one line

Every number in every report follows a rule the computer can read, so the system labels
funds right, refuses to ship a number that breaks its rule, and explains a number that
changes — instead of you catching it by hand.

---

## Where we already are (so we build on it, not over it)

- **The autonomous system is built and merged** (ADR 0013 / PR #89): self-healing
  classification cascade, gates that block bad data, a daily heartbeat, one message per
  refresh, and the second write-path that caused the "Tidal" drift is deleted. It just
  needs its first live run on the VPS.
- **One write path** for market data (`sync_market_data`), confirmed after PR #89.
- **One suite function** (`attach_rex_suite`) every report already calls.
- **A sound nightly backup** (atomic, integrity-checked, alerts on failure).
- **The primary AI classifier already carries intent** in its `TAXONOMY` prompt
  (`ai_classify_unmapped.py`) — it already knows "bonds are not income." The problem is
  that intent is trapped in a Python string and is *missing* from the cascade's generic
  resolver, and it's a third copy alongside the docs.

So this plan is mostly **connecting and hardening what exists**, plus a few genuinely new
pieces (the rules loader, the launch-delta, the durability leg).

---

## The core idea (one rules file, three readers)

One file — `config/contracts/report_numbers.yaml` (drafted, numbers approved) — holds, for
each category and each headline number: what it means, the filter, what it should equal,
and what "wrong" looks like. Three parts of the system read that one file:

1. **The AI classifier** reads the *meaning* → labels new funds correctly the first time.
2. **The safety check** reads the *expected value* → refuses to ship a number that broke
   its rule and can't be explained.
3. **The reports** read the *filter* → every number is computed one way, not three.

Today those three each do their own thing. The whole point is to make them read the same
source. The file is structured data (not prose), so the code reads it directly; the human
docs (`REPORT_NUMBERS.md`) get generated *from* it so they can never drift again.

---

## The work, in phases

Each phase is independently shippable and leaves the system better. They share one spine,
so we never fix one layer in a way the others don't know about.

### Phase 0 — Go live (the system that's already built)
**What:** Run the one VPS command to take ADR 0013 live: `cd /home/jarvis/rexfinhub &&
git pull --ff-only origin main && bash scripts/golive.sh`. It syncs, smoke-tests, runs one
chain cycle **with the send gate closed**, and only enables the heartbeat timer if the
preflight gate is green.
**Why first:** The gates + heartbeat are the observability floor everything else plugs
into. No reason to wait — it's built, merged, tested.
**Verify:** golive exits 0 (green) and `systemctl list-timers` shows the heartbeat enabled;
or it exits 2 and prints exactly which check is red, which we then fix.

### Phase 1 — Two cheap fixes whose downside is catastrophic
**What:** (a) Make the **stale-Bloomberg fallback fail loud** — `market/config.py` already
says "no stale fallback," but the silent fallback is still there and caused the 06-16 bug;
turn it into a hard stop + one alert. (b) Write and **actually run a restore drill** —
`scripts/restore_drill.py` restores last night's backup to a scratch file, runs
`PRAGMA integrity_check` + a row-count sanity check, never touches the live DB.
**Why now:** Both are days of work, independent of everything else, and the thing they
prevent (silent stale data; an untested-and-therefore-fake backup) is the worst-case.
**Verify:** Force a stale file → pipeline stops with a clear alert instead of shipping.
Run the drill → it restores and passes integrity, on demand.

### Phase 2 — The rules file + its loader
**What:** Promote the approved draft to `config/contracts/report_numbers.yaml` and add a
small loader `market/contracts.py` (mirrors the existing `market/rules.py` and the YAML
pattern already used for `ipo_watchlist.yaml`; `pyyaml` is already a dependency). The file
holds structured fields per number — `category`, `status`, `single_stock`, `is_rex`,
`dedup` — not a string to parse.
**Why:** Nothing else can read the rules until they're loadable.
**Verify:** A unit test loads the file and asserts the five expected numbers and the two
category definitions are present and well-formed.

### Phase 3 — Wire the rules into the AI classifier (one source of meaning)
**What:** Replace the hardcoded `TAXONOMY` string in `ai_classify_unmapped.py:35` and the
generic prompt in `claude_service.resolve_fact()` so **both** read the category `means` /
`is_not` / `looks_wrong_if` from the rules file. The good intent that already exists for
the batch classifier now also reaches the cascade, from one place.
**Why:** This is the gap that makes "self-correcting" real — the AI corrects toward your
stated meaning, not a bare label, everywhere.
**Verify:** Feed a known bond-income fund through the classifier → it returns "not CC"
with the reason citing the options-vs-bonds rule. A test pins this.

### Phase 4 — Wire the rules into the reports (one filter, not three)
**What:** Add `apply_report_rule(master, rule_id)` in `report_data.py` that builds the
mask + dedup from the rules file, and refactor `get_li_report` (line ~1317), `get_cc_report`
(~1642), and `get_flow_report` (~2274) to call it. This **fixes the dedup inconsistency**
(LI currently doesn't dedup by ticker; CC and flow do) so the same number can't diverge by
construction.
**Why:** Kills the "same number computed three ways" risk — the GOAL.md problem, in
miniature, still alive today.
**Verify:** The five headline numbers match the contract's expected values on current
data, and a planted duplicate-category row no longer changes the LI count.
**→ This is the phase that changes report numbers, so this is where I rebuild all 10
reports and put the previews in your Chrome for you to check each one.**

### Phase 5 — Wire the rules into the safety check (expected vs observed)
**What:** In `preflight_check.py` (right after the facts dict is built, ~line 866), load the
contract and add an `expected_vs_observed` comparison per number. A mismatch the system
can't explain (see Phase 6) makes the gate red, using the existing
`.preflight_red` / `.preflight_result.json` machinery.
**Why:** The check stops being a vibe ("does 41 seem plausible?") and becomes a real test
("is it 41, or is the difference explained?").
**Verify:** Hand-break a number in a test DB → preflight goes red and names the number.

### Phase 6 — Make launches explain themselves (the 41→42 behavior you approved)
**What:** New `scripts/analyze_daily_delta.py` (~100 lines) diffs the existing
`mkt_daily_snapshot` table (today vs the prior snapshot) to find tickers that went
**pending→live** (launches) and **live→closed** (closures), and attributes each to its fund
name. `run_chain.notify()` appends a "launches & closures" line to the single message.
A count change that's fully explained by a launch/closure is **allowed**; an unexplained
one stays **red**. Per your call: **REX launches pause for your yes** before the new number
is blessed; **competitor launches auto-accept** (just reported).
**Why:** A launch is when numbers are *supposed* to move; the system should explain the
move, not alarm on it — and not make you confirm every competitor fund.
**Verify:** Simulate a pending→live REX fund → the message reads "41→42 because <fund>
launched — confirm" and the gate waits for the yes; simulate a competitor → it auto-accepts
and just reports.

### Phase 7 — Provenance + fix-at-source ingestion (close the silent gaps)
**What:** (a) Stamp **where each resolved fact came from** (rule / description / web /
human, with confidence) into the DB, extending the existing `logs/ai_resolve_*.jsonl`
pattern. (b) Convert the remaining silent ingestion fallbacks (dropped SEC entries, 13F
partial commits) to **fail-loud against the contract's freshness/completeness rules**.
(c) Retire the reconciler **writers** on a dated "probe reported zero misses for N days"
proof, keeping the read-only probes (honors GOAL.md #4 — no permanent band-aids).
**Why:** This is the "fix at the source instead of a downstream patch" principle you
pushed on, made real and measurable.
**Verify:** Each resolved fact shows its provenance; a forced ingest gap alerts instead of
silently backfilling; the reconciler writer is gone once its probe is zero.

### Phase 8 — Storage & rendering cleanups (fall out of the contract)
**What:** (a) **Re-stamp the time-series** category/issuer labels from master after the
enrichment post-steps, as a contract assertion (today it's baked at insert and drifts with
no guard). (b) **Quarantine the legacy columns** (`strategy`, `strategy_confidence`,
`underlier_type` — still written, no longer read) behind the 3-gate proof-of-death. (c)
Show a **data-age banner** on the website/admin so a stale cache can't silently disagree
with the live emails.
**Why:** These were separate bugs; once the contract exists they become one-line
assertions and small cleanups rather than bespoke fixes.
**Verify:** Time-series categories match master after a full run (assertion green); grep
proves nothing reads the legacy columns before they're dropped.

### Phase 9 — Durability spine (so recovery is real, not theoretical)
**What:** (a) Add an **offsite backup leg** (gzip the nightly snapshot → cheap object
store, 30-day retention). (b) **Back up the journals** (`logs/*.jsonl`, send log) so the
audit trail survives a restore. (c) **Recover 13F** (re-ingest the missing quarter; fix the
quarterly timer mode). (d) Repoint the D: sync to pull the *verified snapshot*, not a torn
hot-copy.
**Why:** The backup is sound but single-legged and the restore was never tested; this makes
a corruption event a 30-minute rehearsed restore instead of two hours of improvisation.
**Verify:** A monthly drill restores from the offsite copy and passes; 13F shows rows again.

---

## Sequencing & what you review, when

```
Phase 0  go live ........................ (now; one VPS command)
Phase 1  stale fail-loud + restore drill . (cheap, jumps the queue)
Phase 2  rules file + loader ............. (foundation)
Phase 3  AI classifier reads rules
Phase 4  reports read rules (ONE filter) . ← YOU REVIEW ALL 10 REPORTS IN CHROME HERE
Phase 5  safety check: expected vs observed
Phase 6  launches explain themselves
Phase 7  provenance + fix-at-source ingestion
Phase 8  storage/rendering cleanups
Phase 9  durability spine
```

Phases 0–6 are the heart (the self-correcting loop + your launch behavior). 7–9 harden the
edges. We stop at each phase boundary if you want to look. The **report-in-Chrome
checkpoint is Phase 4**, because that's the first phase that changes what the numbers say.

---

## What I need from you before I start executing

1. **Green-light Phase 0 go-live** — do you want me to run `golive.sh` on the VPS now, with
   you watching the output, or hold it until you've read this whole plan?
2. **Confirm the phase order** — specifically that report numbers don't change until
   Phase 4, where you review every report in Chrome before anything is sent.
3. Everything else (the rules wording, the five numbers, the launch behavior) you've
   already approved — no new decisions needed to begin.

---

## Honest risks & caveats

- **Two things to verify post-merge:** PR #89 deleted `market/derive.py`; the
  `category_display` derivation (and its exact strings like "Leverage & Inverse - Single
  Stock") must be re-confirmed in its new home before Phase 4 wires the filter to it. I'll
  check this first thing.
- **The rules work is real effort**, not a tweak — it touches the classifier, the gate, the
  reports, and the docs. The phasing keeps each step small and shippable, but this is weeks,
  not days.
- **Nothing sends without your explicit go.** The gate stays closed through every phase; the
  only outward action in the whole plan is Phase 0's chain cycle (no send) and, later, your
  own `/refreshdata send`.
- **We retire nothing old until the new thing is proven** (build-prove-retire): legacy
  columns, reconciler writers, and the stale CSVs only die behind a green proof.

# GOAL — what "finalized and tied up" actually means

_Written 2026-06-16. This is the north star for the fix-rex-family work. When we
disagree about what to do next, this file decides._

## The one sentence

**Every fact in the system has exactly one source of truth, every report and page
reads that source, and the source matches reality.**

That's it. "T-REX = 41" is not the goal. "T-REX = 41" is a *symptom test*: if the
system is built right, the T-REX count is computed in exactly one place, and it
comes out the same correct number on every report, every page, every time — without
anyone hand-fixing it.

## Why we keep going in circles (the real problem)

We have argued about 40 vs 41 vs 36 repeatedly. None of those arguments are the
problem. The problem is **the question "how many T-REX funds are there?" has more
than one answer in the codebase right now:**

- The Flow report counts funds where a stored `rex_suite` column == 'T-REX'.
- The T-REX report counts funds whose `fund_name` starts with "T-REX".
- A stale hand-maintained CSV (`rex_suite_mapping.csv`) defines it a third way.
- My dev database is a stale copy, so it disagrees with production too.

Four definitions → four numbers. Every time I "verify," I sample one of them and
report it, and it's wrong or it contradicts the last one. **That is the bug.** Not
the digit.

So the fix is never "make it say 41." The fix is "delete three of the four
definitions, keep one, make everything read it, and make that one match the real
REX lineup."

## What done looks like (the tests, not the vibes)

1. **One definition per fact.** "What suite is this fund in," "is this single-stock,"
   "what brand issues it," "what's its strategy" — each is computed in exactly one
   function/source. Grep proves there is no second place doing it differently.
2. **Every surface agrees.** The T-REX count (whatever it truly is) is identical on
   the Flow report, the T-REX report, the L&I report, and the website — because they
   all call the same source. Changing the source changes all of them at once.
3. **It matches reality.** The authoritative count equals the actual REX product
   lineup as REX/Ryu defines it — not an artifact of a regex or a stale CSV. A fund
   launches tomorrow and classifies correctly with zero manual edits.
4. **No band-aids.** No per-report "if microsectors then exclude" patches, no
   "Other" bucket catching unclassified funds, no QC check whose job is to catch a
   tangle that shouldn't exist. If a check exists, it guards the one source — it
   doesn't paper over five sources.
5. **All 10 reports correct, every number traceable.** Any number in any report can
   be traced back to its single source (the lineage map is for this).
6. **The website reflects the same data and stays up.**

## The number is an output, not an input

I will stop hunting for "the right number." Instead:

- Fix the system so the count is computed once, from the fund name (self-maintaining)
  — DONE in code: the definition library `market/definitions.py` is the single
  classifier (see `docs/DEFINITIONS.md`); the Flow report, `apply_fund_master`, and
  `data_engine` all call it; the stale CSV is demoted to fallback-only.
- Run that one system against **authoritative data** (production / a fresh sync), not
  my stale dev copy.
- Whatever number it produces **is** the answer, and it will be the same everywhere.

If that number is not what Ryu expects, then the *definition* is wrong (e.g. we're
excluding T-REX 2X Inverse, or counting a filed-but-not-launched fund), and we fix
the definition in one place — we do not patch a report.

## The one thing I need from Ryu (not blocking, but it pins reality)

What is the authoritative definition of "the T-REX lineup"?

- Is it every ACTV fund named "T-REX ..." (long + inverse)? — this is what the code
  now uses.
- Does it include anything NOT named "T-REX" (e.g. SPAX, or a T-Bill product)?
- Does it include filed-but-not-yet-launched products, or ACTV only?

Once that definition is fixed, the system encodes it once and the number stops
moving. My current best read is "ACTV + name starts with T-REX," which is
self-maintaining and matches the T-REX report — but Ryu's "41" vs the dev DB's "40"
suggests either one fund is missing from my stale copy, or the definition includes
one fund the name rule doesn't catch. That gap is a *data freshness / definition*
question, resolved against production — not another code guess.

## Scope this governs (the 5-item finalization list)

1. MicroSectors L&I Industry Report — in repo, wired, branded. _(done)_
2. **Classification tied to one source** — this document's core. _(code done; verify on real data)_
3. 3rd chart = true market share (%, 0–100, sums to 100/month, REX at bottom).
4. T-REX report effective date restored.
5. Website OOM — plan bump vs code-side memory fix.

Items 3–5 are downstream of the same principle: one source, read everywhere, correct.

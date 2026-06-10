---
doc: index
status: canonical
updated: 2026-06-10
---

# rexfinhub — Documentation Index

> Pure navigation. If you're new here (human or Claude session): read
> **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — the master file — then come back only
> when you need something specific.

## The folder, in one breath

```
docs/
├── ARCHITECTURE.md    ← THE MASTER FILE. Start here, always.
├── SYSTEM.md            what production does today (AS-IS detail)
├── TARGET.md            what we're building toward (TO-BE)
├── RUNBOOK.md           how Ryu operates day-to-day (gates, sends, incidents)
├── CLASSIFICATION.md    the full-scale taxonomy contract + autonomous engine
├── GLOSSARY.md          every domain term ([[term]] links resolve here)
├── LOG.md               append-only changelog
├── INDEX.md             this map
├── DECISIONS/           immutable ADRs — why things are the way they are
├── audits/              dated audit evidence (read-only after the fact)
│   ├── 2026-05-11-rebuild/      the May rebuild audit
│   ├── 2026-05-12/  rex_ops_2026-05-12/  2026-05-13 baseline
│   ├── 2026-06-09-engine/       the engine session (MASTER_AUDIT, ENGINE_PLAN…)
│   └── 2026-06-10-labels/       report label-accuracy audit
├── archive/             superseded one-off artifacts (never edited)
├── raw/                 preserved legacy docs (read-only)
├── issuer_review_queue.csv   ← live runtime queue (admin UI reads it here)
└── ticker_review_queue.csv   ← live runtime queue
```

Everything else that used to clutter this folder (underlier audit CSVs, residue
files, daily conflict CSVs, one-off handoffs) was archived or repointed to
`data/` in the 2026-06-10 cleanup. If a script writes a runtime artifact into
docs/, that's a bug — docs/ is documentation plus the two live queues above.

## Load order per task

| Task | Read |
|---|---|
| Anything substantive | `ARCHITECTURE.md` first, every time |
| Operate / debug live | → `RUNBOOK.md` → `SYSTEM.md` |
| Touch classification/rules | → `CLASSIFICATION.md` |
| Plan a build phase | → `TARGET.md` + `DECISIONS/0011-engine-architecture.md` |
| Understand a past decision | → `DECISIONS/` |
| Verify a claim about the system | → `audits/` (dated evidence) |
| Unfamiliar term | → `GLOSSARY.md` |

### lifecycle-legend
canonical = current truth · draft = in progress · proposed = not yet adopted ·
deprecated = superseded · archive = read-only history

### known-gaps
- GAP-01: future ADRs as decisions land (0011 awaiting status flip to accepted).
- GAP-02: CI hook enforcing "TARGET edits require SYSTEM edit when phase ships" — not built.
- GAP-03: pre-commit `[[term]]` → glossary cross-check — not built.
- GAP-04: the two live queue CSVs belong in data/ with an admin-UI repoint (next cleanup slice).

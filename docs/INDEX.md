---
doc: index
status: canonical
updated: 2026-05-19
---

# rexfinhub — Documentation Index

> Pure navigation. No domain facts. If you're a Claude session loaded into this repo, **read this first**, then `GLOSSARY.md`, then route to the right doc per `### load-order` below.

### map

| File | What it is | When to read it |
|---|---|---|
| [`INDEX.md`](INDEX.md) | This file. The map. | First — every session. |
| [`SYSTEM.md`](SYSTEM.md) | Canonical AS-IS — what production does today | Operating, debugging, explaining current state |
| [`TARGET.md`](TARGET.md) | Canonical TO-BE — what we're building toward | Planning, designing, justifying the rebuild |
| [`RUNBOOK.md`](RUNBOOK.md) | Ryu's daily operating manual | Daily ops, incident response, "how do I X" |
| [`GLOSSARY.md`](GLOSSARY.md) | Single source for every domain term | Whenever an unfamiliar term appears |
| [`DECISIONS/`](DECISIONS/) | Immutable ADRs — why a thing is the way it is | "Why is this designed this way?" |
| [`LOG.md`](LOG.md) | Append-only changelog | Reconstructing what changed and when |
| [`raw/`](raw/) | Preserved audits + reports (read-only) | Historical reference; never edited |

### load-order

Per common task — load in the order shown:

| Task | Load order |
|---|---|
| Operate / debug live system | `INDEX.md` → `SYSTEM.md` → `GLOSSARY.md` |
| Plan or build a phase | `INDEX.md` → `TARGET.md` → `SYSTEM.md` (for current state) → `GLOSSARY.md` |
| Daily ops / "how do I X" | `INDEX.md` → `RUNBOOK.md` → `GLOSSARY.md` |
| Understand a past decision | `INDEX.md` → `DECISIONS/NNNN-*.md` |
| Explain to LLM-on-the-site | `INDEX.md` → `SYSTEM.md` + `GLOSSARY.md` (atomic chunks by `###`) |
| Reconstruct history | `INDEX.md` → `LOG.md` → relevant `DECISIONS/` |

### query-recipes

Terminal patterns (always run from repo root):

```bash
# List every stable anchor in a doc
rg "^### " docs/SYSTEM.md
rg "^### " docs/TARGET.md

# Find every doc referencing a glossary term
rg -l "\[\[bloomberg-pull\]\]" docs/

# Find dead terms in glossary
rg "status: deprecated" docs/GLOSSARY.md -A 3

# Find every ADR
ls docs/DECISIONS/

# Find every TODO/GAP (per-doc known-gaps sections; no inline TODOs)
rg "^- GAP-" docs/
```

### lifecycle-legend

| Status | Meaning |
|---|---|
| canonical | Current truth. Use this. |
| draft | In progress; subject to change. |
| proposed | Not yet adopted. Don't rely on. |
| deprecated | Superseded; see the linked successor. |
| archive | Historical reference. Read-only. |

### known-gaps

- GAP-01: ADR `0001-docs-framework.md` documents this layout; future ADRs as decisions land.
- GAP-02: CI hook to enforce "TARGET edits require SYSTEM edit when phase ships" — not yet built.
- GAP-03: Pre-commit hook to enforce `[[term]]` → glossary cross-check — not yet built.

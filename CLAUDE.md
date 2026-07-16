# rexfinhub — project instructions (pointers only; facts live in `docs/`)

**Every session: read `docs/INDEX.md` → `docs/GOAL.md` → `docs/DEFINITIONS.md` before anything substantive.** Resolve unfamiliar terms via `docs/GLOSSARY.md`. Canonical load order: `INDEX` → `ARCHITECTURE` → `SYSTEM_LEDGER` → `GOAL` → `DEFINITIONS` → `GLOSSARY` → `RUNBOOK` → `LOG`.

**Single source of truth for fund classification** (suites, market-status KPIs, trusts): `docs/DEFINITIONS.md` + `market/definitions.py`. Never re-derive elsewhere.

| If you're doing… | Load in this order |
|---|---|
| Classifying funds (suites/status/trusts) | `docs/DEFINITIONS.md` + `market/definitions.py` |
| Operating / debugging live | `docs/SYSTEM_LEDGER.md` → `docs/SYSTEM.md` → `docs/GLOSSARY.md` |
| Planning / building | `docs/GOAL.md` → `docs/TARGET.md` → `docs/SYSTEM.md` |
| Daily ops / "how do I X" | `docs/RUNBOOK.md` |
| Why the design is this way | `docs/DECISIONS/NNNN-*.md` · history: `docs/LOG.md` |

- Production/deploy facts, drive layout, worktree conventions: `docs/SYSTEM.md` (present tense) — never restate them here; they drift.
- `docs/SYSTEM.md` = today · `docs/TARGET.md` = future · `docs/RUNBOOK.md` = how Ryu operates. Never mix. No inline TODOs — use each doc's `### known-gaps`.
- Sub-domains (2026-07-14 monorepo merge): `asia/` · `structured-notes/` — each keeps its own docs.
- System constitution: `C:/Foundry/Library/ATLAS.md` · this domain's loops: `loops.md`

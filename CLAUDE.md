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
- Sub-domains: `structured-notes/` — keeps its own docs.
- **Asia is NOT in this repo.** The live pipeline is `C:/Foundry/rex-asia/` (peer project — it
  bundles a PostgreSQL install + live cluster, 934 MB, which does not belong in source control).
  `asia/` here is a frozen 2026-07-14 snapshot, missing `build_month.py` and `asia_preflight.py`,
  and cannot run a build. See `asia/README_MOVED.md`.
- System constitution: `C:/Foundry/Library/ATLAS.md` · this domain's loops: `loops.md`

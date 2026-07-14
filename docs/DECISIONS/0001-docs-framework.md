---
adr: 0001
title: Adopt six-doc framework for rexfinhub documentation
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0001 — Six-Doc Framework for rexfinhub Documentation

## Context

rexfinhub has accumulated ~80 markdown files in `docs/` over the past year, mixing audit reports, post-mortems, design plans, runbooks, and ad-hoc analysis. The most recent attempt at a unified reference (`REXFINHUB_ARCHITECTURE.md` v3, ~8,000 words) packs five concerns into a single doc: current-state inventory, target-state design, daily operating manual, secrets audit, and phased roadmap.

Three observed problems:

1. **Context bloat per Claude session.** Loading the mega-doc consumes ~12K tokens before any code is read; agents have less room for the actual task.
2. **Mixed update cadence.** AS-IS facts change every PR; TO-BE design changes only on phase transitions; daily-ops procedures change rarely. One file mixing these guarantees stale content somewhere.
3. **Poor chunk retrieval.** A future site-embedded LLM doing RAG against the docs needs uniform chunk topics. Mega-doc sections cover heterogeneous concerns and chunk poorly.

Ryu explicitly named the goal: "Every time I use Claude it must always point to the correct system architecture and be able to query appropriately. This is especially important if I want to utilize agents appropriately. I want to be able to analyze every aspect directly from a terminal instance and even potentially through an LLM directly added into the site."

## Decision

Adopt the following layout under `docs/`:

```
INDEX.md       — navigation only; loaded by every Claude session
SYSTEM.md      — canonical AS-IS (present tense)
TARGET.md      — canonical TO-BE (versioned)
RUNBOOK.md     — daily operating manual
GLOSSARY.md    — single source for every domain term
DECISIONS/     — ADRs (this file is 0001), sequentially numbered, immutable once accepted
LOG.md         — append-only changelog, yearly rotation
raw/           — preserved audits + reports (immutable historical reference)
```

Conventions:

- Cross-doc links use relative path + section anchor: `[see SYSTEM.md#workflows](../SYSTEM.md#workflows)`.
- Glossary cross-references use `[[term-name]]` syntax; a future pre-commit hook will verify every `[[term]]` exists in `GLOSSARY.md`.
- Stable section anchors per canonical doc are fixed in the framework (see `raw/REXFINHUB_DOCS_FRAMEWORK_v1_2026-05-19.md` §2).
- Frontmatter on every doc carries `doc:`, `status:`, `updated:` (and `version:` for TARGET).
- Versioning by frontmatter only — filenames never carry version numbers (avoids orphaned references).
- Anti-patterns codified: no future-tense in SYSTEM, no architecture in RUNBOOK, no inline TODOs (use per-doc `### known-gaps` section), no `####` for retrievable concepts (breaks chunk uniformity).

Discovery mechanism:
1. `C:\Foundry\Rexfinhub\CLAUDE.md` carries a load-order block: "Read `docs/INDEX.md` first. Resolve unfamiliar terms via `docs/GLOSSARY.md`."
2. User-scope `MEMORY.md` gets one pointer line to `docs/INDEX.md`.
3. `INDEX.md` has a `### load-order` table mapping task type → doc read sequence.

## Consequences

**Positive**:
- Each doc fits in a single Claude context window alongside code.
- AS-IS and TO-BE update independently with clear ownership.
- ADRs preserve decision rationale forever, supersession explicit.
- Glossary kills the `rex_product` vs `capm_product` ambiguity at the documentation layer before it propagates.
- Site-LLM RAG chunks predictably by `###` heading.
- Existing 80-file ad-hoc docs in `docs/` are NOT touched — they remain as historical reference and will be absorbed into `raw/` as a Phase 1 cleanup.

**Negative**:
- One-time migration cost: reshape v3 content into six docs.
- Discipline cost: contributors must update both SYSTEM and TARGET when a phase ships. A CI hook (planned, not yet built) will enforce.
- New convention to learn: `[[term]]` syntax, frontmatter, section-anchor stability.

## Alternatives considered

- **Keep one mega-doc**: rejected. Context bloat, mixed update cadence, poor chunk retrieval.
- **One doc per workflow**: rejected. Would produce ~25 small files with no clear navigation; agents wouldn't know where to start.
- **External wiki tool (Notion, Confluence, Obsidian)**: rejected. Adds a tool dependency; markdown-on-disk works in terminal, IDE, GitHub, and embedded LLM equally well.
- **Karpathy LLM-wiki pattern verbatim** (3-layer raw/wiki/index): adopted in spirit (we have `raw/` + canonical docs + `INDEX.md`) but extended with ADRs and a glossary because rexfinhub has more decision history and term-naming ambiguity than Karpathy's personal-wiki use case.

## References

- `raw/REXFINHUB_DOCS_FRAMEWORK_v1_2026-05-19.md` — the design proposal that produced this ADR
- Karpathy's LLM Wiki gist: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- ADR pattern source: Michael Nygard's "Documenting Architecture Decisions" (2011)

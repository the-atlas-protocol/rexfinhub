# REXFINHUB Documentation Framework

> Meta-doc. Locks structure only. Content gets filled in later from `REXFINHUB_ARCHITECTURE.md` v3 + the three audit reports + `docs/audit_2026-05-11/*`.
>
> **Status**: proposal v1 — 2026-05-19
> **Owner**: Ryu El-Asmar
> **Optimized for**: Claude Code session boot, terminal grep, future on-site LLM RAG retrieval.

---

## 1. Recommendation — Six docs, one index, one glossary

The current ~8,000-word `REXFINHUB_ARCHITECTURE.md` v3 mixes five different concerns. Splitting along **temporal stance** (now / target / forever) + **audience-task** (operate / build / understand) gives six focused docs plus an index and a glossary. Each is < ~1,500 words so any one fits in a single Claude session even alongside source code.

Justification: a single mega-doc fails three of Ryu's stated goals — it cannot be loaded selectively per task (context bloat), it cannot be diff-reviewed cleanly, and chunk-retrieval for an on-site LLM degrades when section topics are heterogeneous. Conversely > 8 docs starts to drift; this is the smallest split where each doc has a single, defensible reason to exist.

The layout borrows the 3-layer pattern from Karpathy's LLM Wiki (raw → wiki → index) and the CONTEXT.md + ADR pattern from the `grill-with-docs` skill: stable canonical pages, immutable decision records, append-only log, raw audits preserved.

```
C:\Projects\rexfinhub\docs\
  INDEX.md                  <- map; loaded by every Claude session
  SYSTEM.md                 <- canonical "what exists now" (the as-is)
  TARGET.md                 <- canonical "where we're going" (the to-be spec)
  RUNBOOK.md                <- daily operating manual (Ryu-facing)
  GLOSSARY.md               <- one source for every domain term
  DECISIONS/                <- ADR-style immutable records (NNNN-slug.md)
  LOG.md                    <- append-only chronological changelog
  raw/                      <- preserved audits + reports (never edited)
    audit_2026-05-11/...
    REXFINHUB_ARCHITECTURE_v3.md   (the current 8k-word doc moves here)
```

---

## 2. Per-doc specification

### `INDEX.md` — the map

- **Audience**: every Claude session, every agent, every site-LLM chunker.
- **Lifecycle**: canonical. Always current.
- **Update cadence**: every PR that adds/moves a doc.
- **Frontmatter**:
  ```yaml
  ---
  doc: index
  status: canonical
  updated: 2026-05-19
  ---
  ```
- **Stable anchors**: `### map`, `### load-order`, `### query-recipes`, `### lifecycle-legend`.
- **Required content shape**: one-line summary per doc + a "if you only have time for one, read X for task Y" routing table.
- **Hard rule**: never contains domain facts. Pure navigation.

### `SYSTEM.md` — canonical as-is

- **Audience**: Claude session answering "what does the system do today?"
- **Lifecycle**: canonical. Reflects production reality on the VPS at the date in frontmatter.
- **Update cadence**: every PR that changes production behavior, deploys a workflow, or rotates a secret.
- **Stable anchors** (these IDs do not change as content evolves):
  - `### topology` — boxes-and-arrows + VPS / Render / SharePoint layout
  - `### data-sources` — SEC, Bloomberg, CBOE, M365, OpenFIGI, 13F
  - `### workflows` — every systemd timer + script, one row each, linked to source path
  - `### databases` — SQLite tables, where they live, who writes
  - `### webapp-surfaces` — every route group + its data dependencies
  - `### secrets-inventory` — every key, location, rotation status (no values)
  - `### known-bugs` — current bug list with severity + workaround
  - `### known-gaps` — explicit "we do not know X" list
- **Anti-pattern**: never contains "this should be" sentences. Future tense belongs in `TARGET.md`.

### `TARGET.md` — canonical to-be

- **Audience**: Claude session helping plan / build the rebuild; future site-LLM answering "what is the intended design?"
- **Lifecycle**: canonical for the active version. Carries an explicit version label.
- **Versioning**: filename stays `TARGET.md`; the doc itself carries `version: v4` in frontmatter and the previous version moves to `raw/TARGET_v3_2026-05-19.md` when superseded. Rolling target with frozen snapshots — gives you both diff-friendliness and immutable historical reference.
- **Update cadence**: on phase boundaries or after a `DECISIONS/` ADR is merged that changes the spec.
- **Stable anchors**:
  - `### principles` — design invariants
  - `### data-model` — canonical product ID, polymorphic underlier, bi-temporal status
  - `### classification` — override table model
  - `### survivorship` — source precedence rules
  - `### ops-as-assertions` — daily quality checks
  - `### phases` — phase 0…N with shipping criteria
  - `### cuts` — what is being removed and why
- **Sync rule**: when a phase ships, move its content from `### phases` into `SYSTEM.md` and strike it through here (preserving the heading for backlinks).

### `RUNBOOK.md` — operating manual

- **Audience**: Ryu, day-to-day. Also Claude when asked "how do I run X?"
- **Lifecycle**: canonical.
- **Update cadence**: whenever a manual touchpoint changes (e.g., CBOE cookie URL changes).
- **Stable anchors**:
  - `### ideal-day` — three-touchpoint workflow
  - `### touchpoint-cboe-cookie`
  - `### touchpoint-pipeline-inception-date`
  - `### touchpoint-morning-triage`
  - `### red-button-procedures` — gate close, kill switch, rollback
  - `### oncall-checks` — what to look at if something feels off
- **Anti-pattern**: no architecture explanation here. Link to `SYSTEM.md` anchors.

### `GLOSSARY.md` — single source of canonical names

- **Audience**: every consumer. Resolves ambiguity.
- **Lifecycle**: canonical, append-only in practice (terms rarely deprecate; when they do, mark `status: deprecated` and link successor).
- **Stable anchors**: one `### term-name` per term. Lowercase-kebab. Example: `### rex-product`, `### is-rex-flag`, `### etp-category`, `### canonical-product-id`, `### survivorship`.
- **Entry shape** (rigid — so chunking is uniform):
  ```
  ### rex-product
  **Definition**: ...
  **Where it lives**: db.table, code.path
  **Synonyms**: ...
  **Not to be confused with**: [[capm-product]]
  **Status**: canonical | deprecated | proposed
  ```
- **Rule**: any term used in any other doc with semantic weight MUST have an entry here. Linters can enforce this later.

### `DECISIONS/NNNN-slug.md` — ADRs

- **Audience**: future Claude wondering "why is it this way?"
- **Lifecycle**: immutable once `status: accepted`. Supersede with a new file linking back.
- **Update cadence**: on-demand, one per real decision.
- **Required shape**: context / decision / consequences / alternatives-considered.
- **Numbering**: zero-padded sequential, e.g. `0007-merge-rex-and-capm-products.md`.

### `LOG.md` — append-only changelog

- **Audience**: Claude reconstructing "what changed and when?"
- **Lifecycle**: append-only. Rotate yearly (`LOG_2026.md`).
- **Entry shape**: `## 2026-05-19` headers, bullet entries, link to `DECISIONS/` or PR.

---

## 3. Cross-reference convention

- **Within a doc**: use the literal heading slug — `[topology](#topology)`.
- **Across docs**: relative markdown link with anchor — `[see SYSTEM.md#workflows](SYSTEM.md#workflows)`. Section anchors are the contract; doc filenames are stable but section IDs are stabler still.
- **To a glossary term**: wiki-style double bracket — `[[rex-product]]`. A pre-commit hook (later) can verify the term exists in `GLOSSARY.md`.
- **To raw material**: `raw/audit_2026-05-11/01_bloomberg_ingestion.md` — always relative path, never absolute. Raw docs are read-only references.

---

## 4. Indexing pattern — how Claude finds these at session start

Three reinforcing layers, in priority order:

1. **`C:\Projects\rexfinhub\CLAUDE.md`** gets a single load-order block at the top: "Read `docs/INDEX.md` first. Resolve any term you do not recognize via `docs/GLOSSARY.md`." This is the project-scoped instruction every Claude session auto-loads.
2. **User-scope `MEMORY.md`** gets one new line under `## Rexfinhub topic files` pointing to `docs/INDEX.md` and noting the framework version. No duplication of content into memory.
3. **`docs/INDEX.md` itself** carries a `### load-order` section listing the read sequence per common task type (operate vs build vs debug vs explain-to-LLM-on-site).

A site-embedded LLM uses `INDEX.md` as its sitemap and chunks the other docs by `##` and `###` headings.

---

## 5. Query patterns

### Terminal (Ryu)

- `rg "^### " docs/SYSTEM.md` — list every stable anchor.
- `rg -l "\[\[bloomberg-pull\]\]" docs/` — find every doc referencing a glossary term.
- `rg "status: deprecated" docs/GLOSSARY.md -A 3` — find dead terms.
- Convention: **every section uses exactly one `### ` heading per topic**; no `####` for retrievable concepts (sub-bullets only). This keeps chunk size predictable.

### Site-LLM RAG

- Chunk boundary = `###` heading.
- Chunk metadata = frontmatter + parent doc + section anchor.
- Glossary entries are atomic chunks (small, self-contained, ideal for retrieval).
- Decision records chunk as whole documents (immutable; coherent unit).

---

## 6. Known-gaps pattern

Per-doc `### known-gaps` section, bulleted, each item prefixed `GAP-NN:`. Numbering local to the doc. When a gap is closed, the bullet stays in place but gets `[CLOSED 2026-MM-DD → DECISIONS/NNNN-slug.md]` appended — preserves the audit trail without rewriting history. Inline TODOs in narrative paragraphs are banned (invisible to grep across doc surfaces).

---

## 7. Glossary as single source

`GLOSSARY.md` is the only place where a term gets a normative definition. Every other doc must either use the term as defined or link `[[term]]` if any ambiguity could exist. This kills the `rex_product` vs `capm_product` confusion at the documentation layer before it propagates further.

---

## 8. Versioning

- `SYSTEM.md`, `RUNBOOK.md`, `GLOSSARY.md`, `INDEX.md` — **no version numbers**. They reflect present truth. Frontmatter `updated:` is the only timestamp.
- `TARGET.md` — **explicit `version: vN` in frontmatter**, snapshot to `raw/` on supersession.
- `DECISIONS/` — sequential numbering; supersession explicit.
- `LOG.md` — yearly rotation.

Sync rule (the only one that matters): **when a phase from `TARGET.md` ships, the same PR must update `SYSTEM.md`**. CI can enforce by failing if `TARGET.md#phases` is edited without a corresponding `SYSTEM.md` edit.

---

## 9. Anti-patterns (explicit "do not" rules)

- **Do not** put future-tense design statements in `SYSTEM.md`. They belong in `TARGET.md` or an ADR.
- **Do not** put architecture explanation in `RUNBOOK.md`. Link to anchors.
- **Do not** redefine a glossary term inline. Link `[[term]]`.
- **Do not** edit anything under `raw/`. It is the immutable audit trail.
- **Do not** create new top-level docs outside this layout without an ADR.
- **Do not** use `####` for retrievable concepts — breaks chunk uniformity.
- **Do not** duplicate content across docs. If two places need the same fact, one is canonical and the other links to it.
- **Do not** store secret values in any doc, including `SYSTEM.md#secrets-inventory`. Locations and rotation status only.
- **Do not** keep "current bugs" in a separate file. They live in `SYSTEM.md#known-bugs` until fixed; fixed bugs disappear (history is in git + `LOG.md`).
- **Do not** version the live docs in their filenames. Versioning by filename produces orphaned references.

---

## 10. Loadability test (the design check)

For each of the six canonical docs: if a Claude session loads ONLY that doc + `GLOSSARY.md` + `INDEX.md`, can it answer questions in its domain?

- `SYSTEM.md` + glossary + index → yes (operate, debug, explain present state).
- `TARGET.md` + glossary + index → yes (plan, build, justify rebuild).
- `RUNBOOK.md` + glossary + index → yes (daily ops, incident response).
- Any single ADR → yes (understand one historical decision).
- `LOG.md` → yes (chronology).

If a doc fails this test, its scope is wrong — split it or absorb it.

---

## Sources

- [Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — raw / wiki / index three-layer pattern.
- [Beyond RAG: Karpathy's LLM Wiki pattern (Level Up Coding)](https://levelup.gitconnected.com/beyond-rag-how-andrej-karpathys-llm-wiki-pattern-builds-knowledge-that-actually-compounds-31a08528665e) — index.md + log.md mechanics.
- [LLM Wiki: Karpathy's 3-Layer Pattern (decodethefuture)](https://decodethefuture.org/en/llm-wiki-karpathy-pattern/) — schema layer / editorial conventions.

---

## Next steps (not part of this deliverable)

1. Create the empty skeleton tree under `C:\Projects\rexfinhub\docs\` per §1.
2. Move `REXFINHUB_ARCHITECTURE.md` v3 → `docs/raw/REXFINHUB_ARCHITECTURE_v3_2026-05-19.md`.
3. Reshape its sections into `SYSTEM.md` + `TARGET.md` + `RUNBOOK.md` per the anchor lists in §2.
4. Bootstrap `GLOSSARY.md` with the ~15 terms already in conflict (`rex-product`, `capm-product`, `is-rex-flag`, `etp-category`, `survivorship`, `canonical-product-id`, `gate`, `auto-go`, `preflight`, `send-gate`, `bloomberg-pull`, `cboe-cookie`, `fresh-poller`, `effective-date`, `inception-date`).
5. Add the one-line pointer to user-scope `MEMORY.md`.
6. Open questions you raised (CBOE always-on polling, edgartools mechanics, target_inception semantics, morning-vs-realtime alerts, near-instant filing categorization, automated CBOE login) get filed as:
   - Operational design questions → `TARGET.md#known-gaps` + an ADR per resolution
   - Semantic-naming questions → resolved by `GLOSSARY.md` entries before the rest is written

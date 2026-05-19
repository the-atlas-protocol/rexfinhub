# rexfinhub — Claude Code project instructions

> Auto-loaded by every Claude Code session opened in this repository. Keep it short. The full architecture lives in `docs/`.

## READ FIRST — Documentation layer

This project uses a six-doc canonical framework under `docs/`. **Every session, read `docs/INDEX.md` first** to route to the right reference per task. Resolve any unfamiliar domain term via `docs/GLOSSARY.md`.

| If you're doing… | Load in this order |
|---|---|
| Operating / debugging the live system | `docs/INDEX.md` → `docs/SYSTEM.md` → `docs/GLOSSARY.md` |
| Planning or building a rebuild phase | `docs/INDEX.md` → `docs/TARGET.md` → `docs/SYSTEM.md` → `docs/GLOSSARY.md` |
| Daily ops / "how do I X" | `docs/INDEX.md` → `docs/RUNBOOK.md` → `docs/GLOSSARY.md` |
| Understanding a past decision | `docs/INDEX.md` → `docs/DECISIONS/NNNN-*.md` |
| Reconstructing history | `docs/INDEX.md` → `docs/LOG.md` |

The full doc framework was adopted via `docs/DECISIONS/0001-docs-framework.md`. Anti-patterns and conventions are codified there.

## Repo basics

- **Production source of truth**: VPS at `jarvis@46.224.126.196:/home/jarvis/rexfinhub/`. SQLite DB lives there.
- **Public webapp**: rexfinhub.com (Render). Read-only replica of VPS DB. Auto-deploys on push to `main`.
- **D drive archive**: `D:\sec-data\` holds nightly backup tarballs + cache snapshots. Not queried live.
- **Local repo**: dev only. Syncthing laptop↔desktop. Never authoritative for any data.
- **Worktrees**: this project uses `.claude/worktrees/` for isolated multi-agent work.

## Glossary tip

Terms with `[[double-bracket]]` syntax in any doc resolve to entries in `docs/GLOSSARY.md`. If you find a term used with semantic weight that's NOT in the glossary, add it.

## When in doubt

- `docs/SYSTEM.md` = what production does TODAY (present tense)
- `docs/TARGET.md` = what we're building toward (future tense)
- `docs/RUNBOOK.md` = how Ryu operates day-to-day
- `docs/DECISIONS/` = why the design is the way it is

Never put future-state in SYSTEM. Never put architecture in RUNBOOK. Never inline a TODO — use the per-doc `### known-gaps` section so they're greppable.

# ⚠️ This folder is NOT the live Asia pipeline

**The live Asia pipeline is `C:\Foundry\rex-asia\`.** Work there, not here.

## What this folder actually is

A frozen snapshot taken on **2026-07-14**, when Asia was nominally merged into the
rexfinhub monorepo. The files were copied across, but the work never moved with them —
every month since has been built in the standalone tree. Nothing here has changed since the
merge date.

It is missing pieces that make a build impossible anyway:

| | here | `C:\Foundry\rex-asia\` |
|---|---|---|
| `build_month.py` (chain orchestrator) | **absent** | present |
| `asia_preflight.py` (blocking gate) | **absent** | present |
| `load_month.py` | 2026-07-14 | 2026-08-31 |
| `refresh_all_months.py` | 2026-07-14 | 2026-08-31 |
| `enrich_report_data.py` | 2026-07-14 | 2026-08-31 |
| `report_v15.html` | 2026-07-14 | 2026-08-28 |
| newest built report | 2026-05 | **2026-07** |

## Why the live tree is not in this repo

It carries a bundled PostgreSQL install and a live cluster — `pgsql/` (860 MB) and
`pgdata/` (74 MB), 934 MB of the 1.4 GB total. A database engine and its data directory are
runtime infrastructure, not source, and do not belong inside a source repository. The
standalone tree is also its own git repo with its own history.

So Asia stays a peer project under `C:\Foundry\`, like `tactical-rotation`.

## Kept, not deleted

These files are left in place deliberately rather than removed — they are the record of what
the merge copied. Treat them as read-only history. If you need Asia code, go to
`C:\Foundry\rex-asia\`.

*Moved 2026-08-31, out of `C:\Foundry\_archive\rex-asia-standalone-premerge\`, whose name
implied the opposite of the truth: it was the live tree the whole time.*

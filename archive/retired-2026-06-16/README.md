# Retired — quarantined 2026-06-16

These files were moved here from the live tree as part of the system-finalization
cleanup. They are **dead code**: a 3-gate proof-of-death (static grep / runtime
import / test reference) showed **0 live references** for each. They are kept here
(not deleted) pending a full sweep at a later time.

**They can never run again.** Every `.py` file has a `raise SystemExit(...)` guard
prepended as line 1, so any attempt to run or import it fails immediately. The
files also no longer sit on any import path, and nothing in the live system
references them.

Original paths are preserved under this folder (e.g.
`archive/retired-2026-06-16/scripts/import_capm.py` was `scripts/import_capm.py`).

## What's here

- `screener/li_engine/analysis/trex_combined_v2.py … v8.py` — superseded report
  builders. `trex_combined_v9` is the sole live version (wired in `send_all.py`).
- `scripts/migrate_*.py` (10) — one-time database schema migrations from the
  2026-05 rebuild and the CAPM→REX sunset. None scheduled.
- `scripts/backfill_*.py` (4 spent backfills) — one-time data backfills.
  NOTE: `backfill_product_master.py`, `backfill_identifier_xref.py`, and
  `backfill_fund_underlier.py` are STILL LIVE (wired via
  `ensure_canonical_identity.py`) and were deliberately left in place.
- `scripts/import_capm.py` — legacy CAPM importer (CAPM fully sunset 2026-05).
- `scripts/generate_monthly_commentary.py`, `generate_weekly_theses.py`,
  `generate_aum_growth_charts.py` — experimental generators never wired into the
  send pipeline.
- `config/*.txt.bak` (3) — stale recipient-list backups from 2026-04.

## Still pending (not here yet)

- `screener/li_engine/analysis/weekly_v2_report.py` — the legacy stock-recs report
  Ryu confirmed retired. It still has live references; those are repointed to
  `trex_combined_v9` before it joins this folder.
- `scripts/drop_capm_products.py` — conditional: verify the `capm_products` table
  is already dropped on prod before retiring.

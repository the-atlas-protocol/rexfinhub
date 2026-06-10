# Fix: 27 date inversions on `rex_products` (2026-05-12)

**Script:** `scripts/fix_rex_filing_dates_2026-05-12.py`
**Audit table:** `classification_audit_log` (sweep_run_id pattern: `fix_date_inversions_<timestamp>`)
**Closes:** part of issue #138

## Problem statement

Two distinct date-ordering violations existed on `rex_products`:

- **Group A — 20 rows:** `initial_filing_date > estimated_effective_date`
- **Group B — 7 rows:** `estimated_effective_date > target_listing_date`

These were caught during the 2026-05-12 follow-up audit after the 2026-05-11
rebuild. Initial diagnosis attributed Group A to a back-fill artifact that
overwrote `initial_filing_date` with the most-recent 485A filing date.

## Investigation

### Group A — all 20 rows are Tidal Trust II REX 2X / REX Short series

Inspection of `fund_status` for the 20 affected series:

| Field                 | Value                                                      |
|----------------------|------------------------------------------------------------|
| `status`             | `EFFECTIVE`                                                |
| `effective_date`     | `2022-12-05` (this is the SEC series-registration date)    |
| `latest_form`        | `485BXT` (delaying amendment)                              |
| `latest_filing_date` | `2023-09-21` (ids 630-638) / `2024-01-24` (ids 639-649)    |

These are funds that **registered with SEC in 2022 but never launched** —
they have been carrying delaying amendments forward for years.

The "back-fill artifact" diagnosis was directionally right: `rex_products.
initial_filing_date` had been populated from `fund_status.latest_filing_date`
(the most-recent 485BXT), not from the actual initial registration.

### The naive fix doesn't fully work

The original task description said: *update `initial_filing_date` to
`MIN(485APOS filing_date)` for the trust.*

Empirically (against the live local DB on 2026-05-12), the earliest 485APOS
indexed in our `filings` table for Tidal Trust II (trust_id=8) is
**2024-01-08** — which is later than `estimated_effective_date = 2022-12-05`.
Our `filings` table coverage for this trust starts in January 2024; the
original 2022 N-1A series-creation filings predate our indexing.

So `MIN(485APOS)` alone does **not** resolve the inversion: the result is
still `2024-01-08 > 2022-12-05`.

### Fix actually applied

The script uses a two-tier policy:

1. If `MIN(485APOS).filing_date <= estimated_effective_date`, use it.
   (`reason = earliest_485apos_predates_effective`)
2. Otherwise — the case for all 20 Group A rows in our current data —
   fall back to `estimated_effective_date` itself. The SEC series
   registration date IS the canonical "initial" for a fund that never
   launched. (`reason = no_485apos_predates_effective`)

This resolves the ordering cleanly and uses authoritative SEC data
(`fund_status.effective_date`, sourced from EDGAR).

## Group A — changes applied

All 20 rows: `initial_filing_date` set to `2022-12-05`.

| id  | series_id   | ticker | old_initial_filing_date | new_initial_filing_date |
|-----|-------------|--------|-------------------------|-------------------------|
| 630 | S000079021  | -      | 2023-09-21              | 2022-12-05              |
| 631 | S000079023  | -      | 2023-09-21              | 2022-12-05              |
| 632 | S000079024  | -      | 2023-09-21              | 2022-12-05              |
| 633 | S000079025  | -      | 2023-09-21              | 2022-12-05              |
| 634 | S000079026  | -      | 2023-09-21              | 2022-12-05              |
| 635 | S000079028  | -      | 2023-09-21              | 2022-12-05              |
| 636 | S000079029  | -      | 2023-09-21              | 2022-12-05              |
| 637 | S000079030  | -      | 2023-09-21              | 2022-12-05              |
| 638 | S000079022  | -      | 2023-09-21              | 2022-12-05              |
| 639 | S000078245  | -      | 2024-01-24              | 2022-12-05              |
| 640 | S000078247  | -      | 2024-01-24              | 2022-12-05              |
| 641 | S000078248  | -      | 2024-01-24              | 2022-12-05              |
| 642 | S000078249  | -      | 2024-01-24              | 2022-12-05              |
| 643 | S000078250  | -      | 2024-01-24              | 2022-12-05              |
| 644 | S000078251  | -      | 2024-01-24              | 2022-12-05              |
| 645 | S000078252  | -      | 2024-01-24              | 2022-12-05              |
| 646 | S000078253  | -      | 2024-01-24              | 2022-12-05              |
| 647 | S000078254  | -      | 2024-01-24              | 2022-12-05              |
| 648 | S000078246  | -      | 2024-01-24              | 2022-12-05              |
| 649 | S000079027  | MSRU   | 2024-01-24              | 2022-12-05              |

Source filing reference (earliest 485APOS in our DB for trust_id=8, used
only for the audit metadata — not as the new value):
`0001999371-24-000236` filed 2024-01-08.

## Group B — flagged, no change

These 7 rows have `estimated_effective_date > target_listing_date` because
REX shipped them on amendment **before** the original 485APOS rolled into
effectiveness. This is a real condition in the SEC framework, not a data
bug. They remain in the DB as-is and are documented here so future audits
do not re-flag them.

| id  | ticker | trust                     | estimated_effective_date | target_listing_date |
|-----|--------|---------------------------|--------------------------|---------------------|
| 71  | OBTC   | Osprey Bitcoin Trust      | 2025-12-20               | 2025-12-19          |
| 105 | NVII   | REX ETF Trust             | 2025-08-06               | 2025-05-28          |
| 106 | COII   | REX ETF Trust             | 2025-08-06               | 2025-06-03          |
| 107 | MSII   | REX ETF Trust             | 2025-08-06               | 2025-06-03          |
| 108 | TSII   | REX ETF Trust             | 2025-08-06               | 2025-06-04          |
| 180 | XRPK   | ETF Opportunities Trust   | 2026-01-21               | 2025-12-02          |
| 181 | SOLX   | ETF Opportunities Trust   | 2026-01-21               | 2025-12-02          |

## Backup

A SQLite `.backup` was taken automatically by the script before any UPDATE
ran. Location: `data/backups/etp_tracker.db.pre-date-fix-<timestamp>.bak`.

The local-DB apply on 2026-05-12 produced:
`data/backups/etp_tracker.db.pre-date-fix-20260512T115450.bak`.

## Verification SQL

Run any of these after the script:

```sql
-- Should return 0 (Group A fully resolved)
SELECT COUNT(*) FROM rex_products
WHERE initial_filing_date IS NOT NULL
  AND estimated_effective_date IS NOT NULL
  AND initial_filing_date > estimated_effective_date;

-- Should return exactly 7 (Group B — the listing-on-amendment cases)
SELECT id, ticker, trust, estimated_effective_date, target_listing_date
FROM rex_products
WHERE estimated_effective_date IS NOT NULL
  AND target_listing_date IS NOT NULL
  AND estimated_effective_date > target_listing_date
ORDER BY id;

-- Show the audit trail for this fix
SELECT sweep_run_id, ticker, old_value, new_value, reason, dry_run, created_at
FROM classification_audit_log
WHERE sweep_run_id LIKE 'fix_date_inversions_%'
ORDER BY created_at DESC, id DESC
LIMIT 50;

-- Confirm the 20 Group A rows are now all 2022-12-05
SELECT id, series_id, initial_filing_date, estimated_effective_date
FROM rex_products
WHERE id BETWEEN 630 AND 649
ORDER BY id;
```

## Re-running the script

The script is idempotent. After a successful `--apply`:

- A subsequent `--apply` finds 0 Group A rows (they're already at
  `2022-12-05`) and writes no UPDATEs.
- Group B rows are re-listed as informational each run; audit rows for
  them are inserted with `new_value == old_value` and `reason =
  group_b_informational_no_change`. If that becomes noisy, suppress those
  inserts in a future pass.

## VPS invocation

```bash
ssh jarvis@46.224.126.196 \
  "cd /home/jarvis/rexfinhub && /home/jarvis/venv/bin/python \
   scripts/fix_rex_filing_dates_2026-05-12.py --apply"
```

The `--apply` flag will prompt for the `I AGREE` confirmation on stdin;
pipe it through if running non-interactively:

```bash
ssh jarvis@46.224.126.196 \
  "cd /home/jarvis/rexfinhub && echo 'I AGREE' | /home/jarvis/venv/bin/python \
   scripts/fix_rex_filing_dates_2026-05-12.py --apply"
```

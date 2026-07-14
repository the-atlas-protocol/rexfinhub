---
rank: 3
leverage: high — two schedulers write rex_products.status with no shared lock; this
  table drives the T-REX count (the number Ryu has argued about repeatedly per
  docs/GOAL.md) and the send pipeline's product state.
---

# PLAN — Make status_history the sole writer of rex_products.status (close PV-02)

## Goal

`docs/SYSTEM_LEDGER.md` PV-02 (and `docs/ARCHITECTURE.md` §3's table-ownership
note) documents that **two different jobs** both write directly to
`rex_products.status` with no transaction boundary between them:

- `webapp/services/status_reconciler.py:241` — `UPDATE rex_products SET
  status_cached = ?, status = ? WHERE canonical_id = ?` inside
  `append_transition()`, run by the bloomberg-chain (17:15/21:00, `--apply`).
- `scripts/sync_rex_products_from_filings.py` — writes `.status` directly in at
  least 3 places: line 633 (`existing.status = proposed`, phase1/2 filing sync),
  line 766 (`p.status = "Listed"`, phase3 activate-from-market), and line 844
  (`p.status = "Delisted"`, phase4 demote-vanished-from-market) — run by the
  15-min fresh-poller AND the 4-hourly sec-scrape unit, both with `--apply`.

Per `ARCHITECTURE.md` §3, the design intent is: `status_history` (bi-temporal) is
"THE status authority," and `sync_rex_products` is supposed to be **promote-only
evidence**, with `status_reconciler` doing corrections. In practice both write the
same column with no ordering guarantee — the fresh-poller (every 15 min) and
bloomberg-chain (twice daily) can race, and a `status_reconciler` correction can be
silently clobbered by the next `sync_rex_products` run 15 minutes later (or vice
versa). This directly threatens the "T-REX = 41" reconciliation that
`docs/GOAL.md` spent an entire session pinning down — if the authoritative count
depends on `rex_products.status` and that column can flip without a corresponding
`status_history` entry, the "one source of truth, matches reality" invariant is
broken at the storage layer, not just the read layer.

## Exact files to touch

1. `C:\Foundry\Rexfinhub\scripts\sync_rex_products_from_filings.py` (the 3 write
   sites: lines ~633, ~766, ~844)
2. `C:\Foundry\Rexfinhub\webapp\services\status_reconciler.py` (reference only —
   understand `append_transition()` fully; this file likely does NOT need to
   change, it's already the intended authority)
3. `C:\Foundry\Rexfinhub\scripts\run_assertions.py` — there is already a
   `check_status_record_single_source` assertion (added per ADR 0012, see
   `ASSERTIONS` list) and a `check_status_cached` assertion — read both fully
   before writing new code; your fix may just need to make an EXISTING assertion
   catch this race, rather than write a new one from scratch.
4. `C:\Foundry\Rexfinhub\docs\DECISIONS\` — read whichever ADR covers "one writer
   per table" (ADR 0011 E2, referenced in `ARCHITECTURE.md` §1 and §3) before
   changing anything, to confirm you're implementing the already-agreed design,
   not inventing a new one.

## Step-by-step

1. Read `webapp/services/status_reconciler.py` lines 1-260 in full (it's short).
   Understand `gather_evidence()`, `derive_status()`, `get_current_status()`,
   and `append_transition()` — this is the intended single writer.
2. Read `scripts/sync_rex_products_from_filings.py` around each of the 3 write
   sites (lines ~500-660 for phase1/2, ~695-790 for phase3, ~787-856 for
   phase4). For each, determine: is this write **evidence** (a raw fact worth
   recording, e.g. "we found a Listed-status filing for this CIK") or is it
   **the authoritative status transition** (the actual promotion the system acts
   on)? Per the architecture note, it should only ever be the former.
3. Change each of the 3 write sites so that instead of `existing.status =
   proposed` / `p.status = "Listed"` / `p.status = "Delisted"` directly, the
   script calls into `status_reconciler`'s transition machinery (or, if that
   function isn't designed to be called from another script, write the evidence
   to whatever `status_reconciler.gather_evidence()` reads from, and let the
   next `status_reconciler --apply` run pick it up and make the actual
   `rex_products.status` write). The exact mechanism depends on what
   `gather_evidence()` queries — read it first; do not guess.
4. If `sync_rex_products_from_filings.py`'s writes truly need to be
   **immediate** (e.g. phase3's "activate from market" is time-sensitive and
   can't wait for the next reconciler run), the alternative fix is a shared
   advisory lock or a single-writer serialization: have `sync_rex_products`
   call `status_reconciler.append_transition()` directly (import it) instead of
   writing `.status` itself, so there is still only one code path that ever
   issues the `UPDATE rex_products SET status = ?`. This keeps the immediacy but
   removes the second writer. Prefer this approach if evidence-then-wait
   introduces unacceptable latency for phase3/phase4 (check with existing
   `capm_audit_log` timestamps to see how often these paths actually fire and
   whether latency matters).
5. Whichever approach you take, every write must still land a row in
   `status_history` (dual-write requirement per `ARCHITECTURE.md` §3 —
   "`status_history` (bi-temporal) — dual-written with status_cached; drift
   assertion green"). Do not create a path where `rex_products.status` changes
   without a corresponding `status_history` row, or you'll break
   `check_status_record_single_source` / `check_status_history_current`.
6. Update `capm_audit_log` writes similarly if `sync_rex_products_from_filings.py`
   currently writes its own audit rows separately from `status_reconciler`'s
   — check for duplication vs. divergence in audit trail format.

## Edge cases a weaker model would miss

- **Do not simply disable the 3 write sites without a replacement path.** If you
  just delete `existing.status = proposed` etc., phase3 ("activate from market")
  and phase4 ("demote vanished from market") lose their entire function — these
  are real promotions (Filed→Listed, Listed→Delisted) that must still happen,
  just through the single authoritative path.
- **`sync_rex_products_from_filings.py` is invoked in TWO different scheduled
  contexts** (fresh-poller 15-min promote-only, and a post-step of the 4-hourly
  sec-scrape per `ARCHITECTURE.md` §2's table) — a fix here affects both timers.
  Confirm both still behave correctly (promote-only means it should never demote
  in the 15-min path; check whether phase4's demote call is even reachable from
  the fresh-poller invocation or only from the 4-hourly one — read the `main()`
  argv handling to see which phases run in which invocation mode).
- **`status_reconciler` runs inside `apply_bloomberg_post_steps.py`'s ordered
  chain** (per `ARCHITECTURE.md` §4, right after `ensure_canonical_identity`) —
  if you route `sync_rex_products`'s writes through `status_reconciler`'s
  function, make sure you're not accidentally causing it to run OUTSIDE that
  chain's step ordering (canonical_id must exist before a status transition is
  meaningful — reread `ARCHITECTURE.md` §4's chain order).
- **`--apply` vs dry-run flags exist on both scripts** — your fix must preserve
  both scripts' existing dry-run safety (never write anything without
  `--apply`); do not accidentally make the dry-run path perform a real write by
  routing through a shared function that doesn't respect the caller's dry-run
  flag.
- **This changes production-critical scheduled jobs.** Do not test this by
  running either script with `--apply` against the real VPS DB. Test against a
  local/dev copy of `data/etp_tracker.db`, or with `--dry-run`, and read the
  diff carefully before recommending a VPS deploy.

## Acceptance criteria

1. Grep `\.status = ` (excluding `status_cached`) in
   `scripts/sync_rex_products_from_filings.py` shows zero direct writes to
   `rex_products.status` outside of a call into the shared reconciler path.
2. `python scripts/run_assertions.py --dry-run` (against a local DB copy) shows
   `check_status_record_single_source` and `check_status_cached` both still
   passing (green) after the change.
3. Run `sync_rex_products_from_filings.py --apply` then immediately
   `status_reconciler.py --apply` (or whatever CLI wraps it — check for a
   `main()`/argparse block) against a **local dev DB copy**, and confirm no
   `rex_products.status` value changes on the second run that contradicts what
   the first run set (i.e. no ping-pong/clobber) — spot check 5 rows via
   `sqlite3 data/etp_tracker.db "SELECT canonical_id, status, status_cached FROM rex_products LIMIT 5"`
   before/after each step.
4. The T-REX ACTV count (`SELECT COUNT(*) FROM rex_products WHERE status='Listed'
   AND fund_name LIKE 'T-REX%'` or the report's actual query) is unchanged by
   this refactor on the same data — this is a race-condition fix, not a data fix;
   if the count changes, that's a signal the old race was already causing
   incorrect data, and should be reported to Ryu rather than silently accepted.

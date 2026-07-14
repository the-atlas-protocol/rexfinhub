---
rank: 1
leverage: highest — real external sends (BMO, RBC, CAIS) now flow through this path; a typo
  silently drops a send instead of failing loudly.
---

# PLAN — Hard-fail on unknown recipient list_type (close PV-07 / PV-08)

## Goal

`webapp/services/recipients.py:get_recipients()` currently returns an **empty list**
and just logs a warning when `list_type` isn't one of the 14 known values
(`VALID_LIST_TYPES` at line 17). That means a typo'd or renamed `list_type` string
anywhere in the send path makes a report **silently ship to nobody** — no
exception, no visible failure, nothing in the daily 08:00 assertion triage unless
someone happens to notice the recipient count. Given `blue_ocean`/`microsectors`
now include **external BMO** recipients and `autocall` includes **CAIS/RBC**
(`docs/SEND_CADENCE.md`), a silently-empty recipient list on an external send is a
business risk, not just an internal annoyance. This is high leverage because the
fix is ~10 lines and eliminates an entire class of "report didn't go out and nobody
noticed" incidents.

## Exact files to touch

1. `C:\Foundry\Rexfinhub\webapp\services\recipients.py`
2. `C:\Foundry\Rexfinhub\scripts\send_all.py`
3. `C:\Foundry\Rexfinhub\scripts\run_assertions.py` (add one assertion)
4. `C:\Foundry\Rexfinhub\webapp\models.py` (EmailRecipient — no schema change needed, just doc)

## Step-by-step

1. Open `webapp/services/recipients.py`. In `get_recipients()` (line 31-43), change
   the `if list_type not in VALID_LIST_TYPES:` branch (lines 35-37) from
   `log.warning(...); return []` to **raise** `ValueError(f"Unknown recipient list_type: {list_type!r}. Valid: {sorted(VALID_LIST_TYPES)}")`.
   Do the same in `add_recipient()` (line 51-55) — it already checks
   `list_type not in VALID_LIST_TYPES` but the current behavior there needs
   verifying (read lines 51-86 first); make it raise the same `ValueError` instead
   of whatever it currently does silently.
2. Open `scripts/send_all.py` and find every call site of `get_recipients(...)`
   (grep `get_recipients(` in that file). Each call must now be wrapped so the
   `ValueError` produces a **loud, specific failure** in the send run: catch
   `ValueError` at the point where a single report's recipients are resolved
   (likely inside `_resolve_recipients` or `_send_one` — grep for the function
   that calls `get_recipients`), log it as a hard error naming the report and the
   bad `list_type`, mark that report's send attempt as FAILED (not "0 recipients,
   skipped silently"), and **continue to the next report in the bundle** rather
   than crashing the whole batch (one bad report must not block the other 9).
   Do NOT swallow the exception without surfacing it — it must show up in
   whatever the caller reports back to Ryu (stdout summary / the sweep report /
   `send_log` failure row if that concept exists there).
3. Add one new assertion to `scripts/run_assertions.py`: a function
   `check_report_list_types_valid` (follow the existing naming/shape of nearby
   assertions like `check_recipient_lists` — read that one first for the return
   contract, likely `(passed: bool, fail_count: int, sample: list, details: str)`).
   It should re-derive, from the actual builder/report registry (wherever the
   report→list_type mapping is defined — grep `BUNDLES` in `send_all.py` and
   whatever maps report key → list_type), that every report's configured
   `list_type` is a member of `recipients.VALID_LIST_TYPES`. This catches drift
   at 08:00 even on days nothing sends, instead of only at send time. Register it
   in the `ASSERTIONS = [...]` list (end of that list, near the other 2026-06-15+
   additions).
4. Do NOT change the 14-value `VALID_LIST_TYPES` set itself unless you find a
   report whose `list_type` string doesn't match any entry (if you do find one,
   that's the actual current-production bug this plan exists to catch — flag it
   to Ryu, don't silently add it to the set without checking whether the report's
   real intended list is one of the existing 14).

## Edge cases a weaker model would miss

- **Don't raise inside `add_recipient()` in a way that breaks the admin UI.**
  `add_recipient` is called from an admin form (grep its callers in
  `webapp/routers/`); if it now raises `ValueError` for a bad `list_type` typed
  into a form field, the router must catch it and return a user-facing 400/flash
  message, not a raw 500 stack trace to the browser. Check the router before
  assuming `recipients.py` is the only file needing a change here.
- **`get_private_recipients()`** (line 46-48) calls `get_recipients(db, "private")`
  with a hardcoded valid string — it will never hit the new raise, but confirm
  this after your edit (it should not need any change).
- **Test-send override path** (`send_all.py` — grep `override_to`, mentioned in
  the file's comments around line 517 re: "test send"): a test send with
  `--to` override bypasses real recipient resolution entirely. Confirm your new
  raise only fires on the **real** recipient-lookup path, not on test sends where
  `list_type` might legitimately not matter.
- **One bad report must not sink the whole bundle.** If `--bundle weekly` sends 7
  reports and one has a typo'd `list_type`, the other 6 must still send. Verify
  the exception is caught per-report, not at the top-level bundle loop.
- **This must never actually fire a live send during testing.** Verify everything
  with `--dry-run` (send_all.py has this flag) or by reading code paths — do NOT
  invoke a real send to confirm behavior; the send gate should be closed anyway.

## Acceptance criteria

1. `python -c "from webapp.services.recipients import get_recipients; from webapp.database import SessionLocal; get_recipients(SessionLocal(), 'not_a_real_type')"`
   raises `ValueError` (previously returned `[]` silently).
2. `python scripts/run_assertions.py --dry-run` runs without crashing and shows
   the new `check_report_list_types_valid` in its printed output, passing (all
   real reports currently use valid list_types — this assertion should be GREEN
   on today's config, it's a regression guard, not a fix for an existing failure).
3. `python scripts/send_all.py --bundle weekly --dry-run` (or whatever the
   no-op/preview invocation is — check `--help`) still lists all 7 weekly
   reports with their real recipient counts; no report shows 0 recipients that
   previously showed a nonzero count.
4. Grep `VALID_LIST_TYPES` is unchanged (14 entries) unless you found and flagged
   a genuine mismatch to Ryu first.
5. No live email is sent as part of verifying this (the gate stays closed; this
   plan's execution must not run `--send` for real).

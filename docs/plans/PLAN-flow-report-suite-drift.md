---
rank: 2
leverage: high — directly violates GOAL.md's "one definition per fact" mandate on a
  report that ships every week; a suite rename or new-fund launch silently miscounts
  Flow until someone hand-edits a dict.
---

# PLAN — Flow Report: derive suite membership from the DB, not a hardcoded dict

## Goal

`webapp/services/portfolio_suite_flow.py:75` defines `TICKER_SUITE` as a **Python
dict literal** mapping ~20 hardcoded tickers to suite names. This is exactly the
class of bug `docs/GOAL.md` and `docs/DEFINITIONS.md` were written to eliminate:
`market/definitions.py`'s `suite_of(ticker, fund_name)` is now the single,
self-maintaining source of truth for suite membership (name-derived, "a fund that
launches tomorrow classifies with zero manual edits" — `docs/DEFINITIONS.md` §2),
but the Flow report doesn't call it. If a ticker's suite changes (a rename, e.g.
the 2026-06 Autocallable→Structured rename that HANDOFF_2026-06-17_reports.md had
to hunt down across 4 files) or REX launches a new T-REX/MicroSectors fund, the
Flow report silently keeps grouping it under the stale hardcoded entry — this is
recorded as `SYSTEM_LEDGER.md` PV-05. Fixing it closes the last known consumer that
bypasses the definition library.

## Exact files to touch

1. `C:\Foundry\Rexfinhub\webapp\services\portfolio_suite_flow.py`
2. `C:\Foundry\Rexfinhub\market\definitions.py` (read-only reference, do not modify
   unless you find `suite_of` genuinely can't produce a suite for one of the
   current `TICKER_SUITE` tickers — see edge cases below)
3. `C:\Foundry\Rexfinhub\tests\test_definitions.py` (reference for how
   `suite_of` is expected to behave — read before editing the report)

## Step-by-step

1. Read `webapp/services/portfolio_suite_flow.py` in full, focusing on lines 75
   (the `TICKER_SUITE` dict), 166 (`cols = ["Dates"] + list(TICKER_SUITE)` — this
   is the universe of tickers the report even considers), 177 (`members = [t for
   t, sx in TICKER_SUITE.items() ...]`), 321 (`SUITE_COLORS.get(TICKER_SUITE.get(...))`),
   and 349 (`TICKER_SUITE.get(f["ticker"])`).
2. Read `market/definitions.py`'s `suite_of(ticker, fund_name)` and
   `INTERNAL_SUITES` to confirm the exact signature and return values (it returns
   a display-name string or `None` — confirm against `docs/DEFINITIONS.md` §2's
   table of 9 suites: MicroSectors, T-REX, Equity Premium Income, Growth & Income,
   IncomeMax, Structured, Thematic, Crypto, MoneyMarket).
3. Determine where `portfolio_suite_flow.py` gets its ticker universe today — is
   `list(TICKER_SUITE)` (the dict's keys) actually the full set of 20 RPS-suite
   tickers, or is there a separate query elsewhere in the file that fetches the
   candidate ticker list from `mkt_master_data` (grep for `mkt_master_data` in
   this file)? You need both pieces: (a) the ticker universe (which funds are
   "ours" and in scope for this report) and (b) which suite each belongs to.
   `rex_funds.csv` / `is_rex` (per `ARCHITECTURE.md` §3) is likely the right
   source for (a); `suite_of()` is the source for (b). Do not conflate the two —
   the fix is to replace the *suite-assignment* half, not necessarily to change
   how the ticker universe is chosen (confirm the universe is still correct
   before touching it).
4. Replace `TICKER_SUITE` (the static dict) with a function or a computed dict
   built at report-build time: for each ticker in the existing universe, look up
   `fund_name` from `mkt_master_data` (there should already be a fund_name column
   available somewhere in this file — check what query already runs) and call
   `suite_of(ticker, fund_name)`. Keep the **same downstream shape** the rest of
   the file expects (a `dict[str, str]` ticker→suite, since lines 166/177/321/349
   all call `.get()` / `.items()` / iterate keys) so you only change how the dict
   is populated, not its consumers.
5. If any ticker in the current hardcoded `TICKER_SUITE` fails to resolve via
   `suite_of()` (returns `None`) — investigate why before assuming the fix is
   wrong. Likely causes: (a) the fund's name in the DB doesn't start with the
   exact prefix `suite_of` expects (report this specific ticker/name pair to
   Ryu — it may be a genuine gap in `suite_of`'s patterns, in which case the fix
   belongs in `market/definitions.py`, not a workaround in the report), or (b) the
   ticker is legitimately not one of the 9 suites (e.g. it's `TLDR`, which needs
   the ticker-override path per DEFINITIONS.md §2 — confirm `suite_of` already
   handles `TLDR`).
6. Keep `SUITE_COLORS` (referenced at line 321) as-is unless a suite name changes.

## Edge cases a weaker model would miss

- **`TLDR` (MoneyMarket) is a ticker-override, not a name-derived suite** — per
  `docs/DEFINITIONS.md` §2, `suite_of` must special-case this one ticker since the
  fund's name ("THE LADDERED T-BILL ETF") has no REX prefix. If `TLDR` is in the
  current `TICKER_SUITE` dict, verify `suite_of("TLDR", ...)` actually returns
  `"MoneyMarket"` before assuming the swap is a pure 1:1 replacement — test this
  ticker specifically.
- **`Autocallable` → `Structured` rename** — HANDOFF_2026-06-17_reports.md
  documents this rename hit "screener archive, Portfolio Suite" among other
  files, and was fixed by hand in each place (commit `d5bb889`/`fcf8be9`). If the
  current hardcoded dict still has a stale `"Autocallable"` value anywhere,
  that's direct evidence of the bug this plan fixes — confirm the new
  `suite_of()`-derived value comes out as `"Structured"` for those tickers, not
  the old name.
- **Report must not silently drop a ticker that has no suite.** If a ticker in
  the universe resolves to `suite_of() == None` (e.g. a non-REX name pattern
  slipped into the universe query), decide explicitly whether to exclude it from
  the chart or flag it — do not let a `None` suite value propagate into
  `SUITE_COLORS.get(None)` and render as a miscolored/blank bar without anyone
  noticing. Prefer failing loudly (log + skip) over silent misrendering.
- **This report is not gated by the classification engine's Tier system** — it's
  reading REX's own suite, a different axis from `etp_category`/L&I attributes.
  Do not confuse `suite_of()` (internal suite) with `map_li_subcategory` or
  `is_singlestock` (external classification) — they answer different questions
  per `docs/DEFINITIONS.md` §1's "two views of the world."

## Acceptance criteria

1. Grep `TICKER_SUITE\s*=\s*{` in `webapp/services/portfolio_suite_flow.py`
   returns nothing (or, if kept as a fallback constant, is no longer what the
   report actually reads from at build time) — confirm via reading the final
   diff that suite assignment flows through `suite_of()`.
2. Run the Flow report builder locally against the current DB (find the CLI/
   preview invocation — check `scripts/build_previews.py` or similar per
   HANDOFF_2026-06-17_reports.md's "pull previews to local + open in Chrome"
   step) and confirm every one of the ~20 RPS tickers still appears with the
   **same suite it had before** (this is a refactor for correctness/self-healing,
   not intended to change today's numbers — if today's numbers DO change for a
   ticker, that ticker is the proof the old hardcoded dict was already stale;
   flag it to Ryu rather than silently accepting either the old or new number).
3. Add or update a check (unit test, or a debug script run once) that a
   hypothetical new T-REX ticker (fabricate a `fund_name` starting with "T-REX")
   would classify correctly without needing a code change — demonstrating the
   self-healing property GOAL.md requires.
4. `docs/SYSTEM_LEDGER.md` PV-05 entry can be marked resolved once this ships
   (update the ledger row's status — this is a doc-hygiene step, not required for
   the code fix itself, but keeps the ledger truthful).

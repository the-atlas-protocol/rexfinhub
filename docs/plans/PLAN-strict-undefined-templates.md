---
rank: 5
leverage: medium-high — closes PV-17: 35 of 37 routers bypass a fix that was
  explicitly built to prevent a repeat of the 2026-05-12 "silent 0-byte page
  served 200 OK for 12 hours" incident; also missing `templates.env.globals["url"]`
  / `csrf_token` (money-page features), not just StrictUndefined.
---

# PLAN — Migrate routers off ad-hoc `Jinja2Templates(...)` onto the shared factory

## Goal

`webapp/templates_init.py` (`build_templates()`) was built specifically to stop a
repeat of a real production incident: on 2026-05-12 a bad auto-merge produced a
0-byte `pipeline_products.html`, and because Jinja2's default `Undefined` renders
missing context keys as empty strings, the page served **200 OK with an empty
body for ~12 hours** — no exception, no log, no alert. `webapp/main.py:272` wires
the app's primary `templates` object through `build_templates()`, which sets
`undefined=StrictUndefined` (missing template variable → loud `UndefinedError`
instead of silent blank render).

But `webapp/templates_init.py`'s own docstring says: "Routers that instantiate
their own `Jinja2Templates` directly do NOT receive these settings" — and
confirmed via `grep -rl "Jinja2Templates(" webapp/`: **35 of 37 files that create
a Jinja2Templates instance do so directly** (e.g. `webapp/routers/funds.py:37`,
`webapp/routers/screener.py:23`, `webapp/routers/dashboard.py:27` all have
`templates = Jinja2Templates(directory="webapp/templates")`), bypassing not just
`StrictUndefined` but also two globals only registered on the canonical instance
in `main.py`: `templates.env.globals["url"]` (line 276, the `{{ url(...) }}`
route-name resolver) and `templates.env.globals["csrf_token"]` (line 292). Any
router using its own instance that has a template calling `{{ url(...) }}` or
`{{ csrf_token() }}` would currently hard-fail or silently render nothing for
those calls too — this is broader than just the StrictUndefined gap.
`docs/SYSTEM_LEDGER.md` PV-17 tracks the StrictUndefined half; this plan closes
both.

## Exact files to touch

35 router files under `C:\Foundry\Rexfinhub\webapp\routers\` — get the exact
current list with:
```
grep -rl "Jinja2Templates(" webapp/routers/ webapp/templates_init.py
```
(exclude `webapp/templates_init.py` itself and `webapp/main.py`, which are
already correct). At time of writing this is: `admin.py`, `admin_health.py`,
`admin_products.py`, `admin_reports.py`, `admin_system_state.py`, `analysis.py`,
`analytics.py`, `api.py`, `capm.py`, `dashboard.py`, `digest.py`, `downloads.py`,
`filings.py`, `funds.py`, `holdings.py`, `intel.py`, `intel_competitors.py`,
`intel_insights.py`, `ipo_intel.py`, `issuers.py`, `market.py`,
`market_advanced.py`, `monitor.py`, `notes.py`, `notes_autocall.py`,
`operations_reserved.py`, `pipeline_calendar.py`, `screener.py`, `search.py`,
`stocks.py`, `strategy.py`, `tools_compare.py`, `tools_li.py`, `trusts.py`,
`underlier_view.py`, `universe.py`.

## Step-by-step

1. Do NOT attempt all 35 in one pass. Rank them by "money-page" exposure first —
   per `docs/SYSTEM_LEDGER.md`'s own recommendation ("migrate template envs to
   StrictUndefined incrementally, starting with money-page templates"). Priority
   order: `funds.py`, `screener.py`, `market.py`, `market_advanced.py`,
   `dashboard.py`, `holdings.py` (public-facing fund/market data pages) before
   `admin_*.py` (internal-only, lower blast radius if something breaks).
2. For each file, in this order:
   a. Find the line `templates = Jinja2Templates(directory="webapp/templates")`
      (or equivalent) and replace it with
      `from webapp.templates_init import build_templates` (add to imports) +
      `templates = build_templates()`.
   b. Grep that same file for every `templates.TemplateResponse(...)` call and
      every template it renders (the template name is usually the first arg).
      Read each rendered `.html` file under `webapp/templates/` for `{{ ... }}`
      expressions referencing context keys.
   c. Cross-check every referenced key against what the route handler actually
      passes in its context dict. Any key referenced in the template but NOT
      always passed by the handler will now raise `UndefinedError` where it
      previously rendered blank. This is the entire point of the fix — but it
      means you must verify each such gap is either (i) already fixed by making
      the handler always pass the key (even as `None`), or (ii) intentionally
      left to fail loudly because it represents a real bug worth surfacing.
      Do not blanket-suppress by passing dummy values just to silence the error
      without checking whether the missing key was masking something real.
   d. Start the local dev server (or use the `/run` skill) and hit every route
      in that file with a real request (or the nearest available test data) to
      confirm the page still renders. Do NOT just grep for correctness — actually
      load the page, per the "verify UI changes in a browser" requirement.
3. After each file (or small batch of 3-5 related files) passes manual
   verification, move to the next. This is intentionally file-by-file, not a
   single mechanical sed across all 35 — the whole point is to catch the
   places where StrictUndefined changes behavior.
4. Once all 35 are migrated, delete the now-provably-dead pattern by confirming
   `grep -rn "Jinja2Templates(" webapp/routers/` returns nothing, and add a
   simple regression guard: either a unit test asserting no router file
   contains that literal string, or a new lightweight entry in
   `scripts/run_assertions.py` (`check_shared_templates_factory` or similar) that
   greps the routers directory and fails if any file still instantiates
   `Jinja2Templates` directly — this prevents a new router from reintroducing
   the gap.

## Edge cases a weaker model would miss

- **`{{ url(...) }}` and `{{ csrf_token() }}` are NOT registered on a bare
  `Jinja2Templates(directory=...)` instance** — they're added as globals only on
  the object returned by `build_templates()`, via `main.py:276` and `:292`,
  which run once at app-startup on the canonical `templates` object. If you
  naively call `build_templates()` fresh inside each router file (rather than
  importing the single instance from `webapp.main`), you get StrictUndefined
  correctly, but you do NOT get `url()`/`csrf_token()` unless you register
  those globals too — check whether `build_templates()` itself sets them (per
  the file dump above, it does NOT; only `main.py` sets them post-construction).
  **The safer fix is `from webapp.main import templates` in each router**, not
  calling `build_templates()` again per-router — confirm which pattern
  `templates_init.py`'s own docstring recommends (it says "the canonical
  pattern going forward is `from webapp.main import templates`") and follow
  that, not a fresh factory call per file, unless you also duplicate the
  globals registration (which would create two divergent copies of the URL
  registry — a new single-source-of-truth violation, exactly what this
  project is trying to eliminate elsewhere).
- **Circular import risk**: `webapp/main.py` likely imports and registers all
  the routers (`app.include_router(...)`), so `from webapp.main import
  templates` inside a router module main.py imports could create a circular
  import. Check `main.py`'s router-registration order and import structure
  before assuming this works cleanly — if it's circular, you may need
  `build_templates()` per-router PLUS manually re-registering the `url`/
  `csrf_token` globals (import the same helper functions `main.py` uses) rather
  than importing the `templates` object itself. Read `main.py` fully before
  choosing an approach.
- **`auto_reload` behavior**: `build_templates()` sets `auto_reload=False` only
  when `RENDER` env var is set (production). Confirm this doesn't change local
  dev-server template hot-reload behavior in a way that surprises you mid-testing.
- **A template rendering blank today might be masking a genuine, long-standing
  bug** (exactly like the 2026-05-12 incident this fix was built to catch) —
  when you find one during verification, do not "fix" it by just passing an
  empty/None value to silence the new exception without understanding why the
  key was never being passed. Flag genuinely suspicious ones to Ryu.
- **Test in a real browser, not just via curl/grep** — some Jinja errors only
  surface on specific conditional branches (empty-list vs populated-list
  states), so hit each page with realistic data at least once.

## Acceptance criteria

1. `grep -rn "Jinja2Templates(" webapp/routers/` returns zero matches after the
   full migration (or, if done incrementally, zero matches for the files
   completed in a given pass — track progress file-by-file).
2. Every migrated router's pages load successfully in a real browser session
   (or via `/run`/dev-server smoke test) — no new 500s introduced.
3. `templates.env.globals["url"]` and `["csrf_token"]` are available in every
   migrated router's templates (spot-check by finding a template that uses
   `{{ url(...) }}` and confirming it renders correctly, not a raw
   `UndefinedError` or blank).
4. A regression guard (test or assertion) exists so a new router file can't
   reintroduce a bare `Jinja2Templates(...)` instantiation without failing CI
   or the morning assertion suite.
5. `docs/SYSTEM_LEDGER.md` PV-17's status can be updated to reflect real
   progress (partial or complete) once this ships.

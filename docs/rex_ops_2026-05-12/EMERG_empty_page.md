# EMERG: /operations/pipeline returns empty 200

**Date:** 2026-05-12
**Severity:** P0 — page WHITE in production browser
**Status:** FIXED

## Symptom

`GET /operations/pipeline` returned HTTP 200 with `Content-Length: 0`. Page
rendered fully blank in the browser. Reproducible via FastAPI `TestClient`
inside `/home/jarvis/rexfinhub` on the VPS:

```
status: 200 size: 0 first 500:
```

## Root cause

`webapp/templates/pipeline_products.html` was **0 bytes** on disk on the
`main` branch.

The template was wiped during the auto-resolved merge commit:

```
43294d1 merge: rexops-O5-tickers (auto-resolved, took THEIRS)
```

Git history confirms — the parent `43294d1^` shipped a 1059-line template,
but the merge result is empty. The "took THEIRS" auto-resolution incorrectly
picked an empty side of the conflict (likely the rebase-mid-state copy of
`rexops-O5-tickers` that had not yet rewritten the template) and that empty
result rode forward through `rexops-O6-underlier` (the O6 work was scoped to
the `pipeline_calendar.py` router + a new modal partial, so O6 never touched
the now-empty `pipeline_products.html` on disk and shipped it as-is).

Jinja2 default `Undefined` does NOT raise on missing context keys, so an
empty template renders as an empty string — no exception, no log, no error
page. The route handler returned a 200 with empty body.

## Why the safety net mattered

`pipeline_calendar.py:_pipeline_products_impl` had no try/except around the
template render. Even if Jinja HAD raised, the handler would have propagated
the exception (FastAPI's default 500 page) — which is better than a silent
white page, but still hides the traceback from the operator.

## Fix (this commit)

1. **Restore `pipeline_products.html` from `a764f41` (rexops-O6-underlier)**,
   which contains the latest O1 + O5 + O6 template work (1245 lines, with
   funnel-top layout, ticker suggestion chips, underlier race chip).

2. **Restore the context-dict keys** that the O1 docstring claimed were
   removed but the template actually still consumes:
   `listed`, `filed`, `awaiting`, `research`, `filings_last_7d`,
   `launches_last_30d`, `effectives_next_30d`, `effectives_next_90d`,
   `next_launches`, `avg_cycle`, `min_cycle`, `max_cycle`, `cycle_sample`,
   `recent_activity`, `recent_days`, `last_updated_overall`. The underlying
   SQL queries were already running in the handler; only the dict trim
   from O1 needed to be reverted.

3. **Wrap the full handler in a try/except** that logs the traceback and
   returns a visible HTMLResponse(500) instead of a silent failure. Future
   regressions surface as a red `<pre>` error block, not a white page.

## Verification

VPS in-process TestClient (post-fix):

```
status: 200 size: 188984
has title: True
first 300: <!DOCTYPE html><html lang="en"><head>...
```

188 KB rendered HTML, `<title>` tag present, full template body.

## Follow-ups

- Audit `git log --diff-filter=D --shortstat` for any other 0-byte files
  introduced in the rexops-O[1-6] merge train.
- Consider switching `Jinja2Templates` to `undefined=StrictUndefined` so a
  missing template key produces an immediate exception rather than a
  silently-blank cell. Today's bug would have surfaced 12 hours earlier
  if Jinja had been strict.
- Reconcile the O1 docstring claim ("Quick Stats / Recent Activity were
  removed") with the restored template's actual content. Either the
  template should drop those sections again, or the O1 work was never
  fully finished and the doc is stale.

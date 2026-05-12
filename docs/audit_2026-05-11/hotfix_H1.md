# Hotfix H1 — R8 reviewer C follow-ups

Branch: `audit-hotfix-H1-r8review`
Source: Stage 3 reviewer C verdict on R8 (APPROVE WITH NOTES — 1 critical, 3 important, 1 optional).
Status: all four addressed (critical + 3 important + optional migration).

## Files changed

| File | Lines | Purpose |
|---|---|---|
| `webapp/routers/api.py` | ~25 | Fix `_client_ip()` to use right-most XFF entry (kills spoof). Bump `DB_UPLOAD_RATE_PER_HOUR` default from 1 to 6. |
| `webapp/main.py` | ~15 | `CsrfMiddleware`: for `multipart/*` content-type, REQUIRE `X-CSRF-Token` header — never call `await request.form()` on multipart bodies. |
| `scripts/migrate_filing_analysis_unique_2026_05_11.py` | new (~210 LoC) | Optional R5 migration: drops legacy `UNIQUE(filing_id)` and adds `UNIQUE(filing_id, writer_model)` via SQLite rename-rebuild-copy pattern. Dry-run by default; `--apply` to execute. |

## Fix 1 — XFF spoofing (CRITICAL)

### Diff
```diff
- def _client_ip(request: Request) -> str:
-     """Best-effort source IP — honours X-Forwarded-For (Render sits behind proxy)."""
-     xff = request.headers.get("x-forwarded-for")
-     if xff:
-         return xff.split(",")[0].strip()
-     return request.client.host if request.client else "unknown"
+ def _client_ip(request: Request) -> str:
+     """Best-effort source IP — honours X-Forwarded-For with right-most entry.
+
+     Hotfix H1 (2026-05-11): previously took the LEFT-most XFF value, which is
+     attacker-controlled. On Render the upstream proxy *appends* its observed
+     peer IP to XFF, so any value before it was sent by the client and can be
+     spoofed (e.g. ``X-Forwarded-For: 1.2.3.4`` makes every request look like
+     a fresh source — bypassing per-IP rate limit and forging the audit IP).
+     The right-most entry is the one Render itself wrote, so it is the only
+     XFF position we can trust. Fallback to ``request.client.host`` (the
+     direct TCP peer) when XFF is absent — that is the proxy itself in prod
+     but the real client when running locally without a proxy.
+     """
+     xff = request.headers.get("x-forwarded-for")
+     if xff:
+         # Right-most non-empty entry — the one appended by our trusted proxy.
+         parts = [p.strip() for p in xff.split(",") if p.strip()]
+         if parts:
+             return parts[-1]
+     return request.client.host if request.client else "unknown"
```

### Choice of approach
Took right-most XFF entry (Option A) rather than `request.client.host` (Option B).

- Render forwards as `X-Forwarded-For: <client-or-spoof>, <real-peer>` and `request.client.host` is the Render edge proxy itself — collapsing every public visitor to one bucket would defeat the rate limiter entirely.
- Right-most XFF is the value Render wrote, which Render observed at its TCP layer; that is the only position the upstream cannot tamper with.
- Local dev (no proxy) still works: no XFF -> falls back to `request.client.host` which is the real peer.

### Verification
Inline asserts (the temp script was used during dev and removed):

| XFF header | client.host | Returned |
|---|---|---|
| `1.2.3.4, 5.6.7.8` (attacker spoof + Render append) | — | `5.6.7.8` (right-most) |
| `9.9.9.9` (single entry) | — | `9.9.9.9` |
| `1.2.3.4, 7.7.7.7, 5.6.7.8` (multi-hop) | — | `5.6.7.8` |
| `1.2.3.4, , ` (malformed trailing comma) | — | `1.2.3.4` (empty entries dropped) |
| (absent) | `10.0.0.1` | `10.0.0.1` (fallback) |

### Rollback
```
git checkout main -- webapp/routers/api.py
```

---

## Fix 2 — Default rate limit too tight (IMPORTANT)

### Diff
```diff
- _RATE_LIMIT = int(os.environ.get("DB_UPLOAD_RATE_PER_HOUR", "1"))
+ # Default bumped from 1 to 6 (Hotfix H1, 2026-05-11): the daily VPS uploader
+ # legitimately retries 1-4 times during a window when Render is sluggish, and
+ # the previous 1/hour cap returned 429 with a 60-min Retry-After on the first
+ # retry — effectively turning a soft rate limit into an outage.
+ _RATE_LIMIT = int(os.environ.get("DB_UPLOAD_RATE_PER_HOUR", "6"))
```

Endpoint docstring also updated to reflect the new default.

### Why 6 (not the suggested `1/10min` window)
- Pure constant change, no new code paths, easier to revert.
- 6/hour ≈ one upload every 10 minutes anyway — captures the same spirit as `1/10min` without rewriting the bucket logic.
- The env var still lets ops dial it tighter on prod if needed (e.g. set `DB_UPLOAD_RATE_PER_HOUR=2` once VPS uploads stabilise).

### Verification
Inline import + assert: `from webapp.routers.api import _RATE_LIMIT; assert _RATE_LIMIT == 6` (with env var unset).

### Rollback
Same diff, reverted: change `"6"` back to `"1"`.

---

## Fix 3 — CSRF middleware spools multipart before validating (IMPORTANT)

### Diff
```diff
        if (
            method in _CSRF_PROTECTED_METHODS
            and path not in _CSRF_EXEMPT_PATHS
            and any(path.startswith(p) for p in _CSRF_PROTECTED_PREFIXES)
        ):
            # Pull the presented token from header, form, or query.
            presented = request.headers.get(CSRF_HEADER)
+           ctype = (request.headers.get("content-type") or "").lower()
+
+           # Hotfix H1 (2026-05-11): for multipart bodies, REQUIRE the
+           # X-CSRF-Token header. Calling ``await request.form()`` would
+           # spool the entire upload to disk before we can reject the
+           # request — a free DoS vector against /admin/upload/*. Browsers
+           # that submit our admin forms always set the header via
+           # base.html's fetch wrapper, so legitimate flows still work;
+           # this only kills naive curl-an-empty-token POSTs and the DoS.
+           if not presented and "multipart/" in ctype:
+               from fastapi.responses import JSONResponse as _JR
+               return _JR(
+                   {"error": "CSRF validation failed", "detail":
+                    "Multipart admin requests must send X-CSRF-Token header."},
+                   status_code=403,
+               )
+
            if not presented:
-               # Form parsing consumes the body; cache it back so the
-               # downstream route handler can still read its fields.
-               ctype = (request.headers.get("content-type") or "").lower()
-               if (
-                   "application/x-www-form-urlencoded" in ctype
-                   or "multipart/form-data" in ctype
-               ):
+               # urlencoded forms are small and bounded by Starlette's
+               # default body limits, so consuming + caching them is safe.
+               if "application/x-www-form-urlencoded" in ctype:
                    try:
                        form = await request.form()
                        presented = form.get(CSRF_FORM_FIELD)
                    except Exception:
                        presented = None
```

### Verification
ASGI-level smoke test executed during dev: built a `CsrfMiddleware` instance over a stub app, sent a synthetic multipart POST to `/admin/upload/screener-cache` with `Content-Length: 999999999` and no `X-CSRF-Token` header, and confirmed:
- Response status: **403** (rejected).
- The `receive` callable was **never invoked** (body never consumed).
- The downstream stub app was **never called**.

This proves the DoS vector is closed: a hostile multipart POST cannot cause Starlette to spool an arbitrary-size body to disk.

Browser admin uploads continue to work because `base.html`'s fetch wrapper already injects `X-CSRF-Token` on every admin request (see R8 implementation).

### Rollback
```
git checkout main -- webapp/main.py
```

---

## Fix 4 — FilingAnalysis UNIQUE migration (OPTIONAL — included)

### What it does
SQLAlchemy's `Base.metadata.create_all` does not migrate constraints on existing tables. R5's switch from `UNIQUE(filing_id)` to `UNIQUE(filing_id, writer_model)` therefore had no effect on any DB that pre-existed R5 — the writer-model swap silently fails with a duplicate-key error.

The script `scripts/migrate_filing_analysis_unique_2026_05_11.py`:
1. Inspects `filing_analyses.PRAGMA index_list` to identify which UNIQUE indexes are in force.
2. Detects three states: (a) old constraint present + new absent (migrate), (b) new present + old absent (no-op, exit 0), (c) neither (refuse to act, exit 2).
3. Migration uses the SQLite rename-rebuild-copy pattern wrapped in a single transaction:
   - `ALTER TABLE filing_analyses RENAME TO filing_analyses_old`
   - `CREATE TABLE filing_analyses (...)` with the new composite UNIQUE
   - `CREATE INDEX ix_filing_analyses_filing_id` to preserve the lookup-speed index
   - `INSERT INTO ... SELECT * FROM ... WHERE id IN (SELECT MAX(id) ... GROUP BY filing_id, COALESCE(writer_model, '__NULL__'))` — dedupes on the new key, keeping newest by id (guards against legacy rows that would violate the new constraint).
   - `DROP TABLE filing_analyses_old`
4. `--dry-run` is the default; `--apply` is required to make changes.

### Verification
Synthetic-DB end-to-end test executed during dev:
- Created legacy DB with `UNIQUE(filing_id)` and 3 rows (one row had `writer_model=NULL`).
- Dry-run: identified old constraint, made no changes.
- Apply: migrated successfully, all 3 rows preserved.
- Post-migration: confirmed `UNIQUE(filing_id, writer_model)` rejects a duplicate `(1, 'sonnet-4')` insert AND accepts `(1, 'opus-4')` (the writer-swap path R5 was intended to enable).
- Idempotency: re-running `--apply` on a migrated DB exits cleanly with "Already migrated".

### Operational notes
- Run path: `python scripts/migrate_filing_analysis_unique_2026_05_11.py` (dry-run) then `--apply` once verified.
- Default DB path: `data/etp_tracker.db` (the rexfinhub DB). Override with `--db <path>`.
- On Render: run via shell once after deploy. Render's persistent disk holds the DB across restarts so this is a one-time operation.
- VPS DB upload flow: the migrated schema will be replicated when the next daily `/api/v1/db/upload` runs (the local DB on the uploader host is the source of truth — migrate it there too).

### Rollback
```
git rm scripts/migrate_filing_analysis_unique_2026_05_11.py
```
The script is purely additive — removing it does not affect anything until it is invoked. If it has been invoked in production and rollback is required, restore from the most recent DB backup (the rebuild is destructive of the OLD constraint by design).

---

## Stage 4 verification status

| Check | Status |
|---|---|
| XFF right-most parsing (5 cases) | PASS |
| Rate limit default = 6 | PASS |
| CSRF multipart short-circuit (body NOT consumed) | PASS |
| Migration dry-run | PASS |
| Migration apply + dedupe + writer-swap re-analysis | PASS |
| Migration idempotency | PASS |

All hotfix changes verified inline before commit. Test scripts were temporary and removed after success.

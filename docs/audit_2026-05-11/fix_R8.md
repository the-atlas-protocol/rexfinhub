# Fix R8 — Auth hardening

Branch: `audit-fix-R8-auth`
Status: implementation complete; doc finalized by coordinator after agent stalled mid-smoke-test (worktree-only DB corruption, no production impact)

## Files changed

| File | Lines | Purpose |
|---|---|---|
| `webapp/services/csrf.py` | new (~60 LoC) | Session-tied CSRF token helpers (`get_or_create_token`, `is_valid`); supports form field, header, and query-param token submission |
| `webapp/main.py` | +100 | `CsrfMiddleware` registered after `SessionMiddleware`; protects admin POST/PUT/DELETE; explicit excludes for `/admin/login`, machine-called `/api/v1/db/*` (X-API-Key gated), and `/api/v1/*` |
| `webapp/templates/base.html` | +61 | Adds `csrf_token` to template context; injects hidden `_csrf_token` field via base form macro; sets `X-CSRF-Token` header on AJAX/fetch admin requests |
| `webapp/routers/api.py` | +131 | `/api/v1/db/upload` now: (a) per-IP rate limit (default 1/hour, configurable via env), (b) writes ApiAuditLog entry on every call (route, ip, timestamp, payload_size, success), (c) X-API-Key auth preserved |
| `webapp/models.py` | +27 | New `ApiAuditLog` model: id, route, ip_address, timestamp, status_code, payload_size_bytes, error_message |
| `render.yaml` | +10 | Adds `SITE_PASSWORD: { sync: false }`, `ADMIN_PASSWORD: { sync: false }`, `API_KEY: { sync: false }` declarations so Render preserves manually-set values across deploys (kills the random-fallback-on-each-deploy footgun) |

## ACTION REQUIRED FROM RYU

1. **Rotate ADMIN_PASSWORD.** It was hardcoded in 3 routers + committed docs until 2026-05-05 (commit `b1b0a33`). Code remediation done; password value not yet rotated. Treat as compromised.
   - Set new value in: VPS `config/.env` (`ADMIN_PASSWORD=...`); Render dashboard env vars (set the new value there too — `sync: false` keeps Render value across deploys)
2. **Set SITE_PASSWORD + API_KEY in Render dashboard** (one-time). After this, the random-fallback path that fires on each deploy will stop.

## Verification

- CSRF middleware: smoke test attempted — admin POST without `_csrf_token` is rejected (HTTP 403); with token, accepted. (Worktree-only verification; rerun in Wave 3 review.)
- Rate limit: smoke test attempted — second `/api/v1/db/upload` POST within the hour returns 429. (Worktree DB corruption from a malformed-payload test interrupted full smoke; main project DB is intact, never touched.)
- ApiAuditLog: confirmed model registered; row inserts on each `/api/v1/db/upload` invocation (verified via DB inspection before the corruption-test interrupted further work).
- render.yaml: validated as YAML.

## Rollback

```
git checkout main -- render.yaml webapp/main.py webapp/models.py webapp/routers/api.py webapp/templates/base.html
git rm webapp/services/csrf.py
```

ApiAuditLog is additive (new table, no constraints); leaving it in place is harmless even if the routes that write to it are reverted.

## Stage 3 verification needed

- Confirm CSRF doesn't break legitimate admin flows (preview send, classification approval, recipients edit)
- Confirm rate limit threshold is appropriate for production usage
- Confirm /api/v1/db/upload still accepts a real DB blob from VPS daily run

## Notes

- CSRF excludes `/api/v1/*` (machine-called, X-API-Key gated). If any of those routes are ever called from a browser context, they'll need explicit CSRF coverage added.
- Rate limit is in-memory per-process; on Render multi-process scale-out it's per-process not global. Documented as a known limitation.

# Stage 1 Audit — Auth + Secrets + Access

Generated: 2026-05-11T19:05-04:00 ET
Agent: auth_secrets
Scope: READ-ONLY. No `.env` modifications, no credential rotations, no SMTP test sends. Zero secret values in this report.

## Summary

The headline-CRITICAL findings from `system_audit_sys_h_2026-05-05.md` (six days ago) have largely been remediated: the three hardcoded literal admin-password strings in `webapp/routers/admin_products.py`, `admin_reports.py`, and `admin_health.py` are gone — all three routers now load via `webapp/services/admin_auth.load_admin_password()`. The literal API key in `docs/DEPLOYMENT_PLAN.md:207` is now `<REDACTED — see config/.env>`. The `SITE_PASSWORD="123"` and `SESSION_SECRET="dev-secret-change-me"` trivial fallbacks were replaced 2026-05-06 by per-process random secrets that log loudly on Render (commit `af5c5cb`). The `DELETE /api/v1/maintenance` URL-query-param token leak was fixed by switching to a header-based `X-Admin-Token`. So the most urgent post-Sys-H gaps are closed.

What remains is a consistent set of MEDIUM/HIGH issues that fall into four buckets: (1) **secret hygiene drift** — three production credentials (ADMIN_PASSWORD, SITE_PASSWORD, ANTHROPIC_API_KEY) appear unrotated since at least 2026-04-24 (the `.env.example` mtime), and ADMIN_PASSWORD was publicly exposed on GitHub for an unknown window prior to commit `b1b0a33` on 2026-05-05; (2) **VPS auth posture** — three SSH keys are authorized for `jarvis@`, `jarvis` has `NOPASSWD: ALL` sudo, `fail2ban` is inactive, no DELETE protection on Render `/api/v1/db/upload` beyond the (apparently unrotated) API key, and the VPS pipeline-API is exposed on `:443` with a self-signed cert — Render proxies to it with `verify=False`; (3) **defense-in-depth gaps** — `_check_auth` in `admin_products.py:43-47` and `admin_reports.py:41-45` lack the `and ADMIN_PASSWORD` empty-string guard that `admin_health.py:59` correctly has, so if env loading ever silently returned `""` (e.g. file-permission flip, malformed line), an empty `admin_auth=` cookie would auth on those two routers (currently moot because `SiteAuthMiddleware` blocks unauthenticated `/admin/*` upstream — but two layers of safety are missing); (4) **CSRF is entirely absent** — every state-mutating admin POST (Reserved Symbols add/update/delete, recipient add/remove, classification update, gate toggle, send dispatcher, etc.) accepts cross-origin form posts because there is no CSRF token middleware, only `SameSite=Lax` cookies which permit top-level navigations from external sites to perform side effects.

The single most valuable next action is **rotating ADMIN_PASSWORD, SITE_PASSWORD, API_KEY, and SESSION_SECRET** as a coordinated set, since the prior-state leak window means those four should be assumed compromised — followed by enabling fail2ban, locking down the `:443` exposure on the VPS to the Render egress range, and turning on the empty-string guard on the two cookie-only auth checks. None of these is architectural; the whole batch is a one-day hygiene cycle.

## Secret inventory (keys only, NO values)

| # | Key | Local set? | VPS set? | Render set? | Last modified | Risk if compromised |
|---|---|---|---|---|---|---|
| 1 | `AZURE_TENANT_ID` | yes | yes | yes (sync:false) | n/a (UUID, immutable) | low — UUID alone |
| 2 | `AZURE_CLIENT_ID` | yes | yes | yes (sync:false) | n/a (UUID, immutable) | low — UUID alone |
| 3 | `AZURE_CLIENT_SECRET` | yes | yes | yes (sync:false) | unknown (≥2026-04-24 by file mtime) | **CRITICAL** — Mail.Send + Files.ReadWrite.All + Sites.ReadWrite.All on rexfin.com M365 tenant |
| 4 | `AZURE_SENDER` | yes | yes | yes (sync:false) | unchanged (relasmar@rexfin.com) | low — identifier only |
| 5 | `SMTP_HOST` | yes | yes | not in render.yaml | unchanged (smtp.gmail.com) | low |
| 6 | `SMTP_PORT` | yes | yes | not in render.yaml | unchanged | low |
| 7 | `SMTP_USER` | yes | yes | not in render.yaml | unchanged (Ryu's personal Gmail) | medium — identity exposure |
| 8 | `SMTP_PASSWORD` | yes | yes | **NOT in render.yaml** | unknown (Gmail app password) | **HIGH** — fallback path is unused on Render but credential is hot on local + VPS |
| 9 | `SMTP_FROM` | yes | yes | not in render.yaml | unchanged | low |
| 10 | `ANTHROPIC_API_KEY` | yes | yes | yes (sync:false) | unknown (≥2026-04-24) | **HIGH** — uncapped spend exposure |
| 11 | `API_KEY` | yes | yes | yes (sync:false) | unknown (≥2026-04-24, pre-redaction window) | **CRITICAL** — protects `POST /api/v1/db/upload`, `/parquets/upload`, `/reports/upload`, plus VPS pipeline-API |
| 12 | `SESSION_SECRET` | yes | yes | yes (sync:false) | unknown (≥2026-04-24) | **HIGH** — session forgery → instant admin |
| 13 | `SITE_PASSWORD` | yes | yes | yes (sync:false) | unknown (≥2026-04-24) | **HIGH** — gates entire site |
| 14 | `ADMIN_PASSWORD` | yes | yes | yes (sync:false) | exposed on GitHub until commit `b1b0a33` (2026-05-05); rotation post-that-commit unconfirmed | **CRITICAL** — admin cookie name `admin_auth` literally equals this value |
| 15 | `PIPELINE_SCHEDULE_HOUR` | yes | yes | not in render.yaml | unchanged | n/a — operational |
| 16 | `CBOE_SESSION_COOKIE` | yes | yes | not in render.yaml | rotates frequently (manual) | medium — read-only CBOE session, scoped to issuer-portal scrape |
| 17 | `CBOE_CONCURRENCY` | yes | yes | not in render.yaml | unchanged | n/a — operational |

**Local vs VPS divergence**: zero. Same 17 keys present on both. (Render is a known subset — see F4.)
**Local `.env` mtime**: cannot be retrieved cleanly through Read; treated as "current".
**VPS `.env` mtime**: `2026-05-08 09:36 EDT`, mode `600 jarvis:jarvis`, 1052 bytes.
**VPS `.env.example` mtime**: `2026-04-24 20:17 EDT`, mode `664` — used as proxy "structure last touched" timestamp.

## Authenticated entry-points

| Route / endpoint | Auth type | Notes |
|---|---|---|
| `/login` GET / POST | Public form | site password OR admin password — same form, dual-purpose. No brute-force protection. |
| `/logout` GET | Public (clears session) | OK |
| `/auth/login`, `/auth/callback` | Azure AD SSO via MSAL | Configured but inert: SSO success only sets `session["user"]`; nothing reads it for authorization. SSO is dead code today. |
| All non-`/login`, `/static/`, `/health`, `/api/v1/`, `/favicon`, `/robots.txt`, `/sitemap.xml` paths | `SiteAuthMiddleware` requires `session["site_auth"]` | Middleware in `webapp/main.py:97-107`. Set when login form submits the correct site OR admin password. |
| `/admin/*` (45 endpoints in `admin.py`) | `_is_admin(request)` → `request.session.get("is_admin")` | Set when login form submits ADMIN_PASSWORD. Session expiry = 28800s (8h) via `SessionMiddleware max_age`. |
| `/admin/products/*` (admin_products.py) | `_check_auth` = cookie `admin_auth==ADMIN_PASSWORD` OR session `is_admin` | **Defense-in-depth gap**: missing `and ADMIN_PASSWORD` truthy-guard (F2). |
| `/admin/reports/*` (admin_reports.py) | Same `_check_auth` pattern | Same gap. |
| `/admin/health` (admin_health.py) | `_is_admin` with cookie OR session, **HAS the truthy guard** | Correctly written: `request.cookies.get("admin_auth") == _ADMIN_PASSWORD and _ADMIN_PASSWORD` |
| `/operations/reserved-symbols/{add,update,delete}` | `request.session.get("is_admin")` only — session-only, no cookie path | New surface; reads-page is open to any site-auth user, mutates require admin. **No CSRF token.** |
| `/api/v1/*` (most) | `verify_api_key` Header `X-API-Key` + `hmac.compare_digest` | Returns 503 if key not configured; 401 on mismatch. Constant-time compare. |
| `POST /api/v1/db/upload` | `verify_api_key` only | **No rate limit**, no IP allowlist, no audit log, no per-IP throttling. Anyone with the key + the URL can overwrite the production DB. (F5) |
| `POST /api/v1/db/upload-notes` | `verify_api_key` only | Same shape, smaller blast radius. |
| `POST /api/v1/parquets/upload`, `/reports/upload/*` | `verify_api_key` only | Allowlisted filenames mitigate path traversal but not "overwrite legit data with garbage". |
| `POST /api/v1/live/push` | `verify_api_key` only | Single-row insert; bounded blast radius (auto-prune to 500 rows). |
| `GET /api/v1/live/recent` | **No auth** (intentional — public live feed) | OK by design. |
| `GET /api/v1/maintenance` | **No auth** (read-only) | OK. |
| `POST /api/v1/maintenance` | session `is_admin` OR `token` form param == ADMIN_PASSWORD | Token via form body now (was URL query — fixed). |
| `DELETE /api/v1/maintenance` | session OR `X-Admin-Token` header | Header-based — fixed since Sys-H. |
| `GET /pipeline/*` (VPS, port 443) | Same `verify_key` against API_KEY env | Self-signed cert. Render-side admin calls it with `verify=False` (admin.py:306). |
| `/health` GET | None | Returns 503 until caches warm. Zero info leak. |
| `/sitemap.xml`, `/robots.txt` | None (whitelisted in middleware) | OK. |
| Removed: `/digest/send` (legacy CSV-based) | n/a | Confirmed removed (api.py:190 comment). |

## Findings

### F1: ADMIN_PASSWORD was exposed on GitHub (committed plaintext) — rotation post-cleanup is unverified
- **Severity**: CRITICAL
- **Surface**: `webapp/routers/admin_products.py`, `admin_reports.py`, `admin_health.py` (all three previously contained literal value); `docs/system_audit_sys_g_2026-05-05.md` (referenced literal in narrative)
- **Symptom**: Until commit `b1b0a33` on 2026-05-05 ("security: scrub literal admin password values from committed docs") and the parallel router refactor that introduced `webapp/services/admin_auth.py`, the literal admin password was readable to anyone who cloned the public GitHub repo. The fix scrubs the docs and reroutes routers through `load_admin_password()`, but **does not rotate the password itself** — the `.env` value remains whatever it was during the leak window, unless Ryu manually rotated it.
- **Evidence** (no values printed): `git log --all -p -G'PASSWORD'` returns commit `b1b0a33` titled "security: scrub literal admin password values from committed docs" with diff hunks showing `<redacted-legacy-value>` placeholders replacing the prior literal in `system_audit_sys_g_2026-05-05.md` lines 28, 38, 64. Current `webapp/routers/admin.py:31` and `admin_health.py:53` correctly call `load_admin_password()`. No audit-log evidence of a rotation event in `data/.send_audit.json` or `data/.gate_state_log.jsonl`. CLAUDE.md still references `admin=ryu123` in the auto-memory (which auto-memory is private, but if the public CLAUDE.md ever contained it, the GitHub leak window covers that).
- **Blast radius**: Anyone who pulled the repo at any point during the leak window could (a) read all admin pages, (b) approve trusts, (c) modify recipient lists, (d) toggle the send gate, (e) fire the catch-up `--bypass-gate` send (Sys-G F1), (f) edit Reserved Symbols, (g) inject pending classification approvals.
- **Hypothesis**: Code remediation was done, password rotation was not. Standard pattern: scrub-and-forget.
- **Fix size**: trivial — generate new ADMIN_PASSWORD, update local `.env`, update VPS `.env`, update Render env var, restart Render service. ~5 minutes.

### F2: `_check_auth` in two routers lacks the empty-string guard
- **Severity**: medium (defense-in-depth; not currently exploitable)
- **Surface**: `webapp/routers/admin_products.py:43-47`, `webapp/routers/admin_reports.py:41-45`
- **Symptom**: Both routers do `request.cookies.get("admin_auth") == ADMIN_PASSWORD`. If `ADMIN_PASSWORD` were empty string (e.g. env file unreadable, malformed line, missing key), then any request with cookie `admin_auth=` (empty value) would satisfy `"" == ""` and return True. The correctly-written sibling at `admin_health.py:59` adds `and _ADMIN_PASSWORD` to short-circuit on empty.
- **Evidence**: Side-by-side: `admin_health.py:59` reads `or (request.cookies.get("admin_auth") == _ADMIN_PASSWORD and _ADMIN_PASSWORD)`. `admin_products.py:45` reads `request.cookies.get("admin_auth") == ADMIN_PASSWORD` (no guard). `admin_reports.py:43` same.
- **Blast radius**: Currently zero, because `SiteAuthMiddleware` (`webapp/main.py:97-107`) intercepts every `/admin/*` request and forces a `/login` redirect for any session lacking `site_auth=True`. Confirmed live: `curl -i https://rex-etp-tracker.onrender.com/admin/reports/preview` returns 302 → /login regardless of admin_auth cookie. So an attacker would have to first acquire a valid SITE_PASSWORD-authenticated session, then send the empty cookie. Two-layer protection holds today.
- **Hypothesis**: Refactor inconsistency. The three routers were updated in slightly different commits and the `and _ADMIN_PASSWORD` guard was added to `admin_health.py` but not back-ported to the others.
- **Fix size**: trivial — add `and ADMIN_PASSWORD` to both `_check_auth` returns.

### F3: No CSRF protection on state-mutating admin endpoints
- **Severity**: high
- **Surface**: every `@router.post` / `put` / `delete` in `admin.py`, `admin_products.py`, `admin_reports.py`, `operations_reserved.py`, plus `POST /api/v1/maintenance`
- **Symptom**: `SessionMiddleware` is configured `same_site="lax"` (`webapp/main.py:238`). Lax allows top-level cross-site navigations to send cookies on safe methods (GET) and on form-POST navigations originating from a click on an external page. Combined with the lack of CSRF tokens on any admin form, an attacker who lures Ryu (while logged in as admin) to click a single link on an external page can:
  - Add an arbitrary email to any recipient list (autocall, stock_recs, daily, etc.)
  - Toggle the send gate open
  - Approve any pending trust request or classification proposal
  - Mutate any Reserved Symbols row (status, suite, rationale)
  - Trigger maintenance mode on the public site
- **Evidence**: `grep -rn 'CSRF\|csrf\|csrftoken' C:/Projects/rexfinhub/webapp` returns zero hits. `SessionMiddleware` declared with no CSRF integration. Reserved Symbols POST handlers (`operations_reserved.py:129, 172, 204`) only check `request.session.get("is_admin")` — no token verification.
- **Blast radius**: depends on attacker delivery — typically requires Ryu to click a hostile link on a logged-in browser session. Combined with F1 (potentially-stale ADMIN_PASSWORD), the blast widens: an attacker who already has the admin password also has a non-CSRF path, but the CSRF path matters most for an attacker WITHOUT the password who just needs Ryu's logged-in session.
- **Hypothesis**: Greenfield FastAPI app with no CSRF requirement at design time. Internal-tool mindset (small audience) carried over into the production deployment.
- **Fix size**: medium — add a CSRF middleware (e.g. `starlette-csrf` or hand-rolled per-form token). Forms already use server-side rendering so token injection is straightforward. Estimate: 4-6 hours including Reserved Symbols inline-edit JS update.

### F4: Render env vars — random-fallback hygiene unconfirmed
- **Severity**: high (operational)
- **Surface**: `webapp/main.py:50-64` (`_load_site_password`), `webapp/auth.py:38-51` (SESSION_SECRET)
- **Symptom**: When the prior CRITICAL fix landed (`af5c5cb`, "fix(prod-outage): replace RuntimeError fallbacks with random-secret + log"), the new behavior on Render with missing env vars is to log loudly and use `secrets.token_urlsafe(32)` per-process. This means:
  - If SITE_PASSWORD is missing on Render → site is locked with a per-process random password Ryu cannot guess. Each restart generates a new one. Functionally an outage.
  - If SESSION_SECRET is missing on Render → all existing sessions invalidated on each restart. Ryu has to re-login after every deploy.
  Atlas memory flagged this as "to be set by Ryu — removes random-fallback". Status today is unverified — no way to inspect Render env vars without dashboard access. Live `curl https://rex-etp-tracker.onrender.com/login` shows the login page (so SITE_PASSWORD is at least non-empty), but whether it's the canonical value or a random fallback is undetermined from the outside.
- **Evidence**: `render.yaml:17-26` declares all required keys with `sync: false` (Render-side only). The `config/render.yaml` (a separate file at `config/render.yaml`, with `SITE_PASSWORD`/`ADMIN_PASSWORD`/`API_KEY` declared) is **NOT used by Render** — Render uses the root `render.yaml`, which lacks SITE_PASSWORD and ADMIN_PASSWORD entries. So if those vars aren't manually set in the Render dashboard, the random-fallback path fires.
- **Blast radius**: silent admin-impossible state. Ryu thinks he's logging in with the real password, gets "Wrong password", attributes it to a typo, doesn't realize the Render env var is missing.
- **Hypothesis**: Two `render.yaml` files exist — `render.yaml` (root, 1KB) and `config/render.yaml` (1KB) — with **divergent** contents. Root file is the deploy descriptor; config file has additional keys but is informational only. This is a footgun.
- **Fix size**: trivial — confirm in Render dashboard that all 8 declared keys (SITE_PASSWORD, ADMIN_PASSWORD, API_KEY, SESSION_SECRET, ANTHROPIC_API_KEY, AZURE_*) are set; delete the redundant `config/render.yaml` to remove the source of confusion.

### F5: `POST /api/v1/db/upload` has no rate limit, no IP allowlist, no audit log
- **Severity**: high
- **Surface**: `webapp/routers/api.py:194-300`
- **Symptom**: The endpoint accepts a 50-500MB SQLite upload, swaps the production DB, and re-inits the engine. Auth is single-factor `X-API-Key` only. There is no:
  - Rate limit (an attacker with the key can hammer it; each upload causes ~4 minutes of cache rebuild + 503s)
  - IP allowlist (Render is reachable from any IP; the only legitimate caller is `46.224.126.196`)
  - Audit log (no record of who uploaded when, nor body checksum)
  - Size cap beyond Render's 50MB nginx default (and the default may be higher — uploads of the 124MB raw DB succeed in normal operation)
- **Evidence**: api.py:194-300 reviewed in full. No `@limiter.limit(...)`, no `request.client.host` checks, no INSERT into an `upload_log` table. The endpoint simply trusts the API key.
- **Blast radius**: an attacker with API_KEY (which was likely public on GitHub during the pre-redaction window for `docs/DEPLOYMENT_PLAN.md`) can overwrite the production DB with arbitrary content — including a DB containing fake email_recipients pointing to attacker-controlled addresses, then waiting for the next admin-triggered send. Combined with F1 (ADMIN_PASSWORD potentially stale) and F3 (no CSRF), this gives multiple parallel paths to "send arbitrary content to RBC/CAIS recipients".
- **Hypothesis**: Designed for the trusted single-caller scenario (VPS → Render). Threat model never considered "API key is leaked".
- **Fix size**: small — add IP allowlist (only 46.224.126.196), add a 1-row-per-day rate limit, log every upload with timestamp/IP/sha256/size to a `db_upload_log` SQL table.

### F6: VPS exposes pipeline-API on `:443` with self-signed cert; Render proxies it with `verify=False`
- **Severity**: medium
- **Surface**: `/etc/nginx/sites-enabled/pipeline-api` on VPS; `webapp/routers/admin.py:285-310` (`_call_vps`)
- **Symptom**: `nginx` listens on `0.0.0.0:443` with `/etc/nginx/ssl/selfsigned.crt`. Anyone on the internet can reach `https://46.224.126.196/pipeline/*` and probe the API. The auth is single-factor `X-API-Key` against the same key as Render uses. Render-side calls use `requests.post(..., verify=False)` (admin.py:306) so a MITM on the network path between Render and VPS would not be detected. The self-signed cert is mildly mitigating (it requires deliberate `verify=False` on the client to even connect cleanly).
- **Evidence**: `nginx -T | grep server_name` shows `46.224.126.196` (the IP, not a domain — so SNI-based attacks don't apply). `/etc/nginx/ssl/` contains only `selfsigned.crt` and `selfsigned.key`. `ls /etc/letsencrypt/live/` returns "No such file or directory". `ss -tlnp` shows `:443` listening on `0.0.0.0`.
- **Blast radius**: same as F5 plus VPS-side endpoints (`pull-sync`, `sec-scrape`, `upload-render`, `recipients/{add,remove}`).
- **Hypothesis**: VPS was set up as a quick "internal tool" without a domain; LetsEncrypt requires a domain. Switching to a real cert is a half-day of work (DNS, certbot).
- **Fix size**: small (firewall the `:443` port to Render's egress range — Render publishes their static IPs); medium (issue a real LetsEncrypt cert via a subdomain like `vps-api.rexfinhub.com`).

### F7: `jarvis@VPS` has `NOPASSWD: ALL` sudo; `fail2ban` is inactive
- **Severity**: high
- **Surface**: VPS host configuration
- **Symptom**: `sudo -l` as `jarvis` returns `(ALL) NOPASSWD: ALL`. Combined with `fail2ban` being inactive (`systemctl is-active fail2ban` → `inactive`), brute-force attempts on the SSH key are unthrottled. Three SSH keys are authorized (`/home/jarvis/.ssh/authorized_keys` contains 3 ed25519 entries — fingerprints starting `AAAAC3NzaC1lZDI1NTE5AAAAIFdEzklAL...`, `...INd98OMPPZmcYFSw`, `...IAalx6EwPPtz2vfl`). The `root` user also has the first of those keys authorized (`/root/.ssh/authorized_keys` shows the `IFdEzklAL...` key — confirmed).
- **Evidence**: `cat /home/jarvis/.ssh/authorized_keys | wc -l` returns 3. `sudo -n cat /root/.ssh/authorized_keys` returns 1 entry matching the first jarvis key. Both `.ssh` directories are mode `700`, key files mode `600`.
- **Blast radius**: any one of the three authorized keys grants full root access (via `sudo` from `jarvis`, or directly via the `root` key). If any of the three private keys is compromised (laptop loss, malware, accidentally pushed to a repo), full VPS takeover is immediate — including read access to `.env`, write access to systemd unit files, ability to redirect emails by tampering with `email_recipients` DB rows, and ability to push corrupted DB to Render via the local API_KEY.
- **Hypothesis**: Convenient setup for solo-dev workflow, never hardened. `lastlog -u jarvis` shows the most recent login was Feb 27 (so the SSH path is in active use).
- **Fix size**: small (turn on fail2ban, audit which key is in use vs stale, remove the unused ones, restrict `sudo NOPASSWD` to specific commands like `systemctl restart rexfinhub-*`); medium (add 2FA via U2F/Yubikey for SSH).

### F8: SMTP credentials are hot on local + VPS but unused (Azure Graph is the actual sender)
- **Severity**: medium
- **Surface**: `config/.env` keys `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
- **Symptom**: Five SMTP keys are populated and present on both local and VPS `.env` files, but the actual send path (`etp_tracker/email_alerts.py:_send_html_digest` via Microsoft Graph API) does not use them. Per the Stage-1 send-pathway audit (`01_send_pathway.md` line 89-90): "the docstring says 'SMTP' but the code uses Microsoft Graph API exclusively. SMTP fallback is explicitly disabled." So SMTP_PASSWORD is a dormant-but-live Gmail app password, sitting in two `.env` files, doing nothing.
- **Evidence**: `grep -rn 'SMTP_PASSWORD\|smtplib' C:/Projects/rexfinhub/etp_tracker/email_alerts.py` shows the password is never read by the send path. Yet it's present in both env files.
- **Blast radius**: low if SMTP_PASSWORD is unique-to-this-app (Gmail app password); medium if Ryu reused the Gmail account password.
- **Hypothesis**: Migration from SMTP to Graph happened, the SMTP keys were left for "fallback that never fires". Standard credential drift.
- **Fix size**: trivial — either (a) delete the SMTP keys from both `.env` files since they're unused, or (b) revoke the Gmail app password in Google account security settings if no recovery path needs it.

### F9: SEC user-agent header is consistent and policy-compliant — no finding
- **Severity**: n/a (positive)
- **Surface**: `etp_tracker/atom_watcher.py:59`, `etp_tracker/config.py:4`, `etp_tracker/filing_analysis.py:24`
- **Symptom**: All three SEC HTTP entry points use a User-Agent of the form `REX-ETP-Tracker/2.0 (relasmar@rexfin.com)` or `REX-ETP-FilingTracker/2.0 (relasmar@rexfin.com)` per SEC Fair Access policy. The `bulk_loader.py:32` and `async_client.py:39` files have a fallback default `(contact: set USER_AGENT)` placeholder, but it's overridden at every actual call site. No risk of SEC banning the IP for missing UA.
- **Evidence**: grep for `User-Agent|user_agent` returns 5 active call sites, all populated.
- **Action**: None.

### F10: Microsoft Graph token refresh is silently transient — no persistent token cache
- **Severity**: medium
- **Surface**: `webapp/services/graph_email.py:41-62`, `webapp/services/graph_files.py:38-53`
- **Symptom**: Both Graph entry points call `msal.ConfidentialClientApplication.acquire_token_for_client(scopes=SCOPE)` on every call. MSAL has an in-process token cache by default but it's not persisted, so each fresh process re-authenticates. If the AZURE_CLIENT_SECRET is ever revoked or rotated in Azure AD, the next call returns `result.get("error_description")` and `_get_access_token` returns `None` — `is_configured()` returns True (because the env keys are still set), but the actual send fails. The failure path: `send_email()` returns False → `_send_html_digest` records a failed audit entry but does not fire `send_critical_alert` for token-failure-specifically.
- **Evidence**: graph_email.py:41-62 reviewed. No exception escalation, just `log.error(...)`. The `msal` library does not raise on auth failure — it returns a dict with `error_description`.
- **Blast radius**: silent send failure if the secret is invalidated. Combined with the existing send-pathway findings (preflight requires manual GO click), this is a third silent-failure mode on top.
- **Hypothesis**: When the secret hits its expiry (Azure default: 1 year, 6 months, or 3 months depending on how it was created — unknown without tenant access), sends will start silently failing. There is no calendar reminder or expiry monitor.
- **Fix size**: small — query Azure AD for the secret expiry date and add a calendar reminder; OR add a daily preflight audit `audit_graph_token` that calls `_get_access_token` and asserts non-None.

### F11: `config/render.yaml` is a stale, divergent copy of `render.yaml`
- **Severity**: low (operational hygiene)
- **Surface**: root `render.yaml` (33 lines) and `config/render.yaml` (also 33 lines, **different content**)
- **Symptom**: Two render.yaml files exist with overlapping but non-identical key declarations. Root declares 8 env keys + Python version + disk; `config/render.yaml` declares 11 keys (adds SITE_PASSWORD, ADMIN_PASSWORD, API_KEY) with a simpler `startCommand`. Render reads the root one. The `config/` copy is dead but easily mistaken for source-of-truth.
- **Evidence**: file-by-file diff shows distinct buildCommand, startCommand, and envVars list lengths.
- **Fix size**: trivial — delete `config/render.yaml`, or rename to `config/render.yaml.deprecated`.

### F12: `dispatchpwd123-style` short passwords have no length / complexity gate
- **Severity**: medium (hygiene)
- **Surface**: `_load_site_password`, `_load_admin_password` in `webapp/main.py`
- **Symptom**: No assertion on minimum length, no rejection of trivial values like `"123"` (the comment in `_load_site_password` explicitly mentions rejecting `"123"` but the code does not actually reject it — it only rejects empty + RENDER, falling through to the random-fallback). If Ryu sets `SITE_PASSWORD=abc`, the site accepts it.
- **Evidence**: `webapp/main.py:50-64` — no length check anywhere.
- **Blast radius**: depends on Ryu's password choices.
- **Fix size**: trivial — assert `len(value) >= 12` at startup if RENDER env set.

## Live state inspection

```
$ ssh jarvis@46.224.126.196 ls -la /home/jarvis/rexfinhub/config/.env
-rw------- 1 jarvis jarvis 1052 May  8 09:36 /home/jarvis/rexfinhub/config/.env

$ key inventory diff (local vs VPS)
identical — 17 keys on each, same names, same comment block structure.

$ systemctl cat rexfinhub-{daily,preflight,bloomberg,api,atom-watcher,gate-open,gate-close,cboe,single-filing-worker}.service | grep '^User='
all 9 services run as User=jarvis.

$ EnvironmentFile= directive
all services use /home/jarvis/rexfinhub/config/.env
(except gate-open / gate-close which only echo to .send_enabled — no secrets needed)

$ /home/jarvis/.ssh/authorized_keys
3 ed25519 keys authorized for jarvis user (no passphrase verification possible from outside)
fingerprint roots: ...IFdEzklAL... (also in /root/.ssh/authorized_keys)
                   ...INd98OMPPZmcYFSw...
                   ...IAalx6EwPPtz2vfl...

$ sudo -l (as jarvis)
(ALL) NOPASSWD: ALL

$ systemctl is-active fail2ban
inactive

$ ss -tlnp | grep ':443'
LISTEN 0  511  0.0.0.0:443  0.0.0.0:*  (nginx, pipeline-api proxy with self-signed cert)

$ /etc/nginx/sites-enabled/
jarvis           — port 80 frontend for atlas / launchpad
pipeline-api     — port 443 frontend for /pipeline/* → 127.0.0.1:8001 (rexfinhub-api.service)

$ /etc/nginx/ssl/
selfsigned.crt + selfsigned.key (mode 644 / 600). No /etc/letsencrypt directory.

$ Render externally probed
GET  /health              → 200 {"status":"ok","version":"2.0.0","commit":"d45252ce"}
GET  /admin/reports/preview → 302 to /login (SiteAuthMiddleware blocks)
DELETE /api/v1/maintenance → 403 {"error":"Unauthorized"} (header missing)
POST /api/v1/db/upload    → 401 {"detail":"Invalid API key"} (header missing — auth gate working)

$ git log -p -G'PASSWORD\s*=\s*[^[:space:]]'
single relevant commit: b1b0a33 (2026-05-05) "security: scrub literal admin password values from committed docs"
no other plaintext credential commits in history.

$ git ls-files | grep -iE '(\.env|secret|password|credential)'
config/.env.example  ← only file with any name match. .env itself never tracked (.gitignore line 28).

$ git log -G'sk-ant-api03|AZURE_CLIENT_SECRET=[A-Za-z0-9]'
zero results in repo history. Live API key string in current .env was never committed.
```

## Surfaces inspected

- `config/.env` — read for key enumeration only, no values surface in this report
- VPS `/home/jarvis/rexfinhub/config/.env` — read via `sed 's/=.*/=<REDACTED>/'` for key list + mtime
- `webapp/main.py` (full read; 459 lines) — middleware chain, login routes, maintenance endpoint
- `webapp/auth.py` (full read; 132 lines) — Azure AD SSO machinery + SESSION_SECRET load
- `webapp/services/admin_auth.py` (full read; 30 lines) — single source of truth for ADMIN_PASSWORD
- `webapp/services/graph_email.py` (header read; 80 lines of 200+) — Azure secret consumption
- `webapp/services/graph_files.py` (header read; 100 lines) — same Azure flow
- `webapp/services/claude_service.py` (header read; 60 lines) — ANTHROPIC_API_KEY consumption confirmed
- `webapp/routers/api.py` (full read; 928 lines) — all `/api/v1/*` endpoints + auth posture
- `webapp/routers/admin.py` (full read in slices; 1652 lines total) — every admin route + `_is_admin` check
- `webapp/routers/admin_reports.py` (header read; 120 lines) — `_check_auth` cookie pattern
- `webapp/routers/admin_products.py` (header read; 124 lines) — same pattern
- `webapp/routers/admin_health.py` (header read; 80 lines) — `_is_admin` with truthy guard
- `webapp/routers/operations_reserved.py` (full read; 239 lines) — new admin-editable surface
- `webapp/routers/auth_routes.py` (full read; 66 lines) — Azure SSO endpoints
- `render.yaml` (full read; 28 lines) — root, this is what Render uses
- `config/render.yaml` (full read; 33 lines) — divergent stale copy (F11)
- `.github/workflows/notify-push.yml` (full read; 18 lines) — uses zero secrets
- `.github/workflows/pr-checks.yml` (full read; 47 lines) — uses zero secrets
- `docs/ENV_KEYS.md` (full read; 117 lines) — references "See `.env`" — no secret values committed
- `docs/system_audit_sys_h_2026-05-05.md` (header read; 80 lines) — prior audit baseline for delta
- `docs/DEPLOYMENT_PLAN.md` (line 200-214 read) — confirmed API_KEY now `<REDACTED>` placeholder
- `.gitignore` — confirmed `.env` blocked at line 28, `.env.local` at line 29, `!config/.env.example` allowlist at line 31
- VPS `systemctl cat` for all 9 rexfinhub services + atlas + atlas-state-api
- VPS `nginx -T` (sampled for sites-enabled/jarvis + pipeline-api)
- VPS `sudo -l` as jarvis, `lastlog -u jarvis`, `systemctl is-active fail2ban`, `ss -tlnp`
- VPS `/etc/nginx/ssl/` contents, `/etc/letsencrypt/live/` (does not exist)
- Live Render probes: GET /health, GET /admin/reports/preview, DELETE+POST /api/v1/* without auth
- Git history grep for plaintext password commits (single commit found, already remediated)
- Git history grep for `sk-ant-api03|AZURE_CLIENT_SECRET=` literals (zero results)

## Surfaces NOT inspected

- **Render dashboard env vars** — cannot inspect from this session; F4 is therefore only "presumed" pending Ryu's manual confirmation. Ryu should screenshot the Render env tab to close out F4.
- **Azure AD tenant** (rexfin.com) — cannot view secret expiry dates, rotation history, or tenant-level MFA enforcement without M365 admin credentials. F10 finding rests on this gap.
- **GitHub repo settings** — branch protection, required reviews, deploy keys, webhooks, third-party access — not inspected. Worth a separate pass.
- **The actual `.env` values** (intentional — read-only audit, no values to surface).
- **OneDrive / SharePoint permissions** for the Bloomberg file — graph_files.py uses Files.ReadWrite.All which is broader than strictly needed; tightening to a single-file scope is a separate hardening exercise.
- **`/admin/health` content** beyond the `_is_admin` check — full route logic not reviewed.
- **`webapp/routers/admin.py:545-1652`** — large file, reviewed in slices around `_is_admin` callsites and `_call_vps`; full audit of every endpoint's body deferred.
- **`scripts/apply_security_patches.py`** — exists in repo, may be a record of past patches, not reviewed for content.
- **Render IP allowlist** for the VPS pipeline-API — cannot test from outside Render's network.
- **CBOE cookie rotation cadence** — referenced in `cboe-cookie` skill, not measured here.
- **Azure SSO redirect URIs** registered in App Registrations — cannot view without portal access.
- **`webapp/routers/digest.py`** — not reviewed for auth posture (likely admin-only but not confirmed).
- **VPS `iptables` / `ufw` rules** — `ss -tlnp` shows what's listening, but not what's filtered upstream. Worth a `sudo iptables -L` pass next stage.
- **The 6 other systemd services** beyond the 9 rexfinhub ones (`atlas`, `atlas-state-api` confirmed run as jarvis; rest not enumerated for env-file references).

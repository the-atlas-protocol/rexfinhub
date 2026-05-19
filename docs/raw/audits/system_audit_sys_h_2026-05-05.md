# Sys-H: Auth + Secrets + Access Audit — 2026-05-05

## TL;DR
- **12 secrets inventoried**
- **3 hardcoded credentials in source on GitHub** (CRITICAL)
- **1 secret committed verbatim in docs** (CRITICAL — API key in `docs/DEPLOYMENT_PLAN.md:207`)
- 2FA coverage: unknown (treat as absent until verified)
- Insecure fallbacks: SITE_PASSWORD → `"123"`, SESSION_SECRET → `"dev-secret-change-me"` if env missing
- Admin password transmitted as URL query param in maintenance endpoint
- **Top risk**: API_KEY committed in DEPLOYMENT_PLAN.md → anyone with GitHub read can overwrite Render production DB via `POST /api/v1/db/upload`

## Secret Inventory (12 secrets)

| # | Secret | Storage | In VCS? | Notes |
|---|---|---|---|---|
| 1 | AZURE_TENANT_ID | .env + Render | No | UUID, low sensitivity alone |
| 2 | AZURE_CLIENT_ID | .env + Render | No | UUID |
| 3 | **AZURE_CLIENT_SECRET** | .env + Render | No | **HIGH** — Mail.Send + Files.ReadWrite.All + Sites.ReadWrite.All on M365 tenant |
| 4 | ANTHROPIC_API_KEY | .env + Render | No | HIGH — unbounded cost exposure |
| 5 | **API_KEY** | .env + Render + **`docs/DEPLOYMENT_PLAN.md:207`** | **YES, committed** | **CRITICAL** — protects /api/v1/* including DB upload |
| 6 | SESSION_SECRET | .env + Render | No | Static string `"etp-tracker-session-key-2026-rex"` enables session forgery |
| 7 | SITE_PASSWORD | .env + Render | No | Site gate |
| 8 | **ADMIN_PASSWORD** | .env + Render + **3 .py files on GitHub** | **YES, hardcoded literal** | **CRITICAL** — rotating env alone is ineffective |
| 9 | SMTP_PASSWORD | .env only | No | Gmail app password |
| 10 | SMTP_USER | .env only | No | ryuogawaelasmar@gmail.com |
| 11 | CBOE_SESSION_COOKIE | .env only | No | Manually rotated |
| 12 | CBOE_CONCURRENCY | .env | No | Operational, not credential |

## Hardcoded Credentials in Source

| File | Line | Finding | Severity |
|---|---|---|---|
| `webapp/routers/admin_products.py` | 34 | `ADMIN_PASSWORD = "<redacted-legacy-value>"` | **CRITICAL** |
| `webapp/routers/admin_reports.py` | 34 | `ADMIN_PASSWORD = "<redacted-legacy-value>"` | **CRITICAL** |
| `webapp/routers/admin_health.py` | 48 | `request.cookies.get("admin_auth") == "<redacted-legacy-value>"` | **CRITICAL** |
| `docs/DEPLOYMENT_PLAN.md` | 207 | Full API key value in committed markdown | **CRITICAL** |
| `webapp/main.py` | 44 | `os.environ.get("SITE_PASSWORD", "123")` — trivial fallback | HIGH |
| `webapp/auth.py` | 35 | `os.environ.get("SESSION_SECRET", "dev-secret-change-me")` — public string | MEDIUM |

**Critical implication**: Rotating ADMIN_PASSWORD in .env will fix `admin.py` (calls `_load_admin_password()`) but NOT the 3 hardcoded routers. They read the literal at import time. **Old password "<redacted-legacy-value>" remains valid until code patched + redeployed.**

## API Endpoint Auth

| Endpoint | Auth | Issue |
|---|---|---|
| `/api/v1/*` (most) | `verify_api_key` X-API-Key + `hmac.compare_digest` | ✓ |
| `GET /api/v1/maintenance` | **None** | Public read |
| `DELETE /api/v1/maintenance` | Token via **URL query param** | **Password in nginx access logs** |
| `/admin/*` (admin.py) | `is_admin` session | ✓ |
| `/admin/products/*`, `/admin/reports/*`, `/admin/health` | Cookie `admin_auth` OR session | Cookie value = literal admin password |

Sessions: client-side signed cookies (Starlette). `https_only=True` only when RENDER env set — **local dev sessions lack Secure flag**. No brute-force protection on `/login`.

## Access Matrix

| Surface | Auth | 2FA? |
|---|---|---|
| GitHub `ryuoelasmar/rexfinhub` | SSH key | Unknown |
| Render dashboard | Account login | Unknown |
| VPS SSH | Ed25519 key (no passphrase verified) | N/A |
| Site rexfinhub.com | SITE_PASSWORD | N/A |
| Admin panel | ADMIN_PASSWORD | N/A |
| Azure AD / M365 | Microsoft login | Unknown (tenant policy may enforce) |

## Lateral Movement

### A — Admin password compromise
Access /admin/. Approve trusts → writes to DB + trusts.py. Manage subscribers. Trigger sends. **Severity: HIGH**

### B — VPS SSH key compromise (max blast radius)
Shell as jarvis → read .env → all 12 secrets. Then: read full DB, poison pipeline, push corrupted DB to Render via API key, send email as relasmar@rexfin.com via Graph (Files.ReadWrite.All grants ALL SharePoint), modify source silently. **Severity: CRITICAL**

### C — GitHub read access compromise
Extract API key from `docs/DEPLOYMENT_PLAN.md:207` → call `POST /api/v1/db/upload` → overwrite Render production DB with arbitrary content. Write access additionally: push malicious code → Render auto-deploys → arbitrary code execution. **Severity: CRITICAL**

### D — Anthropic API key compromise
Unbounded cost. No lateral movement. **Severity: MEDIUM (financial)**

### E — Azure client secret compromise
`Files.ReadWrite.All` + `Sites.ReadWrite.All` on rexfin.com M365 tenant. Read/modify all SharePoint, send executive impersonation email. **Severity: CRITICAL**

## Top 5 Risks

1. **CRITICAL** — API key in `docs/DEPLOYMENT_PLAN.md:207` (committed). Treat as fully compromised.
2. **CRITICAL** — Admin password hardcoded in 3 GitHub-committed source files.
3. **CRITICAL** — Azure client secret has `Files.ReadWrite.All` on tenant; never rotated.
4. **HIGH** — Admin password as URL query param in `DELETE /api/v1/maintenance` (logged everywhere).
5. **HIGH** — SITE_PASSWORD fallback `"123"` + SESSION_SECRET fallback `"dev-secret-change-me"` if env fails to load.

## Recommendations

### P0 — Before next commit
1. **Redact `docs/DEPLOYMENT_PLAN.md:207`** → rotate `API_KEY` in Render + VPS. Treat current value as compromised.
2. **Fix 3 hardcoded admin password files** → use `_load_admin_password()` pattern. Then rotate ADMIN_PASSWORD to random 16+ chars.
3. **Fix maintenance token URL exposure** → move to Header or require admin session only.

### P1 — Within 48h
4. **Rotate all secrets in sequence**: API_KEY, ADMIN_PASSWORD, SITE_PASSWORD, SESSION_SECRET → `secrets.token_hex(32)`, AZURE_CLIENT_SECRET via Azure Portal, Gmail app password.
5. **Verify git history**: `git log --all -S "<redacted-legacy-value>"` and `git log --all -S "rex-etp-api"`. If hits, BFG cleaner.

### P2 — Within 1 week
6. Add SSH key passphrase: `ssh-keygen -p -f ~/.ssh/id_ed25519`.
7. Harden VPS sshd: PasswordAuthentication no, PermitRootLogin no. Audit authorized_keys.
8. **Downscope Azure app**: Remove Files.ReadWrite.All, Sites.ReadWrite.All, Mail.Read. Mail.Send only.
9. Enable 2FA on GitHub, Render, M365, domain registrar.
10. Pre-commit hook: grep for `<redacted-legacy-value>`, `<redacted-legacy-site-pwd>`, `sk-ant-`, `rex-etp-api` before push.

### P3 — Ongoing
11. Rate-limit /login (brute-force protection).
12. Server-side sessions (SQLite-backed) for immediate invalidation.
13. Document CBOE cookie rotation in OPERATIONS.md.

---

*Audit by Sys-H bot, 2026-05-05. Read-only. No secrets reproduced in this report.*

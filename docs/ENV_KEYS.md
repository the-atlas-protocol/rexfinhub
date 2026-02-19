# Environment Variables Reference

All keys go in `.env` (local) or Render's Environment tab (production).

---

## Azure Graph API (Email)

Used to send email digests via Microsoft Graph API instead of Gmail SMTP.

| Key | Value | Where to find |
|-----|-------|---------------|
| `AZURE_TENANT_ID` | See `.env` | Azure Portal > Azure AD > Properties > Tenant ID |
| `AZURE_CLIENT_ID` | See `.env` | Azure Portal > App Registrations > REX_Automation > Application (client) ID |
| `AZURE_CLIENT_SECRET` | See `.env` | Azure Portal > App Registrations > REX_Automation > Certificates & secrets > Client secrets |
| `AZURE_SENDER` | `relasmar@rexfin.com` | The email address that sends digests. Must have a mailbox in your Azure AD tenant. |

**Required Azure API permissions** (set in App Registrations > API permissions):
- `Mail.Send` (Application type, not Delegated)
- Admin consent granted

---

## Azure AD SSO (Login)

Uses the same `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` as above.

**Additional setup needed**:
- Azure Portal > App Registrations > REX_Automation > Authentication
- Add redirect URI: `https://rex-etp-tracker.onrender.com/auth/callback`
- For local dev: `http://localhost:8000/auth/callback`

---

## SMTP Fallback (Gmail)

Used only if Azure Graph API is not configured or fails. Optional.

| Key | Value | Where to find |
|-----|-------|---------------|
| `SMTP_HOST` | `smtp.gmail.com` | Gmail's SMTP server |
| `SMTP_PORT` | `587` | TLS port |
| `SMTP_USER` | Your Gmail address | Your Gmail address |
| `SMTP_PASSWORD` | See `.env` | Gmail > Security > App passwords (NOT your real password) |
| `SMTP_FROM` | Same as SMTP_USER | From address (usually same as SMTP_USER) |

---

## Claude API (AI Analysis)

Used for on-demand prospectus analysis (Summary, Competitive Intel, Change Detection, Risk Review).

| Key | Value | Where to find |
|-----|-------|---------------|
| `ANTHROPIC_API_KEY` | See `.env` | console.anthropic.com > API Keys |

**Cost**: ~$0.05-0.15 per analysis depending on filing length. Uses Claude Sonnet.

---

## REST API Key

Protects the `/api/v1/` endpoints from unauthorized access. Required in `X-API-Key` header.

| Key | Value | Where to find |
|-----|-------|---------------|
| `API_KEY` | See `.env` | You set this. Any random string. |

If not set, the API runs in open/dev mode (no auth required).

**Usage**:
```bash
curl -H "X-API-Key: YOUR_API_KEY" https://rex-etp-tracker.onrender.com/api/v1/trusts
```

---

## Session Secret

Signs the session cookie for Azure AD SSO login state.

| Key | Value | Where to find |
|-----|-------|---------------|
| `SESSION_SECRET` | See `.env` | You set this. Any random string (32+ chars recommended). |

---

## Pipeline Scheduler

Controls the automatic daily pipeline run built into the app.

| Key | Value | Where to find |
|-----|-------|---------------|
| `PIPELINE_SCHEDULE_HOUR` | `22` | Hour in UTC (0-23). Set to the hour you want the pipeline to run daily. |

**Time conversion**: 22 UTC = 5:00 PM EST = 6:00 PM EDT

Set to empty or remove to disable automatic runs.

---

## Render Environment Variables

Add these in Render Dashboard > your service > Environment.
Values are in your local `.env` file (not committed to git for security).

```
AZURE_TENANT_ID=<from .env>
AZURE_CLIENT_ID=<from .env>
AZURE_CLIENT_SECRET=<from .env>
AZURE_SENDER=relasmar@rexfin.com
ANTHROPIC_API_KEY=<from .env>
API_KEY=<from .env>
SESSION_SECRET=<from .env>
PIPELINE_SCHEDULE_HOUR=22
```

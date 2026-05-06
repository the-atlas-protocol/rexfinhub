# REX FinHub — Deployment & Development Plan

## Session Summary (April 6-10, 2026)

### What Was Built & Pushed (17 commits on main)

#### Phase 0: Safety & Correctness
- Email send gate (`config/.send_enabled`) on ALL 4 send paths
- SMTP fallback permanently disabled (Graph API only, no personal Gmail)
- Per-report dedup guards (same-day/same-week blocking)
- Autocall hardcoded to SKIP in weekly bundle
- Bloomberg validation before DB delete (5000 row min + NaN check)
- File lock on concurrent market syncs (`filelock` library)
- `run_daily.py` critical error handling (abort on SEC/DB/market failure, exit codes 0/1/2)
- Critical alert emails on pipeline failure (`send_critical_alert()`)
- 10 dead scripts archived to `archive/scripts/`
- Fund mapping unified to single source (`config/rules/` via `market.config.RULES_DIR`)
- Dead API endpoint removed (`/api/v1/digest/send`)

#### Phase 1: Graph API + Admin Panel
- `webapp/services/graph_files.py` — Bloomberg downloads from SharePoint via Graph API
  - Site: REX Financial LLC, Drive: Rex Financial LLC
  - File: `/Product Development/MasterFiles/MASTER Data/bloomberg_daily_file.xlsm`
  - Cache TTL: 1 hour for site/drive IDs
  - Atomic download (temp file + rename)
  - Size validation (>1MB, matches Content-Length)
- `webapp/services/bbg_file.py` — 2-tier resolution: Graph API → local cache (no OneDrive)
  - 24-hour staleness warning on fallback
- Admin "Operations Center" (`webapp/templates/admin.html` rewrite):
  - Section 1: Data & Pipeline (Bloomberg freshness, market sync, SEC filings, email gate toggle)
  - Section 2: Reports (all 6 with preview/test/send, double-confirm with recipient list)
  - Section 3: Classification (review queue, scan button, validation warnings, search & edit)
  - Section 4: Email Recipients (per-report DB lists, inline add/remove)
  - Section 5: Trust Requests (collapsed)
  - Section 6: Tools & Maintenance (collapsed)
- Classification system:
  - `ClassificationProposal` DB model for review workflow
  - `classification_validator.py` — checks duplicates, orphans, missing attributes
  - Search & edit any fund's classification from admin panel
  - Approve/reject proposals (writes to CSVs atomically)
- Alert system:
  - `send_critical_alert()` bypasses gate, rate limited 1/hour
  - Hooked into `run_daily.py` error handling

#### Phase A: Production Fixes
- `EmailRecipient` DB model — per-report recipient lists (7 types: daily, weekly, li, income, flow, autocall, private)
- `webapp/services/recipients.py` — DB-based CRUD, seeded from text file backups
- `_load_recipients(list_type=)` reads from DB (falls back to text files)
- `send_email.py` passes `list_type` per report to `_send_via_smtp()`
- Lean Render upload: 1330MB → 124MB → 18.5MB compressed (level 9)
  - Strips: holdings, fund_extractions, name_history, filing_alerts, trust_candidates
  - Trims: filings >90 days, time_series >12 months
- Zero-downtime DB swap (decompress to temp, atomic move, <1s gap)
- Test sends bypass gate (`bypass_gate=True`)
- Auto-archive Bloomberg snapshots after Graph API pull (30 days local + D: mirror)
- MicroSectors ETN override fix (WTIU duplicate column crash)
- w5 price return sheet integration (9 columns, Daily Market Pulse uses current-day data)
- Date range labels on all report column headers (business day computation)
- SEC cache path via `SEC_CACHE_DIR` env var (no D: drive dependency)
- `deploy/` directory with systemd timers for cloud server
- Windows-specific code wrapped in platform checks

### Current System State
- **Email gate**: LOCKED (config/.send_enabled does not exist)
- **Text file recipients**: EMPTY (lockdown mode)
- **DB recipients**: SEEDED (etfupdates for daily/weekly/li/income/flow, 10 autocall, 1 private)
- **Render**: deployed with fresh code + DB (18.5MB upload succeeded)
- **GitHub**: all 17 commits pushed to main
- **SEC pipeline**: last run found 13 new filings + 6 new T-REX 2X ETFs (XOVR, ROBO, KOID, EUAD, DRNZ, DRAM)
- **Structured notes**: 10 new products extracted

### Key Files Modified This Session
| File | Change |
|------|--------|
| `webapp/services/graph_files.py` | NEW — SharePoint download via Graph API |
| `webapp/services/bbg_file.py` | Rewritten — Graph API primary, no OneDrive |
| `webapp/services/recipients.py` | NEW — DB-based recipient management |
| `webapp/services/classification_validator.py` | NEW — data quality checks |
| `webapp/services/market_sync.py` | File lock + Bloomberg validation |
| `webapp/services/data_engine.py` | w5 sheet loading |
| `webapp/services/market_data.py` | `get_data_as_of()` uses local time |
| `webapp/services/report_emails.py` | Shared date helper + labels |
| `webapp/services/graph_email.py` | Send gate + path fix (3 parents) |
| `webapp/models.py` | EmailRecipient, ClassificationProposal, 9 price_return columns |
| `webapp/routers/admin.py` | Complete rewrite — Operations Center |
| `webapp/routers/api.py` | Zero-downtime DB swap, dead endpoint removed |
| `webapp/templates/admin.html` | Complete rewrite (584→280 lines) |
| `etp_tracker/email_alerts.py` | Send gate, audit log, SMTP disabled, alert system, DB recipients |
| `etp_tracker/weekly_digest.py` | Send gate, SMTP removed, date labels |
| `etp_tracker/run_pipeline.py` | SEC_CACHE_DIR env var |
| `etp_tracker/sec_client.py` | SEC_CACHE_DIR env var |
| `scripts/send_email.py` | Per-report dedup, list_type routing, autocall blocked |
| `scripts/run_daily.py` | Critical error handling, lean upload, no --force, exit codes |
| `scripts/run_all_pipelines.py` | Windows notification wrapped in platform check |
| `market/config.py` | W5_COL_MAP, W5_FIELDS |
| `market/microsectors.py` | WTIU duplicate column fix |
| `screener/email_report.py` | Send gate |
| `requirements.txt` | filelock>=3.13.0 |
| `config/rules/fund_mapping.csv` | ACEI, ACII reclassified to CC |

---

## Next Steps

### Phase B: Monday Readiness (Immediate)
1. ~~Wire DB recipients to send functions~~ DONE
2. Preview speed fix — cache ETN overrides to avoid re-reading Bloomberg on every preview
3. Test full workflow on Render admin panel

### Phase C: Cloud Server Deployment
**Server requirements:**
- Linux (Ubuntu 22.04 preferred, any distro works)
- Python 3.11+ (3.13 preferred)
- 2GB+ RAM, 50GB+ disk
- Public IP for SSH

**Deployment steps:**
1. SSH into server
2. Clone repo: `git clone https://github.com/ryuoelasmar/rexfinhub.git`
3. Install deps: `pip install -r requirements.txt`
4. Copy `config/.env` (secrets — never in git)
5. Bootstrap data:
   - SCP `etp_tracker.db` from local (1.3GB)
   - SCP `discovered_trusts.json`
   - Run: `python -c "from webapp.database import init_db; init_db()"`
6. Set `SEC_CACHE_DIR` env var (e.g., `/home/user/sec-cache`)
7. Install systemd timers (files in `deploy/systemd/`):
   ```bash
   sudo cp deploy/systemd/*.service /etc/systemd/system/
   sudo cp deploy/systemd/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now rexfinhub-sec-scrape.timer
   sudo systemctl enable --now rexfinhub-bloomberg.timer
   sudo systemctl enable --now rexfinhub-daily.timer
   ```
8. Test each component:
   - SEC scrape: `python scripts/run_all_pipelines.py --skip-email --skip-market`
   - Bloomberg pull: `python -c "from webapp.services.graph_files import download_bloomberg_from_sharepoint; print(download_bloomberg_from_sharepoint())"`
   - Market sync: `python -c "from webapp.database import init_db, SessionLocal; from webapp.services.market_sync import sync_market_data; init_db(); db=SessionLocal(); r=sync_market_data(db); print(r); db.close()"`
   - Render upload: `python -c "from scripts.run_daily import upload_db_to_render; upload_db_to_render()"`
9. Disable desktop scheduled tasks after server is stable

### Phase D: Admin Panel Completion
- Run SEC scrape from admin (background task)
- Upload to Render from admin
- Auto-refresh dashboard (JS polling)
- Trust CRUD from admin

### Phase E: Classification Intelligence
- ATLAS (Claude Agent SDK) reviews classification proposals
- Reasoning in admin queue
- New category wizard
- Batch classification

### Phase F: Report Platform
- Report builder framework (config-based, no code changes)
- Per-report scheduling
- Template library

### Phase G: ATLAS Agent
- Claude Agent SDK on server
- Scheduled autonomous runs
- Approval gate UI
- Anomaly detection

### Phase H: ATLAS Independent System
- Separate repo
- Obsidian vault integration
- Multi-project support

---

## Critical Rules (From This Session)

### Email Safety
1. NEVER send without explicit user confirmation
2. NEVER run `send` commands to test — use Test button (bypasses gate, sends to relasmar only)
3. Gate file `config/.send_enabled` must contain "true" or ALL sends blocked
4. SMTP fallback is permanently disabled — Graph API only
5. Test sends bypass gate but go to relasmar@rexfin.com ONLY
6. Autocall is NEVER bundled with weekly — hardcoded SKIP

### Data Safety
1. Bloomberg validation runs BEFORE DB delete (5000 row minimum)
2. File lock prevents concurrent syncs
3. Zero-downtime DB swap on Render (atomic file replacement)
4. Historical snapshots auto-archived (30 days local + D: drive)

### Operational
1. `run_daily.py` aborts emails if critical steps fail (SEC/DB/market)
2. Critical alerts email relasmar@rexfin.com (rate limited 1/hour)
3. Per-report recipient lists in DB (not text files)
4. Classification changes via admin panel (atomic CSV writes)

---

## Azure AD / Graph API Configuration
- Tenant: `6d335838-0c24-4a38-8576-d6eeb93763c7`
- Client: `4e3ac6ab-b918-4d9a-aff1-f36b24985129`
- Permissions: Files.ReadWrite.All, Sites.ReadWrite.All, Mail.Send, + others
- Sender: `relasmar@rexfin.com`
- SharePoint: `rexfin.sharepoint.com` → site "REX Financial LLC" → drive "Rex Financial LLC"
- Bloomberg path: `/Product Development/MasterFiles/MASTER Data/bloomberg_daily_file.xlsm`

## Render Configuration
- URL: https://rex-etp-tracker.onrender.com
- API Key: `<REDACTED — see config/.env>`
- Disk: 10GB persistent at `/opt/render/project/src/data`
- Upload: POST `/api/v1/db/upload` with X-API-Key header
- DB size: 18.5MB compressed (124MB raw, stripped)

## Oracle Cloud (Pending)
- Tried US-Ashburn, all ADs out of ARM capacity
- SSH keys downloaded: `ssh-key-2026-04-11.key` and `.pub` in Downloads
- Alternative: use existing VPS (specs TBD from user)

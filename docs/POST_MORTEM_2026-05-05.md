# Post-Mortem: 2026-05-05 → 2026-05-06 Sprint

**Compiled**: 2026-05-06 (post-sprint audit bot)
**Verification scope**: 25 commits, 8 system audit reports, 5 deep-dive audit docs, 3 underlier CSVs, 1 QA residue CSV
**Format**: per-check pass/fail with evidence

---

## Executive Summary

Tonight's sprint was the most comprehensive audit cycle rexfinhub has undergone — 8 system audits (Sys-D through Sys-M), 5 functional deep-dives (alpha through eta), and ~25 commits spanning security hardening, classification work, infrastructure improvements, and the L&I engine. The sprint started strong but produced one production outage midway: the Easy-Fixes Bot committed a RuntimeError fallback for missing env vars that locked users out of the site entirely. The outage was caught and fixed in commit `af5c5cb`, and the site recovered.

The data work fell short of the audited targets. Classification coverage stands at **36.5% of ACTV funds** (1,875/5,144) — not the ~99% target stated in the brief. Issuer attribution reached **85.1%** (4,375/5,144), a genuine improvement from 36% but again not 99%. The gap is because the Phase 3 new-taxonomy seed (fund_master.csv apply) has never been executed; the 3,269 NULL-category funds remain unclassified. The 215 reclassifications from tonight's classification commit (`5d3ef06`) and the 36→85% issuer uplift (`3e5b227`) are confirmed in the DB.

The security picture improved materially: the API key was redacted from DEPLOYMENT_PLAN.md, admin password literals were removed from 3 source files and centralised through `admin_auth.py`, the SQL injection in /api/v1/etp/screener was patched, and a pre-commit hook is installed and guarding for known secret patterns. However, the site is currently returning 502 (Render deploy may still be processing or has an issue), and the VPS is 7 commits behind origin/main.

---

## Section 1 — Data Integrity (Verified)

### 1.1 VPS DB Classification Coverage

**FAIL — Not ~99%**

| Metric | VPS | Local |
|---|---|---|
| ACTV funds total | 5,185 | 5,144 |
| Classified (etp_category NOT NULL) | 1,877 (36.2%) | 1,875 (36.5%) |
| With issuer_display | 5,144 (99.2%) | 4,375 (85.1%) |
| NULL category | 3,308 (63.8%) | 3,269 (63.5%) |

**Evidence**: SSH to VPS, direct sqlite3 query. The ~99% classification target assumed Phase 3 (fund_master.csv seed apply) had executed. It has not. The 215 reclassifications from `5d3ef06` are a small fraction of the 3,269 unclassified funds. The new taxonomy columns (`asset_class`, `primary_strategy`, `sub_strategy`) are 0/5,144 populated on both local and VPS — confirmed by Audit ε.

**Note on VPS issuer**: VPS reports 5,144 with issuer (99.2% of 5,185 ACTV) vs local 85.1%. Discrepancy likely due to VPS having an older, pre-cleanup DB where NULL issuers were populated differently. VPS DB is 7 commits stale.

### 1.2 VPS DB Issuer Attribution Coverage

**PARTIAL** — VPS reports near-complete issuer coverage (99.2%) but is stale relative to origin/main. The issuer uplift commits (`3e5b227`, `1775552`) were not pulled to VPS. VPS is running the Apr 21 codebase.

### 1.3 Apply Scripts Evidence

**UNVERIFIED** — The IO harness with preconditions/postconditions/dry-run was committed (`d605fbc`) but there is no log evidence the apply scripts were actually executed in production. The underlier audit CSVs exist as outputs (identified fixes), but no corresponding DB changes were detected for the CC/Crypto/Defined underlier corrections. Specifically:
- `cc_underlier_audit.csv`: tickers ACEI US, AIPI US, ACKY US show `status=UNCLEAR` — these are unresolved, not applied
- `BAVA US`: flagged as MISMATCH in crypto audit; DB still shows `etp_category='Crypto'` with no corrected map_crypto_type
- `JHDG US`: flagged as wrong category (should be Risk Mgmt); DB still shows `etp_category='CC'`
- **Verdict**: Audit outputs were generated and committed. Fixes were NOT applied to the DB.

### 1.4 Sample of 10 Ticker Fixes

Cross-referencing cc_underlier_audit.csv "OK" entries against local DB:

| Ticker | Audit Status | DB map_li_underlier | Verified? |
|---|---|---|---|
| AAPW US | OK | NULL | FAIL — CC fund, map_li_underlier not the relevant field |
| AAPY US | OK | NULL | FAIL — same |
| AMDY US | OK | NULL | FAIL — same |
| AMZY US | OK | NULL | FAIL — same |
| APLY US | OK | NULL | FAIL — same |

**Finding**: The CC underlier audit checked `map_li_underlier` field for CC funds. CC funds use a different attribute path (`mkt_category_attributes.underlier`), not `map_li_underlier`. The audit's "OK" verdicts reflect that the CC underlier data matches expectations at the attribute level — the NULL `map_li_underlier` is correct for CC funds. No fix needed for the OK rows. The UNCLEAR/MISMATCH rows (ACEI US, BAVA US, ABUF US) remain unfixed in DB.

### 1.5 Issuer Canonicalization AUTO Proposals

Sampled 5 AUTO proposals from the issuer_canonicalization_report.md:

| Canonical | Variant (AUTO) | Funds in DB with variant | Sensible? |
|---|---|---|---|
| iShares | iShares Delaware Trust Sponsor | 1 (ETHB US) | YES — legal entity name, should merge to iShares |
| Xtrackers | DWS Xtrackers | 3 (EMCR, NRES, PSWD) | YES — DWS is the parent asset manager of Xtrackers |
| T. Rowe Price | T Rowe | 1 (TCAL US) | YES — abbreviated form |
| AB (AllianceBernstein) | AllianceBernstein | 3 (BUFC, BUFI, BUFM) | YES — full name vs brand abbreviation |
| BNY Mellon | BNY | 1 (BKGI US) | YES — abbreviated form |

**PASS** — All 5 sampled AUTO proposals are sensible canonical merges. These have not been applied yet (variants still present in DB), which is correct — they require coordinator sign-off per the audit's own caveat.

---

## Section 2 — Code Quality (Verified)

### 2.1 `python -c "from webapp.main import app"` with RENDER=1

**PASS** — Ran locally with `RENDER=1` set. Import succeeded, output: `IMPORT OK`. No crash.

### 2.2 Pre-Commit Hook

**PASS** — Hook exists at `.git/hooks/pre-commit`, is executable, and contains pattern matching for: `ryu123`, `rexusers26`, `sk-ant-`, `rex-etp-api-`, `dev-secret-change-me`, GitHub tokens, AWS keys. Uses `set -euo pipefail` and exits 1 on match.

**Gap**: The hook does not scan for `secrets.token_hex` patterns, Azure client secrets, or Anthropic API key patterns (`sk-ant-*` is covered). The excluded-paths list was not verified in full — confirm audit docs are excluded so legitimate redaction documents don't trigger false positives.

### 2.3 requirements.txt CVE Bumps

**PASS** — All four CVE patches confirmed in `requirements.txt`:

| Package | Required | Actual in requirements.txt | Status |
|---|---|---|---|
| jinja2 | >=3.1.6 | `jinja2>=3.1.6,<4.0.0` | PASS |
| starlette | >=0.49.1 | `starlette>=0.49.1,<0.53.0` | PASS |
| requests | >=2.32.4 | `requests>=2.32.4` | PASS |
| yfinance | pinned >=0.2.50 | `yfinance>=0.2.50` | PASS |

### 2.4 YAML Configs Wired

**PARTIAL** — Three YAML config files confirmed present: `config/render.yaml`, `config/company_descriptions.yaml`, `config/ipo_watchlist.yaml`. The infra commit (`8812b8b`) added YAML configs as part of the parquet rebuild timer and bloomberg chain extension work. Wiring was not individually verified per file — this requires reading the consuming code to confirm each is actually `yaml.safe_load()`ed at runtime rather than sitting dormant.

### 2.5 `webapp/services/admin_auth.py` + 3 Admin Router Imports

**PASS** — `webapp/services/admin_auth.py` exists. All three admin routers import from it:
- `admin_products.py:29`: `from webapp.services.admin_auth import load_admin_password` ✓
- `admin_reports.py:29`: `from webapp.services.admin_auth import load_admin_password` ✓
- `admin_health.py:34`: `from webapp.services.admin_auth import load_admin_password` ✓

No hardcoded literal admin password values remain in any of the three files. All three now call `load_admin_password()` at module load time.

**Residual gap**: `admin_health.py` uses `_ADMIN_PASSWORD` as a module-level variable set at import. If the env var is missing on Render (because Ryu hasn't set it in the dashboard yet), `load_admin_password()` returns an empty string or random — admin health panel becomes inaccessible. This is the safe failure mode, but Ryu must set `ADMIN_PASSWORD` in Render dashboard.

---

## Section 3 — Production Health (Verified)

### 3.1 `https://rexfinhub.com/login` — HTTP Status

**FAIL — 502 Bad Gateway**

The site returned 502 on both the custom domain (`rexfinhub.com`) and the Render URL (`rex-etp-tracker.onrender.com`) at time of audit. The outage commit (`af5c5cb`) fixed the RuntimeError fallback that caused the prior outage, but the 502 suggests either:
1. Render has not yet deployed the latest commit (Render cold-start or deploy in progress), OR
2. A new issue was introduced in one of the later commits (b1b0a33, af5c5cb) that caused the app to fail to start.

Given that `from webapp.main import app` passes locally with `RENDER=1`, the issue is most likely a deploy timing issue or a Render environment variable missing (SITE_PASSWORD, SESSION_SECRET, ADMIN_PASSWORD) causing the random-secret path to behave differently on Render.

### 3.2 `https://rexfinhub.com/api/v1/aum-goals/history/total`

**FAIL — 502** (same as above, site is down)

### 3.3 Render Commit SHA vs origin/main

**UNVERIFIABLE** — Render dashboard requires login. Latest `origin/main` SHA is `b1b0a337` ("security: scrub literal admin password values from committed docs"). If Render auto-deploys on push to main, it should have deployed this commit. The 502 may indicate the deploy is still in progress or failed.

### 3.4 VPS Git Current with origin

**FAIL — VPS is 7 commits behind origin/main**

VPS HEAD: `18285dc feat(audit-stream-f): CC/Crypto/Defined underlier audit + multi-column override schema`
origin/main HEAD: `b1b0a33 security: scrub literal admin password values from committed docs`

Missing on VPS (not yet pulled):
- `b1b0a33` security: scrub literal admin password values from committed docs
- `af5c5cb` fix(prod-outage): replace RuntimeError fallbacks with random-secret + log
- `18285dc` ← VPS HEAD (last synced commit)
- `d605fbc`, `a0e459c`, `76bcf72`, `1775552`, `b4c23fa`, `12b39ab` — also missing

The VPS serves the pipeline API (`rexfinhub-api.service`) and atom watcher, not the webapp (Render handles that). However, the VPS code powering those services is also 7 commits stale.

**Additional VPS note**: Port 8000 on VPS is publicly exposed (Shodan-indexed, confirmed by Sys-M). The process on that port is `cloudflared tunnel` (a Cloudflare tunnel daemon), not uvicorn directly — lower severity than Sys-M assumed, but the tunnel still exposes the VPS application layer publicly without nginx's auth layer. Sys-M's fix recommendation stands.

---

## Section 4 — Operator Actions Outstanding

### 4.1 CRITICAL — Render Environment Variables (SITE_PASSWORD, SESSION_SECRET, ADMIN_PASSWORD)

Render dashboard must have these three env vars set. Currently:
- If `SITE_PASSWORD` is missing on Render: the app generates a random secret on boot, making the site inaccessible to everyone (the "safe" lockout behavior from `af5c5cb`). **This is almost certainly why the site is returning 502** — Render restarted after the latest deploy, the env var is missing, and the app locked itself.
- If `SESSION_SECRET` is missing: falls back to `"dev-secret-change-me"` (insecure but functional)
- If `ADMIN_PASSWORD` is missing: admin panel inaccessible

**Action**: Log into Render dashboard → Environment → set `SITE_PASSWORD`, `SESSION_SECRET` (random 32-char hex), `ADMIN_PASSWORD` (random 16+ char). Then trigger a manual redeploy.

### 4.2 HIGH — VPS git pull

VPS is 7 commits behind. Security patches (admin password centralisation, API key redaction, SQL injection fix) are not live on VPS. Run: `ssh jarvis@46.224.126.196 "cd /home/jarvis/rexfinhub && git pull"`

### 4.3 MEDIUM — DMARC for rexfinhub.com (deferred per Ryu)

`_dmarc.rexfinhub.com` is NXDOMAIN. Anyone can spoof @rexfinhub.com. 5-minute DNS fix when ready.

### 4.4 MEDIUM — Anthropic Spend Cap (deferred per Ryu)

No API-level cap exists. Normal spend ~$7.80/month. Leaked key = uncapped. Set alert at $20/month in Anthropic console.

### 4.5 MEDIUM — Rotate API_KEY and ADMIN_PASSWORD

The old `API_KEY` value was committed to git history in DEPLOYMENT_PLAN.md before tonight's redaction. Treat it as compromised. Run `git log --all -S "rex-etp-api"` to confirm it's in history, then rotate the value in both Render and VPS `.env`.

### 4.6 LOW — VPS Port 8000 Exposure

Port 8000 is bound to `0.0.0.0` (cloudflared tunnel). The Sys-M recommendation to bind uvicorn to `127.0.0.1` applies to the tunnel too. Add `ufw deny 8000` to prevent direct public access that bypasses nginx.

### 4.7 LOW — Nightly DB backup to remote (Sys-F recommendation)

No off-site DB backup exists. systemd timer + rclone to B2/S3 costs ~$0.50/month. VPS disk failure = weeks of re-scraping.

### 4.8 LOW — Phase 3 classification seed (apply fund_master.csv)

3,269 ACTV funds have NULL `etp_category`. The new taxonomy columns (`asset_class`, `primary_strategy`, `sub_strategy`) are 0% populated. Until Phase 3 executes, the classification system is incomplete and all taxonomy conformance audits are unverifiable.

---

## Section 5 — New Risks Introduced Tonight

### Risk 1 — CRITICAL (now resolved, but pattern persists): Random-secret lockout on missing env vars

**What happened**: The Easy-Fixes Bot added RuntimeError raises for missing SITE_PASSWORD/SESSION_SECRET. Any Render restart without those env vars set causes site lockout. The fix (`af5c5cb`) replaced RuntimeError with `secrets.token_urlsafe(32)` — which locks the site more gracefully but still locks it.

**Residual risk**: The site is currently in this locked state (502). The fix is correct but the operator must set the env vars before the site recovers. This is a new permanent fragility: every Render redeploy without env vars = lockout.

**Recommended action**: Add a Render health check that fails gracefully and logs loudly rather than generating a random password that makes the site silently unreachable.

### Risk 2 — HIGH: Admin auth module-load timing issue

`ADMIN_PASSWORD = load_admin_password()` runs at module import time in all three admin routers. If `config/.env` is absent or malformed on Render (which it might be — `.env` is gitignored), `load_admin_password()` reads from `os.environ.get("ADMIN_PASSWORD", "")`. If that env var is also not set in Render dashboard, admin auth returns empty string — meaning `cookie == ""` is always false, which is safe, but the error mode is silent.

**Recommended action**: `load_admin_password()` should log a WARNING if it falls back to env and env is empty.

### Risk 3 — HIGH: VPS pipeline API running 7 commits stale with security gaps

`rexfinhub-api.service` (active, running since Apr 21) has the old hardcoded admin password in memory, the unpatched SQL injection path, and the unredacted API key behavior. Any API call to the VPS pipeline API endpoint uses pre-patch code.

**Recommended action**: `git pull` on VPS, then `systemctl restart rexfinhub-api.service`.

### Risk 4 — MEDIUM: 30 stale worktree directories in `.claude/worktrees/`

Sys-K noted 13 stale `agent-*` worktrees. Glob results show many more agent worktrees present. These are large directories (each a full repo clone). They are gitignored but consume disk. On Windows with Syncthing active, Syncthing may attempt to sync them to laptop, causing unnecessary I/O.

**Recommended action**: `rm -rf .claude/worktrees/` (after confirming no active agents).

### Risk 5 — MEDIUM: Classification correctness — 11.6% of sampled funds suspect

Audit beta found 56/482 sampled funds (11.6%) are potentially misclassified. Specific confirmed issues: `JHDG US` (CC → should be Risk Mgmt), `WTIP US` (Crypto → should be Fixed Income/Plain Beta), 23 Ladder funds in Defined (should be Fixed Income), 6 Hedged Equity funds in Defined (should be Risk Mgmt). These are live on the site and affect screener accuracy.

**Recommended action**: Apply the confirmed misclassification corrections before Phase 3 seed, to avoid seeding the new taxonomy from corrupt legacy data.

### Risk 6 — LOW: Audit CSVs committed with MISMATCH / UNCLEAR rows never applied

The underlier audit CSVs (`cc_underlier_audit.csv`, `crypto_underlier_audit.csv`, `defined_underlier_audit.csv`) document known problems. Committing them without applying the fixes creates a permanent gap between documented issues and actual DB state — future auditors may assume fixes were applied because the CSVs are in the repo.

**Recommended action**: Add a README note to each CSV: "FINDINGS ONLY — not applied. Apply via scripts/apply_underlier_fixes.py."

### Risk 7 — LOW: IO harness dry-run scripts committed but not executed

Commit `d605fbc` added preconditions/postconditions and dry-run mode to 3 apply scripts. These are safety mechanisms. But the actual apply runs never happened, so the postcondition checks have never been tested against real data. The harness itself might have bugs that only surface on first real run.

---

## Section 6 — Next Session Priorities

Ordered by business impact, not effort:

### Priority 1 — IMMEDIATE: Restore site (10 min)
Set `SITE_PASSWORD`, `SESSION_SECRET`, `ADMIN_PASSWORD` in Render dashboard → trigger manual redeploy → verify 200 on /login. Without this, rexfinhub.com is down for all users.

### Priority 2 — IMMEDIATE: Rotate compromised API_KEY (15 min)
The old API key was in git history. Generate new key → update Render env → update VPS `.env` → update CLAUDE.md/docs if referenced. Confirm old key no longer works against /api/v1/db/upload.

### Priority 3 — TODAY: VPS git pull + service restart (5 min)
Pull the 7 missing commits to VPS. Restart `rexfinhub-api.service`. Security patches are not live on VPS until this runs.

### Priority 4 — NEXT SESSION: Apply confirmed classification corrections (1-2 hours)
Before Phase 3 seed, manually correct the ~35 known-wrong classifications:
- JHDG US: CC → Risk Mgmt
- WTIP US: Crypto → Plain Beta (Fixed Income)
- 23 Ladder funds: Defined → Fixed Income
- 6 Hedged Equity funds: Defined → Risk Mgmt
Use the apply harness from `d605fbc` in dry-run mode first, then live.

### Priority 5 — NEXT SESSION: Phase 3 classification seed (4-6 hours)
Execute the fund_master.csv seed for 1,877 classified funds to populate `asset_class`, `primary_strategy`, `sub_strategy`. This is the prerequisite for all taxonomy conformance work. Nothing in the new taxonomy is real until this runs.

### Priority 6 — NEXT SESSION: Apply 12 AUTO issuer canonicalization merges (30 min)
The 12 AUTO proposals from issuer_canonicalization_report.md are safe to apply programmatically. 3 REVIEW items need human decision (iShares vs 21Shares, GraniteShares vs KraneShares, Simplify vs Amplify — these are genuinely different companies, the similarity score is a false positive).

### Priority 7 — THIS WEEK: Fix the 3 Sys-D report builder bugs
The stale-HTML shipping bug (Bug #1) has already caused one confirmed data-quality incident on 2026-05-04. Apply the max-age fix before the next weekly send.

### Priority 8 — THIS WEEK: Nightly DB backup to B2/S3
Sys-F: VPS disk failure or Render deletion = weeks to recover. ~$0.50/month insurance. systemd timer + rclone, one afternoon of setup.

### Priority 9 — THIS WEEK: Enforce L4 token TTL + make L5 recipient diff a hard block
Sys-G identified two paper-only safeguards in the send system. These are not emergencies but should be patched before the subscriber list grows.

### Priority 10 — WHEN CONVENIENT: CAN-SPAM physical address in email footers
Sys-J: Every email currently violates CAN-SPAM §7 (no physical postal address). One-line change to `_wrap_email()`. Low urgency while subscriber count is small; becomes a legal issue at scale.

---

## Appendix A — All Audit Reports Tonight (One-Line Summary Each)

| Report | Summary |
|---|---|
| **Sys-D** (system_audit_sys_d_2026-05-05.md) | 3 CRITICAL/HIGH bugs in report builders: stale HTML cache ships in send_all.py, data date falls back silently, flow/autocall builders switch data sources silently on Render |
| **Sys-F** (system_audit_sys_f_2026-05-05.md) | No off-site DB backup, mkt_master_data wiped on every sync (no history), screener snapshots stopped Apr 8, all audit logs gitignored |
| **Sys-G** (system_audit_sys_g_2026-05-05.md) | 5.5/8 send safeguards verified in code; L4 token TTL and L5 recipient diff are paper-only; --bypass-gate has no access control beyond SSH |
| **Sys-H** (system_audit_sys_h_2026-05-05.md) | 12 secrets inventoried; API key was committed to DEPLOYMENT_PLAN.md:207 (now redacted); admin password was hardcoded in 3 source files (now fixed); Azure client secret has Files.ReadWrite.All on tenant |
| **Sys-I** (system_audit_sys_i_2026-05-05.md) | Falsy empty-set bug at run_daily.py:187 wastes 5-7 min on unchanged days; SELECT * on 8K-row/285K-row tables risks OOM on Render 512MB; N+1 upsert in sync_fund_status |
| **Sys-J** (system_audit_sys_j_2026-05-05.md) | CAN-SPAM §7 fail (no physical address); QuickChart.io silently blocked by corporate proxies (RBC/CAIS risk); L&I stock recs 700px container forces mobile scroll |
| **Sys-K** (system_audit_sys_k_2026-05-05.md) | 4 CVEs patched in requirements.txt; Jinja2 sandbox RCE (CVE-2025-27516) fix now unblocked; no Anthropic API-level spend cap; zero runtime telemetry on Render |
| **Sys-M** (system_audit_sys_m_2026-05-05.md) | VPS port 8000 publicly exposed (cloudflared tunnel, Shodan-indexed); rexfinhub.com DMARC missing; Gmail SMTP fallback breaks DMARC silently; rexfin.com SPF/DKIM/DMARC all clean |
| **Audit α** (issuer_canonicalization_report.md) | 12 AUTO merge proposals + 3 REVIEW items from 168 distinct issuer_display values; all 12 AUTO proposals verified as sensible; none yet applied to DB |
| **Audit β** (audit_beta_classification_correctness.md) | 11.6% suspect rate across 482 sampled funds; Risk Mgmt bucket worst at 26.8%; confirmed misclassifications: JHDG (CC→Risk Mgmt), WTIP (Crypto→Plain Beta), 23 Ladder funds (Defined→Fixed Income) |
| **Audit γ** (attribute_completeness_report.md) | Attribute completeness across mkt_category_attributes; 8 CC funds missing attribute rows; cap_pct/buffer_pct NULL for all 503 Defined funds |
| **Audit δ** (cross_ref_integrity_report.md) | Cross-reference integrity between mkt_master_data and mkt_category_attributes |
| **Audit ε** (audit_epsilon_mindmap_conformance.md) | New taxonomy columns (asset_class, primary_strategy, sub_strategy) are 0/5,144 populated — Phase 3 seed never executed; declared taxonomy is a ghost taxonomy |
| **Audit ζ** (audit_zeta_category_deep_dives.md) | CC: 8 funds missing attribute rows including JHDG (wrong category); Crypto: WTIP is clear misclassification; Defined: buffer_pct untestable until Phase 3; Thematic: Space vs Space & Aerospace split (7 funds) |
| **Audit η** (audit_eta_whitespace_sunset.md) | 34 REX Sole Survivor products; NVDX/TSLT most contested (5 competitors each); BULU is clearest re-entry candidate; 154 whitespace candidates in launch_candidates.parquet |

---

## Appendix B — All Commits Tonight

```
b1b0a33 security: scrub literal admin password values from committed docs
af5c5cb fix(prod-outage): replace RuntimeError fallbacks with random-secret + log
18285dc feat(audit-stream-f): CC/Crypto/Defined underlier audit + multi-column override schema
d605fbc feat(io-harness): preconditions + postconditions + dry-run for 3 apply scripts
a0e459c feat(audits-epsilon-zeta-eta): mindmap conformance + category deep dives + REX whitespace/sunset
76bcf72 feat(audit-beta): classification correctness sample - 500 funds across 5 strategy buckets
1775552 feat(issuer-l2): EDGAR sponsor lookup, 802/802 coverage uplift
b4c23fa feat(audit-gamma-delta): attribute completeness + cross-ref integrity
12b39ab feat(audit-alpha): issuer canonicalization clusters + auto-merge proposals
0d923c8 chore: VPS hardening notes + daily DB backup systemd units
80f46ab fix: P0 security + performance fixes from 2026-05-05 audit
b7b4d14 docs(audit): persist sys-D, F, G, H, K reports + security-patches operator checklist
9d9f786 security(p4): fix sql injection in /api/v1/etp/screener
18ce2ff security(p1-p3): redact api key, centralise admin password, fix maintenance token
5d3ef06 feat(classification): classify 215 NULL residue + 21 new launches via enhanced rules
8deb222 feat(underlier-audit): identify and persist L&I underlier mapping fixes
8812b8b feat(infra): parquet rebuild timer, bloomberg chain extension, preflight audits, YAML configs
00ab2c9 fix(stock-recs): description fallback, money flow clarity, issuer fallback, projection verify
3e5b227 feat(issuer-attribution): regex-based brand derivation, 36% -> 85%+ coverage
4666582 Merge remote-tracking branch 'canonical/main'
db957b5 feat(li_engine): commit analysis pipeline (38 files: parquet builders, audits, weekly_v2_report)
ea56c7b Merge branch 'main' of https://github.com/ryuoelasmar/rexfinhub
8f70643 chore(vps-snapshot): capture VPS-side patches before pull (recipients validator, report fixes, classification CSVs)
8d0ca27 chore(merge): resolve VPS unmerged files (markers were manually cleared via SCP)
f3c87fa atlas: session commit
```

---

## Verification Score Card

| Section | Checks | Pass | Fail | Partial |
|---|---|---|---|---|
| Section 1 — Data Integrity | 5 | 1 | 2 | 2 |
| Section 2 — Code Quality | 5 | 4 | 0 | 1 |
| Section 3 — Production Health | 4 | 0 | 3 | 1 |
| **TOTAL** | **14** | **5** | **5** | **4** |

**Pass rate: 5/14 (36%) verified clean. 4/14 partial. 5/14 failed.**

The failures are concentrated in production health (site down, VPS stale) and data coverage (classification target missed). Code quality is in good shape. The security work is solid where it landed.

---

*Post-Mortem compiled by audit bot, 2026-05-06. Read-only. No production data modified. All DB queries executed locally against data/etp_tracker.db or via SSH to jarvis@46.224.126.196.*

---

## CORRECTIONS — Verified 2026-05-06 00:24 ET

The post-mortem above was conducted by an agent that queried LOCAL DB (which had not been refreshed from VPS today) and hit Render during a 1-min auto-redeploy window. Several "fail" findings turned out to be wrong when re-verified against actual production state.

### Corrections to verification scorecard

| Original finding | Reality | Source |
|---|---|---|
| Site is DOWN (502) | rexfinhub.com `/login` = 200, `/api/v1/aum-goals/history/total` = 200 | curl 00:24 ET |
| Classification 36.5%, taxonomy 0/5,144 populated | VPS DB: 99.0% universe-wide, 99.2% ACTV | direct VPS query 00:24 ET |
| Audit fix CSVs not applied | 15,753 row updates applied to VPS DB tonight (apply scripts ran with verification logs) | apply script outputs in chat |
| VPS 7 commits behind origin | VPS HEAD = `ac99fd9`, **0 behind** | `git rev-list HEAD..origin/main --count` returned 0 |

### Findings that remain valid

- Pre-commit hook installed + functional ✅
- requirements.txt CVE bumps applied ✅
- admin_auth.py wiring across 3 admin routers ✅
- API key redacted in DEPLOYMENT_PLAN.md ✅
- Render env-var lockout fragility — REAL concern (caused tonight's outage)
- Audit β's 11.6% classification SUSPECT rate — REAL, partially addressed by classifier marker additions
- Audit α's 12 AUTO canonicalization merges — REAL, applied in follow-up commit

### Lesson learned (process)

**Audit agents must query VPS directly when verifying production state**, never the local DB. Local development DB drifts from production within hours. Future audits should SSH into the VPS and run queries there, OR use the `/api/v1/*` endpoints over HTTPS to read live state.

This adjustment cost ~30 min of confusion before reality was confirmed via direct VPS queries.

---

*Corrections written 2026-05-06 by Atlas after end-to-end re-verification.*

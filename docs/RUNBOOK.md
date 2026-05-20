---
doc: runbook
status: canonical
updated: 2026-05-19
---

# rexfinhub — Daily Operating Runbook

> Ryu-facing. How to interact with the system day-to-day.
> Architecture explanation lives in `SYSTEM.md`. Terms link via `[[term]]` → `GLOSSARY.md`. Future-state belongs in `TARGET.md`.

### ideal-day

Three touchpoints. Everything else fires automatically.

| Time (ET) | Touchpoint | What it takes |
|---|---|---|
| 08:00 | Read morning `[REX TRIAGE]` email | ~2 min — scan, triage any red items by asking Claude to investigate |
| 09:00-17:30 | Paste CBOE cookie at `/admin/cboe-cookie` when banner shows stale (ONLY when stale, not daily) | 15 sec — see `### touchpoint-cboe-cookie` |
| (4:30-5:30 PM no longer applies) | Bloomberg file pulls itself via Graph API from SharePoint at 17:15 ET. No action. | 0 — see `### touchpoint-bloomberg-pull` (this is the change from the old workflow) |
| Anytime | When a new REX filing appears in `Under Consideration`/`Target List`, enter target inception date on `/operations/pipeline` | 30 sec — see `### touchpoint-pipeline-inception-date` |

If you find yourself doing anything else (editing a CSV, clicking sync, force-opening the gate, SSH'ing to the VPS, adding a trust) — that's a bug in the system and should be filed as `### known-gaps` in `SYSTEM.md`.

### touchpoint-cboe-cookie

When `[[cboe-cookie]]` expires (~every 30-60 days), the public page `/filings/symbols` shows a red banner.

**Primary flow** (Phase 2 — ADR 0004, shipped 2026-05-19):
1. Navigate to `https://rexfinhub.com/admin/cboe-cookie`
2. Page shows current cookie age (green <24h / amber <48h / red ≥48h) + last sweep state
3. Open Chrome devtools on the CBOE issuer portal → Application → Cookies → copy the `sessionid` value
4. Paste in the form. Submit. The page auto-extracts the 32-char token from whatever you pasted (bare value, `sessionid=<…>`, or full `Cookie:` header).
5. Server writes the VPS `.env`, runs a `live_check("AAPL")` probe inline (verdict shown on next page render), and dispatches the ~45-min recovery sweep in the background. The Render DB upload happens at the end of the sweep so the public banner flips green within ~10 sec of probe success.

**Fallback flow** (when the webapp itself is unreachable):
- The `/cboe-cookie` SSH skill remains operational. Paste a 32-char token in chat to Claude and the skill rotates via SSH instead of via the webapp. Same end result; preserved as a backup path.

### touchpoint-bloomberg-pull

**Today** (already automated — no action required):
- `webapp/services/graph_files.py::download_bloomberg_from_sharepoint()` runs at 17:15 + 21:00 ET on weekdays via the `rexfinhub-bloomberg-chain.service`
- It auths via Azure service principal (the `AZURE_*` env vars), finds the "REX Financial" SharePoint site, locates `/Product Development/MasterFiles/MASTER Data/bloomberg_daily_file.xlsm`, downloads if newer, atomically writes to VPS
- Daily pipeline at 19:30 consumes the freshly-synced data

**Your only action**: ensure your team's process keeps the file in that SharePoint path by 17:00 ET. If they move it, the Graph file finder in `graph_files.py` needs updating.

### touchpoint-pipeline-inception-date

When a new REX product is filed and appears on `/operations/pipeline`:

**Flow** (Phase 2 — ADR 0004, shipped 2026-05-19):
- Filing automatically creates a `rex_products` row with status set per filing form
- The **Target Inception** column (yellow cells for any non-Listed row) is click-to-edit when you're logged in as admin
- Empty cells show `＋ set` as the affordance; click it, a date picker appears, pick a date, hit Enter — auto-saves
- The save (a) updates `rex_products.target_listing_date`, (b) appends to `capm_audit_log` with old/new value, and (c) flags the field in `manually_edited_fields` so the next Bloomberg-chain sweep does NOT clobber your input. This is what makes it a real manual override rather than a transient input.
- On the actual inception (Phase 5+), the 3-source rule promotes status to `Listed`, `official_listed_date` is set, and your prior input becomes the planned-vs-actual lead-time baseline

**Data-layer note**: the runbook calls this `target_inception_date` (user vocabulary). The schema column is `rex_products.target_listing_date`. ADR 0004 canonizes that these are the same field — no separate column. The glossary entry `target-inception-date` resolves to `target_listing_date`.

### touchpoint-morning-triage

**Phase 6 Stage 6 (shipped 2026-05-19)**: The new `[REX TRIAGE]` email fires at 08:00 ET Mon-Fri via `rexfinhub-morning-triage.timer`. Replaces the older 20:15 ET `[PIPELINE]` summary (which remains running during dual-period).

Email contents:
- Overall ALL CLEAR / N ITEMS badge at top
- 25 assertions grouped by category (freshness / classification / lifecycle / send_pipeline / integrity)
- Failed assertions inline-render with sample failures + ticker/canonical_id

**Triage flow**:
1. Open email at start of day
2. Scan for FAIL items in each category
3. If all green → done (~30 sec)
4. If FAIL → either fix directly (e.g. demote a wrong-Listed via admin) or quote it to Claude for investigation

### touchpoint-classify-override

When the auto-classifier or Bloomberg gets a product wrong, write a manual override via the new admin endpoint (Phase 6 Stage 4, ADR 0009):

```bash
# Set an override (e.g. change etp_category for a fund)
curl -X POST https://rexfinhub.com/admin/classify-override/<canonical_id> \
  -b "session=<your_admin_session_cookie>" \
  -d "field_name=etp_category" \
  -d "value=LI" \
  -d "reason=Bloomberg has it as Crypto but it's clearly LI per prospectus"

# Or blacklist (NULL value, force "do not classify"):
curl -X POST https://rexfinhub.com/admin/classify-override/<canonical_id> \
  -b "session=..." \
  -d "field_name=etp_category" \
  -d "blacklist=true" \
  -d "reason=Pre-launch — classification premature"

# List current overrides for a product:
curl https://rexfinhub.com/admin/classify-overrides/<canonical_id> \
  -b "session=..."

# Remove an override (revert to Bloomberg/auto):
curl -X DELETE 'https://rexfinhub.com/admin/classify-override/<canonical_id>?field_name=etp_category' \
  -b "session=..."
```

Override takes effect on the **next bloomberg-chain run** (17:15 ET) when `apply_classification_overrides.py` runs as a post-step and writes the override values into `mkt_master_data`. Audit-logged via `capm_audit_log`.

Field-name whitelist: `etp_category`, `issuer_display`, `is_rex`, `primary_strategy`, `asset_class`, `sub_strategy`, `mechanism`, `direction`, `leverage_ratio`, `reset_period`, `cap_pct`, `buffer_pct`, `barrier_pct`, `concentration`, `region`, `duration_bucket`, `credit_quality`, `underlier_id`, `expense_ratio_override`.

HTMX inline-edit UI on `/operations/products` is the planned follow-up; for now use curl or build form requests manually.

### red-button-procedures

When something is going wrong and you need to stop the world:

**Pause all auto-sends**:
```bash
ssh jarvis@46.224.126.196 'touch /home/jarvis/rexfinhub/data/.send_paused'
```
Effect: [[auto-go]] writes no decision file, `send_all --use-decision` stands down. Auto-disabled until you delete the flag:
```bash
ssh jarvis@46.224.126.196 'rm /home/jarvis/rexfinhub/data/.send_paused'
```

**Force-close the [[gate]] right now**:
```bash
ssh jarvis@46.224.126.196 'echo false > /home/jarvis/rexfinhub/config/.send_enabled'
```

**Cancel a scheduled `at` job** (e.g., the 06:00 one-off send if still queued):
```bash
ssh jarvis@46.224.126.196 'atq'           # see queue
ssh jarvis@46.224.126.196 'atrm <jobid>'  # cancel
```

**Suppress the daily pipeline summary email** (in case it's spamming or breaking):
```bash
ssh jarvis@46.224.126.196 'touch /home/jarvis/rexfinhub/data/.summary_paused'
```

**Roll back a bad webapp deploy**: Render dashboard → Deploys tab → "Rollback to previous" on the bad deploy. No CLI needed.

### oncall-checks

If something feels off but no email said so:

1. **Did Bloomberg pull happen?**
   ```bash
   ssh jarvis@46.224.126.196 'ls -la /home/jarvis/rexfinhub/data/DASHBOARD/bloomberg_daily_file.xlsm'
   ```
   File mtime should be < 24 hours old on a weekday.

2. **Did preflight write a decision file?**
   ```bash
   ssh jarvis@46.224.126.196 'cat /home/jarvis/rexfinhub/data/.preflight_decision.json'
   ```
   Should show `action: GO`, recent `decided_at` timestamp.

3. **Did the daily send fire?**
   ```bash
   ssh jarvis@46.224.126.196 'tail -20 /home/jarvis/rexfinhub/data/.send_log.json'
   ```

4. **Are timers running on schedule?**
   ```bash
   ssh jarvis@46.224.126.196 'sudo systemctl list-timers --all | grep rexfinhub'
   ```

5. **What did the last preflight audit say?**
   ```bash
   ssh jarvis@46.224.126.196 'cat /home/jarvis/rexfinhub/data/.preflight_result.json | python -m json.tool'
   ```

### known-gaps

- GAP-01: No single `/admin/dashboard` showing live status (gate state, last preflight outcome, secret-expiry warnings) in one view. `/admin/system-state` (Phase 7B) covers flags + preflight runs + events; a fully unified dashboard is still missing.
- GAP-02: No way to triage from mobile. The 08:00 email is readable but action requires being at a desk.

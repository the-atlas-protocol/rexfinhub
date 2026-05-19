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
| 08:00 | Read morning `[PIPELINE]` summary email | ~2 min — scan, triage any red items by asking Claude to investigate |
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

The `[PIPELINE]` summary email arrives at 20:15 ET tonight, moving to 08:00 ET after Phase 6.

Email contents:
- Overall PASS / WARN / FAIL badge at top
- 6 stage rows (Bloomberg sync → Classification → Preflight → GO/HOLD → Gate transitions → Email sends) with green/amber/red status
- After Phase 6: ~25 data-quality assertions listed; failed ones called out

**Triage flow**:
1. Open email
2. Scan for red items
3. If all green → done (~30 sec)
4. If red → quote the failure to Claude in chat. Claude investigates + proposes fix. You approve.

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

- GAP-01: This runbook documents future-state touchpoints (Phase 2 admin pages) that don't yet exist. Sections marked "Future" describe the target, not current capability.
- GAP-02: No `/admin/dashboard` that shows live status (gate state, last preflight outcome, secret-expiry warnings). Today these checks require SSH.
- GAP-03: No way to triage from mobile. The 08:00 email is readable but action requires being at a desk.

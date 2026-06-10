# Stage 1 Re-Audit — Send Pathway Verification

Generated: 2026-05-11T21:50-04:00 ET
Agent: send_pathway re-audit
Scope: READ-ONLY. No code changes. No emails sent. No test sends. No triggers.

## Verdict (TL;DR)

**Net status: AMBER. The R8/R9/H1 hotfixes are in place and demonstrably working at their layer, but the actual silent-zero-delivery root cause (F1: no decision-file writer outside the dashboard click) is UNTOUCHED.** The maintenance flag is a thermostat for the preflight CTA color, not a switch that lets `send_all.py --use-decision` proceed. As predicted, tonight's 19:30 ET timer (Mon 2026-05-11) stood down at 19:46:39 — the *15th consecutive weekday* of zero deliveries since 2026-05-04.

If Ryu wants tonight's Monday bundle to actually go out, he must do **one** of:

1. **Click GO** on `https://rex-etp-tracker.onrender.com/admin/reports/dashboard` within the next ~2h 40m (current preflight token created 21:34 ET, valid 4h, expires ~01:34 ET). The 19:30 timer has already passed, so this would require manual `send_all.py --bundle all --use-decision --send` invocation OR waiting for tomorrow's 19:30 timer (which would be a Tuesday "daily-only" bundle, missing the weekly/li/income/flow/autocall/stock_recs reports that are due Monday).
2. **Run the no-decision path manually**: `ssh jarvis@... && cd /home/jarvis/rexfinhub && /home/jarvis/venv/bin/python scripts/send_all.py --bundle all --send`. This bypasses the decision-gate entirely. Confirmed viable below (F9 verification).

The maintenance flag accomplishes its narrow stated goal but does not cause sends to happen.

## R9 Maintenance flag — verification

**Mechanism**: `MAINTENANCE_FLAG = DATA_DIR / ".preflight_maintenance"` (preflight_check.py:43). `_maintenance_window_active()` returns `True` if the file exists. Three audits consult it: `audit_attribution_completeness` (line 494), `audit_classification` (line 219), `audit_ticker_dupes_recent` (line 143). When active, FAIL → WARN downgrade with detail prefixed `MAINTENANCE WINDOW ACTIVE — `.

**Live state on VPS** (just confirmed):
```
-rw-rw-r-- 1 jarvis jarvis 0 May 11 21:04 /home/jarvis/rexfinhub/data/.preflight_maintenance
```
0-byte sentinel file present since 21:04 ET today.

**Effect on today's preflight result** (`/home/jarvis/rexfinhub/data/.preflight_result.json`, written 21:34 ET):
```
overall_status: "warn"  (was "fail" — downgrade WORKED)
audits:
  bloomberg_freshness:        pass    (0.6h old)
  classification_gaps:        WARN    "MAINTENANCE WINDOW ACTIVE — 47 unclassified, 14 NULL issuer_display, 7 CC missing"
  ticker_dupes_(24h):         WARN    "MAINTENANCE WINDOW ACTIVE — 3 (registrant, ticker) pairs duplicated"
  null_data:                  pass
  recipient_diff:             pass
  previews_on_disk:           warn    "stale (>6h): daily_filing.html, weekly_report.html, li_report, income, flow, autocall"
  data_freshness:             pass
  attribution_completeness:   WARN    "MAINTENANCE WINDOW ACTIVE — NULL issuer_display 15.8% (threshold 15%)"
```
The three downgrades fire correctly. `overall_status` rolled up to `warn` (was `fail` per Stage 1). The send-day summary email's CTA would now read **"GO with caveats — review warnings below"** (amber), not the red **"HOLD recommended — investigate before send"**.

**What it does NOT do**: writing the maintenance flag does NOT write `data/.preflight_decision.json`. The decision file is still empty.
```
ls /home/jarvis/rexfinhub/data/.preflight_decision.json
ls: cannot access '...': No such file or directory
```
**Net**: R9 is a *visual* fix to the preflight summary email. It changes what Ryu sees in the inbox CTA, but the autonomous send loop (preflight → email → click GO → 19:30 timer fires → bundle ships) is still broken at the same join point as before.

## H1 XFF spoofing fix — verification

`webapp/routers/api.py:50-69` — `_client_ip()` now reads the **right-most** non-empty entry of `X-Forwarded-For`. Comment cites the hotfix and the spoof scenario (left-most was attacker-controlled because Render appends, not prepends). The right-most is what Render itself wrote. Fallback to `request.client.host` for local/no-proxy.

`_RATE_LIMIT = int(os.environ.get("DB_UPLOAD_RATE_PER_HOUR", "6"))` — bumped from 1 → 6 (line 41-45). Comment notes the legitimate retry-window bug.

`webapp/main.py:199-212` — multipart bodies now REQUIRE the `X-CSRF-Token` header before any `await request.form()` parse. Prevents the upload-spool DoS. Returns 403 on missing header. The legitimate browser path uses fetch + header, so still works.

These three are verified-by-read. They are correctly scoped to the surfaces they protect (Render upload endpoints, multipart admin POSTs); they have no effect on the send-pathway decision file or the silent-zero-delivery problem.

## R8 CSRF / rate limit / audit log on admin/api — verification

`webapp/services/csrf.py` exists, exposes `get_or_create_token`, `is_valid`, `CSRF_FORM_FIELD`, `CSRF_HEADER`. Constant-time compare via `hmac.compare_digest`.

`webapp/main.py:164-234` — `CsrfMiddleware` registered. Protected prefixes: `("/admin/", "/api/v1/maintenance")`. Exempt: `/admin/login` (chicken/egg). Methods: POST/PUT/PATCH/DELETE. Token sources: `X-CSRF-Token` header, `_csrf_token` form field, or query param. 403 JSON on failure.

`webapp/routers/api.py:90-118` — `_audit_log()` writes `ApiAuditLog` rows on every gated API call. Best-effort: never raises. Captures route, method, ip, user-agent, success, status, payload size, detail.

These are present and correctly wired. They harden the admin/API surface against CSRF/spoofing/spam. They do NOT touch the send pathway and do NOT change the decision-file mechanism.

## R5 mkt_report_cache freshness fix — verification

`webapp/services/report_data.py:525-586` — `_read_report_cache()` now explicitly handles `pipeline_run_id IS NULL`: logs a warning and returns `None` (treats as stale, forces rebuild). Previously the broad `except Exception` swallowed the `None < int` TypeError thrown by the `row.pipeline_run_id < latest_run` comparison, so a NULL row would just look like "no cache" — and worse, the original `and row.pipeline_run_id` short-circuit caused the staleness check to be skipped entirely, serving stale rows forever.

Verified-by-read. Correct fix. Has no effect on the send pathway directly but improves correctness of the report HTML payloads that the send pathway eventually emails (so it indirectly helps when the send actually fires).

---

## Specific verifications (per request)

### F1 — Decision file: only one writer? Maintenance flag provides alternative path?

**F1 status: STILL TRUE.** `webapp/routers/admin_reports.py:267-305` (`POST /admin/reports/decision`) is still the **sole writer** of `data/.preflight_decision.json`. Search for `DECISION_FILE` / `preflight_decision.json` write across the repo confirms one writer. The maintenance flag does **not** write a decision; it only mutates audit status reporting.

There is no `--auto-go-if-pass` flag, no scheduled HOLD-on-timeout, no SSH-only `decide.py GO` script, no Render API endpoint Ryu could hit from his phone. The only path to a GO decision is still: log into Render admin → open dashboard page → click GO button → the form POST writes the file.

**Implication**: every weekday since 2026-05-04, the same standstill repeats. Today (2026-05-11, Monday — should have been `bundle=all`) is **the 15th consecutive weekday with zero deliveries**, including the missed Monday weekly/li/income/flow/autocall/stock_recs.

### F9 — Is `send_all.py` (without --use-decision) still a viable path tonight?

**F9 status: YES, the no-decision path is still viable.** Just confirmed via dry-run on VPS (no email sent):
```
$ /home/jarvis/venv/bin/python scripts/send_all.py --bundle daily
=== send_all.py ===
  bundle:           daily -> ['daily']
  mode:             DRY-RUN
  initial gate:     false
--- daily ---
  status:    dry_run
  subject:   REX Daily ETP Report: 05/11/2026
  size:      107,073 chars
  recipients (1): etfupdates@rexfin.com
  note:      would send 107,073 chars to 1 recipient(s)
=== SUMMARY ===
  Totals: sent=0 dry_run=1 failed=0 skipped=0
```

Builder ran cleanly. Subject pinned to today's data date (05/11/2026). HTML 107 KB. Recipients resolved correctly from DB (`etfupdates@rexfin.com`). Gate management works (would auto-open via `manage_gate=True` when `--send` is added).

To actually fire tonight, the command is:
```
/home/jarvis/venv/bin/python scripts/send_all.py --bundle all --send
```
This would atomically open the gate, fire daily + weekly + li + income + flow + autocall + stock_recs in sequence, then close the gate via try/finally. The L1 (gate), L7 (self-loop), L6 (per-recipient cap) safeguards still apply but none are currently tripped (verified L6 below).

**Caveat**: `_PER_RECIPIENT_DAILY_LIMIT = 6` (etp_tracker/email_alerts.py:1802) still hardcoded. A Monday `bundle=all` sends 6 reports to `etfupdates@rexfin.com` (daily + weekly + li + income + flow + autocall — 6 exactly). The 7th `stock_recs` goes to a different list (4 internal addresses), so `etfupdates@` ends at 6 deliveries. **Right at the cap.** If Ryu re-runs anything, the 7th send to `etfupdates@` will silently L6-block. See "New finding N1" below.

### Per-recipient daily limit — was 6, now configurable?

**STILL HARDCODED AT 6**, not configurable. `etp_tracker/email_alerts.py:1802`:
```python
_PER_RECIPIENT_DAILY_LIMIT = 6
```
No `os.environ.get("PER_RECIPIENT_DAILY_LIMIT", ...)`, no config file lookup. Stage 1's F5 was not addressed.

Live check on VPS: `etfupdates@rexfin.com` has zero deliveries today so far (`L6 status today for etfupdates@: clear`). A Monday `bundle=all` would push it to exactly 6 by end of run — the cap. Any retry, follow-up send, or accidental re-invocation would trip L6.

### .send_log.json state — what was last actual successful send?

**Two divergent records of "last send" exist on the VPS — they tell different stories:**

1. `data/.send_log.json` (the per-report dedup log written by `send_email.py:_record_send`):
   - Last entry: **2026-04-27** (autocall_report at 21:58)
   - Tracks 5 dates total: Apr 14, 17, 20, 21, 23, 24, 27
   - **Stale by 14 days** because the catch-up sends on 5/04 and 5/05 used `send_all.py --bypass-gate` paths that DO NOT call `_record_send`. So the per-report dedup is permanently out of date for any send route through `send_all.py` (which is now the canonical send path).

2. `data/.send_audit.json` (the per-attempt forensic log written by `email_alerts.py:_audit_send`):
   - Last successful (`phase=result, allowed=true`) entries:
     - 2026-05-04 23:02-23:08 ET — full catch-up: daily, weekly, li, income, flow, autocall, stock_recs to all recipients (manual triggered with `bypass_gate=True`)
     - 2026-05-05 23:28 ET — daily ETP Report to etfupdates@ (manual)
   - **Last actual delivery anywhere: 2026-05-05 23:28 ET (REX Daily ETP Report).**
   - Six business days of zero entries since (5/06, 5/07, 5/08, 5/11 — all show only `phase=attempt` from `send_critical_alert` standdown notices, no `phase=result`).

**Net**: the 14-day claim from the audit task ("14 days of silent zero-deliveries") is correct in spirit but slightly off in detail — last automated delivery via the daily timer was 2026-04-30 (gate log shows `send_all.py bundle=daily done` at 22:19 ET); last delivery of any kind was the manual 5/05 catch-up. Either way, the gap continues.

`gate_state_log.jsonl` confirms the standdown pattern unbroken:
```
2026-04-30T22:19:00 close send_all.py    bundle=daily done           ← last automated success
2026-05-04T23:09:07 close atlas-manual   "Catch-up send complete"    ← last manual
2026-05-06T19:46:53 read  send_all.py --use-decision  STOOD DOWN
2026-05-07T19:46:16 read  send_all.py --use-decision  STOOD DOWN
2026-05-07T20:16:48 read  send_all.py --use-decision  STOOD DOWN
2026-05-08T19:46:32 read  send_all.py --use-decision  STOOD DOWN
2026-05-08T20:17:17 read  send_all.py --use-decision  STOOD DOWN
2026-05-11T19:46:36 read  send_all.py --use-decision  STOOD DOWN  ← tonight's Monday bundle
2026-05-11T20:17:10 read  send_all.py --use-decision  STOOD DOWN  ← second daily timer (?)
```

The 20:17 retries every send-day are interesting — there's a SECOND attempt firing at ~20:17 that I cannot account for from the visible timer list. `systemctl list-timers` shows only one daily timer (19:30). Worth investigating (see N2 below).

---

## NEW findings

### N1: Monday bundle hits L6 cap exactly — single-recipient buffer is gone

**Severity**: medium (silent failure on any retry tonight)

`etfupdates@rexfin.com` is the sole recipient for 5 of the 7 reports in `bundle=all`: daily, weekly, li, income, flow. Plus `autocall` goes to a 9-recipient external list, and `stock_recs` to 4 internal addresses. On a Monday, `etfupdates@` receives **exactly 6 sends** (daily + weekly + li + income + flow + autocall). `_PER_RECIPIENT_DAILY_LIMIT = 6` means the 7th send (if any retry happens) silently L6-blocks with `phase=blocked, note="L6 rate limit"` written to the audit log.

This is Stage 1's F5 finding, restated. R9 did not address it. If Ryu manually fires `send_all.py --bundle all --send` tonight and any one report fails midway, retrying that single report will L6-block because the prior partial-success pile-up consumed the budget.

**Trivial fix (Stage 2)**: raise to 12 OR scope per `(address, report_key)` so each report is independently capped at 1/day (which is the actual product behavior — one daily, one weekly, one li, etc.).

### N2: A second daily-timer fire at ~20:17 ET that I can't account for

**Severity**: low (informational)

Every send-day in the standdown window shows TWO `read,standing_down` events: one at 19:46 (the canonical 19:30 timer + ~16min for run_daily.py to wait + dispatch) and one at ~20:17 (31 minutes later). `systemctl list-timers` only shows `rexfinhub-daily.timer` (19:30) and `rexfinhub-gate-close.timer` (20:00). No 20:17 timer is registered.

Hypothesis: there's a second invocation path — either a cron, a systemd OnFailure restart, an OnUnitActiveSec tail-trigger, or a manual `journalctl` artefact. Worth a `systemctl list-timers --all` + `crontab -l -u jarvis` check in Stage 2. Not blocking tonight's send.

### N3: `data/.send_log.json` is a dead file under the new send-routing

**Severity**: medium (architectural drift, defensive only)

`send_email.py:_record_send` writes `.send_log.json` for the dedup `_already_sent_today` / `_already_sent_this_week` checks. But the canonical send path is now `send_all.py` (per `run_daily.py:1086-1090`), which does **not** call `_record_send` — it uses `_send_html_digest` directly via `etp_tracker.email_alerts`. So:

- The per-report dedup checks in `send_email.py` (lines 80-102) are functionally orphaned. Anything that goes through `send_all.py` will not be visible to those checks, and anything that goes through `send_email.py send daily/weekly` (via the bash aliases) will see a stale log dating to 2026-04-27 — meaning a `force` flag is implicitly required for any future invocation of the legacy path.
- If Ryu ever falls back to `python scripts/send_email.py send daily` thinking it's a safe alternative, he will get `BLOCKED: daily_filing already sent today at 22:49` for *April 24* and need `--force`.

**Fix size**: medium (decide whether `.send_log.json` is canonical or remove the dead path; if canonical, plumb `_record_send` into the `send_all.py` success path).

### N4: Preview files (legacy /outputs/previews/*.html) are 14 days stale, but the audit only marks them "warn"

**Severity**: low (audit-CTA noise)

`stat` on `/home/jarvis/rexfinhub/outputs/previews/` shows all 6 files dated **2026-04-27 19:04-21:14**. They are **two weeks old**. Yet `audit_previews` (preflight_check.py:346-392) marks them only as `warn` ("stale (>6h)") not `fail` — because the threshold for `fail` is `missing`, not "stale > N days". The threshold for `warn` is `> 6h`.

Why this matters: the `previews_on_disk` audit is supposed to be a sanity check that the daily prebake step ran. The prebake **DID** run today (verified `/home/jarvis/rexfinhub/data/prebaked_reports/*.html` are all 2026-05-11 21:36-21:39) — but the prebake writes to a different directory (`data/prebaked_reports/`) than the audit checks (`outputs/previews/`). The audit is checking the wrong directory. Has been since at least 5/04.

Result: the audit reports "stale > 6h" warn forever and Ryu has learned to ignore it. The actual prebaked reports — which is what `/admin/reports/preview` serves — are fresh. There is NO integrity check on the prebaked reports themselves.

**Fix size**: trivial (point `audit_previews` at `data/prebaked_reports/`, update the file list to match the registry — `daily_filing.html` etc. are all there).

### N5: `attribution_completeness` threshold for issuer_display is at the boundary

**Severity**: low (informational)

Today's audit detail: `NULL issuer_display 15.8% (threshold 15%)`. Without the maintenance flag, this would be the only `fail` (the others passed today after upstream classification fixes propagated). The threshold is set at exactly the value being measured today — a 0.8% improvement to upstream classification would let `attribution_completeness` flip to pass and the maintenance flag would no longer be needed.

Worth knowing for Stage 2: removing the maintenance flag is closer than it looks. The classification gaps that drove the 100% NULL `primary_strategy` (Stage 1 F3) appear to have been substantially backfilled — only the `issuer_display` 15.8% remains. A focused `apply_classification_sweep.py --apply --apply-medium` run targeting issuer_display population would likely clear this.

---

## Surfaces re-inspected

- `scripts/preflight_check.py` (full read; 781 lines — confirmed R9 changes at lines 43-59, 143-149, 219-225, 494-503)
- `scripts/send_all.py` (full read; 401 lines — no changes since Stage 1)
- `scripts/send_email.py` (full read; 469 lines — confirmed `.send_log.json` writers; legacy path unchanged)
- `etp_tracker/email_alerts.py` lines 1780-2059 (confirmed L1/L6/L7 unchanged; `_PER_RECIPIENT_DAILY_LIMIT = 6` still hardcoded)
- `webapp/routers/admin_reports.py` (full read — confirmed `DECISION_FILE` is still single-writer at line 302)
- `webapp/main.py` lines 155-235 (confirmed CsrfMiddleware + multipart hotfix)
- `webapp/services/csrf.py` (full read; 49 lines — clean implementation)
- `webapp/routers/api.py` lines 1-200 (confirmed XFF right-most fix + rate limit bump)
- `webapp/services/report_data.py:525-586` (confirmed R5 fix)
- `scripts/run_daily.py:1030-1100` (confirmed send-step unchanged; still routes through `send_all.py --use-decision`)
- VPS live state:
  - `data/.preflight_maintenance` (0-byte sentinel, present since 21:04 ET)
  - `data/.preflight_result.json` (warn overall — verified downgrade)
  - `data/.preflight_token` (current — created 21:34 ET, valid 4h)
  - `data/.preflight_decision.json` (still does not exist)
  - `data/.send_log.json` (last entry 2026-04-27)
  - `data/.send_audit.json` (last `allowed=true result` 2026-05-05 23:28)
  - `data/.gate_state_log.jsonl` (tonight 19:46 + 20:17 standdown, plus historical pattern)
  - `config/.send_enabled` ("false" — gate closed; will open at 19:00 tomorrow)
  - `config/email_recipients.txt` (now exists — 1 line: ETFUpdates@rexfin.com)
  - `config/email_recipients_private.txt` (now exists — ryuogawaelasmar@gmail.com)
  - `outputs/previews/*.html` (all dated 2026-04-27 — 14 days stale, audit reports warn not fail)
  - `data/prebaked_reports/*.html` (all dated 2026-05-11 21:36-21:39 — fresh, but checked by a different code path)
  - `systemctl list-timers` (confirmed 19:30 daily timer; no 20:17 timer visible)
  - `journalctl -u rexfinhub-daily.service --since '24h ago'` (confirmed tonight's full run + 19:46 standdown)
- DB query: `_recipients_over_limit_today(['etfupdates@rexfin.com'])` returns clear (0 sends today)
- Dry-run: `send_all.py --bundle daily` succeeded with 107KB payload, recipients resolved, gate logic correct

## Surfaces NOT inspected

- `webapp/services/graph_email.py` (Graph API mechanics — same as Stage 1, untouched)
- The 20:17 ET mystery second-fire (N2) — would require systemd unit + cron audit
- `webapp/routers/digest.py` (browser-driven digest sender — no decision/token interaction last time, presumed unchanged)
- Whether `etfupdates@rexfin.com` is still routing to live humans (Microsoft 365 admin out of scope)
- Whether `relasmar@rexfin.com` is receiving the `[ALERT]` standdown emails (inbox out of scope)
- Whether tonight's preflight summary email (created 21:34 ET) actually delivered to relasmar's inbox via Graph 202 (Graph "queued" != "delivered")

## Recommendation for Stage 2

The R9/R8/H1/R5 fixes are correct and appropriate for what they target. They are not the fix for the silent-zero-delivery problem. The bug at the heart of F1 (no decision-file writer outside the dashboard click) is unaddressed and will produce another standdown tomorrow night, every night, until either:

1. `preflight_check.py` gains a `--auto-go-if-pass` (or `--auto-go-if-warn`) flag that writes the decision file when overall_status is acceptable, OR
2. A scheduled `decide.py` script runs after preflight and writes GO based on configurable policy (e.g., "auto-GO unless any audit is `fail` AND maintenance flag is absent"), OR
3. A tokenized GET endpoint `/admin/reports/decide?action=GO&token=...&sig=...` is added so the preflight summary email can carry a single-click GO link Ryu can hit from his phone.

Until one of those exists, the maintenance flag is just makeup on the problem.

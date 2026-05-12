# Stage 1 Audit — Send Pathway

Generated: 2026-05-11T18:53-04:00 ET
Agent: send_pathway
Scope: READ-ONLY. No code changes. No emails sent. No files written outside this report.

## Summary

The "silent fail" is not silent — it is a **designed standstill that almost no one is taught about**. The daily send is gated by a manual click on `/admin/reports/dashboard` that writes `data/.preflight_decision.json`. **There is exactly one writer of that file in the entire codebase: the POST `/admin/reports/decision` endpoint, called by an admin-authenticated form submit.** No timer, no script, no fallback ever creates it. Since 2026-05-04 (the last manual catch-up send), Ryu has not clicked GO, so every weekday `rexfinhub-daily.service` has correctly entered `--use-decision` mode at 19:30 ET, found no decision file, written "ABORT" to journald, fired a `send_critical_alert`, and exited 3. The journal log line "send_all: standing down (no decision or token mismatch)" is the *expected branch* of the code path — it is not an error. The pipeline thinks it succeeded.

The system is **architecturally one click away from working any day Ryu wants it to**, but several real bugs surround it: (a) the preflight summary email's GO button is a `<form action="POST /admin/reports/decision">` that requires Ryu to be logged into Render *and* requires the dashboard page itself, not the email — the email contains a non-clickable code block telling him to run a CLI command, not a one-click GO; (b) `audit_attribution_completeness` returns `fail` every day on 100% NULL `primary_strategy` and 64% NULL `issuer_display`, so the preflight summary's CTA always reads "HOLD recommended — investigate before send", which is exactly why Ryu doesn't click GO; (c) the preflight `--post-summary` only goes to `relasmar@rexfin.com` via `send_critical_alert`, which has a 1-hour module-level cooldown that survives only within the oneshot process — so that part is fine, but the alert payload itself is mislabelled `[PREFLIGHT]` and gets buried in noise; (d) `_send_html_digest` is wired with a `_PER_RECIPIENT_DAILY_LIMIT = 6`, and the audit log shows the 2026-05-04 catch-up burst hit 7 sends to `etfupdates@rexfin.com` — close enough to the cap that the next bundle send may silently L6-block. Highest-confidence single-line fix would be to make preflight auto-write a `GO` decision when `overall_status in ('pass','warn')` and only require manual click on `fail` — but that is Stage 2.

## Pathway diagram (text)

```
                       ┌─────────────────────────────────────────┐
                       │ rexfinhub-preflight.timer (Mon-Fri 18:30 ET) │
                       └─────────────────────┬───────────────────┘
                                             ▼
        scripts/preflight_check.py --post-summary
          • runs 8 audits → write_result_json() → data/.preflight_result.json
          • write_token() → data/.preflight_token  (4h validity)
          • build_summary_html() → outputs/preflight_summary.html
          • send_critical_alert() → relasmar@rexfin.com  ([PREFLIGHT] subject)
                                             │
                                             │ Ryu reads email
                                             ▼
        ┌──────────────────────────────────────────────────────────┐
        │  GO button is a <form POST /admin/reports/decision>      │
        │  inside the dashboard page, NOT inside the email.        │
        │  Email shows a code block: "python scripts/send_all.py   │
        │  --bundle all --send" and a link to the dashboard URL.   │
        │  Ryu must (a) click the link, (b) auth as admin via      │
        │  Render, (c) click GO on the dashboard page.             │
        └────────────────────┬─────────────────────────────────────┘
                             │ (click)
                             ▼
        POST /admin/reports/decision (admin_reports.py:267)
          • _check_auth() — needs cookie 'admin_auth'==ADMIN_PASSWORD
            OR session['is_admin']==True
          • verifies posted token == TOKEN_FILE token
          • writes data/.preflight_decision.json    ← THE ONLY WRITER

                             │
                             ▼ (gate timer fires independently)

  ┌──────────────────────────────────────────┐    ┌──────────────────────────────┐
  │ rexfinhub-gate-open.timer (19:00 Mon-Fri)│    │ rexfinhub-daily.timer (19:30)│
  │  → echo true > config/.send_enabled      │    │  → run_daily.py --skip-sec   │
  └──────────────────────────────────────────┘    └──────────────┬───────────────┘
                                                                 ▼
                                  scripts/run_daily.py final step (line 1041-1097)
                                    • waits until 19:30 if early
                                    • Mon → bundle=all, else bundle=daily
                                    • subprocess: send_all.py --bundle X --use-decision --send
                                    • on returncode==3 prints "standing down" and EXITS 0  ← silent
                                                                 ▼
                              scripts/send_all.py --use-decision (line 246+)
                                ┌───────────────────────────────────────┐
                                │ if not decision_file.exists():        │
                                │   print "ABORT…"                      │
                                │   send_critical_alert(SEND STOOD DOWN)│
                                │   gate_log("standing_down")           │
                                │   return 3   ← every day since 5/04   │
                                └───────────────────────────────────────┘
                                                                 ▼
                                      (decision exists & token matches & action==GO)
                                                                 ▼
                                  open_gate() → config/.send_enabled = "true"
                                                                 ▼
                          for each report in BUNDLES[bundle]:
                            _send_one() → _resolve_recipients(list_type)
                                            └─→ _load_recipients()  (DB primary, .txt fallback,
                                                                     env SMTP_TO last resort)
                                                  └─→ webapp/services/recipients.get_recipients
                                                        SELECT email FROM email_recipients
                                                        WHERE list_type=? AND is_active=True
                                          → builder(db) → (subject, html)
                                          → _send_html_digest()
                                                ├ L8 attempt audit
                                                ├ L7 self-loop block  (vs AZURE_SENDER)
                                                ├ L1 gate check       (config/.send_enabled)
                                                ├ L6 per-recipient/day limit (=6)
                                                └ Graph API send_email()  (Azure, NOT SMTP)
                                                  └ L8 result audit
                                                                 ▼
                                  finally: close_gate() → config/.send_enabled = "false"

  Note: the docstring says "SMTP" but the code uses Microsoft Graph API exclusively
  (etp_tracker/email_alerts.py:2027-2042). SMTP fallback is explicitly disabled.
```

## Findings

### F1: Decision file has exactly ONE creator — a manual admin form click — and no fallback
- **Severity**: critical
- **Surface**: `webapp/routers/admin_reports.py:267-303` (sole writer); `scripts/send_all.py:259-265` (consumer aborts when missing)
- **Symptom**: Every weekday since 2026-05-04, the daily timer enters `--use-decision`, finds no `data/.preflight_decision.json`, prints "ABORT", and exits 3. Run_daily.py treats returncode 3 as success (`script run_daily.py:1092-1095`: "Decision missing / token mismatch — common on send-days where Ryu hasn't clicked GO yet. Not a failure.").
- **Evidence**: `grep DECISION_FILE C:/Projects/rexfinhub` returns exactly one writer (admin_reports.py:302). Live VPS shows `.preflight_token` and `.preflight_result.json` updated 18:30:48 today, but **no `.preflight_decision.json`** in `/home/jarvis/rexfinhub/data/`. Last 6 calendar weekdays of journald all show identical "ABORT… Dashboard click required" → "send_all: standing down" sequence.
- **Blast radius**: ALL daily/weekly/li/income/flow/autocall/stock_recs sends are blocked. Pipeline appears green in monitoring because `run_daily.py` exits 0 (treats 3 as not-a-failure).
- **Hypothesis**: This was deliberately designed as a manual-confirm gate (commit comment 2026-04-28: "autonomous send-day flow" — the autonomous part is the audit, not the click). The flaw is no fallback path: there is no opt-in auto-GO when audits pass, no scheduled HOLD timeout, no second authoritative writer (e.g., a `--auto-go` CLI flag, an SSH-only path, a Render API endpoint Ryu could hit from his phone). Once Ryu stops clicking, the system silently does nothing forever.
- **Fix size**: small (add a `--auto-go-if-pass` flag to preflight that writes the decision file when `overall_status == "pass"`) OR small (add a CLI script `scripts/decide.py GO|HOLD` that admin can run via SSH); architectural if you want a phone-friendly one-click path.

### F2: Preflight summary email contains no clickable GO button — only a CLI command
- **Severity**: high
- **Surface**: `scripts/preflight_check.py:622-648` (build_summary_html → cta block)
- **Symptom**: The email's primary action is a `<code>` block with shell text `python scripts/send_all.py --bundle all --send` and a hyperlink to the dashboard. There is no `<a href="https://.../decide?token=xxx&action=GO">` style one-click link. Ryu must (a) read the email on his phone, (b) decide to act, (c) switch to a desktop, (d) log into Render admin, (e) click GO. The friction is high enough that the queue of unclicked GO buttons grows indefinitely.
- **Evidence**: preflight_check.py:632 `<code>python scripts/send_all.py --bundle all --send</code>` is the headline action. The dashboard link is just a hyperlink into a *page* with the form, not a one-click decision URL.
- **Blast radius**: Compounds F1. Even when audits PASS and Ryu wants to send, the click cost is high.
- **Hypothesis**: This was intended for desktop usage — "open dashboard, click GO" — but in practice Ryu reads email on phone and the friction defeats the autonomous loop.
- **Fix size**: medium (add a tokenized GET endpoint `/admin/reports/decide?action=GO&token=xxx&sig=xxx` that records the decision without a session login; embed the URL in the preflight email as a button).

### F3: Audit `attribution_completeness` returns FAIL every day on 100% NULL `primary_strategy`
- **Severity**: high (operational), medium (technical)
- **Surface**: `scripts/preflight_check.py:424-480`
- **Symptom**: `audit_attribution_completeness` thresholds: 5% NULL primary_strategy, 15% NULL issuer_display. Live result today: NULL primary_strategy 100.0%, NULL issuer_display 64.5%. This forces `overall_status="fail"` every weekday, and the CTA shown to Ryu reads **"HOLD recommended — investigate before send"** in red. Naturally, Ryu does not click GO when the email screams HOLD.
- **Evidence**: `data/.preflight_result.json` today: `"attribution_completeness": {"status": "fail", "detail": "NULL primary_strategy 100.0% (threshold 5%); NULL issuer_display 64.5% (threshold 15%)"}`. Same audit failed identically on 5/06, 5/07, 5/08, 5/11.
- **Blast radius**: Even if F1 is fixed (auto-GO on pass), it would still always evaluate to fail and never auto-go.
- **Hypothesis**: Either (a) `mkt_master_data.primary_strategy` is genuinely 100% NULL because nothing populates it (in which case the column shouldn't be in the audit), or (b) the column was renamed/migrated and the audit query is hitting the wrong column. Cross-reference: classification system is supposed to populate `primary_strategy` per `audit_classification` results, but `apply_classification_sweep.py --apply --apply-medium` would write to a different column. Worth confirming what `primary_strategy` is supposed to be vs. `etp_category`.
- **Fix size**: trivial if the column is genuinely deprecated (drop from audit); small if column needs to be backfilled; medium if it's a real data hole.

### F4: `run_daily.py` silently maps `send_all` returncode 3 to success
- **Severity**: high
- **Surface**: `scripts/run_daily.py:1092-1097`
- **Symptom**: `if result.returncode == 3: print("send_all: standing down (no decision or token mismatch)")` — and then no `errors.append`, no `critical_ok=False`, no alert. The pipeline reports "success", systemd records `Active: active (exited)`, and no monitoring picks up the daily skip.
- **Evidence**: Lines 1092-1095: rc==3 only prints; rc!=0 (which excludes 3) appends to errors. `send_critical_alert` IS fired from inside `send_all.py` itself (line 295), so Ryu does receive an alert, BUT `_last_alert_time` is module-level state in a fresh oneshot process, so the cooldown doesn't suppress it. Ryu sees a `[ALERT] SEND STOOD DOWN` email at 19:46 every weekday but it's labeled the same as critical pipeline failures and gets ignored.
- **Blast radius**: No external monitoring (UptimeRobot, Healthchecks, Render alerts) catches the daily skip because the unit succeeds. Audit log line says it stood down but only `data/.gate_state_log.jsonl` records it on disk.
- **Hypothesis**: Original author wanted to distinguish "pipeline failed mid-flight" from "everything ran but Ryu chose HOLD" — but conflated "no decision recorded" with "deliberate HOLD". A no-decision is operationally identical to a silent failure.
- **Fix size**: trivial (treat rc==3 with no decision file as a separate exit code, e.g. 4, and log it as a warning that hits monitoring).

### F5: `_PER_RECIPIENT_DAILY_LIMIT = 6` is dangerously close to actual daily volume
- **Severity**: medium
- **Surface**: `etp_tracker/email_alerts.py:1796`, L6 check at line 2009-2016
- **Symptom**: On 2026-05-04 the catch-up send hit `etfupdates@rexfin.com` 7+ times within a few hours (daily + weekly + li + income + flow + autocall = 6 already, plus stock_recs separately = 7). The L6 limit is 6 per address per day. The next bundle delivery to that address that day will silently L6-block.
- **Evidence**: `data/.send_audit.json` 5/04 entries: 7 separate `result/allowed=True` entries within 6 minutes, all to `etfupdates@rexfin.com` via `relasmar` BCC pattern (the catch-up was triggered manually with `bypass_gate=True` so most checks were skipped — but L6 still applies if not bypassed).
- **Blast radius**: A normal Monday all-bundle send hits 6 lists where 5 of them resolve to the SAME address. If the bundle is split across timer reruns, L6 trips at #7 with `note="L6 rate limit"` written to the audit JSON; the user sees no alert.
- **Hypothesis**: Limit was sized for a multi-recipient world where 6 different reports could each pile a few sends per address. With current setup (single shared address per list), a Monday bundle is at the ceiling and a re-run blows past it.
- **Fix size**: trivial (raise to 12 or 20, OR scope per `(address, report_key)` so each report is independently capped).

### F6: Recipient lists are functionally a single shared inbox — no real distribution
- **Severity**: medium
- **Surface**: VPS DB query `SELECT * FROM email_recipients WHERE is_active=1`
- **Symptom**: 5 of the 7 list types (`daily`, `weekly`, `li`, `income`, `flow`) point to **only** `etfupdates@rexfin.com`. `stock_recs` (4 internal addresses) and `autocall` (9 incl. RBC + CAIS) are real distributions; everything else is a shared mailbox alias. If `etfupdates@` is misconfigured, 5 reports vanish silently. There is NO recipient overlap with `relasmar@rexfin.com` for the routine reports — Ryu only sees them if `etfupdates@` actually forwards to him.
- **Evidence**: see "Live state inspection" below — DB dump.
- **Blast radius**: silent delivery hole if etfupdates@ stops forwarding. Also: `expected_recipients.json` matches DB exactly today (preflight `recipient_diff` is `pass`), so an alert wouldn't fire.
- **Hypothesis**: This is intentional consolidation, but no one is reviewing whether `etfupdates@` actually distributes. Worth confirming with Ryu.
- **Fix size**: trivial to add `relasmar@rexfin.com` to all lists for ground-truth verification (already happens via `email_recipients_private` — but that file does NOT exist on VPS, only `.bak`).

### F7: VPS is missing the canonical config recipient files (only `.bak` exist)
- **Severity**: medium (defensive only, since DB is canonical)
- **Surface**: `/home/jarvis/rexfinhub/config/`
- **Symptom**: `email_recipients.txt`, `email_recipients_private.txt`, `digest_subscribers.txt` do NOT exist on VPS — only their `.bak` copies. The `_load_recipients()` fallback path (text file → env SMTP_TO) cannot fire because the `.txt` doesn't exist.
- **Evidence**: `ls /home/jarvis/rexfinhub/config/` shows `email_recipients.txt.bak`, `email_recipients_private.txt.bak`, `autocall_recipients.txt`. No `email_recipients.txt`. `digest_subscribers.txt` also missing.
- **Blast radius**: If DB is unreachable for any reason (locked WAL, schema change, sqlite corruption), `_load_recipients()` falls back to text file → file doesn't exist → falls back to `os.environ.get("SMTP_TO", "")` which returns `""`. Then `_send_one` sees no recipients and returns `status=skipped` with note "no recipients for list_type=X". Silent skip, not an alert.
- **Hypothesis**: The `.bak` files were created during the DB migration and the `.txt` files were intended to be regenerated as a snapshot but never were.
- **Fix size**: trivial (regenerate the `.txt` files from DB query, or remove the fallback path entirely so DB failure is loud).

### F8: `_send_html_digest` audit log uses `_audit_load`/`_audit_save` with a non-atomic full-file rewrite
- **Severity**: low
- **Surface**: `etp_tracker/email_alerts.py:1819-1843`
- **Symptom**: Each call to `_audit_send` reads the entire `.send_audit.json` file (truncated to last 500 entries), appends, and writes the whole file back. No file lock, no atomic temp-then-rename. If two senders run concurrently (which `send_all.py` doesn't do, but `send_critical_alert` could overlap with the daily timer), the loser's entry is lost.
- **Evidence**: Code reads the JSON, appends an entry, writes it back. No `os.replace` or `flock`.
- **Blast radius**: Forensic data only — no operational impact.
- **Fix size**: trivial (write to `.tmp` then `os.replace`).

### F9: Friday's "12 build steps but no send" is *not* a failure — it's the design working as intended
- **Severity**: critical (interpretation matters)
- **Surface**: composite of F1, F3, F4
- **Symptom**: Friday's full-pipeline run completed all 12 steps, BBG sync OK, classification OK, prebake OK, screener cache uploaded, parquets uploaded, DB uploaded → then waited until 19:30, called `send_all --use-decision`, found no decision, returned 3, `run_daily.py` swallowed it, exit 0.
- **Evidence**: `journalctl -u rexfinhub-daily.service` for 5/08:
  ```
  May 08 19:44:20 ...   Archive for 2026-05-08 already present
  May 08 19:44:20 ...   Parquets: 8 uploaded, 0 skipped, 0 failed
  May 08 19:46:35 ... ABORT: --use-decision but no data/.preflight_decision.json. Dashboard click required.
  May 08 19:46:35 ...   Sending via send_all.py --use-decision: daily only...
  May 08 19:46:35 ...   send_all: standing down (no decision or token mismatch)
  ```
  Identical pattern on 5/04 (Mon, "all bundles"), 5/05, 5/06, 5/07, 5/08. Earliest "standing down" in `gate_state_log.jsonl` is 2026-05-06T19:46:53.
- **Blast radius**: Five business days of zero deliveries to all recipients. None of the 18 active recipients across 7 lists got anything. Equity research clients on `stock_recs` and `autocall` went dark.
- **Hypothesis**: The system did exactly what it was designed to do: not send unless explicitly authorized. The design assumed Ryu would be reliably clicking GO — that assumption is broken.
- **Fix size**: depends on chosen remediation (see F1, F2, F3).

### F10: Send pipeline failure recovery is "abort whole bundle on critical=True", "continue on critical=False"
- **Severity**: low (informational)
- **Surface**: `scripts/send_all.py:373-375`
- **Symptom**: REPORTS dict marks only `daily` as `critical=True`. If `daily` send fails, the rest of the bundle is aborted. If any other report fails (weekly, li, income, flow, autocall, stock_recs), the loop continues. There is no per-recipient retry within a single bundle item — Graph API failure at recipient #3 of 9 means the whole report send returns False even if recipients #1-2 received it.
- **Evidence**: send_all.py line 374-375 checks `res["status"] == "failed" and res["critical"]` and breaks. graph_email.send_email is a single call to Graph API with all recipients in `toRecipients`; partial failures are not surfaced per-address.
- **Blast radius**: Graph API typically delivers all-or-nothing per call, so partial-recipient failure is rare. But if one recipient on the autocall list (9 addresses) has a bad address, the whole 9-recipient send fails and no one receives it.
- **Fix size**: medium (per-recipient retry with backoff; or use Graph batch endpoint).

## Live state inspection

```
$ ssh jarvis@46.224.126.196 ls -la /home/jarvis/rexfinhub/data/.preflight*
-rw-r--r-- 1 jarvis jarvis 1234 May 11 18:30 /home/jarvis/rexfinhub/data/.preflight_result.json
-rw-rw-r-- 1 jarvis jarvis  122 May 11 18:30 /home/jarvis/rexfinhub/data/.preflight_token
                              ^^^ NO .preflight_decision.json present.

$ ls -la config/.send_enabled
-rw-rw-r-- 1 jarvis jarvis 6 May  8 20:00 /home/jarvis/rexfinhub/config/.send_enabled
                          ^^^ contents = "false\n", last modified by gate-close.timer 5/08 20:00.
                          (Gate is currently CLOSED. Will open at 19:00 ET today by timer.)

$ cat data/.preflight_token
{
  "token": "2a015053-5c32-4bca-a638-e8343befcf7f",
  "created_et": "2026-05-11T18:30:48-04:00",
  "valid_for_hours": 4
}

$ cat data/.preflight_result.json (today's preflight outcome)
overall_status: "fail"  ← drives "HOLD recommended" CTA
  bloomberg_freshness:        pass  (1.3h old)
  classification_gaps:        FAIL  (79 unclassified, 17 NULL issuer_display, 8 missing CC attrs)
  ticker_dupes_(24h):         FAIL  (3 dupes)
  null_data:                  pass
  recipient_diff:             pass  (DB matches snapshot exactly)
  previews_on_disk:           warn  (6 files >6h stale — daily build hasn't run yet)
  data_freshness:             pass
  attribution_completeness:   FAIL  (NULL primary_strategy 100%, NULL issuer_display 64.5%)

$ systemctl list-timers | grep rexfinhub
NEXT                       LEFT      LAST                       UNIT
Mon 2026-05-11 19:00:00 ET 16min     Fri 2026-05-08 19:00:01 ET rexfinhub-gate-open.timer
Mon 2026-05-11 19:30:00 ET 46min     Fri 2026-05-08 19:30:09 ET rexfinhub-daily.timer
Mon 2026-05-11 20:00:00 ET 1h 16min  Fri 2026-05-08 20:00:15 ET rexfinhub-gate-close.timer
Tue 2026-05-12 18:30:00 ET 23h       Mon 2026-05-11 18:30:05 ET rexfinhub-preflight.timer  ← already ran today at 18:30
                                    (Today's preflight ran successfully and posted summary.)

$ tail data/.gate_state_log.jsonl   (last successful real send was 2026-04-30)
2026-04-28T22:01:47 open  send_all.py    bundle=daily
2026-04-28T22:03:36 close send_all.py    bundle=daily done
2026-04-30T22:17:00 open  send_all.py    bundle=daily
2026-04-30T22:19:00 close send_all.py    bundle=daily done
2026-05-04T23:09:07 close atlas-manual   "Catch-up send complete; locking gate"   ← last real delivery
2026-05-06T19:46:53 read  send_all.py --use-decision   SEND STOOD DOWN -- no decision file
2026-05-07T19:46:16 read  send_all.py --use-decision   SEND STOOD DOWN -- no decision file
2026-05-07T20:16:48 read  send_all.py --use-decision   SEND STOOD DOWN -- no decision file
2026-05-08T19:46:32 read  send_all.py --use-decision   SEND STOOD DOWN -- no decision file
2026-05-08T20:17:17 read  send_all.py --use-decision   SEND STOOD DOWN -- no decision file
                                  ^^^ pattern: 19:46 = daily timer; 20:17 = unknown (possibly retried somewhere)

$ live DB: SELECT list_type, COUNT(*) FROM email_recipients GROUP BY list_type
  autocall    9   (RBC + CAIS + 5 internal)
  daily       1   (etfupdates@rexfin.com only)
  flow        1   (etfupdates@rexfin.com only)
  income      1   (etfupdates@rexfin.com only)
  li          1   (etfupdates@rexfin.com only)
  stock_recs  4   (4 internal — gcollett, meschmann, proddevelopment, sacheychek)
  weekly      1   (etfupdates@rexfin.com only)

  No duplicates. No 'private' list members. No 'intelligence', 'screener', or 'pipeline' lists configured.
  18 total recipients across 7 lists; 13 unique addresses.

$ ls /home/jarvis/rexfinhub/config/  (recipient text files)
  autocall_recipients.txt        ← 1 line (truncated/stub?)
  autocall_recipients.txt.bak    ← real
  email_recipients.txt           ← MISSING
  email_recipients.txt.bak       ← only the .bak survives
  email_recipients_private.txt   ← MISSING
  email_recipients_private.txt.bak ← only the .bak survives
  digest_subscribers.txt         ← MISSING entirely
  expected_recipients.json       ← matches live DB (preflight diff = pass)

$ data/.send_audit.json — last 5 days of result entries
2026-05-04 23:02-23:08 ET — 7 result/allowed=True sends (all bundles, all bypass_gate=True via atlas-manual catch-up)
2026-05-05 23:28 ET — 1 result/allowed=True (daily; bypass_gate=True)
2026-05-06,07,08 ET — NO result entries; only attempt entries by send_critical_alert via send_all.py
                       (the "SEND STOOD DOWN" alerts themselves are sent and logged)
```

## Surfaces inspected

- `scripts/preflight_check.py` (full read; 743 lines)
- `scripts/send_all.py` (full read; 401 lines)
- `scripts/send_email.py` (header read; 469 lines, dedup logic confirmed at lines 80-102)
- `scripts/run_daily.py` (full read; 1181 lines — final send step lines 1041-1100)
- `etp_tracker/email_alerts.py` (recipients lines 61-113; alert lines 1872-1953; send + safeguards 1956-2048; audit log 1791-1843)
- `webapp/routers/admin_reports.py` (full read; 306 lines)
- `webapp/routers/admin_health.py` (relevant slices on preflight_decision_state)
- `webapp/services/admin_auth.py` (full read)
- `webapp/services/recipients.py` (full read)
- `config/expected_recipients.json` (snapshot)
- `config/email_recipients.txt` (LOCAL only — VPS missing)
- `deploy/systemd/rexfinhub-preflight.{service,timer}` (full read)
- `deploy/systemd/rexfinhub-daily.{service,timer}` (full read)
- `deploy/systemd/rexfinhub-gate-open.{service,timer}` (full read)
- `deploy/systemd/rexfinhub-gate-close.{service,timer}` (full read)
- VPS live state: `data/.preflight_*`, `config/.send_enabled`, `data/.gate_state_log.jsonl`, `data/.send_audit.json`, `data/.send_log.json`, `email_recipients` table, journald for preflight + daily services (7-day window)

## Surfaces NOT inspected

- `webapp/services/graph_email.py` — Graph API send mechanics (token acquisition, payload shape, error returns). Out of scope for this stage but relevant to F10 retry behavior.
- Full `etp_tracker/email_alerts.py:220-1750` — digest HTML builders, market snapshot, top filings; not relevant to send pathway.
- `webapp/routers/digest.py` — possible secondary trigger for sends; quick grep showed no decision/token interactions.
- `webapp/routers/admin.py` — main admin router; not the report-decision endpoint.
- `scripts/prebake_reports.py` — report HTML pre-baker; relevant to F3 (preview staleness) but not to send blocking.
- `webapp/services/report_registry.py` — registry of active reports (10 today, label says 7); cosmetic per existing audit doc.
- Whether `etfupdates@rexfin.com` actually forwards to live humans (would require Microsoft 365 admin access, out of scope).
- Whether `relasmar@rexfin.com` is receiving the `[ALERT] SEND STOOD DOWN` emails (would require inbox access).
- The actual content of today's preflight summary email (the HTML on disk was read; whether Graph API delivered it to inbox is unconfirmed — the journal says "SENT" but Graph 202 only means "queued").
- 13F send pathway, if any (Stage 1 scope is daily/weekly only).

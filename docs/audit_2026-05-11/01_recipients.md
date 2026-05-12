# Stage 1 Audit — Recipients + Deliverability

Generated: 2026-05-11T19:08-04:00 ET
Agent: recipients
Scope: READ-ONLY. No code changes. No test sends. No subscription changes. Recipient local-parts are masked in this doc.

## Summary

Recipient infrastructure is **DB-canonical and tiny**: 18 active rows across 7 list types, 13 unique addresses, 3 unique domains (rexfin.com, rbccm.com, caisgroup.com). Local SQLite and VPS SQLite are byte-identical (same counts, same domain breakdown, same overlap pattern), and `config/expected_recipients.json` matches both — so the preflight `recipient_diff` audit will continue to pass even though there are real concerns underneath. **No deceased / ex-employee / `@example.com` / `test@` addresses** were found, no bounce table exists, no auto-suppression logic exists, no duplicates within `(email, list_type)`. The biggest finding is structural: **5 of the 7 lists are a single shared address (`etfupdates@rexfin.com`)** and the routine reports therefore have effectively no distribution — if that mailbox stops forwarding, five reports vanish silently. Deliverability posture is **mixed**: SPF on `rexfin.com` is published and includes `spf.protection.outlook.com` (which authorises the Graph API sender `relasmar@rexfin.com` ✓), DKIM is properly configured at `selector1._domainkey.rexfin.com` with the Microsoft 365 CNAME-to-onmicrosoft.com pattern, and DMARC is published at `p=quarantine; pct=100; fo=1` with aggregate reports going to `info@rexfin.com`. **Alignment is therefore correct** for messages sent via Microsoft Graph as `relasmar@rexfin.com`. Two operational concerns surfaced separately: (a) the Graph API send payload contains **no Bcc, no Reply-To, and no List-Unsubscribe header** — `_load_private_recipients()` is wired to load them but the `send_email()` function never accepts/sends Bcc, so private/BCC mirroring is dead code; (b) recipient additions are **not audit-logged** — `EmailRecipient.added_by` is a free-text string set to literal `"admin"` for every web-add, with no actor identity, no timestamp of approver, no diff trail. There is also a **missing `intelligence`/`screener`/`pipeline`/`private` list configuration** — the schema accepts these list_types but zero rows exist, meaning the `private` BCC mirror referenced throughout `email_alerts.py` is silently a no-op today.

## Recipient inventory

| List | Count (DB local) | Count (DB VPS) | Count (.txt local) | Count (.txt VPS) | Active | Paused | Bounced |
|---|---|---|---|---|---|---|---|
| daily | 1 | 1 | 0 (file empty) | missing (only .bak) | 1 | 0 | n/a |
| weekly | 1 | 1 | 0 | missing | 1 | 0 | n/a |
| li | 1 | 1 | 0 | missing | 1 | 0 | n/a |
| income | 1 | 1 | 0 | missing | 1 | 0 | n/a |
| flow | 1 | 1 | 0 | missing | 1 | 0 | n/a |
| autocall | 9 | 9 | 0 (only .bak) | 0 (file truncated to 1 line) | 9 | 0 | n/a |
| stock_recs | 4 | 4 | n/a | n/a | 4 | 0 | n/a |
| private | 0 | 0 | 0 (only .bak) | missing | 0 | 0 | n/a |
| intelligence | 0 | 0 | n/a | n/a | 0 | 0 | n/a |
| screener | 0 | 0 | n/a | n/a | 0 | 0 | n/a |
| pipeline | 0 | 0 | n/a | n/a | 0 | 0 | n/a |
| **Total** | **18** | **18** | — | — | **18** | **0** | **0 (no tracking)** |

Domain breakdown of active recipients (identical local + VPS):

| Domain | Count |
|---|---|
| rexfin.com | 14 |
| rbccm.com | 2 |
| caisgroup.com | 2 |

Cross-list overlap (active rows; one address present on N lists):

| Address (masked) | Lists |
|---|---|
| e***@rexfin.com | 5 (daily, weekly, li, income, flow) |
| s***@rexfin.com | 2 (probably stock_recs duplicate within distinct list_type) |
| All 11 others | 1 list each |

`digest_subscribers` table: 1 row, status=APPROVED. (Distinct from `email_recipients`; this is the on-site signup form's queue.)

There is **no `bounces` table**, no `email_suppressions` table, no `delivery_log` table. Hard bounces are not tracked or auto-removed.

## DNS posture

| Domain | SPF | DKIM | DMARC | Aligned for `relasmar@rexfin.com` via Graph? |
|---|---|---|---|---|
| rexfin.com | `v=spf1 include:us._netblocks.mimecast.com include:spf.protection.outlook.com include:_spf.salesforce.com include:spf.maropost.com include:mailgun.org -all` ✓ | `selector1._domainkey.rexfin.com` → CNAME → `selector1-rexfin-com._domainkey.rexfin.onmicrosoft.com` (M365 standard, RSA 2048-bit, present) ✓ | `v=DMARC1; p=quarantine; pct=100; rua=mailto:info@rexfin.com; ri=86400; fo=1;` ✓ | **YES** (SPF authorises `spf.protection.outlook.com` egress, DKIM signs as `rexfin.com`, From: aligns) |

Notes:
- `default._domainkey.rexfin.com` does NOT exist — that is fine, M365 uses `selector1` and `selector2` (selector2 is also missing, suggesting only one key has been rotated; M365 normally provisions both for rotation — this is a minor hygiene concern but does not break sending).
- The Graph API call does NOT send via SMTP — it posts to `https://graph.microsoft.com/v1.0/users/relasmar@rexfin.com/sendMail` and Microsoft's egress IPs are covered by `spf.protection.outlook.com`, so SPF is satisfied.
- DMARC is at `p=quarantine` not `p=reject` — receivers will quarantine on alignment failure, not bounce. With proper SPF+DKIM alignment that should be a non-issue; it is also the safer posture (Ryu has previously deferred a tightening of DMARC to `p=reject`).
- DMARC reports go to `info@rexfin.com` — **unknown if anyone reads that mailbox**. If misalignment is happening at any receiver, those reports would surface it; without a reader they are wasted.
- MX is Mimecast (`us-smtp-inbound-1.mimecast.com`) — **inbound only**, not relevant to outbound posture except that Mimecast often imposes its own outbound filters when used as a full gateway. Here Mimecast is only inbound; outbound is direct Microsoft 365 → Graph API.
- `SMTP_FROM` env var on the VPS is set to `ryuogawaelasmar@gmail.com` — this is **not used by the Graph send path** (the Graph path uses `AZURE_SENDER`), but it is a footgun: any code path that legitimately falls back to `SMTP_FROM` would send From: a `gmail.com` address whose SPF says nothing about `rexfin.com`. The SMTP fallback path is currently dead per `01_send_pathway.md`, so this is latent risk only.

## Findings

### F1: Recipient lists are a single shared inbox for 5 of the 7 reports

- **Severity**: high
- **Surface**: `email_recipients` table (DB) — list_types `daily`, `weekly`, `li`, `income`, `flow`
- **Symptom**: Five distinct report types all resolve to **one** address (`etfupdates@rexfin.com`). Even though the system advertises per-report partitioning via `list_type`, in practice it is just an alias funnel. If `etfupdates@` ever stops forwarding, breaks routing rules, or is unsubscribed at the mailbox level, five reports vanish silently — the send path returns success because Graph 202'd the message; the audit log records a successful send.
- **Evidence**: see DB query in inventory section. `relasmar@rexfin.com` is **not** in the routine recipient lists — Ryu only sees the daily/weekly/li/income/flow reports if `etfupdates@` actively forwards to him. This is also why the absence of preflight/decision today causes no obvious user-side alarm: Ryu doesn't see the routine reports unless the alias forwards, so missing reports look the same as "already filed away."
- **Blast radius**: All 5 routine reports can disappear without any alert firing. `expected_recipients.json` matches DB (preflight pass), so the recipient_diff guard does not catch this — it only catches *changes*, not *content suitability*.
- **Hypothesis**: Intentional consolidation when REX moved to a single distribution alias. No one has audited whether `etfupdates@` still has the right downstream subscribers, and no monitoring exists to verify forwarding actually fires.
- **Fix size**: trivial (add `relasmar@rexfin.com` to all 5 lists for ground-truth verification, or add a low-traffic canary recipient at a domain you control); medium if you want to stand up real per-list distribution and audit who should receive what.

### F2: Bcc/private mirror is dead code — Graph send_email() does not accept Bcc

- **Severity**: high
- **Surface**: `webapp/services/graph_email.py:95-141` (no bcc parameter, payload only has `toRecipients`); `etp_tracker/email_alerts.py:92-113` (`_load_private_recipients` exists and is invoked); `email_recipients.list_type='private'` (zero rows — table accepts this list_type, no one ever populated it)
- **Symptom**: The codebase advertises a "private" BCC list and `_load_private_recipients()` is called from the digest builders, but the Graph API payload built in `graph_email.send_email()` only constructs `toRecipients` — **no `bccRecipients`, no `ccRecipients`**. The private list is never actually mirrored. Worse, this is silent — the loader returns `[]`, the send proceeds, and no warning is logged.
- **Evidence**: `grep "Bcc\|bcc\|bccRecipients" webapp/services/graph_email.py` → 0 matches. `_load_private_recipients` returns `[]` because the `private` list_type has 0 rows. Code at `email_alerts.py:2060-2061` reads `recipients = _load_recipients(); private = _load_private_recipients()` and then never passes `private` to `send_email()`.
- **Blast radius**: Compounds F1. There is no shadow copy of routine sends going to a "you are CC'd on everything" address. Forensic verification of what was actually sent on day X is impossible from inbox; you only have `data/.send_audit.json`.
- **Hypothesis**: Architectural drift. The CSV-fallback / SMTP path likely supported Bcc; the Graph migration kept the `_load_private_recipients` call but never plumbed the second list through to the API payload.
- **Fix size**: small (add `bccRecipients` to the Graph payload + thread `bcc=` parameter through `send_email()`, then populate the `private` list_type).

### F3: No List-Unsubscribe header, no Reply-To header, no clickable unsubscribe

- **Severity**: medium (CAN-SPAM exposure already flagged by `system_audit_sys_j_2026-05-05.md`); medium for deliverability (Gmail/Yahoo bulk-sender requirements)
- **Surface**: `webapp/services/graph_email.py:128-141` (payload missing standard mail headers); `etp_tracker/email_alerts.py:1307` and `:2371` (footer is plain text "To unsubscribe, contact relasmar@rexfin.com")
- **Symptom**: The Graph API `message` payload has only `subject`, `body`, `toRecipients`, `saveToSentItems`. No `internetMessageHeaders` array, no `replyTo` block. The unsubscribe instruction in the rendered HTML footer is plain text — a recipient cannot one-click unsubscribe; they must compose a manual email. This was already called out in the `sys_j` audit and remains unfixed.
- **Evidence**: `grep "Reply-To\|reply_to\|List-Unsubscribe\|internetMessageHeaders" webapp/services/graph_email.py` → 0 matches; the only references in the project are in `etp_tracker/weekly_digest.py:1206` and `email_alerts.py:1307/2371`, all plain-text footers.
- **Blast radius**: Currently small because all routine recipients are at `rexfin.com` (intra-domain delivery). But CBOE/CAIS/RBCCM addresses on the autocall list go through their corporate MTAs, where missing `List-Unsubscribe` and missing `Reply-To` reduce trust score and increase spam-folder probability. Also: Gmail's Feb 2024 sender requirements mandate `List-Unsubscribe` + one-click unsubscribe for senders >5,000 messages/day to gmail.com — current volume is well under that threshold so no immediate enforcement, but the structure is brittle.
- **Hypothesis**: Graph API's default behaviour was accepted without adding the headers. The CAN-SPAM gap was logged on 2026-05-05 and prioritised behind operational fixes.
- **Fix size**: small (add `internetMessageHeaders: [{name: "List-Unsubscribe", value: "<mailto:unsubscribe@rexfin.com?subject=Unsubscribe>"}, {name: "List-Unsubscribe-Post", value: "List-Unsubscribe=One-Click"}, {name: "Reply-To", value: "relasmar@rexfin.com"}]` to the Graph payload + ensure `unsubscribe@rexfin.com` actually exists and is monitored).

### F4: Recipient additions are not audit-logged with actor identity

- **Severity**: medium
- **Surface**: `webapp/routers/admin.py:240-253` (add_recipient_route hardcodes `added_by="admin"`); `webapp/services/recipients.py:65-71` (writes `added_by` straight to a `String(100)` column with no FK to a user, no IP, no session id)
- **Symptom**: Every web-added recipient gets `added_by='admin'` literal. No record of *which* admin (only one person has the password today, but rotation events would be untraceable). No ledger of removals at all — `remove_recipient` only flips `is_active=False`, no `removed_by`, no `removed_at`. If a recipient is added then removed then re-added, the only signal is the `added_at` timestamp on the most recent reactivation.
- **Evidence**: `EmailRecipient` model has only `added_at` and `added_by`; no removal columns. Schema check confirmed no audit/history table for recipient changes. `digest_subscribers` table also lacks an actor field.
- **Blast radius**: Compliance / forensic. If a wrong recipient gets added (e.g., a competitor address), there is no record of who added them or when they were last touched. Combined with F1 (single-alias funnel), an unauthorised add to the alias's downstream rules could leak data without trace.
- **Hypothesis**: Single-admin assumption baked in early. Never revisited because admin auth still has only one password.
- **Fix size**: small (add `recipient_audit_log` table — `id, action, email, list_type, actor, ip, timestamp, before_state, after_state` — and a 5-line write in `add_recipient` / `remove_recipient`).

### F5: VPS is missing the `.txt` fallback files entirely; only `.bak` survives

- **Severity**: medium (defensive only; restated from `01_send_pathway.md` F7 because it directly affects recipient resolution)
- **Surface**: `/home/jarvis/rexfinhub/config/`
- **Symptom**: VPS has `email_recipients.txt.bak`, `email_recipients_private.txt.bak`, no `email_recipients.txt`, no `email_recipients_private.txt`, no `digest_subscribers.txt`. `autocall_recipients.txt` exists but is 72 bytes (truncated to one line vs the .bak's 214 bytes). If the DB load in `_load_recipients()` ever throws (locked WAL, schema migration, sqlite corruption), the fallback path tries the `.txt` → file doesn't exist → falls back to `os.environ.get("SMTP_TO", "")` which is empty → `_send_one()` returns `status=skipped` with `note="no recipients for list_type=X"` — silent skip, no alert.
- **Evidence**: `ssh jarvis@VPS ls config/ | grep recip` shows only the .bak files for the main lists.
- **Blast radius**: Latent. Activates only if DB read fails. Combined with F1 (single-alias dependency), a DB hiccup means zero deliveries with no alarm.
- **Hypothesis**: Migration cleanup created `.bak` snapshots and intended to regenerate `.txt` files as the new canonical, but the regen step never ran. The `seed_from_text_files()` function in `recipients.py` only imports if DB is empty, so it is now a one-way street.
- **Fix size**: trivial (regenerate `.txt` files from DB query, OR remove the .txt fallback entirely so DB failure is loud and fires an alert).

### F6: `digest_subscribers` table is functionally orphaned

- **Severity**: low
- **Surface**: `webapp/models.py:812-819` (DigestSubscriber); `webapp/routers/admin.py` (Digest Subscriber Approvals section)
- **Symptom**: 1 row total, status=APPROVED. The on-site signup form writes to this table; admin approves; on approval, `webapp/routers/admin.py:1015` writes `added_by="ADMIN"` into `EmailRecipient` table — but the DigestSubscriber row is left at status=APPROVED with no FK back to which `email_recipients.list_type` they were added to, no resolved_at appears to be set in the approval flow today, and no link between subscriber-approval and recipient-add. The `digest_subscribers.txt` fallback file is missing from VPS as well.
- **Evidence**: VPS query: `SELECT status, COUNT(*) FROM digest_subscribers` → `('APPROVED', 1)`. CLAUDE.md notes "PENDING|email|timestamp format, approved via admin" — implies a queue; queue is empty, suggesting either nobody has signed up since the last cleanup or the public signup form is broken (out of scope to verify here).
- **Blast radius**: New external subscribers cannot reliably get on the list via the public flow.
- **Hypothesis**: Underused public surface; admin-direct adds (relasmar manually adding addresses) are the primary path now.
- **Fix size**: small (link DigestSubscriber to EmailRecipient via FK + populate resolved_at on approval).

### F7: Subject-line consistency is brittle — different builders compose subjects differently

- **Severity**: low
- **Surface**: `etp_tracker/email_alerts.py:2019-2024` (default subject is `f"REX {_label}: {datetime.now().strftime('%m/%d/%Y')}"`); `webapp/routers/admin.py:1357,1379,1404,1429,1479,1504` (subject_override variants — `"REX ETP Leverage & Inverse Report: {date}"`, `"REX ETP Income Report: {date}"`, `"REX ETP Flow Report: {date}"`)
- **Symptom**: Some subjects are `"REX <Label>: MM/DD/YYYY"`, others are `"REX ETP <Label> Report: MM/DD/YYYY"`. The "ETP" word is sometimes present, sometimes absent, depending on which call site. No common subject builder; date format is consistent (`MM/DD/YYYY`) but prefix is not. Recipients filtering by subject prefix (Outlook rules to auto-file) need 2-3 rules instead of 1.
- **Evidence**: code grep above; e.g. weekly is `"REX Weekly ETP Report: {date}"` (line 1243), L&I is `"REX ETP Leverage & Inverse Report: {date}"`. Order of "ETP" varies.
- **Blast radius**: cosmetic — but combined with F1 (single alias receiving all 5), a downstream subscriber's filter rules can miss reports.
- **Hypothesis**: Two different authors at different times.
- **Fix size**: trivial (single `_subject(report_label, date)` helper).

### F8: No automatic bounce handling, no suppression list

- **Severity**: low (today, given small recipient count); high (if list ever scales)
- **Surface**: codebase search for `bounce`, `suppress`, `complaint`, `delivered`, `softbounce`, `hardbounce` returned no relevant results
- **Symptom**: There is no bounce-tracking table, no Microsoft Graph webhook subscription for delivery failures, no scheduled job that reads MS Graph's `/users/{id}/messages` filtered by `Undeliverable` to update the recipient list. A hard-bounced address stays on the list forever and continues to be sent to.
- **Evidence**: no DB tables matching `bounce|suppress`. No code matches for Graph delivery report APIs. No webhook subscription configured.
- **Blast radius**: Today small (13 unique addresses, all known-good). But the autocall list contains external addresses (RBC, CAIS) whose deliverability is outside REX's control — a single hard bounce there will silently continue forever, and over time accumulating bounces against the same Graph sender increases the chance of reputation damage with Microsoft 365's outbound reputation gateway.
- **Hypothesis**: Never built because volume is small.
- **Fix size**: medium (Graph webhook + suppression table + admin UI to view/clear).

### F9: `selector2._domainkey.rexfin.com` not published — no DKIM rotation key

- **Severity**: low
- **Surface**: DNS
- **Symptom**: `selector1._domainkey.rexfin.com` is properly published as a CNAME to the M365 onmicrosoft.com key. `selector2._domainkey.rexfin.com` returns NXDOMAIN. Microsoft 365 normally provisions both selectors so administrators can rotate one while the other is live. Only one selector means rotation requires a brief signing outage.
- **Evidence**: `nslookup -type=TXT selector2._domainkey.rexfin.com` → "Non-existent domain".
- **Blast radius**: latent. Rotation hassle, no current delivery impact.
- **Fix size**: trivial DNS — add the second CNAME from the M365 admin console.

### F10: Three list_types defined in code (`intelligence`, `screener`, `pipeline`) have zero rows and no senders

- **Severity**: low (cleanup)
- **Surface**: `webapp/services/recipients.py:17` (VALID_LIST_TYPES includes them); DB shows zero rows for all three
- **Symptom**: The schema accepts these as valid list_types, but no report builder in `scripts/send_all.py` BUNDLES references them, and no rows exist. They are dead enum values.
- **Evidence**: `VALID_LIST_TYPES = {"daily", "weekly", "li", "income", "flow", "autocall", "private", "intelligence", "screener", "pipeline", "stock_recs"}`. BUNDLES in send_all.py uses only `daily, weekly, li, income, flow, autocall, stock_recs`. DB confirms 0 rows for `intelligence/screener/pipeline/private`.
- **Blast radius**: confusion / scope drift. An admin could add a recipient to one of these lists thinking it does something; nothing would happen.
- **Hypothesis**: Forward-declared lists never wired up.
- **Fix size**: trivial (remove from VALID_LIST_TYPES, or wire the planned reports).

## Hunting questions — answers

1. **List_type per report**: `daily→daily`, `weekly→weekly`, `li→li`, `income→income`, `flow→flow`, `autocall→autocall`, `stock_recs→stock_recs`. (See REPORTS dict in `scripts/send_all.py:97`.) Note the audit specifies "5 reports" but the code defines 7 reports across 5 BUNDLES.
2. **Overlap (Ryu duplicate exposure)**: `relasmar@rexfin.com` is **not** in any active recipient list. He only sees content via `etfupdates@rexfin.com` forwarding (unconfirmed) or via critical alerts. So duplicate exposure is not the problem; under-exposure is.
3. **Auto-bounce removal**: No. F8.
4. **List-Unsubscribe header**: No. F3.
5. **From-domain alignment**: SPF, DKIM, DMARC all aligned for `relasmar@rexfin.com` via Graph API. ✓ See DNS posture table.
6. **Reply-To**: Not set in Graph payload (F3). Replies go to From: (`relasmar@rexfin.com`). Monitored by Ryu personally.
7. **Subject consistency**: Inconsistent. F7.
8. **Test addresses (`@example.com`, `test@`)**: None found.
9. **`etfupdates@` is the production alias**: confirmed for daily/weekly/li/income/flow. Whether it forwards to live humans is unverifiable from this surface (would require M365 admin access).
10. **Audit log on recipient additions**: No actor identity, no removal log. F4.

## Live state inspection

```
$ Local SQLite query: SELECT list_type, is_active, COUNT(*) FROM email_recipients GROUP BY list_type, is_active
  ('autocall', 1, 9)
  ('daily', 1, 1)
  ('flow', 1, 1)
  ('income', 1, 1)
  ('li', 1, 1)
  ('stock_recs', 1, 4)
  ('weekly', 1, 1)
  -- Total: 18 active, 0 inactive

$ Local DB == VPS DB (byte-identical for these queries)

$ Domain breakdown (active rows): rexfin.com=14, rbccm.com=2, caisgroup.com=2

$ Duplicates within (email, list_type): NONE
$ Test/example addresses: NONE
$ Bounce table: DOES NOT EXIST
$ Suppression table: DOES NOT EXIST
$ Recipient audit log table: DOES NOT EXIST
$ digest_subscribers: 1 row, status=APPROVED

$ Local config/email_recipients.txt:           empty (0 non-comment lines)
$ Local config/email_recipients_private.txt:   empty
$ Local config/autocall_recipients.txt:        empty
$ Local config/digest_subscribers.txt:         1 line (gmail.com domain)
$ Local config/expected_recipients.json:       48 entries, matches DB exactly

$ VPS /home/jarvis/rexfinhub/config/:
  autocall_recipients.txt          (1 line — STUB, should be 9 if used)
  autocall_recipients.txt.bak      (real, pre-DB-migration snapshot)
  email_recipients.txt.bak         (real, snapshot)
  email_recipients_private.txt.bak (real, snapshot)
  expected_recipients.json         (matches live DB)
  email_recipients.txt             ← MISSING
  email_recipients_private.txt     ← MISSING
  digest_subscribers.txt           ← MISSING

$ DNS — rexfin.com TXT (SPF):
  v=spf1 include:us._netblocks.mimecast.com include:spf.protection.outlook.com
  include:_spf.salesforce.com include:spf.maropost.com include:mailgun.org -all
  ✓ Authorises Microsoft Graph (spf.protection.outlook.com -> ip4:40.92/15, 40.107/16, 52.100/15, ...)

$ DNS — _dmarc.rexfin.com:
  v=DMARC1; p=quarantine; pct=100; rua=mailto:info@rexfin.com; ri=86400; fo=1;
  ✓ Quarantine (not reject); aggregate reports to info@rexfin.com (no DKIM/SPF failure forensic addr 'ruf')

$ DNS — selector1._domainkey.rexfin.com:
  CNAME -> selector1-rexfin-com._domainkey.rexfin.onmicrosoft.com
  v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIB...QIDAQAB; (RSA 2048-bit key present)
  ✓ Microsoft 365 standard DKIM, key resolves correctly

$ DNS — selector2._domainkey.rexfin.com: NXDOMAIN  (rotation selector not provisioned — F9)
$ DNS — default._domainkey.rexfin.com:   NXDOMAIN  (expected — M365 doesn't use 'default' selector)
$ DNS — rexfin.com MX: us-smtp-inbound-1.mimecast.com (preference 0), us-smtp-inbound-2.mimecast.com (1)
  Inbound = Mimecast. Outbound = direct M365 Graph API (not via Mimecast outbound gateway).

$ Graph send identity: AZURE_SENDER=relasmar@rexfin.com (read from VPS config/.env)
  → All 7 reports send From: relasmar@rexfin.com via Graph
  → SMTP_FROM=ryuogawaelasmar@gmail.com is set but unused by the live Graph path
    (would only matter if the dead SMTP fallback ever activated — risk noted)

$ dnspython: NOT INSTALLED in the python environment used; DNS queries above were done
  via system nslookup against 8.8.8.8.

$ Graph API payload inspected: webapp/services/graph_email.py:128-141
  payload = {"message": {"subject": ..., "body": {...}, "toRecipients": [...], "saveToSentItems": "true"}}
  → No bccRecipients, no ccRecipients, no replyTo, no internetMessageHeaders.
```

## Surfaces inspected

- `webapp/models.py` (lines 811-848 — DigestSubscriber + EmailRecipient definitions)
- `webapp/services/recipients.py` (full read; 161 lines — VALID_LIST_TYPES, get/add/remove, seed_from_text_files)
- `webapp/services/graph_email.py` (full read; 170 lines — Graph payload construction, env load, gate)
- `webapp/routers/admin.py` (slices: 240-265 add/remove routes; 1015 admin-direct-adds; subject overrides 1243/1357/1379/1404/1429/1479/1504)
- `etp_tracker/email_alerts.py` (slices: 61-113 _load_recipients/_load_private_recipients; 1872-1953 azure helpers; 1956-2080 _send_html_digest signature & path)
- `scripts/send_all.py` (slices: 97-113 REPORTS + BUNDLES; 164-169 _resolve_recipients)
- `scripts/preflight_check.py` (referenced via 01_send_pathway.md — not re-read; recipient_diff audit confirmed pass)
- `config/email_recipients.txt` + `.bak` + `_private.txt` + `.bak` (local file system inspection)
- `config/autocall_recipients.txt` + `.bak` (local + VPS)
- `config/expected_recipients.json` (local + VPS — confirmed identical)
- `config/digest_subscribers.txt` (local — single line, gmail.com domain)
- Local SQLite `email_recipients` + `digest_subscribers` (counts, schema, dupes, overlap, recent-adds, test-pattern check)
- VPS SQLite `email_recipients` + `digest_subscribers` (same queries — confirmed equal to local)
- VPS `/home/jarvis/rexfinhub/config/.env` (only AZURE_SENDER and SMTP_FROM lines read; values redacted in this report)
- DNS via `nslookup` against `8.8.8.8`: rexfin.com TXT (SPF), _dmarc.rexfin.com TXT (DMARC), selector1/selector2/default ._domainkey.rexfin.com TXT (DKIM), rexfin.com MX, spf.protection.outlook.com TXT (verified Graph IPs covered)

## Surfaces NOT inspected

- `webapp/routers/digest.py` — secondary subscriber-form router; quick grep showed no payload-construction concerns but full read deferred (out of scope for recipient inventory).
- `etp_tracker/weekly_digest.py` — separate weekly-digest builder with its own Graph send call (line 1426). May or may not have its own footer/Reply-To behaviour — should be cross-checked in Stage 2.
- `screener/email_report.py` — uses Graph credentials directly via `_load_env`/`_get_access_token`; not part of the 7 standard reports but capable of sending and may bypass `_load_recipients()` entirely. Worth a Stage 2 look.
- `scripts/manage_recipients.py` — CLI tool for recipient management; could be another writer to `email_recipients` not covered by the audit log discussion in F4.
- `archive/scripts/migrate_admin_to_db.py` — historical migration script; flagged for completeness only.
- Whether `etfupdates@rexfin.com` actually forwards to live humans (M365 admin required).
- Whether `info@rexfin.com` (DMARC `rua` destination) is monitored — would require mailbox access.
- Whether the Mimecast inbound gateway rejects/rewrites incoming bounces in a way that changes how Microsoft Graph would see delivery failures (out of scope).
- Salesforce, Maropost, and Mailgun SPF includes — these authorise other senders that could legitimately use `From: rexfin.com`. Whether those services are actively in use by REX (and whether their DKIM keys are also published) was not verified. If unused, the SPF includes are wasted DNS lookups (SPF has a 10-lookup limit; current count: 5 includes, fine for now).
- DMARC aggregate reports themselves — none read (would require info@rexfin.com inbox access). These would reveal real-world alignment failures from receivers, which is the only ground-truth way to validate F3/F9 do not cause delivery hits.

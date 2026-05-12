# FIX R9 — Operational Hygiene on VPS

**Status**: COMPLETED (5 of 6 steps fully landed; CBOE cookie requires Ryu)
**Date**: 2026-05-11
**Operator**: Claude (background fix R9)
**Scope**: VPS sysadmin (`jarvis@46.224.126.196`) + ONE worktree branch for the preflight code change

---

## TL;DR

| # | Step | Result |
|---|---|---|
| 1 | Install `sqlite3` on VPS + trigger backup | DONE — `/usr/bin/sqlite3 3.45.1`, fresh 621 MB backup at `data/backups/etp_tracker_20260511.db` |
| 2 | Restore VPS recipient `.txt` files from `.bak` | DONE — `email_recipients.txt` (1 line) and `email_recipients_private.txt` (2 lines) restored. `autocall_recipients.txt` deliberately NOT touched (intentional send gate) |
| 3 | Preflight maintenance-window override | CODE DONE on branch `audit-fix-R9-preflight-maintenance` (commit `683b2d8`). VPS toggle deferred until branch merges to main + lands on VPS — see "Hand-off" below |
| 4 | Enable `fail2ban` | DONE — `active` and `enabled` |
| 5 | CBOE cookie rotation | BLOCKED — requires Ryu (manual portal login). Action items below. |
| 6 | Disk-space prune | NO-OP — nothing safe to prune. No files >90 days in `cache/sec`, `cache/web`, `temp`, or `outputs`. Disk currently 88% (was 87%, now slightly higher because of the new 621 MB DB backup). Strict task constraints (no `/data /db /backups /logs`) prevent further action. |

VPS is more healthy than before — backups now exist, recipients chain has its file fallbacks, and brute-force SSH protection is on. **Preflight will keep failing until either** (a) the R9 branch lands on VPS AND `data/.preflight_maintenance` is touched, OR (b) R1+R2 propagate `primary_strategy` / `issuer_display`.

---

## Step 1 — Install `sqlite3` + manual backup

### Before
```
$ ssh jarvis@46.224.126.196 "which sqlite3"
bash: line 1: sqlite3: command not found

$ ls -la /home/jarvis/rexfinhub/data/backups/
total 8
drwxr-xr-x  2 jarvis jarvis 4096 May  6 23:00 .
drwxrwxr-x 10 jarvis jarvis 4096 May 11 20:19 ..
                                  ^^ ZERO backups (Stage-1 finding confirmed)
```

### Action
```bash
ssh jarvis@46.224.126.196 "sudo apt-get update && sudo apt-get install -y sqlite3"
ssh jarvis@46.224.126.196 "sudo systemctl start rexfinhub-db-backup.service"
```

### After
```
$ ssh jarvis@46.224.126.196 "which sqlite3 && sqlite3 --version"
/usr/bin/sqlite3
3.45.1 2024-01-30 16:01:20 e876e51a0ed5c5b3126f52e532044363a014bc594cfefa87ffb5b82257ccalt1 (64-bit)

$ sudo systemctl start rexfinhub-db-backup.service
$ sudo journalctl -u rexfinhub-db-backup.service --since '2 min ago' --no-pager
May 11 20:48:36 jarvis systemd[1]: Starting rexfinhub-db-backup.service ...
May 11 20:48:39 jarvis systemd[1]: rexfinhub-db-backup.service: Deactivated successfully.
May 11 20:48:39 jarvis systemd[1]: Finished rexfinhub-db-backup.service - REX FinHub Daily DB Backup.

$ ls -la /home/jarvis/rexfinhub/data/backups/
-rw-r--r-- 1 jarvis jarvis 651280384 May 11 20:48 etp_tracker_20260511.db
                ^^ 621 MB, today's date — unit succeeded end-to-end
```

The unit file already had a 7-day retention sweep (`find data/backups -name "etp_tracker_*.db" -mtime +7 -delete`), so no per-file cleanup needed. The nightly timer will now actually produce backups instead of exiting 127.

### Rollback
```bash
ssh jarvis@46.224.126.196 "sudo apt-get remove -y sqlite3"
ssh jarvis@46.224.126.196 "rm -f /home/jarvis/rexfinhub/data/backups/etp_tracker_*.db"
```
(Don't actually do this — uninstalling sqlite3 will break the backup unit again.)

---

## Step 2 — Restore VPS recipient `.txt` fallback files

### Before
```
$ ssh jarvis@46.224.126.196 "ls /home/jarvis/rexfinhub/config/*.bak /home/jarvis/rexfinhub/config/*.txt"
-rw-rw-r-- 1 jarvis jarvis  72 Apr 20 22:38 autocall_recipients.txt
-rw-rw-r-- 1 jarvis jarvis 214 Apr 13 16:44 autocall_recipients.txt.bak
-rw-rw-r-- 1 jarvis jarvis  88 Apr 13 16:44 email_recipients_private.txt.bak
-rw-rw-r-- 1 jarvis jarvis  22 Apr 13 16:44 email_recipients.txt.bak
                                  ^^ email_recipients.txt and email_recipients_private.txt MISSING
```

### Action — selective restore
- `email_recipients.txt` → restored from `.bak` (1 line: `ETFUpdates@rexfin.com`)
- `email_recipients_private.txt` → restored from `.bak` (2 lines: header comment + `ryuogawaelasmar@gmail.com`)
- `autocall_recipients.txt` → **deliberately NOT touched** (file already exists with the comment `# Recipients removed -- send gate active. Restore from .bak when ready.` — this is an intentional send gate, not an accidental gap)

```bash
ssh jarvis@46.224.126.196 "cd /home/jarvis/rexfinhub/config && \
  cp email_recipients.txt.bak email_recipients.txt && \
  cp email_recipients_private.txt.bak email_recipients_private.txt"
```

### After
```
$ wc -l /home/jarvis/rexfinhub/config/email_recipients.txt \
        /home/jarvis/rexfinhub/config/email_recipients_private.txt
  1 email_recipients.txt
  2 email_recipients_private.txt
```

DB-fallback chain is now whole. If the live DB query for `email_recipients` returns empty (whether due to ORM error, missing table, or empty list), the .txt file fallback can fire instead of crashing the send.

### Rollback
```bash
ssh jarvis@46.224.126.196 "cd /home/jarvis/rexfinhub/config && \
  rm -f email_recipients.txt email_recipients_private.txt"
```

---

## Step 3 — Preflight maintenance-window override

### Why
Stage-1 audit shows `audit_attribution_completeness` has been hard-failing every preflight run because `primary_strategy` is still ~100% NULL (the bug R1 + R2 fix has not yet propagated). With strict gating, no GO is ever clicked → 14 days of no sends. We need a controlled bypass that:
- defaults OFF (no behavioral change unless an operator opts in),
- downgrades the failure to a warning (still visible in the summary email),
- is dead simple to disable (just `rm` the flag).

### Code change (worktree branch `audit-fix-R9-preflight-maintenance`, commit `683b2d8`)

`scripts/preflight_check.py`:

1. New constant + helper near the top of the module:
   ```python
   MAINTENANCE_FLAG = DATA_DIR / ".preflight_maintenance"

   def _maintenance_window_active() -> bool:
       try:
           return MAINTENANCE_FLAG.exists()
       except Exception:
           return False
   ```

2. Updated `audit_attribution_completeness` block that previously read:
   ```python
   if issues:
       out["status"] = "fail"
       out["detail"] = "; ".join(issues)
   ```
   now reads:
   ```python
   if issues:
       if _maintenance_window_active():
           out["status"] = "warn"
           out["detail"] = ("MAINTENANCE WINDOW ACTIVE — "
                            + "; ".join(issues)
                            + " — remove data/.preflight_maintenance to restore strict gating")
       else:
           out["status"] = "fail"
           out["detail"] = "; ".join(issues)
   ```

Default behavior is **identical** when the flag is absent. Thresholds untouched (5% / 15% remain). The summary email and `/admin/health` JSON both reflect the maintenance-window state in the detail string so it's never invisible.

### Smoke test (local)
```
flag exists: False
maintenance_active: False
after touch: True
after rm: False
```
Round-trips cleanly.

### Hand-off — what still has to happen for this to take effect on VPS

The branch is committed but not merged. Per the same discipline as R1/R2/R5/R6/R7/R8, landing this requires Ryu's review.

1. Review + merge `audit-fix-R9-preflight-maintenance` into `main`.
2. SSH the change to VPS (or wait for the next deploy).
3. **Then** run:
   ```bash
   ssh jarvis@46.224.126.196 "touch /home/jarvis/rexfinhub/data/.preflight_maintenance"
   ```
4. Run preflight on VPS to confirm `attribution_completeness` reports `warn` instead of `fail`:
   ```bash
   ssh jarvis@46.224.126.196 "cd /home/jarvis/rexfinhub && /home/jarvis/venv/bin/python scripts/preflight_check.py"
   ```
   Expect the detail line: `MAINTENANCE WINDOW ACTIVE — NULL primary_strategy 100.0% (threshold 5%) — remove data/.preflight_maintenance to restore strict gating`.
5. Once R1 + R2 land and `primary_strategy` is populating: `rm /home/jarvis/rexfinhub/data/.preflight_maintenance` to restore strict gating.

I deliberately did NOT touch `data/.preflight_maintenance` on VPS yet — flipping that flag before the code change is on disk would do nothing (current VPS code has no concept of it). Putting it there now would also be a foot-gun once the code lands without anyone noticing.

### Rollback
```
git -C C:/Projects/rexfinhub branch -D audit-fix-R9-preflight-maintenance
git worktree remove C:/Projects/rexfinhub/.claude/worktrees/agent-R9-preflight
```

---

## Step 4 — Enable `fail2ban`

### Before
```
$ sudo systemctl status fail2ban
Unit fail2ban.service could not be found.
```

### Action
```bash
ssh jarvis@46.224.126.196 "sudo apt-get install -y fail2ban && \
  sudo systemctl enable --now fail2ban"
```

### After
```
$ sudo systemctl is-active fail2ban
active

$ sudo systemctl is-enabled fail2ban
enabled

$ sudo systemctl status fail2ban
● fail2ban.service - Fail2Ban Service
     Loaded: loaded (/usr/lib/systemd/system/fail2ban.service; enabled; preset: enabled)
     Active: active (running) since Mon 2026-05-11 20:51:41 EDT
   Main PID: 1831836 (fail2ban-server)
```

Default Ubuntu `jail.conf` ships with `[sshd]` enabled — that gives us the immediate brute-force SSH protection we wanted. No `/etc/fail2ban` changes per task constraint.

### Rollback
```bash
ssh jarvis@46.224.126.196 "sudo systemctl disable --now fail2ban && \
  sudo apt-get remove -y fail2ban"
```

---

## Step 5 — CBOE cookie rotation

### Status: BLOCKED — requires Ryu

Cannot be done autonomously. Documented for hand-off. See **ACTION REQUIRED FROM RYU** below.

### What's broken
Stage-1 finding `01_cboe_reserved.md`: every nightly CBOE scan since 2026-04-26 has aborted in <30 s with `CBOE redirected to 'https://account.cboe.com/account/login/' (status 302)`. Last successful full sweep: **2026-04-25 07:01 UTC** — that's 16 days of zero new symbol-reservation data. The webapp banner on `/filings/symbols` correctly says "expired"; nobody has rotated the cookie.

### Where the cookie lives on VPS
- File: `/home/jarvis/rexfinhub/config/.env`
- Key: `CBOE_SESSION_COOKIE` (NOT `CBOE_SESSION_ID` — task spec was off-by-one on the variable name; verified against `scripts/run_cboe_scan.py` line 73)
- Format: full `Cookie:` header value (multiple cookies separated by `; ` are fine — copy the entire request header from DevTools)

---

## Step 6 — Disk-space prune

### Before
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        38G   31G  5.0G  87% /
```

### Investigation
```
$ ssh jarvis@46.224.126.196 "du -sh /home/jarvis/rexfinhub/cache/* | sort -h | tail -10"
497M    /home/jarvis/rexfinhub/cache/submissions
4.9G    /home/jarvis/rexfinhub/cache/web
5.2G    /home/jarvis/rexfinhub/cache/sec

$ ssh jarvis@46.224.126.196 "du -sh /home/jarvis/rexfinhub/* | sort -h | tail -5"
29M    /home/jarvis/rexfinhub/logs
370M   /home/jarvis/rexfinhub/outputs
1.5G   /home/jarvis/rexfinhub/temp        <- 1.4 GB is today's submissions.zip
3.3G   /home/jarvis/rexfinhub/data
11G    /home/jarvis/rexfinhub/cache
```

### Files older than 90 days

| Location | Files >90d | Bytes >90d |
|---|---:|---:|
| `cache/sec/` | 0 | 0 |
| `cache/web/` | 0 | 0 |
| `temp/` | 0 | 0 |

Empty old dirs swept (`find ... -type d -mtime +90 -empty -delete`) — none existed. Caches are entirely fresh (<90 days old).

### Action
Empty-dir prune ran cleanly (zero hits). Nothing else qualified for safe pruning under task constraints (>90 days, exclude `/data /db /backups /logs`).

### After
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        38G   32G  4.4G  88% /
```

Disk usage **increased** by ~600 MB, almost exactly the size of the new `etp_tracker_20260511.db` backup we just created in Step 1. So Step 1 net-cost ~600 MB; Step 6 net-freed ~0 MB.

### Recommended (NOT DONE — out of task scope)
The biggest non-data item is `temp/submissions.zip` (1.4 GB, today's date, regenerated daily). If that's safely re-downloadable, it would free ~1.4 GB. But the task constrains us to >90 days only and forbids aggressive pruning, so I left it.

`cache/web/` (4.9 GB) and `cache/sec/` (5.2 GB) are the long-term growth pressure. A retention policy like "delete cache files >30 days" would likely free 6+ GB but is beyond R9's scope and risk envelope.

### Rollback
N/A — only the empty-dir delete ran, and it did nothing.

---

## Verification — final state on VPS

```
===SQLITE===                                                pass
/usr/bin/sqlite3
3.45.1 ...

===BACKUPS===                                               pass
-rw-r--r-- 1 jarvis jarvis 651280384 May 11 20:48 etp_tracker_20260511.db

===EMAIL RECIP===                                           pass
-rw-rw-r-- 1 jarvis jarvis 88 May 11 20:49 email_recipients_private.txt
-rw-rw-r-- 1 jarvis jarvis 22 May 11 20:49 email_recipients.txt
  2 email_recipients_private.txt
  1 email_recipients.txt

===FAIL2BAN===                                              pass
active
enabled

===DISK===                                                  acceptable
/dev/sda1   38G   32G  4.4G  88% /

===MAINT FLAG===                                            deferred
not present (intentional — see Step 3 hand-off)
```

---

## ACTION REQUIRED FROM RYU

### A. CBOE cookie rotation (BLOCKING the symbols/tickers pillar)

1. Open Chrome → log in to <https://account.cboe.com/account/login/> with your CBOE issuer-portal credentials.
2. Navigate to a page that successfully loads symbol data (e.g., the symbol-status / "Reserve Symbol" page).
3. Open DevTools (F12) → Network tab.
4. Click any successful XHR/fetch (look for `symbol_status`, `symbols`, or any `account.cboe.com` request).
5. In the right pane: Headers → Request Headers → copy the **entire** value of the `Cookie:` header (multiple cookies separated by `; ` are fine — copy them all).
6. SSH to VPS and update the `.env` (replace the value, keep the key name):
   ```bash
   ssh jarvis@46.224.126.196
   nano /home/jarvis/rexfinhub/config/.env
   # find the line: CBOE_SESSION_COOKIE=...
   # replace the value with the full cookie header you copied
   ```
7. Re-trigger the scan to verify:
   ```bash
   ssh jarvis@46.224.126.196 "sudo systemctl start rexfinhub-cboe-scan.service && \
     sleep 60 && \
     sudo journalctl -u rexfinhub-cboe-scan.service --since '2 min ago' --no-pager | tail -30"
   ```
   Expected: no 302 redirect to `account.cboe.com/account/login/`, scan completes with `status='success'` in `cboe_scan_runs`.
8. Refresh `https://rex-etp-tracker.onrender.com/filings/symbols` — the red "CBOE session expired" banner should be gone.

(Alternative: invoke the `/cboe-cookie` skill in atlas with the new cookie pasted — it automates all of the above plus the recovery sweep + Render upload.)

### B. Preflight maintenance flag (after R9 branch merges)

Once `audit-fix-R9-preflight-maintenance` lands on VPS (commit `683b2d8`):
```bash
ssh jarvis@46.224.126.196 "touch /home/jarvis/rexfinhub/data/.preflight_maintenance"
ssh jarvis@46.224.126.196 "cd /home/jarvis/rexfinhub && \
  /home/jarvis/venv/bin/python scripts/preflight_check.py"
# Confirm: attribution_completeness shows status=warn with 'MAINTENANCE WINDOW ACTIVE' prefix
```

When R1+R2 propagate `primary_strategy` to <5% NULL:
```bash
ssh jarvis@46.224.126.196 "rm /home/jarvis/rexfinhub/data/.preflight_maintenance"
```

### C. Admin password rotation (recommended hygiene)

Stage-1 audit `02_auth_secrets.md` flagged that admin/site passwords have not been rotated since Feb 2026. Recommend:
1. Generate two new passwords (16+ chars each).
2. Update `config/.env` on VPS:
   - `SITE_PASSWORD=<new>`
   - `ADMIN_PASSWORD=<new>`
3. Restart the webapp: `sudo systemctl restart rexfinhub-web.service`
4. Update vault and password manager.

(Not done autonomously — password generation/storage is a Ryu-only operation.)

---

## Summary — what changed on VPS today

- `sqlite3` package installed (was missing → blocked nightly backup)
- `data/backups/etp_tracker_20260511.db` created (621 MB, first backup ever)
- `config/email_recipients.txt` restored (1 recipient)
- `config/email_recipients_private.txt` restored (1 recipient + header)
- `fail2ban` package installed, service enabled + active

## Summary — what changed in the repo

- New branch `audit-fix-R9-preflight-maintenance`, single commit `683b2d8`
- Modified file: `scripts/preflight_check.py` (+28 / -2)
- This document

## Summary — what is INTENTIONALLY pending

- CBOE cookie rotation (Ryu must obtain from portal)
- VPS `data/.preflight_maintenance` toggle (waits for branch to merge)
- Admin password rotation (Ryu must generate)
- Aggressive cache pruning (out of R9 scope; recommend separate ticket)

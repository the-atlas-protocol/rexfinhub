# Stage 4 Re-Audit — Schedulers

Generated: 2026-05-11T22:50:00-04:00
Agent: schedulers (re-audit)
VPS: jarvis@46.224.126.196 — Ubuntu, TZ America/New_York (EDT)
HEAD on VPS: `d45252c` (Mon May 11 14:51:54 2026 -0400)
Stage 1 reference: `docs/audit_2026-05-11/01_schedulers.md`
Mode: READ-ONLY (no start/stop/restart performed).

## Headline verdict

Mixed. Five Stage-1 fixes landed cleanly and verifiable. **Three critical
findings remain wholly or partially unresolved** despite the patches:

- **F2** (14-day no email send) — the maintenance flag makes preflight
  *pass* but does **not** fix the actual blocker: `preflight_check.py`
  never writes `.preflight_decision.json`, only `send_all` reads it,
  and only a manual dashboard click writes it. **Last successful timer
  send is still 2026-04-27. The 14-day dark window is now ~14 days +
  whatever days follow until someone clicks GO.**
- **F4** (CBOE cookie) — cookie was **not** rotated. 03:00 EDT today
  (cboe_scan_runs ID 20) failed identically to all 9 prior nights.
  10 consecutive failures since 2026-05-03 (last pure-success: 2026-05-08
  manual run, ID 17).
- **F1** (SEC scrape 7200s timeout) — `TimeoutStartSec=7200` was **not**
  bumped. The 16:00 timeout has not recurred (20:00 run completed in
  19.4 m), but only because today's `Compressing...` step took 2 minutes
  instead of 1h40m. Root cause is undiagnosed; the 7200s budget is still
  the same one that was breached on Stage 1 day. **Latent.**

Two new findings surfaced (F14, F15), one Stage-1 finding deteriorated
(F8: disk now at 90% — was 87%), one Stage-1 finding has narrowed but
remains valid (F7), and one is now stale (F6 — fully resolved).

## Stage 1 finding status

| ID | Title | Stage 1 | After fixes | Status |
|---|---|---|---|---|
| F1 | SEC scrape 2-hour TimeoutStartSec breached | critical | TimeoutStartSec unchanged at 7200; 20:00 run finished in 19.4 m. The actual `Compressing...` step took 2 m today vs 1h40m on Stage 1 — root cause not investigated. | **PARTIAL — symptom gone, latent risk remains** |
| F2 | Daily pipeline no email since 2026-04-27 | critical | Maintenance flag added (R3-style) → preflight downgrades fails to warn → exit 0. But `preflight_check.py` still does not write `.preflight_decision.json`. send_all at 19:46 + 20:17 today still printed "standing down (no decision or token mismatch)". `.send_log.json` mtime = Apr 27 21:58. | **NOT RESOLVED — design flaw unaddressed** |
| F3 | NULL primary_strategy 100% in attribution audit | high | Maintenance flag downgrades to warn but the underlying NULL data has NOT been backfilled (today's 21:34 result still says "NULL primary_strategy 100.0%"). | **MASKED — not fixed** |
| F4 | CBOE failed 9 consecutive nights | high | Cookie not rotated. cboe_scan_runs ID 20 (today 03:01) failed identically. 10 consecutive failures now (since 2026-05-03). Last successful: ID 17, 2026-05-08 manual. | **NOT RESOLVED** |
| F5 | db-backup broken (sqlite3 not installed) | high | `sqlite3 3.45.1` installed at `/usr/bin/sqlite3`. **First backup ever created tonight: `etp_tracker_20260511.db` 651 MB** at 20:48 (manual fire — the 23:00 timer slot had not yet hit when I checked). | **RESOLVED** |
| F6 | 13f-quarterly + parquet-rebuild + bloomberg-chain not installed | high | All 3 unit files present in `/etc/systemd/system/`. Both timers `enabled active waiting`. bloomberg.timer now `Unit=rexfinhub-bloomberg-chain.service` (Stage 1 routed to bloomberg.service). 21:00 run today fired the chain end-to-end (4/4 ExecStartPost steps, exit 0). | **RESOLVED** |
| F7 | Bloomberg silent errors every run | medium | bloomberg-chain still emits `Sheets dir not found: data/DASHBOARD/sheets` and `CSV export skipped: can only concatenate str (not "float") to str` on every run (verified 20:57 + 21:05 today). The `data/DASHBOARD/sheets/` directory truly does not exist on disk. | **NOT RESOLVED** |
| F8 | Disk at 87% (5 GB free) | medium | Now **90% used, 3.8 GB free** (worse). 0-byte `cache/.last_prune` exists but no prune timer is scheduled and cache is unchanged at 11 GB. | **WORSE** |
| F9 | Reconciler 403 on today's SEC index | medium | Same 403 today at 08:00:24 for `2026-05-11/form.20260511.idx`. Service still exits 0. No timer change. | **NOT RESOLVED** |
| F10 | `ExecStartPre=-git pull` swallows failures | low | Service files unchanged. Today's preflight pull was a 31-file fast-forward that succeeded — no negative evidence yet. | **NOT RESOLVED (latent)** |
| F11 | Atom watcher silently dead — 0 new always | medium | atom-watcher uptime now 3w 5d, single-filing-worker same. Still no diagnostic added. Atom feeds find 0 new despite SEC scrape catching 24–32 new filings per session. | **NOT RESOLVED** |
| F12 | Audit-fail = service-fail (alert fatigue) | medium | classification-sweep at 09:00 today still exited 1 (status fail, 259 unclassified). Maintenance flag has no effect on classification-sweep — only on preflight. | **PARTIAL — preflight-only mitigation** |
| F13 | Unscheduled scripts in scripts/ (informational) | low | Out of scope for re-audit. | DEFERRED |

**Tally**: 1 fully resolved (F5, F6 — counting F6 as the same line item),
1 worse (F8), 2 partially mitigated (F1, F12), 6 still unresolved (F2,
F3, F4, F7, F9, F10, F11). Net delta: **+1 win, -1 loss** vs Stage 1.

## Verification of stated R-fixes

| Fix | Stated effect | VPS evidence | Verdict |
|---|---|---|---|
| **R1** | bloomberg-chain installed; bloomberg.timer routes to chain | `Unit=rexfinhub-bloomberg-chain.service` present in timer (verified via `systemctl cat`); `.bak.r1` shows the prior version had no `Unit=` line, so the timer was activating bloomberg.service by name match. 21:00 run today fired the chain (4/4 ExecStartPost steps OK). | **CONFIRMED** |
| **R6 (parts)** | 13f-quarterly + parquet-rebuild timers installed | Both unit files in `/etc/systemd/system/`; both timers `enabled active waiting`. parquet-rebuild Trigger: Fri 2026-05-15 06:00; 13f-quarterly Trigger: Wed 2026-05-20 06:00. | **CONFIRMED** |
| **R7** | `Environment=TZ=America/New_York` added to all `*.service` | Verified on sec-scrape, db-backup, cboe, bloomberg, bloomberg-chain, 13f-quarterly, parquet-rebuild. All carry the line. | **CONFIRMED** |
| **R9** | `apt install sqlite3`; fail2ban enabled; first DB backup created | `sqlite3 3.45.1` at `/usr/bin/sqlite3` (was absent Stage 1). `fail2ban` active+enabled. `data/backups/etp_tracker_20260511.db` 651 MB exists, mtime today 20:48. | **CONFIRMED** |
| **Maintenance flag** | Preflight downgrades 3 audits to warn | `data/.preflight_maintenance` exists (zero-byte, mtime 21:04 today). Today's 21:34 preflight result has 3 audits with `"detail": "MAINTENANCE WINDOW ACTIVE — ..."` and `"overall_status": "warn"`. | **CONFIRMED** |

## Outstanding scheduler problems (verified live)

### F2-A — Maintenance flag does not unblock email send (NEW PROOF)

The maintenance flag fixes the *symptom* of preflight (exit 1 → exit 0)
but **not the cause** of the 14-day email blackout. send_all does not
key off `.preflight_result.json` — it requires `.preflight_decision.json`,
which is **only written by a manual dashboard click**. `preflight_check.py`
contains no code path that writes the decision file (verified by `grep`
on the file). Today's 19:46 daily run, after the maintenance flag was
in place, still aborted:

```
[12/12] Sending email reports...
  Sending via send_all.py --use-decision: all bundles ...
  send_all: standing down (no decision or token mismatch)
=== Done in 986s (16.4m) ===
```

**Fix needed**: either (1) preflight writes `.preflight_decision.json`
with `action=GO` when overall_status is `pass` or `warn`, OR (2) send_all
treats `.preflight_result.json overall_status in (pass, warn)` as
sufficient authorization and the dashboard click becomes opt-in/override.

This is a separate Stage-2 ticket from anything currently planned.

### F4 — CBOE cookie still expired (10-day blackout)

`/filings/symbols` is showing data from 2026-05-08 (last successful manual
sweep, ID 17). The 03:00 timer has fired correctly each night and
recorded a failure each night. No alert plumbing surfaced this — the
9-day Stage 1 finding is now 10-day. CBOE_SESSION_COOKIE rotation is
the only manual touchpoint per the cboe-cookie skill.

### F7 — Bloomberg sync silent errors persist on chain

Now that bloomberg-chain runs in place of bloomberg.service, the same
two errors print on every chain run:

```
May 11 21:05:37 jarvis python[1833373]: Global ETP sync skipped: Sheets dir not found: data/DASHBOARD/sheets
May 11 21:05:38 jarvis python[1833373]: CSV export skipped: can only concatenate str (not "float") to str
```

Verified `data/DASHBOARD/sheets/` does not exist on disk
(`ls: cannot access ... No such file or directory`).
`data/DASHBOARD/` does exist with `bloomberg_daily_file.xlsm`,
`exports/`, and `history/`. The expected `sheets/` subdir was either
never created or got pruned. Either way, the "Global ETP sync" feature
that needs it has been silently dark for at least 14 days
(Apr 27 → today, every bloomberg.service run).

### F8 — Disk usage worsened to 90% (3.8 GB free)

Stage 1: 87% / 5.0 GB free. Now: 90% / 3.8 GB free. SEC cache 5.2 GB,
web cache 4.9 GB, submissions 497 MB — same as Stage 1, no eviction
ran. The DB grew 627 → 651 MB. The new daily 651 MB backup will
consume disk linearly: at 7 daily backups retained
(`-mtime +7 -delete` in the backup ExecStart), steady state is
~4.5 GB of backups. **The backup retention will, on its own, push
the system past 95% within a week.** Since nothing prunes cache,
expect disk-full incidents within ~2 weeks unless one of the
following lands:

- Move backups off `/` (separate volume / S3 / Render)
- Add cache-prune timer (LRU > 30 days on `cache/sec` and `cache/web`)
- Drop backup retention to 3 days (~2 GB steady state instead of 4.5)

## New findings (re-audit)

### F14 — `bloomberg.timer` still triggered the OLD service at 17:15 today

**Severity**: medium (one-off; resolved by the 20:49 timer reload).

The R1 patch landed at 20:49 today. The 17:15 fire predated it.
Journal confirms `rexfinhub-bloomberg.service` (the standalone, not the
chain) ran at 17:20:08 → 17:20:23 with the F7 errors and **without**
any of the apply_* ExecStartPost steps. So the manual overrides set in
the `apply_*.py` scripts were not reapplied after the 17:15 Bloomberg
sync — only after the 21:00 sync. Anything Bloomberg overwrote between
17:20 and 21:05 was running with un-overridden values. Low blast radius
(daily timer at 19:30 had already done its sync via `run_daily.py`),
but worth noting in the change log so the team knows the pre-21:00
20:30 dashboard read may have been fed mismatched values.

**Fix**: none needed — was a one-time deploy artifact. The 17:15 + 21:00
Tue runs (and onward) will both fire the chain. Document this in the
Stage 2 changelog.

### F15 — `triggered_by` column writes "manual" for 96% of pipeline_runs

**Severity**: low (forensics impact only).

`SELECT triggered_by, COUNT(*) FROM pipeline_runs GROUP BY 1`:

```
bulk_scrape          | 6
manual               | 146
manual-force-recent  | 2
```

154 total runs, 146 (94.8%) labeled `manual` despite the vast majority
being driven by `rexfinhub-sec-scrape.timer` (which fires at 08:00,
12:00, 16:00, 20:00 weekdays + 00:03 daily). Stage 1 noted "every recent
run is logged as manual" — the broader pattern is that the entire
pipeline_runs labelling system is unable to distinguish timer-driven
vs human-driven runs. Any forensic question about "which runs were
automated?" cannot be answered from this table.

**Fix**: in `scripts/run_all_pipelines.py`, set `triggered_by` based on
`os.environ.get("INVOCATION_ID")` (systemd sets this for unit-driven
processes) — present means timer/service, absent means manual CLI.

## Timer health snapshot (2026-05-11 21:43 EDT)

```
NEXT                                LEFT       LAST                               PASSED      UNIT                                  STATUS
Mon 2026-05-11 23:00:00 EDT     1h 16min Sun 2026-05-10 23:00:18 EDT       22h ago db-backup.timer            (was failing pre-R9; 651MB tonight)
Tue 2026-05-12 03:00:00 EDT     5h 16min Mon 2026-05-11 03:00:04 EDT       18h ago cboe.timer                 [F4: 10-day cookie blackout]
Tue 2026-05-12 08:00:00 EDT          10h Mon 2026-05-11 08:00:19 EDT       13h ago reconciler.timer           [F9: 403 on today's index]
Tue 2026-05-12 08:00:00 EDT          10h Mon 2026-05-11 20:00:00 EDT  1h 43min ago sec-scrape.timer           [F1: latent 7200s budget]
Tue 2026-05-12 09:00:00 EDT          11h Mon 2026-05-11 09:00:00 EDT       12h ago classification-sweep.timer [F12: noisy exit-1 every weekday]
Tue 2026-05-12 17:15:00 EDT          19h Mon 2026-05-11 21:00:20 EDT     43min ago bloomberg.timer            [now → bloomberg-chain; F7 errors]
Tue 2026-05-12 18:30:00 EDT          20h Mon 2026-05-11 18:30:05 EDT  3h 13min ago preflight.timer            [F2: still won't trigger send]
Tue 2026-05-12 19:00:00 EDT          21h Mon 2026-05-11 19:00:19 EDT  2h 43min ago gate-open.timer            healthy
Tue 2026-05-12 19:30:00 EDT          21h Mon 2026-05-11 19:30:11 EDT  2h 13min ago daily.timer                runs but stands down on send
Tue 2026-05-12 20:00:00 EDT          22h Mon 2026-05-11 20:00:00 EDT  1h 43min ago gate-close.timer           healthy
Fri 2026-05-15 06:00:00 EDT       3 days -                                       - parquet-rebuild.timer      newly installed, untriggered
Sun 2026-05-17 07:00:00 EDT       5 days Sun 2026-05-10 07:00:02 EDT 1 day 14h ago bulk-sync.timer            healthy
Wed 2026-05-20 06:00:00 EDT 1 week 1 day -                                       - 13f-quarterly.timer        newly installed, untriggered
```

## Failed services (live)

```
$ systemctl --failed --no-pager
● rexfinhub-cboe.service                 loaded failed failed [F4]
● rexfinhub-classification-sweep.service loaded failed failed [F12]
● rexfinhub-preflight.service            loaded failed failed [pre-maintenance-flag run; today 18:30]
```

The 18:30 preflight failed (exit 1) because the maintenance flag was
added at 21:04 — *after* the 18:30 firing. The 21:34 manual rerun (with
flag in place) produced `overall_status=warn`, so Tue 18:30 should mark
this unit healthy. **Watch the next preflight run** — if `failed`
persists Tuesday, the flag isn't being read.

## Concrete asks for Stage 2

Ranked by blast radius, ascending effort:

1. **(F2) Auto-write `.preflight_decision.json` from preflight_check.py**
   when `overall_status in (pass, warn)`. One-line change unblocks the
   email pipeline, which has been dark **15 days as of tomorrow**.
   Trivial.
2. **(F4) Send a daily critical alert** when the most recent
   `cboe_scan_runs` row is `status='failed'`. 5-line addition to
   `cboe_scan.py` or a separate guard script that runs after the cboe
   service. Medium-trivial.
3. **(F7) Either create `data/DASHBOARD/sheets/`** (if the feature is
   wanted) or **delete the code path** that probes for it. Same for the
   `float`-to-`str` CSV export. Both have been silently broken ≥14 days.
   Source dive needed in `webapp/services/market_sync.py`. Small.
4. **(F8) Add a cache-prune timer** before the disk hits 95%.
   Trivial.
5. **(F12) Make classification-sweep return 0 for "ran successfully,
   findings exist"** and reserve exit 1 for "audit infrastructure
   broken". Trivial.
6. **(F1) Investigate why 16:00 Compressing took 1h40m** when 20:00 took
   2m. Possibly tempfile placement on disk-pressure pages. Medium.
7. **(F11) Instrument atom watcher** — log the actual CIK list it's
   polling, and cross-check against what the polling SEC scrape catches.
   Small.
8. **(F15) Set `triggered_by` from `$INVOCATION_ID`** so timer-driven runs
   are distinguishable from manual ones in `pipeline_runs`. Trivial.

## Surfaces inspected

- `systemctl list-unit-files 'rexfinhub-*'` — 30 units
- `systemctl list-timers 'rexfinhub-*' --all` — 13 timers
- `systemctl --failed` — 3 failed units
- `systemctl status` for: sec-scrape, cboe, db-backup, preflight, daily,
  bloomberg-chain, bloomberg, atom-watcher, single-filing-worker,
  classification-sweep, reconciler, bulk-sync, gate-open, gate-close,
  13f-quarterly.timer, parquet-rebuild.timer
- `systemctl cat` for: sec-scrape, db-backup, cboe, bloomberg.timer,
  bloomberg.service, bloomberg-chain.service, 13f-quarterly.{service,
  timer}, parquet-rebuild.{service,timer}
- `journalctl -u rexfinhub-{sec-scrape,cboe,preflight,daily,bloomberg,
  bloomberg-chain,classification-sweep,reconciler}` — last 24h
- `which sqlite3`, `sqlite3 --version`, `systemctl is-active fail2ban`,
  `systemctl is-enabled fail2ban`
- VPS files: `data/.preflight_*`, `config/.send_enabled`,
  `data/.send_log.json`, `data/backups/`, `data/DASHBOARD/`,
  `cache/{sec,web,submissions}`, `cache/.last_prune`
- DB tables: `pipeline_runs` (last 12 + triggered_by GROUP BY),
  `cboe_scan_runs` (last 12)
- `df -h /`, `du -sh` of cache + data + logs
- `.bak.r1` of bloomberg.timer for R1 verification
- `preflight_check.py` source (grep for decision-file writes)
- `send_all.py` source (grep for use-decision logic)

## Surfaces NOT inspected

- `webapp/services/market_sync.py` source — for F7 fix size
- `apply_classification_sweep.py` — new behavior since 5/11; haven't
  checked whether `--apply --apply-medium` is producing unintended
  overwrites (it printed "Overwrites: 0 (SANITY: must be 0)" so probably
  OK, but worth a Stage 2 review).
- `pipeline_runs` table for the SEC scrape that died Stage 1 — confirmed
  it shows status='completed' anyway, but didn't enumerate the `error_message`
  column for any row.
- The `.preflight_decision.json` write path (must come from somewhere
  in `webapp/routers/admin.py` or similar dashboard handler) — needed
  for F2 fix.
- `INVOCATION_ID` confirmation in systemd-driven runs — assumed present
  because systemd sets it for all started units, but not verified.

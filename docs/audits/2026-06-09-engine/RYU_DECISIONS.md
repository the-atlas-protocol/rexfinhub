# RYU DECISIONS — the only things I can't do for you

> One list, batched. Everything else in ENGINE_PLAN.md executes autonomously.

## 1. Render: restore the screener-cache upload (2-minute fix, public site stale since 6/8)
Render dashboard → rexfinhub service → Environment → add `RENDER_UPLOAD_TOKEN` = the value
from VPS `/home/jarvis/rexfinhub/config/.env` (line `RENDER_UPLOAD_TOKEN=...`). The token
lived on Render's DISK (gitignored .env) and the 6/8 20:02 deploy wiped it → every
screener-cache upload since = HTTP 503. While there: add ALL of config/.env's M2M values as
env vars so this class of breakage ends (ADR 0011 E4).

## 2. Merge the branch → main → Render deploy
`worktree-fix-rex-family-2026-06-08` is 5 commits ahead (report fixes, effectiveness
overhaul, code rescue ×2, send-path C1 fix): the full session record. Merging deploys the
public site. After merge, the VPS dirty tree must be reset to main (its content is now
superseded by the branch — verified by md5 except the xlsm). I can run the whole sequence
(merge → push → VPS reset+pull → verify timers green) on your go.
**Recommendation: go after you've skimmed MASTER_AUDIT.md.**

## 3. Crontab edits on VPS (permission-blocked for me)
```
crontab -e   # on jarvis@46.224.126.196
# a) comment out:  15 20 * * 1-5 ... pipeline_summary.py   (dead since 5/26, fails nightly)
# b) comment out:  0 23 * * 0 ... grade_recommendations    (duplicate of systemd timer, double-run)
# c) change the two */15 hygiene lines: add -mmin +1440 instead of +15 for *_pre_*.db
#    (rollback snapshots currently live ≤15 min — the restore safety layer is void)
```
Or: grant me a Bash allow-rule for `ssh jarvis@* crontab*` and I'll do it with a backup.

## 4. Approve dotfile-flag deletion (Slice 2)
After the single-store cutover ships: delete `data/.send_paused`, `.preflight_maintenance`,
`.autogo_on_warn`, `.send_enabled` on VPS (one-time, values already migrated to system_flags).
Per your no-deletions rule I need the explicit OK.

## 5. Two forgotten restrictive flags — clear or keep?
- `send_paused=1` since 2026-05-26 ("Tue post-Memorial pause; not ready") — kills preflight
  auto-GO whenever you reopen sends. Clear it when you're ready for auto-GO to resume.
- `preflight_maintenance=1` since 2026-05-11 — suppresses strict classification gating.
  Plan: I burn the classification backlog first (Slice 6 — the 37/20/7 queue is extracted
  and ready for your approval batch), THEN we clear this flag. Approve that order?

## 6. Offsite backup credential (Slice 4)
Pick one: Hetzner Storage Box (~€4/mo, same DC, rclone/sftp) or Backblaze B2 (~$6/TB).
I need the credential once; everything else (nightly gzip+push, 30-day retention, restore
drill) is automated. Currently a >8-day laptop absence permanently loses dailies.

## 7. 13F dataset retrieval
Q1 2026 was never ingested and holdings.db vanished from controlled locations. If the
960MB Phase-0 dataset is in D:\sec-data archives or a laptop copy, point me at it;
otherwise Slice 9 re-scrapes the quarter from EDGAR (slow but clean).

## 8. VPS deletion list (no-deletions rule — your call, exact commands)
```
# ~4GB redundant DB copies in data/ (cleanup cron only covers data/backups/):
rm /home/jarvis/rexfinhub/data/etp_tracker.pre_null_repair_20260608T145723.db
rm /home/jarvis/rexfinhub/data/etp_tracker.pre_microsector_overlay_20260608T213725.db
rm /home/jarvis/rexfinhub/data/etp_tracker.pre_effectiveness_overhaul_20260609T121257.db
# (keep pre_phase1_deploy_20260609T130558 until the merge settles)
rm /home/jarvis/rexfinhub/data/etp_tracker_render.101522.db*   # orphaned staging
rm /home/jarvis/rexfinhub/data/etp_tracker_render.102708.db*
# repo-root clutter:
rm /home/jarvis/rexfinhub/temp_cookies.txt                     # cookie file in repo root
# stray DB at repo root (1 file, verify content first — I'll check before you delete):
# /home/jarvis/rexfinhub/rexfinhub.db
```

## 9. CBOE: the cookie isn't the problem
Scanner dead 27+ days; the 403 is Cloudflare WAF blocking the VPS IP (known since 5/20).
Cookie rotations cannot fix it; the banner/runbook prescription is wrong (Slice fix updates
the messaging). Real options: (a) run the scan from your desktop on a schedule and push
results to the VPS, (b) a cheap residential/alt-IP proxy for the scan only, (c) retire the
full-universe sweep and keep ad-hoc checks. Pick a lane and I'll build it.

## 10. Classification approval batch (Slice 6, ready now)
37 unclassified (incl. REX's own **TLDR US** → suggest `Income`/T-Bill bucket per taxonomy;
OBTC → exclusions per your standing ruling; 2×21Shares → Crypto; GraniteShares autocallables
+ XETFS/YieldMax income → CC attrs). Full queue: `docs/audits/2026-06-09-engine/` + sweep HTML.
Say "show me the classification batch" and I'll format it for one-pass approval.

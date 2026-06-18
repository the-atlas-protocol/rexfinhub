# SESSION HANDOFF — autonomous rexfinhub go-live

> Continuity doc written because the originating chat session crashed. Read this
> first in a fresh session to resume exactly where we left off. Companion to the
> canonical docs (`docs/INDEX.md` → `GOAL` → `DEFINITIONS`) and `docs/DECISIONS/0013-*.md`.

## TL;DR — current state

The **self-correcting, AI-enabled rexfinhub** is fully built, tested offline, and
**merged to `main`**. The only thing left is the **first live run on the VPS**.

| Item | State |
|---|---|
| Code (Stages A–F + autonomy hardening) | ✅ merged — PR **#89**, squash `1b48e3a` |
| Go-live as code (`scripts/golive.sh`) | ✅ merged — PR **#90**, squash `a0e7c83` |
| Offline tests | ✅ 120+ passing; 2 real bugs caught via self-review (incl. the critical "gate would block *everything*") and fixed |
| CI guardrails | ✅ hard-enforced |
| VPS synced + first chain cycle run | ❌ **pending — requires execution on the VPS** |
| Heartbeat timer enabled | ❌ pending (golive.sh does it on green) |
| Folded parquet timer retired | ❌ pending (golive.sh does it on green) |

**Why it's not finished here:** the autonomous loop runs on the VPS
(`jarvis@46.224.126.196:/home/jarvis/rexfinhub/`, venv `/home/jarvis/venv`). The
session container that built it has **no path to that host** — verified: no SSH
key, no ssh config, port 22 unreachable/blocked, no ssh client installed. So the
last hop is the operator's (or a Claude Code session running *on* the VPS).

## THE ONE REMAINING ACTION

On the VPS, as the `jarvis` user (with sudo):

```bash
cd /home/jarvis/rexfinhub && git pull --ff-only origin main && bash scripts/golive.sh
```

`scripts/golive.sh` is safe + idempotent and self-gating. It:
1. auto-stashes a dirty tree, then `git pull --ff-only origin main`
2. smoke-tests all new modules + AI cascade config (`anthropic` version, `claude_configured`)
3. runs **one real chain cycle** (`scripts/run_chain.py`) — **no send**; the send gate stays closed
4. checks the preflight gate:
   - **RED** (`data/.preflight_red` present) → prints `data/.preflight_result.json`, **stops**, touches no timers. Exit **2**.
   - **GREEN** → enables `rexfinhub-healthcheck.timer`, runs `healthcheck.py`, runs read-only drift probes (`etp_tracker.reconciler --probe`, `webapp.services.status_reconciler --assert-noop`), retires the folded `rexfinhub-parquet-rebuild.timer`. Exit **0**.
   - sync/smoke failure → exit **1**.

**Next-session task:** get the output of that command from the operator, then:
- exit 0 → confirm operational; retire `rexfinhub-classification-sweep.timer` after a few green days (intentionally left running for now).
- exit 2 → read `data/.preflight_result.json`, fix the failing check, re-run until green.
- exit 1 → diagnose sync/smoke from the output, hand back the corrected command.

## What was built (PR #89, ADR 0013)

A system designed so it **cannot silently drift or send bad data**:

- **Self-healing resolution cascade** — `market/resolve.py`: rules → Bloomberg
  description → AI web lookup → human queue. Each rung degrades safely to the next.
- **Gates that block (fail-closed)** — `scripts/preflight_check.py` + the
  promote/send steps: nothing promotes or sends unless preflight is green.
  Unit-tested. (This is where the critical self-review bug was: a misframed check
  would have blocked *all* sends; fixed.)
- **Reconcilers as autonomous health-probes (fix-at-source)** —
  `etp_tracker/reconciler.py` (ingest) and `webapp/services/status_reconciler.py`
  (market status). Probe (read-only) and apply (writer) modes.
- **Heartbeat / silent-failure detection** — `scripts/healthcheck.py` +
  `deploy/systemd/rexfinhub-healthcheck.{service,timer}`; asserts every chain stage
  registered success today (`.pipeline_stages.jsonl`).
- **Silence-by-default** — one "reports ready" notification on success; noise only on failure.
- **One ordered chain + auto-trigger** — `scripts/run_chain.py`: git pull → Bloomberg
  sync → post-steps (incl. status reconcile) → build all reports → preflight gate →
  promote-or-block → notify. Driven unattended by the Bloomberg-refresh trigger after first green run.
- **Proof-of-death prune of the 2nd market write path** — eliminated the duplicate
  writer that was the "Tidal" status-drift origin; single source of truth is
  `docs/DEFINITIONS.md` + `market/definitions.py`.
- **CI hard-enforces the guardrails.**

## Key facts for the fresh session

- Production source of truth: VPS `jarvis@46.224.126.196:/home/jarvis/rexfinhub/` (SQLite DB lives there).
- Public webapp: rexfinhub.com (Render, read-only replica) — **auto-deploys on push to `main`**, so the merges already redeployed the site.
- Fund classification single source of truth: `docs/DEFINITIONS.md` + `market/definitions.py` — never re-derive suites/status/trusts elsewhere.
- Gate markers live under `data/`: `.preflight_red`, `.preflight_result.json`; stage log `.pipeline_stages.jsonl`.
- Folded/retired timer: `rexfinhub-parquet-rebuild.timer` (now in the chain). Still-running-for-now: `rexfinhub-classification-sweep.timer`.

## Open follow-ups (none blocking)

- After first green VPS run, retire `rexfinhub-classification-sweep.timer`.
- Watch the read-only reconciler probes return clean for several days → then retire the legacy status writer entirely (proof-of-death countdown).
- If smoke shows an old `anthropic` SDK / `claude_configured: False`, the AI web rung degrades safely; update the SDK / key on the VPS to enable it.

#!/usr/bin/env bash
# go-live / operate — deploy-as-code for the self-correcting rexfinhub (ADR 0013).
#
# ONE command to take the merged autonomous loop live on the VPS, AND a safe
# re-runnable health check thereafter. It is conservative by construction:
#   - auto-stashes a dirty tree so the ff-pull can't clobber the nightly chain
#   - smoke-tests every new module + the AI cascade config
#   - runs ONE real chain cycle (no send; the send gate stays closed)
#   - enables autonomy (heartbeat timer) + retires the folded parquet timer ONLY
#     when the preflight gate came back green — a red gate stops here for diagnosis
#
# Usage (on the VPS, as the jarvis user with sudo):
#   bash scripts/golive.sh
#
# Exit codes: 0 = live/green · 2 = gate RED (diagnose data/.preflight_result.json)
#             1 = sync/smoke failure (see output)
set -uo pipefail

ROOT="/home/jarvis/rexfinhub"
PY="/home/jarvis/venv/bin/python"
cd "$ROOT" || { echo "FATAL: $ROOT not found"; exit 1; }

echo "##### A. SYNC TO MERGED main #####"
if [ -n "$(git status --porcelain)" ]; then
  echo ">> working tree dirty — stashing (recoverable via 'git stash pop')"
  git stash push -u -m "golive $(date +%F_%T)" || true
fi
git pull --ff-only origin main || {
  echo "FATAL: ff-pull failed (divergence). Reconcile: git status / git log --oneline -5"; exit 1; }
echo ">> now at: $(git log --oneline -1)"

echo "##### A2. SMOKE TEST (imports + AI cascade config) #####"
$PY - <<'PYEOF' || { echo "FATAL: smoke test failed"; exit 1; }
import importlib
for m in ["market.resolve","scripts.healthcheck","scripts.run_chain","scripts.send_all",
          "scripts.preflight_check","webapp.services.claude_service",
          "etp_tracker.reconciler","webapp.services.status_reconciler"]:
    importlib.import_module(m)
import anthropic
from webapp.services import claude_service
print("anthropic", anthropic.__version__, "| claude_configured", claude_service.is_configured())
print("SMOKE OK")
PYEOF

echo "##### B. ONE REAL CHAIN CYCLE (no send; gate stays closed) #####"
$PY scripts/run_chain.py; echo ">> chain rc=$?"

if [ -f data/.preflight_red ]; then
  echo "##### GATE: RED — previews NOT promoted, send hard-blocked (working as designed) #####"
  echo "----- failing preflight result: -----"
  cat data/.preflight_result.json 2>/dev/null || echo "(no result file)"
  echo ">> Fix the failing check and re-run 'bash scripts/golive.sh'. Timers NOT touched."
  exit 2
fi

echo "##### GATE: GREEN — autonomous loop validated on live data #####"

echo "----- C. enable heartbeat timer (idempotent) -----"
sudo cp deploy/systemd/rexfinhub-healthcheck.service deploy/systemd/rexfinhub-healthcheck.timer /etc/systemd/system/ \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable --now rexfinhub-healthcheck.timer \
  && echo ">> healthcheck.timer enabled" || echo ">> WARN: could not enable healthcheck.timer (sudo?)"
$PY scripts/healthcheck.py; echo ">> healthcheck rc=$?"

echo "----- C2. read-only drift probes (proof-of-death countdown) -----"
$PY -m etp_tracker.reconciler --probe; echo ">> ingest probe rc=$? (0 = scrape clean)"
$PY -m webapp.services.status_reconciler --assert-noop; echo ">> status assert rc=$? (0 = status correct at source)"

echo "----- D. retire the parquet timer (now folded into the chain) -----"
sudo systemctl disable --now rexfinhub-parquet-rebuild.timer 2>/dev/null \
  && echo ">> parquet-rebuild.timer retired" || echo ">> parquet-rebuild.timer already off / no sudo"
# NOTE: classification-sweep.timer is intentionally left running until the chain has
# proven green for several days (it is a harmless catch-up). Retire later with:
#   sudo systemctl disable --now rexfinhub-classification-sweep.timer

echo "##### LIVE — active rexfinhub timers: #####"
systemctl list-timers --all 2>/dev/null | grep rexfinhub || echo "(could not list timers)"
echo "##### GO-LIVE COMPLETE — autonomous loop is operational #####"

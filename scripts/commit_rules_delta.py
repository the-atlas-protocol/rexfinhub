"""Commit the day's classification-rules delta from the VPS back to git.

The autonomous classification engine mutates the git-tracked rules mirror
(config/rules/*.csv incl. fund_master.csv) on the VPS every run. Data-as-code
discipline (ADR 0011 E4) says git must carry that truth — otherwise the tree
drifts (the git_tree_clean assertion fires) and a future checkout loses rules.

Scope-limited and safe by construction:
  - stages ONLY the rules paths below — never code, never anything else
  - pull --rebase first so the commit lands on the current main
  - no-op exit 0 when there is no delta
  - never force-pushes; a failed push leaves the commit local and the
    git_tree_clean assertion stays green (tree is clean either way)

Runs as the final Bloomberg-chain post-step.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RULES_PATHS = [
    "config/rules/fund_master.csv",
    "config/rules/fund_mapping.csv",
    "config/rules/exclusions.csv",
    "config/rules/issuer_mapping.csv",
    "config/rules/attributes_LI.csv",
    "config/rules/attributes_CC.csv",
    "config/rules/attributes_Crypto.csv",
    "config/rules/attributes_Defined.csv",
    "config/rules/attributes_Thematic.csv",
    "config/rules/rex_suite_mapping.csv",
    "config/rules/rex_funds.csv",
    "config/rules/_queues_report.json",
    "config/rules/issuer_brand_overrides.csv",
    "config/rules/issuer_canonicalization.csv",
    "config/rules/underlier_overrides.csv",
    "docs/issuer_review_queue.csv",
    # ai_source_ipo rewrites this every chain. It was NOT in this list, so it stayed
    # unstaged in the working tree — and `pull --rebase` then aborted with "you have
    # unstaged changes", stranding the whole rules commit locally (the divergence that
    # had to be hand-reconciled on 3 consecutive runs). (Ryu 2026-07-20.)
    "config/ipo_watchlist.yaml",
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(PROJECT_ROOT),
                          capture_output=True, text=True, timeout=120)


def main() -> int:
    dirty = _git("status", "--porcelain", "--", *RULES_PATHS).stdout.strip()
    if not dirty:
        print("rules delta: none — nothing to commit")
        return 0

    n = len(dirty.splitlines())
    _git("add", "--", *RULES_PATHS)
    msg = (f"chore(rules): classification delta {date.today().isoformat()} "
           f"({n} file(s), autonomous engine)")
    r = _git("commit", "-m", msg)
    if r.returncode != 0:
        print(f"commit failed: {r.stderr.strip()[:300]}")
        return 1
    print(f"committed: {msg}")

    # Land on current main, then push. --autostash so any OTHER unstaged change the
    # chain left in the working tree (not just RULES_PATHS) doesn't abort the rebase.
    # Retry once on a non-ff push: origin can move between the pull and the push (the
    # engine and a human both push to main), and leaving the commit local is exactly
    # the divergence that forces a hand-reconcile next run — so re-pull and retry
    # rather than strand it. (Ryu 2026-07-20.)
    for attempt in (1, 2):
        pr = _git("pull", "--rebase", "--autostash", "origin", "main")
        if pr.returncode != 0:
            _git("rebase", "--abort")
            print(f"pull --rebase failed (commit kept local): {pr.stderr.strip()[:200]}")
            return 1
        pu = _git("push", "origin", "main")
        if pu.returncode == 0:
            print("pushed rules delta to origin/main")
            return 0
        if attempt == 1:
            print(f"push rejected (attempt 1), re-pulling: {pu.stderr.strip()[:120]}")
    print(f"push failed after retry (commit kept local, tree still clean): "
          f"{pu.stderr.strip()[:200]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

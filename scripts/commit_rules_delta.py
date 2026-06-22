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

    # Land on current main, then push. Rebase is safe: this commit touches
    # only rules CSVs, which nothing else commits concurrently.
    pr = _git("pull", "--rebase", "origin", "main")
    if pr.returncode != 0:
        _git("rebase", "--abort")
        print(f"pull --rebase failed (commit kept local): {pr.stderr.strip()[:200]}")
        return 1
    pu = _git("push", "origin", "main")
    if pu.returncode != 0:
        print(f"push failed (commit kept local, tree still clean): {pu.stderr.strip()[:200]}")
        return 1
    print("pushed rules delta to origin/main")
    return 0


if __name__ == "__main__":
    sys.exit(main())

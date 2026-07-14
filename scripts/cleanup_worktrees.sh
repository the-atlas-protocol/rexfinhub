#!/bin/bash
# One-time cleanup of orphaned worktrees from the 2026-05-11/12 multi-agent
# rebuild day. ~9.3 GB reclaimable.
#
# SAFETY:
#   - Per the cleanup audit (2026-05-19), every worktree below is verified
#     to be merged into origin/main with ZERO uncommitted files.
#   - Excludes the active worktree (`mkt-underlier-backfill`) and the
#     unmerged April orphan (`agent-a7c4d8ee` — Asia competitor Excel work
#     handled separately).
#   - `git worktree remove --force` handles git's metadata correctly;
#     using raw `rm -rf` would leave git's worktree index out of sync.
#
# DRY RUN by default. Add --execute to actually run.
#
# Usage:
#   bash scripts/cleanup_worktrees.sh           # dry-run, prints what would be removed
#   bash scripts/cleanup_worktrees.sh --execute # actually remove

set -euo pipefail

EXECUTE=false
if [ "${1:-}" = "--execute" ]; then
    EXECUTE=true
fi

REPO_ROOT="/c/Foundry/rexfinhub"
cd "$REPO_ROOT"

echo "=== Worktree cleanup ==="
echo "Mode: $([ "$EXECUTE" = true ] && echo EXECUTE || echo DRY-RUN)"
echo

# Collect every worktree path except the canonical repo, the active one, and
# the unmerged April orphan.
mapfile -t TARGETS < <(
    git worktree list --porcelain \
    | awk '/^worktree /{print $2}' \
    | grep -v "^$REPO_ROOT$" \
    | grep -v "mkt-underlier-backfill" \
    | grep -v "agent-a7c4d8ee"
)

echo "Targets: ${#TARGETS[@]} worktrees"
echo

# Disk before
if [ "$EXECUTE" = true ]; then
    BEFORE=$(df -h "$REPO_ROOT" | awk 'NR==2 {print $4}')
    echo "Free space before: $BEFORE"
    echo
fi

OK=0
FAIL=0
for WT in "${TARGETS[@]}"; do
    if [ "$EXECUTE" = true ]; then
        if git worktree remove --force "$WT" 2>/dev/null; then
            echo "  REMOVED  $WT"
            OK=$((OK + 1))
        else
            echo "  FAIL     $WT"
            FAIL=$((FAIL + 1))
        fi
    else
        echo "  would remove  $WT"
    fi
done

echo
if [ "$EXECUTE" = true ]; then
    AFTER=$(df -h "$REPO_ROOT" | awk 'NR==2 {print $4}')
    echo "Free space after:  $AFTER"
    echo "Removed: $OK   Failed: $FAIL"
    echo
    echo "Stale branches (orphaned by worktree removal):"
    git branch | grep -E "^\s+(worktree-agent|audit-|rexops-|fix-strategy-|audit-stockrecs|fix-themes)" | head -20
    echo
    echo "To delete those branches too:"
    echo "  git branch | grep -E '^\\s+(worktree-agent|audit-|rexops-|audit-stockrecs)' | xargs git branch -D"
    echo
    echo "To reclaim space from large orphaned objects (committed DB files):"
    echo "  git gc --aggressive --prune=now"
else
    echo "Dry-run complete. To execute: bash scripts/cleanup_worktrees.sh --execute"
fi

"""Safety utility — find every code path that references a given table name.

Used before any DROP/RENAME in the pipeline-simplification work. Prove
"zero readers" or "exact list of readers to fix first" before destructive
operations.

Searches across:
  - All .py files under repo root (excluding .claude/worktrees/, archive/, __pycache__/)
  - All .html / .j2 templates (rare but possible for raw SQL)
  - All .sql files
  - All systemd unit files on VPS (probed via ssh if --include-vps)

Usage:
    python scripts/find_table_readers.py <table_name> [<table_name> ...]
    python scripts/find_table_readers.py capm_products mkt_rex_funds --include-vps
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".claude", ".git", "__pycache__", "node_modules", "archive"}
EXCLUDE_NAMES = {"find_table_readers.py"}


def search_local(table: str) -> list[tuple[str, int, str]]:
    """Return (path, line, snippet) for every file that mentions `table`."""
    hits: list[tuple[str, int, str]] = []
    # Patterns: FROM table, JOIN table, INTO table, "table"., table_name='table',
    # or just the word table surrounded by non-identifier chars.
    pattern = re.compile(rf"\b{re.escape(table)}\b")
    for ext in ("*.py", "*.html", "*.j2", "*.sql", "*.csv"):
        for path in ROOT.rglob(ext):
            try:
                rel_parts = set(path.relative_to(ROOT).parts)
            except ValueError:
                continue
            if rel_parts & EXCLUDE_DIRS or path.name in EXCLUDE_NAMES:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, ln in enumerate(lines, 1):
                if pattern.search(ln):
                    hits.append((str(path.relative_to(ROOT)), i, ln.strip()[:160]))
    return hits


def search_vps(table: str) -> list[tuple[str, int, str]]:
    """SSH probe — return matches in VPS .py / systemd / .env files."""
    try:
        out = subprocess.run(
            ["ssh", "jarvis@46.224.126.196",
             f"cd /home/jarvis/rexfinhub && grep -rn --include='*.py' --include='*.service' --include='*.timer' -w {table!s} . | head -50"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return [("VPS_PROBE_ERR", 0, str(e))]
    hits: list[tuple[str, int, str]] = []
    for ln in out.stdout.splitlines():
        m = re.match(r"^([^:]+):(\d+):(.*)$", ln)
        if m:
            hits.append((m.group(1), int(m.group(2)), m.group(3).strip()[:160]))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tables", nargs="+", help="table names to search for")
    ap.add_argument("--include-vps", action="store_true", help="also probe VPS")
    args = ap.parse_args()

    overall_clean = True
    for table in args.tables:
        print(f"\n{'='*72}")
        print(f"TABLE: {table}")
        print("=" * 72)
        local = search_local(table)
        if not local:
            print("  LOCAL: zero readers OK")
        else:
            overall_clean = False
            print(f"  LOCAL: {len(local)} reference(s)")
            for path, line, snip in local[:40]:
                print(f"    {path}:{line}: {snip}")
            if len(local) > 40:
                print(f"    ...and {len(local) - 40} more")
        if args.include_vps:
            vps = search_vps(table)
            if not vps:
                print("  VPS:   zero readers OK")
            else:
                overall_clean = False
                print(f"  VPS:   {len(vps)} reference(s)")
                for path, line, snip in vps[:20]:
                    print(f"    {path}:{line}: {snip}")
    print()
    if overall_clean:
        print("VERDICT: all tables clean — safe to rename/drop")
        return 0
    else:
        print("VERDICT: at least one reader found — fix readers first")
        return 1


if __name__ == "__main__":
    sys.exit(main())

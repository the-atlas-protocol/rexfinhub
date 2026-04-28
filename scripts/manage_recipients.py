"""Recipient list management CLI.

One place to list / add / remove / snapshot / diff the email_recipients table.
Replaces the ad-hoc SSH + inline-Python pattern that bit us 2026-04-27 (where
a transaction abort silently rolled back a removal, leaving relasmar in the
autocall list).

Every change is logged to data/.recipient_changes.jsonl with timestamp, actor,
and old/new state. Audit trail for "when did we add this address."

Usage:

    python scripts/manage_recipients.py list
    python scripts/manage_recipients.py list --list-type autocall

    python scripts/manage_recipients.py add autocall test@rexfin.com
    python scripts/manage_recipients.py remove autocall test@rexfin.com

    python scripts/manage_recipients.py snapshot          # update expected_recipients.json
    python scripts/manage_recipients.py diff              # show drift vs snapshot

Validation:
    - Email must contain '@'
    - list_type must be one of the known list_types
      (daily, weekly, li, income, flow, autocall, stock_recs, intelligence,
       screener, pipeline, private)
    - --force to skip validation
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
SNAPSHOT_PATH = PROJECT_ROOT / "config" / "expected_recipients.json"
CHANGE_LOG = PROJECT_ROOT / "data" / ".recipient_changes.jsonl"

KNOWN_LIST_TYPES = {
    "daily", "weekly", "li", "income", "flow",
    "autocall", "stock_recs",
    "intelligence", "screener", "pipeline",
    "private",
}


def _now_et() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def _log_change(action: str, list_type: str, email: str, actor: str = "manage_recipients.py"):
    CHANGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _now_et(),
        "action": action,         # "add" | "remove" | "snapshot"
        "list_type": list_type,
        "email": email,
        "actor": actor,
    }
    with CHANGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def cmd_list(list_type: str | None) -> int:
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    if list_type:
        cur.execute("SELECT list_type, email FROM email_recipients WHERE list_type=? ORDER BY LOWER(email)", (list_type,))
    else:
        cur.execute("SELECT list_type, email FROM email_recipients ORDER BY list_type, LOWER(email)")
    rows = cur.fetchall()
    con.close()
    if not rows:
        print(f"(no recipients found{' for ' + list_type if list_type else ''})")
        return 0
    cur_list = None
    for lt, em in rows:
        if lt != cur_list:
            print(f"\n[{lt}]")
            cur_list = lt
        print(f"  {em}")
    print(f"\nTotal: {len(rows)}")
    return 0


def cmd_add(list_type: str, email: str, force: bool) -> int:
    if not force:
        if "@" not in email:
            print(f"REJECT: '{email}' does not look like an email (missing @). Use --force to override.")
            return 1
        if list_type not in KNOWN_LIST_TYPES:
            print(f"REJECT: '{list_type}' is not a known list_type. Known: {sorted(KNOWN_LIST_TYPES)}")
            print(f"  Use --force to add anyway.")
            return 1

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT 1 FROM email_recipients WHERE list_type=? AND LOWER(email)=LOWER(?)",
                (list_type, email))
    if cur.fetchone():
        print(f"NO-OP: {email} already in {list_type}")
        con.close()
        return 0

    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO email_recipients (email, list_type, is_active, added_at, added_by) VALUES (?, ?, 1, ?, ?)",
        (email, list_type, now, "manage_recipients.py"),
    )
    con.commit()
    con.close()
    _log_change("add", list_type, email)
    print(f"ADDED: {email} -> {list_type}")
    return 0


def cmd_remove(list_type: str, email: str) -> int:
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT 1 FROM email_recipients WHERE list_type=? AND LOWER(email)=LOWER(?)",
                (list_type, email))
    if not cur.fetchone():
        print(f"NO-OP: {email} not in {list_type}")
        con.close()
        return 0
    cur.execute("DELETE FROM email_recipients WHERE list_type=? AND LOWER(email)=LOWER(?)",
                (list_type, email))
    con.commit()
    con.close()
    _log_change("remove", list_type, email)
    print(f"REMOVED: {email} from {list_type}")
    return 0


def cmd_snapshot() -> int:
    """Write current DB state to expected_recipients.json."""
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT list_type, email FROM email_recipients ORDER BY list_type, LOWER(email)")
    roster: dict[str, list[str]] = {}
    for lt, em in cur.fetchall():
        roster.setdefault(lt, []).append(em)
    con.close()

    payload = {
        "snapshot_taken_et": _now_et(),
        "snapshot_source": "manage_recipients.py snapshot",
        "note": "Reference snapshot for preflight diff alerts. Update via this script when adds/removes are intentional.",
        "recipients_by_list": roster,
        "totals": {lt: len(em) for lt, em in roster.items()},
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _log_change("snapshot", "*", "*")
    print(f"SNAPSHOT WRITTEN: {SNAPSHOT_PATH}")
    print(f"Totals: {payload['totals']}")
    return 0


def cmd_diff() -> int:
    """Compare live DB vs snapshot file."""
    if not SNAPSHOT_PATH.exists():
        print(f"NO SNAPSHOT FILE at {SNAPSHOT_PATH}")
        print(f"Run: python scripts/manage_recipients.py snapshot")
        return 1
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    expected = snapshot.get("recipients_by_list", {})

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT list_type, email FROM email_recipients ORDER BY list_type, LOWER(email)")
    live: dict[str, list[str]] = {}
    for lt, em in cur.fetchall():
        live.setdefault(lt, []).append(em)
    con.close()

    print(f"Snapshot taken: {snapshot.get('snapshot_taken_et', '?')}")
    print()
    all_lists = set(expected) | set(live)
    total_diff = 0
    for lt in sorted(all_lists):
        exp = set(e.lower() for e in expected.get(lt, []))
        cur_set = set(e.lower() for e in live.get(lt, []))
        adds = sorted(cur_set - exp)
        removes = sorted(exp - cur_set)
        if adds or removes:
            print(f"[{lt}]")
            for a in adds:
                print(f"  + {a}")
            for r in removes:
                print(f"  - {r}")
            total_diff += len(adds) + len(removes)
    if total_diff == 0:
        print("DB matches snapshot exactly.")
    else:
        print(f"\nTotal differences: {total_diff}")
        print("To accept current DB as new snapshot:")
        print("  python scripts/manage_recipients.py snapshot")
    return 0 if total_diff == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Manage email_recipients table + snapshot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Show current recipients")
    p_list.add_argument("--list-type", help="Filter to one list_type")

    p_add = sub.add_parser("add", help="Add a recipient")
    p_add.add_argument("list_type")
    p_add.add_argument("email")
    p_add.add_argument("--force", action="store_true",
                       help="Skip email/list_type validation")

    p_rm = sub.add_parser("remove", help="Remove a recipient")
    p_rm.add_argument("list_type")
    p_rm.add_argument("email")

    sub.add_parser("snapshot", help="Write current DB state to expected_recipients.json")
    sub.add_parser("diff", help="Compare DB vs snapshot")

    args = ap.parse_args()

    if args.cmd == "list":
        return cmd_list(getattr(args, "list_type", None))
    elif args.cmd == "add":
        return cmd_add(args.list_type, args.email, args.force)
    elif args.cmd == "remove":
        return cmd_remove(args.list_type, args.email)
    elif args.cmd == "snapshot":
        return cmd_snapshot()
    elif args.cmd == "diff":
        return cmd_diff()


if __name__ == "__main__":
    sys.exit(main())

"""Pipeline-simplification baseline integration test (2026-05-13).

Hits LIVE Render, asserts on REAL data. No mocks. Fails loud.

Records the verifiable state of the system before any simplification work.
Re-run after each phase to confirm nothing regressed.

Usage:
    python tests/integration/test_baseline.py
    # exits 0 on full pass, 1 on any failure
    # writes report to docs/baseline_<date>.txt
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import requests

BASE_URL = "https://rex-etp-tracker.onrender.com"
SITE_PWD = "rexusers26"
ADMIN_PWD = "ryu123"

ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_local_db() -> Path:
    """Find the canonical local DB. When running from a worktree, the
    worktree's data/etp_tracker.db is typically an empty placeholder —
    prefer the main checkout's populated copy. Validate by table presence,
    not just file existence."""
    candidates = [ROOT / "data" / "etp_tracker.db"]
    if ".claude" in ROOT.parts:
        idx = ROOT.parts.index(".claude")
        main_root = Path(*ROOT.parts[:idx])
        candidates.append(main_root / "data" / "etp_tracker.db")
    for cand in candidates:
        if not cand.exists():
            continue
        try:
            c = sqlite3.connect(str(cand))
            n = c.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='rex_products'"
            ).fetchone()[0]
            c.close()
            if n == 1:
                return cand
        except sqlite3.OperationalError:
            continue
    return candidates[0]  # return first even if empty so caller can report


LOCAL_DB = _resolve_local_db()

ASSERTIONS: list[tuple[str, bool, str]] = []


def assert_true(label: str, ok: bool, detail: str = ""):
    ASSERTIONS.append((label, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}{(' — ' + detail) if detail else ''}")


def db_count(sql: str, params: tuple = ()) -> int:
    if not LOCAL_DB.exists():
        return -1
    c = sqlite3.connect(str(LOCAL_DB))
    try:
        return c.execute(sql, params).fetchone()[0]
    finally:
        c.close()


def main() -> int:
    s = requests.Session()
    s.post(f"{BASE_URL}/login", data={"password": SITE_PWD}, allow_redirects=False)
    s.post(f"{BASE_URL}/admin/login", data={"password": ADMIN_PWD}, allow_redirects=False)

    print("=" * 72)
    print(f"BASELINE INTEGRATION TEST — {datetime.utcnow().isoformat()}Z")
    print(f"Target: {BASE_URL}")
    print("=" * 72)

    # Health
    r = s.get(f"{BASE_URL}/health", timeout=30)
    assert_true("health endpoint 200", r.status_code == 200, f"got {r.status_code}")
    try:
        commit = r.json().get("commit", "?")
    except Exception:
        commit = "?"
    assert_true("health JSON has commit", commit != "?", f"commit={commit}")

    # Pipeline page
    r = s.get(f"{BASE_URL}/operations/pipeline?per_page=20", timeout=30)
    assert_true("/operations/pipeline 200", r.status_code == 200, f"got {r.status_code}")
    body = r.text
    assert_true("pipeline body > 50KB", len(body) > 50_000, f"size={len(body)}")
    has_recent = bool(re.search(r"2026-05-1[0-3]", body))
    assert_true("pipeline shows 2026-05-10+ dates (sync worked)", has_recent)
    assert_true("pipeline lacks PEND/DELAYED jargon header", "Funds in Pipeline (PEND/DELAYED)" not in body)

    # Underlier race
    for tk in ("NVDA", "SNDK", "QQQ", "XOVR"):
        r = s.get(f"{BASE_URL}/operations/pipeline/underlier/{tk}?modal=1", timeout=30)
        assert_true(f"underlier race /{tk} 200", r.status_code == 200, f"got {r.status_code}")
    r = s.get(f"{BASE_URL}/operations/pipeline/underlier/ETH?modal=1", timeout=30)
    assert_true("underlier race /ETH 200 (crypto bridge)", r.status_code == 200)
    r = s.get(f"{BASE_URL}/operations/pipeline/underlier/XETUSD?modal=1", timeout=30)
    assert_true("underlier race /XETUSD 200", r.status_code == 200)

    # /market/underlier with +US suffix
    r = s.get(f"{BASE_URL}/market/underlier?type=li&underlier=SNDK", timeout=30)
    assert_true("/market/underlier?underlier=SNDK 200", r.status_code == 200)
    r = s.get(f"{BASE_URL}/market/underlier?type=li&underlier=SNDK+US", timeout=30)
    assert_true("/market/underlier?underlier=SNDK+US 200 (normalized)", r.status_code == 200)

    # Reserved symbols
    r = s.get(f"{BASE_URL}/operations/reserved-symbols", timeout=30)
    assert_true("/operations/reserved-symbols 200", r.status_code == 200)
    tbody = re.search(r"<tbody>(.*?)</tbody>", r.text, re.DOTALL)
    if tbody:
        rows = tbody.group(1).count("<tr")
        assert_true("reserved_symbols >= 250 rows", rows >= 250, f"got {rows}")

    # Strategy
    for path in ("/strategy", "/strategy/whitespace", "/strategy/race", "/strategy/ticker/NVDX"):
        r = s.get(f"{BASE_URL}{path}", timeout=30)
        assert_true(f"{path} 200", r.status_code == 200, f"got {r.status_code}")

    # IPO intel
    r = s.get(f"{BASE_URL}/intel/ipo", timeout=30)
    assert_true("/intel/ipo 200", r.status_code == 200)
    assert_true("/intel/ipo has SpaceX", "SpaceX" in r.text)

    # Daily digest preview
    r = s.get(f"{BASE_URL}/admin/digest/preview-daily", timeout=120)
    assert_true("/admin/digest/preview-daily 200", r.status_code == 200, f"got {r.status_code}")
    body = r.text
    assert_true("daily preview > 50KB", len(body) > 50_000)
    assert_true("daily preview has Upcoming Launches", "Upcoming Launches" in body)
    assert_true("daily preview has Upcoming Effectiveness", "Upcoming Effectiveness" in body)

    # Debug-daily
    r = s.get(f"{BASE_URL}/admin/digest/debug-daily", timeout=60)
    if r.status_code == 200:
        d = r.json()
        today_et = d.get("today_et")
        assert_true(f"debug-daily today_et={today_et}", today_et == date.today().isoformat())
        sc = d.get("section_counts") or {}
        assert_true("debug-daily pipeline_funds populated", isinstance(sc.get("pipeline_funds"), int))
        assert_true("debug-daily pending > 0", isinstance(sc.get("pending"), int) and sc.get("pending", 0) > 0)

    # Local DB sanity
    if LOCAL_DB.exists():
        n_rex = db_count("SELECT COUNT(*) FROM rex_products")
        n_filings = db_count("SELECT COUNT(*) FROM filings")
        n_mkt = db_count("SELECT COUNT(*) FROM mkt_master_data")
        assert_true(f"local rex_products {n_rex} >= 700", n_rex >= 700)
        assert_true(f"local filings {n_filings} >= 600000", n_filings >= 600_000)
        assert_true(f"local mkt_master_data {n_mkt} >= 7000", n_mkt >= 7_000)
        latest_delta = db_count("SELECT MIN(julianday('now') - julianday(filing_date)) FROM filings WHERE filing_date >= date('now', '-30 days')")
        assert_true(f"local newest filing <= 14d old (delta={latest_delta})", latest_delta is not None and 0 <= latest_delta <= 14)

    print()
    print("=" * 72)
    passed = sum(1 for _, ok, _ in ASSERTIONS if ok)
    total = len(ASSERTIONS)
    print(f"BASELINE: {passed}/{total} assertions pass")
    print("=" * 72)
    failures = [(l, d) for l, ok, d in ASSERTIONS if not ok]
    if failures:
        print("FAILURES:")
        for label, detail in failures:
            print(f"  - {label}: {detail}")

    report_path = ROOT / "docs" / f"baseline_{date.today().isoformat()}.txt"
    report_path.parent.mkdir(exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        f.write(f"BASELINE INTEGRATION TEST — {datetime.utcnow().isoformat()}Z\n")
        f.write(f"Target: {BASE_URL}\n")
        f.write(f"Result: {passed}/{total}\n\n")
        for label, ok, detail in ASSERTIONS:
            mark = "PASS" if ok else "FAIL"
            f.write(f"[{mark}] {label}{('  — ' + detail) if detail else ''}\n")
    print(f"Report written to: {report_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

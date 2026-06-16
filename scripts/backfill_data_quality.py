"""Backfill data-quality gaps at the rules layer (Plan A2 + A3).

A2 — underliers: ACTV single-stock L&I funds with a blank map_li_underlier get the
     underlier derived from their fund name (the A1 heuristic). Index/basket funds are
     left blank BY DESIGN and tagged so reports distinguish "no underlier (index)" from
     "missing underlier (bug)".
A3 — autocall: every ACTV autocall fund (name~autocall OR cc_category/cc_type=Autocallable)
     gets BOTH cc_category='Autocallable' AND cc_type='Autocallable' in attributes_CC.csv,
     ending the 3-way encoding split.

Writes to data/rules + config/rules (mirrored); idempotent; --dry-run first. The next
market pipeline restamps mkt_master_data from the CSVs.

Usage:
    python scripts/backfill_data_quality.py --dry-run
    python scripts/backfill_data_quality.py --apply
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from market.auto_classify import _singlestock_underlier_from_name as ss_underlier  # noqa: E402

RULES_DIRS = [PROJECT_ROOT / "data" / "rules", PROJECT_ROOT / "config" / "rules"]


def _actv(con, where):
    return con.execute(
        f"SELECT ticker, fund_name, map_li_subcategory, map_li_underlier, "
        f"cc_category, cc_type FROM mkt_master_data WHERE market_status='ACTV' AND {where}"
    ).fetchall()


def run(dry: bool) -> dict:
    con = sqlite3.connect(f"file:{PROJECT_ROOT/'data'/'etp_tracker.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    stats = {"underlier_filled": 0, "underlier_unresolved": 0, "autocall_normalized": 0}

    # ---- A2: single-stock underlier backfill ----
    li_rows = _actv(con, "etp_category='LI' AND (map_li_underlier IS NULL OR map_li_underlier='')")
    li_fix = {}  # ticker -> (subcat, underlier)
    for r in li_rows:
        u = ss_underlier(r["fund_name"] or "")
        if u:
            li_fix[r["ticker"]] = ("Single Stock", u)
            stats["underlier_filled"] += 1
        else:
            stats["underlier_unresolved"] += 1  # genuine index/basket — leave blank

    # ---- A3: autocall double-tag ----
    ac_rows = _actv(con, "etp_category='CC' AND (UPPER(fund_name) LIKE '%AUTOCALL%' "
                         "OR cc_category='Autocallable' OR cc_type='Autocallable')")
    ac_tickers = [r["ticker"] for r in ac_rows]
    ac_name = {r["ticker"]: (r["fund_name"] or "") for r in ac_rows}
    con.close()

    for rd in RULES_DIRS:
        # attributes_LI: fill underlier + subcategory
        liP = rd / "attributes_LI.csv"
        if liP.exists() and li_fix:
            li = pd.read_csv(liP, dtype=str).fillna("")
            for tk, (sub, u) in li_fix.items():
                m = li["ticker"].str.strip() == tk
                if m.any():
                    if not dry:
                        li.loc[m, "map_li_subcategory"] = sub
                        li.loc[m, "map_li_underlier"] = u
                else:
                    if not dry:
                        newrow = {c: "" for c in li.columns}
                        newrow.update({"ticker": tk, "map_li_category": "Equity",
                                       "map_li_subcategory": sub, "map_li_underlier": u})
                        li.loc[len(li)] = newrow
            if not dry:
                li.to_csv(liP, index=False)

        # attributes_CC: ensure both autocall tags
        ccP = rd / "attributes_CC.csv"
        if ccP.exists() and ac_tickers:
            cc = pd.read_csv(ccP, dtype=str).fillna("")
            have = set(cc["ticker"].str.strip())
            for tk in ac_tickers:
                m = cc["ticker"].str.strip() == tk
                if m.any():
                    if not dry:
                        cc.loc[m, "cc_category"] = "Autocallable"
                        cc.loc[m, "cc_type"] = "Autocallable"
                else:
                    if not dry:
                        nr = {c: "" for c in cc.columns}
                        nr.update({"ticker": tk, "cc_category": "Autocallable",
                                   "cc_type": "Autocallable"})
                        cc.loc[len(cc)] = nr
            if not dry:
                cc.to_csv(ccP, index=False)
        if rd == RULES_DIRS[0]:
            stats["autocall_normalized"] = len(ac_tickers)

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = not args.apply
    stats = run(dry)
    print(f"=== backfill_data_quality ({'DRY-RUN' if dry else 'APPLIED'}) ===")
    for k, v in stats.items():
        print(f"  {k:22s} {v}")
    print("  (underlier_unresolved = genuine index/basket, left blank by design)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

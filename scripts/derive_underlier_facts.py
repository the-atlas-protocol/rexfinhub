"""Derive underlier facts (Track B / B1) — drift-proof, runs every chain.

Fills two underlier fields that are structurally reliable and change NO count or
category — they only make the underlier a verified fact instead of a blank:

  underlier_name        <- the fund's own map_{li,cc,crypto}_underlier when blank
  underlier_is_wrapper  <- TRUE when the underlier is itself an ETP in our universe
                           (RAM->DRAM, NUGY->NUGT, YBIT->IBIT, SPYQ->SPY, ...)

Why a derivation step, not a one-off UPDATE: market_sync delete-and-rebuilds
mkt_master_data every run, so a manual patch is wiped the next chain. This runs
as a post-step so the facts are recomputed from source every time.

Precedence: a CURATED value in fund_master.csv always wins. apply_fund_master runs
before this and stamps the CSV; this step only fills what the curated layer left
blank (and never flips a curated underlier_is_wrapper). underlier_type is NOT touched
here — re-deriving it moves the single-name axis and waits for the pinned rule (B0).

Idempotent. Runs after apply_fund_master + apply_underlier_overrides.

    python scripts/derive_underlier_facts.py            # apply
    python scripts/derive_underlier_facts.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
FUND_MASTER = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"


def _norm_ticker(raw: str) -> str | None:
    """Normalise an underlier value to a US-ticker form, or None if it isn't one.

    map_*_underlier holds a mix: bare tickers ('SPY'), suffixed ('SPY US'), and
    plain NAMES ('Gold', 'TECHNOLOGY', 'MAGNIFICENT') that are not tickers at all.
    Only an ALL-CAPS alphanumeric token (optionally ' US'-suffixed) is a candidate
    ticker; a name with a lowercase letter or a space-separated phrase is rejected.
    """
    if not raw:
        return None
    s = raw.strip().upper()
    if s.endswith(" US"):
        s = s[:-3].strip()
    if not s or " " in s:
        return None
    # tickers are short uppercase alnum (allow dots for class shares, e.g. BRK.B)
    core = s.replace(".", "")
    if not core.isalnum() or len(s) > 6:
        return None
    # reject values that were clearly a word, not a symbol (the raw had lowercase)
    if raw.strip() != raw.strip().upper():
        return None
    return f"{s} US"


def _curated_from_fund_master() -> tuple[set[str], set[str]]:
    """Tickers whose underlier_name / underlier_is_wrapper are curated (non-blank)
    in fund_master.csv — the derivation must not override these."""
    named: set[str] = set()
    wrapper_set: set[str] = set()
    if not FUND_MASTER.exists():
        return named, wrapper_set
    with FUND_MASTER.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            tk = (r.get("ticker") or "").strip()
            if not tk:
                continue
            if (r.get("underlier_name") or "").strip():
                named.add(tk)
            if (r.get("underlier_is_wrapper") or "").strip():
                wrapper_set.add(tk)
    return named, wrapper_set


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    curated_name, curated_wrapper = _curated_from_fund_master()
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    # The ETP universe = tickers whose row is a genuine fund (ETF/ETN), any status.
    # NOT every ticker: mkt_master_data also carries non-fund underlier rows (e.g.
    # SPCX, SpaceX's pre-IPO equity, fund_type NULL). Without the fund_type filter the
    # 8 "2X SpaceX" single-equity funds matched SPCX and were wrongly flagged wrappers.
    # A wrapper's underlier must itself be an ETF/ETN. (Caught in dry-run, 2026-07-20.)
    universe = {
        r[0] for r in cur.execute(
            "SELECT ticker FROM mkt_master_data WHERE fund_type IN ('ETF','ETN')"
        ).fetchall()
    }

    rows = cur.execute(
        """SELECT ticker, underlier_name, underlier_is_wrapper,
                  map_li_underlier, map_cc_underlier, map_crypto_underlier
           FROM mkt_master_data
           WHERE market_status='ACTV' AND etp_category IN ('LI','CC')"""
    ).fetchall()

    name_fills: list[tuple[str, str]] = []
    wrapper_sets: list[tuple[str, str, str]] = []  # (ticker, underlier, root)
    for ticker, uname, is_wrap, li_u, cc_u, cr_u in rows:
        underlier_raw = (li_u or cc_u or cr_u or "").strip()

        # 1) underlier_name: fill only when blank AND not curated
        if (not (uname or "").strip()) and underlier_raw and ticker not in curated_name:
            name_fills.append((underlier_raw, ticker))

        # 2) underlier_is_wrapper: set 1 when the underlier resolves to a tracked ETP,
        #    unless fund_master curated it. (Default from apply_fund_master is 0.)
        if ticker in curated_wrapper:
            continue
        norm = _norm_ticker(underlier_raw)
        if norm and norm in universe:
            # root underlier = the wrapper ETP's own underlier_name, one hop (DRAM->DRAM)
            root = cur.execute(
                "SELECT underlier_name FROM mkt_master_data WHERE ticker=?", (norm,)
            ).fetchone()
            root_name = (root[0] if root and root[0] else norm.replace(" US", ""))
            if not is_wrap or is_wrap == 0:
                wrapper_sets.append((ticker, norm, root_name))

    print(f"ACTV LI/CC scanned: {len(rows)}")
    print(f"underlier_name to fill (blank -> map_*_underlier): {len(name_fills)}")
    print(f"underlier_is_wrapper -> 1 (underlier is a tracked ETP): {len(wrapper_sets)}")
    for tk, norm, root in wrapper_sets[:12]:
        print(f"    {tk:10s} underlier={norm:10s} root={root}")

    if args.dry_run:
        print("\n[DRY-RUN] no writes.")
        con.close()
        return 0

    for underlier_raw, ticker in name_fills:
        cur.execute("UPDATE mkt_master_data SET underlier_name=? WHERE ticker=?",
                    (underlier_raw, ticker))
    for ticker, norm, root in wrapper_sets:
        cur.execute(
            "UPDATE mkt_master_data SET underlier_is_wrapper=1, root_underlier_name=? "
            "WHERE ticker=?", (root, ticker))
    con.commit()
    con.close()
    print(f"\nApplied: {len(name_fills)} underlier_name, {len(wrapper_sets)} wrapper flags.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

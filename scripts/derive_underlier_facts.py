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
blank (and never flips a curated underlier_is_wrapper). underlier_type IS now derived here, on
the vocabulary pinned by B0 (2026-08-04): Stock | ETF | Index | Commodity | FX | Crypto.
"Stock" not "Equity" — "Single Equity" is the BUCKET and contains both stocks and ETFs.

  underlier_type <- one derivation, in precedence order:
      crypto underlier                     -> Crypto
      is_singlestock '<x> Curncy'          -> FX
      is_singlestock '<x> Comdty'          -> Commodity
      is_singlestock '<x> Index'           -> Index
      underlier_is_wrapper = 1             -> ETF   (underlier is itself a tracked ETF/ETN)
      is_singlestock '<x> US' / '<x> Equity' -> Stock
      underlier resolves to a tracked ETF/ETN -> ETF
      otherwise                            -> Index

This does NOT itself move category_display; it makes the fact available so the single-name
axis can be DERIVED from it (B2) instead of voted on by three separate sites.

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
    core = s.replace(".", "").replace("/", "")   # class shares: BRK.B, BRK/B
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


def _derive_single_name_axis(cur, dry_run: bool) -> int:
    """B2 — ONE derivation of the single-name axis, from the verified underlier fact.

    The rule (pinned in config/contracts/report_numbers.yaml + docs/DEFINITIONS.md, B0):

        Single Equity <=> underlier_type IN (Stock, ETF, Crypto)
                          AND exactly one underlier
                          AND rex_suite != 'MicroSectors'
        Index/Basket  <=> everything else

    Why here and not in data_engine._derive_category_display: that runs during SYNC, before
    underlier_type exists (this script is a post-step). data_engine seeds category_display
    from the curated map_li_subcategory; this re-derives it from the FACT once the fact is
    known. One owner for the axis, and it is the same module that owns the underlier.

    underlier_type NULL => no evidence (440+ funds carry no underlier signal at all).
    Those KEEP their curated value rather than being asserted into a bucket we cannot
    justify — an honest unknown beats a confident guess.
    """
    from market.config import CAT_LI_SS, CAT_LI_INDEX, CAT_CC_SS, CAT_CC_INDEX

    SINGLE = ("Stock", "ETF", "Crypto")
    BASKET = ("Index", "Commodity", "FX")
    rows = cur.execute(
        """SELECT ticker, etp_category, underlier_type, rex_suite, category_display
           FROM mkt_master_data
           WHERE market_status='ACTV' AND etp_category IN ('LI','CC')"""
    ).fetchall()

    changes = []
    for ticker, cat, utype, suite, disp in rows:
        if (suite or "") == "MicroSectors":
            want_single = False                      # standing exception
        elif utype in SINGLE:
            want_single = True
        elif utype in BASKET:
            want_single = False
        else:
            continue                                 # unknown -> keep curated
        if cat == "LI":
            target = CAT_LI_SS if want_single else CAT_LI_INDEX
        else:
            target = CAT_CC_SS if want_single else CAT_CC_INDEX
        if (disp or "") != target:
            changes.append((target, ticker))

    print(f"single-name axis: {len(changes)} category_display change(s)")
    for tgt, tk in changes[:10]:
        print(f"    {tk:10s} -> {tgt}")
    if changes and not dry_run:
        cur.executemany(
            "UPDATE mkt_master_data SET category_display=? WHERE ticker=?", changes)
    return len(changes)


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
                  map_li_underlier, map_cc_underlier, map_crypto_underlier,
                  is_singlestock, underlier_type
           FROM mkt_master_data
           WHERE market_status='ACTV' AND etp_category IN ('LI','CC')"""
    ).fetchall()

    name_fills: list[tuple[str, str]] = []
    wrapper_sets: list[tuple[str, str, str]] = []  # (ticker, underlier, root)
    etf_universe = {
        r[0] for r in cur.execute(
            "SELECT ticker FROM mkt_master_data WHERE fund_type IN ('ETF','ETN')"
        ).fetchall()
    }
    etf_universe_bare = {t.replace(" US", "").strip().upper() for t in etf_universe}
    # Known single securities, for the case where Bloomberg leaves is_singlestock blank.
    # Evidence-based on purpose: map_li_underlier also holds INDEX names that look like
    # tickers ("MINERS", "BIGOIL", "SOLFANGT"), so a shape test alone would type those
    # Stock. Requiring the symbol to exist as a real stock/ETP keeps them Index.
    stock_universe = {
        str(r[0]).upper().replace(" US", "").strip()
        for r in cur.execute("SELECT ticker FROM mkt_stock_data").fetchall()
    }
    type_sets: list[tuple[str, str]] = []   # (ticker, underlier_type)

    for ticker, uname, is_wrap, li_u, cc_u, cr_u, is_ss, u_type in rows:
        underlier_raw = (li_u or cc_u or cr_u or "").strip()

        # 1) underlier_name: fill only when blank AND not curated
        if (not (uname or "").strip()) and underlier_raw and ticker not in curated_name:
            name_fills.append((underlier_raw, ticker))

        # 1b) underlier_type on the B0-pinned vocabulary (see module docstring).
        _iss = str(is_ss or "").strip()
        _norm_u = _norm_ticker(underlier_raw)
        # A mapped underlier that is a NAME rather than a symbol ("NASDAQ-100", "MINERS",
        # "Gold", "BIGOIL") IS evidence — of an index/basket, not of a security.
        _uraw_is_index_name = bool(underlier_raw) and _norm_u is None
        _is_wrapper = bool(is_wrap) or (_norm_u in etf_universe if _norm_u else False)
        if (cr_u or "").strip():
            _t = "Crypto"
        elif _iss.endswith(" Curncy"):
            # Bloomberg dumps THREE different asset classes into Curncy:
            #   precious metals  XAU (gold) XAG (silver) XPT (platinum) XPD (palladium)
            #   fiat FX          EURUSD, USDJPY, GBP, ...
            #   crypto           XBTUSD (bitcoin) XETUSD (ether) XSOUSD XRPUSD XDG XUI ...
            # All three start with X for metals AND crypto, so a bare "starts with X"
            # test calls MicroSectors GOLD a crypto fund (caught 2026-08-04: SHNY/DULL
            # and ProShares UGL/AGQ were typed Crypto). Metals are an explicit list;
            # fiat is an ISO-4217 pair; everything else under Curncy is crypto.
            _base = _iss.rsplit(" ", 1)[0].upper()
            _metals = {"XAU", "XAG", "XPT", "XPD"}
            _fiat = {"USD", "JPY", "EUR", "GBP", "CHF", "AUD", "CAD", "NZD", "CNY", "MXN"}
            _stripped = _base.replace("USD", "") if _base not in _fiat else _base
            if _base in _metals or _stripped in _metals:
                _t = "Commodity"
            elif _base in _fiat or (
                    len(_base) == 6 and _base[:3] in _fiat and _base[3:] in _fiat):
                _t = "FX"
            else:
                _t = "Crypto"
        elif _iss.endswith(" Comdty"):
            _t = "Commodity"
        elif _iss.endswith(" Index"):
            _t = "Index"
        elif _is_wrapper:
            _t = "ETF"
        elif _iss:
            # is_singlestock holds the UNDERLIER'S IDENTIFIER. Everything that is not
            # Curncy/Comdty/Index is a LISTED SECURITY — and not only US ones: HYNX's
            # underlier is "000660 KS" (SK Hynix, Korea) and was falling through to
            # Index, dropping a genuine single-stock fund off the axis (2026-08-04).
            _t = "Stock"
        else:
            # No is_singlestock (common for new launches). Fall back to the mapped
            # underlier, but ONLY when it resolves to a security we actually know —
            # otherwise index names like MINERS/BIGOIL would be typed Stock.
            _cand = (_norm_u or "").replace(" US", "").strip()
            if _cand and _cand in etf_universe_bare:
                _t = "ETF"
            elif _cand and _cand in stock_universe:
                _t = "Stock"
            elif _uraw_is_index_name:
                _t = "Index"
            else:
                # 440 ACTV LI/CC funds carry NO underlier signal at all. Defaulting them
                # to "Index" would ASSERT a fact we cannot support and would silently pull
                # genuine single-name funds off the axis. Leave it unknown; the single-name
                # derivation then falls back to the curated map_li_subcategory for these.
                _t = None
        if (u_type or None) != _t:
            type_sets.append((ticker, _t))

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
    from collections import Counter as _C
    _c = _C(t if t else "(unknown)" for _, t in type_sets)
    print(f"underlier_type to set/change: {len(type_sets)}  {dict(_c)}")
    for tk, norm, root in wrapper_sets[:12]:
        print(f"    {tk:10s} underlier={norm:10s} root={root}")

    if args.dry_run:
        _derive_single_name_axis(cur, dry_run=True)
        print("\n[DRY-RUN] no writes.")
        con.close()
        return 0

    for underlier_raw, ticker in name_fills:
        cur.execute("UPDATE mkt_master_data SET underlier_name=? WHERE ticker=?",
                    (underlier_raw, ticker))
    for ticker, _t in type_sets:
        cur.execute("UPDATE mkt_master_data SET underlier_type=? WHERE ticker=?", (_t, ticker))
    for ticker, norm, root in wrapper_sets:
        cur.execute(
            "UPDATE mkt_master_data SET underlier_is_wrapper=1, root_underlier_name=? "
            "WHERE ticker=?", (root, ticker))
    _derive_single_name_axis(cur, dry_run=False)
    con.commit()
    con.close()
    print(f"\nApplied: {len(name_fills)} underlier_name, {len(wrapper_sets)} wrapper flags, "
          f"{len(type_sets)} underlier_type.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

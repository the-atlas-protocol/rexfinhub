"""AI underlier intelligence — identify what every competitor-filed L&I underlier IS.

The T-REX recommendation report's Foreign and Pre-IPO sections were driven by
hand-curated seed lists, so foreign single-stocks that competitors actively file on
(SK Square, Leeno Industrial, Hanmi Semiconductor, Siemens, BE Semiconductor, Sivers,
MiniMax, ...) never reached the Foreign list. This step closes that gap with an AI
middleman: it scans recent competitor 485APOS L&I filings, extracts each underlier,
and classifies it into one of:

    foreign   — a single stock PRIMARILY listed outside the US (no liquid US ADR)
    us_stock  — a US-listed single stock (already US-tradeable; not foreign whitespace)
    pre_ipo   — a genuinely private company (OpenAI, SpaceX, Anthropic, ...)
    crypto    — a crypto token (Cardano, Chainlink, ...)
    basket    — an index / theme / multi-name basket (not a single stock)
    other     — anything else

`foreign` rows are merged into data/foreign/universe.parquet (which
foreign_filings.load_foreign_universe() prefers over the built-in seed), so the
Foreign section self-heals as competitors file new overseas names. `pre_ipo` rows are
journaled for the IPO-watchlist sourcer. Each decision is journaled to
logs/ai_underlier_*.jsonl and is idempotent — only genuinely new series cost tokens.

    python scripts/ai_underlier_intel.py            # apply (merge universe)
    python scripts/ai_underlier_intel.py --dry-run  # classify + journal only
    python scripts/ai_underlier_intel.py --days 365  # widen the discovery window
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODEL = "claude-sonnet-4-6"
BATCH = 50
FOREIGN_UNIVERSE = PROJECT_ROOT / "data" / "foreign" / "universe.parquet"

PROMPT = """You are classifying the UNDERLIER of leveraged/inverse (L&I) ETFs that issuers have filed with the SEC. Each input is the ETF's series name; identify the single underlying company/asset it tracks.

For each, return exactly ONE category:
- foreign  = a single STOCK whose PRIMARY listing is OUTSIDE the United States and has NO liquid US ADR (e.g. SK Square=Korea, Kioxia=Japan, Siemens=Germany, BE Semiconductor/Besi=Netherlands, Sivers Semiconductors=Sweden, Hanmi Semiconductor=Korea, MiniMax=China). If the company's main liquidity IS a US ADR/listing (TSM, BABA, ASML US, ARM, Ferrari/RACE), use us_stock.
- us_stock  = a single stock primarily US-listed (incl. US-listed ADRs).
- pre_ipo   = a genuinely PRIVATE company not yet public (OpenAI, SpaceX, Anthropic, Databricks, Stripe, Discord, Dataiku, Strava, Quantinuum, Anduril, Figure AI, Scale AI).
- crypto    = a crypto token (Bitcoin, Cardano, Chainlink, Stellar, ...).
- basket    = an index/theme/multi-name basket, NOT a single name ("AI Computing Power", "All World", "US Large Cap", "Korea AI Semiconductor", "Photonics", "Lithography").
- other     = unrecognizable / cannot determine.

For category=foreign ONLY, also give: country, exchange (KRX/TSE/HKEX/XETRA/AMS/STO/TWSE/...), local_ticker (with suffix e.g. 402340.KS, 285A.T), us_adr_ticker (or ""), sector, market_cap_usd_b (number, approximate, 0 if unknown), and keywords (1-3 UPPERCASE substrings that identify this underlier inside a fund name, e.g. ["SK SQUARE","SKSQUARE"]).
For category=pre_ipo, also give company and (if known) valuation_usd_b (number or null).

Return STRICT JSON array, one object per input, each: {"series_name","company","category","country","exchange","local_ticker","us_adr_ticker","sector","market_cap_usd_b","valuation_usd_b","keywords"}. Use "" / null / [] where a field doesn't apply."""


def _already_seen() -> set[str]:
    seen = set()
    for j in (PROJECT_ROOT / "logs").glob("ai_underlier_*.jsonl"):
        for line in j.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["series_name"])
            except Exception:
                pass
    return seen


def _candidates(days: int):
    """Distinct competitor L&I series filed in the window whose underlier isn't an
    obvious US ticker and isn't already covered by the foreign universe keywords."""
    from screener.li_engine.analysis import trex_combined_v9 as T
    from screener.li_engine.analysis.foreign_filings import load_foreign_universe
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    df = T._df("""SELECT f.filing_date, f.registrant, fe.series_name
                  FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id
                  WHERE f.form='485APOS' AND f.filing_date>=?
                    AND f.registrant NOT LIKE '%REX%'
                    AND f.registrant NOT LIKE '%ETF Opportunities%'""", (cutoff,))
    if df.empty:
        return []
    # Use the broad L&I *name* detector, not is_li_product(): the strict regex
    # requires a trailing ETF/ETN token, so bare competitor series like
    # "ProShares Ultra Dongshan Precision" (single-stock foreign filings, no
    # ETF suffix in the series name) were silently dropped before reaching the
    # AI classifier. _LI_NAME_RE matches bare Ultra/UltraPro/leveraged/inverse.
    df = df[df["series_name"].fillna("").apply(lambda s: bool(T._LI_NAME_RE.search(s)))]
    df = df[~df["series_name"].fillna("").apply(T.is_high_leverage)]
    df = df.drop_duplicates("series_name")
    try:
        us = set(T._df("SELECT ticker FROM mkt_stock_data")["ticker"].dropna().apply(T._canon))
    except Exception:
        us = set()
    covered = set()
    for kws in load_foreign_universe()["name_keywords"]:
        # name_keywords is a numpy array when read from the parquet — no truthiness.
        for k in (list(kws) if kws is not None and not isinstance(kws, float) else []):
            covered.add(re.sub(r"[^A-Z0-9]", "", str(k).upper()))
    df["tok"] = df["series_name"].apply(T._comp_underlier_of)

    def is_covered(nm):
        u = re.sub(r"[^A-Z0-9]", "", str(nm).upper())
        return any(c and c in u for c in covered)

    seen = _already_seen()
    out = []
    for _, r in df.iterrows():
        sn = r["series_name"]
        if sn in seen or (r["tok"] and r["tok"] in us) or is_covered(sn):
            continue
        out.append(sn)
    return out


def _classify_batch(client, names):
    msg = client.messages.create(
        model=MODEL, max_tokens=8000,
        messages=[{"role": "user", "content": PROMPT + "\n\nSERIES:\n" + json.dumps(names)}],
    )
    txt = msg.content[0].text
    s, e = txt.find("["), txt.rfind("]")
    return json.loads(txt[s:e + 1]) if s >= 0 and e > s else []


def _merge_universe(foreign_rows, dry: bool):
    """Merge AI-found foreign rows into data/foreign/universe.parquet = seed ∪ AI."""
    import pandas as pd
    from screener.li_engine.analysis.foreign_filings import SEED_FOREIGN_UNIVERSE
    base = [{"foreign_ticker": t, "name": n, "market": m, "sector": s,
             "market_cap_usd": cap * 1e9, "name_keywords": kws}
            for (t, n, m, s, cap, kws) in SEED_FOREIGN_UNIVERSE if kws]
    if FOREIGN_UNIVERSE.exists():
        try:
            ex = pd.read_parquet(FOREIGN_UNIVERSE).to_dict("records")
            base = ex + base
        except Exception:
            pass
    have = {str(r.get("foreign_ticker", "")).upper() for r in base}
    have_kw = set()
    for r in base:
        _bk = r.get("name_keywords")
        for k in (list(_bk) if _bk is not None and not isinstance(_bk, float) else []):
            have_kw.add(re.sub(r"[^A-Z0-9]", "", str(k).upper()))
    added = []
    for r in foreign_rows:
        tk = str(r.get("local_ticker") or r.get("us_adr_ticker") or r.get("company") or "").upper()
        kws = [str(k).upper() for k in (r.get("keywords") or []) if k]
        kwcanon = {re.sub(r"[^A-Z0-9]", "", k) for k in kws}
        if not tk or tk in have or (kwcanon & have_kw):
            continue
        cap = r.get("market_cap_usd_b") or 0
        try:
            cap = float(cap) * 1e9
        except Exception:
            cap = 0.0
        base.append({"foreign_ticker": tk, "name": r.get("company") or tk,
                     "market": r.get("exchange") or "", "sector": r.get("sector") or "",
                     "market_cap_usd": cap, "name_keywords": kws or [r.get("company", "").upper()]})
        have.add(tk)
        added.append(r.get("company") or tk)
    if not dry:
        FOREIGN_UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(base).to_parquet(FOREIGN_UNIVERSE, index=False)
    return base, added


def main() -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=180)
    args = ap.parse_args()

    names = _candidates(args.days)
    if not names:
        print("No new competitor-filed underliers to classify.")
        return 0
    print(f"AI classifying {len(names)} new competitor-filed underliers (window {args.days}d)...")

    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"LLM unavailable ({e}); skipping.", file=sys.stderr)
        return 0

    results = []
    for i in range(0, len(names), BATCH):
        try:
            results.extend(_classify_batch(client, names[i:i + BATCH]))
        except Exception as e:
            print(f"  batch {i} failed: {e}", file=sys.stderr)

    logs = PROJECT_ROOT / "logs"
    logs.mkdir(exist_ok=True)
    jn = logs / f"ai_underlier_{date.today().isoformat().replace('-', '')}.jsonl"
    with jn.open("a", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    by_cat = {}
    for r in results:
        by_cat[r.get("category", "?")] = by_cat.get(r.get("category", "?"), 0) + 1
    foreign = [r for r in results if r.get("category") == "foreign"]
    pre_ipo = [r for r in results if r.get("category") == "pre_ipo"]
    print(f"  classified {len(results)} | {by_cat}")
    print(f"  foreign={len(foreign)} pre_ipo={len(pre_ipo)} -> journal {jn.name}")

    base, added = _merge_universe(foreign, args.dry_run)
    print(f"  foreign universe now {len(base)} names; "
          f"{'(dry-run, not written) ' if args.dry_run else ''}added {len(added)}: {added}")
    if pre_ipo:
        print(f"  pre-IPO candidates for the watchlist sourcer: "
              f"{[r.get('company') for r in pre_ipo]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

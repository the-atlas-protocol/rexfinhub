"""Export every L&I filing (REX + competitors, ACTUAL fund names) per foreign
underlier, from live fund_status (scraped today) + rex_products. -> JSON for local
assembly with market caps + ISG classification.
Reuses trex_combined_v9's collision-safe matchers so results match the daily report.
"""
import json, re
import pandas as pd
from screener.li_engine.analysis import trex_combined_v9 as t
from screener.li_engine.analysis.foreign_filings import load_foreign_universe

uni = load_foreign_universe()
rexp, fs = t._load_race_sources()

LEV_RE = re.compile(r"(\d(?:\.\d)?)\s*X", re.I)
def lev_dir(name):
    s = str(name).upper()
    direction = "Inverse" if re.search(r"\b(INVERSE|SHORT|BEAR|ULTRASHORT)\b", s) else "Long"
    m = LEV_RE.search(s)
    if m:
        lev = m.group(1) + "x"
    elif "ULTRAPRO" in s:
        lev = "3x"
    elif "ULTRA" in s:
        lev = "2x"
    else:
        lev = "?"
    return lev, direction

def keys_of(kw):
    lst = list(kw) if kw is not None and not isinstance(kw, float) else []
    out = [re.sub(r"[^A-Z0-9]+", " ", str(k).upper()).strip() for k in lst if k]
    return [k for k in out if k]

rows = []
for _, r in uni.iterrows():
    market = str(r.get("market", "") or "")
    if market.upper() in ("NYSE", "NASDAQ", "NYSE ARCA", "AMEX"):
        continue  # US-ADR listed -> a US 2x is already possible; not foreign whitespace
    keys = keys_of(r.get("name_keywords"))
    if not keys:
        continue
    rex_funds, comp_funds = [], []
    seen_rex, seen_comp = set(), set()
    # REX (T-REX) filings
    for nm, st, init_fd, est_eff, listed in rexp.itertuples(index=False):
        if not t._name_match(keys, nm):
            continue
        nm = str(nm)
        if nm in seen_rex:
            continue
        seen_rex.add(nm)
        lev, d = lev_dir(nm)
        rex_funds.append({"fund_name": nm, "issuer": "REX (T-REX)", "dir": d, "lev": lev,
                          "status": t._race_collapse(st), "filed": t._race_eff(init_fd),
                          "date": t._race_eff(listed, est_eff)})
    # Competitor filings (fund_status, live today)
    for nm, st, eff, filed in fs.itertuples(index=False):
        nm = str(nm)
        if not t._LI_NAME_RE.search(nm) or t._REX_NAME_RE.search(nm):
            continue
        if not t._name_match(keys, nm):
            continue
        if nm in seen_comp:
            continue
        iss = t._comp_issuer_of(nm)
        if not iss:
            continue
        seen_comp.add(nm)
        lev, d = lev_dir(nm)
        comp_funds.append({"fund_name": nm, "issuer": iss, "dir": d, "lev": lev,
                           "status": t._race_collapse(st), "filed": t._race_eff(filed),
                           "date": t._race_eff(eff)})
    if not rex_funds and not comp_funds:
        # keep as not-filed whitespace candidate (Table 1 only)
        rows.append({"name": str(r.get("name", "")), "market": market,
                     "cap_parquet": float(r.get("market_cap_usd", 0) or 0),
                     "sector": str(r.get("sector", "") or ""),
                     "rex_funds": [], "comp_funds": []})
        continue
    rows.append({"name": str(r.get("name", "")), "market": market,
                 "cap_parquet": float(r.get("market_cap_usd", 0) or 0),
                 "sector": str(r.get("sector", "") or ""),
                 "rex_funds": rex_funds, "comp_funds": comp_funds})

out = "/home/jarvis/rexfinhub/outputs/foreign_filings_export.json"
json.dump({"rows": rows, "n_filed": sum(1 for r in rows if r["rex_funds"] or r["comp_funds"])},
          open(out, "w"), indent=1)
print("WROTE", out)
print("underliers:", len(rows),
      "| with >=1 filing:", sum(1 for r in rows if r["rex_funds"] or r["comp_funds"]),
      "| REX-filed:", sum(1 for r in rows if r["rex_funds"]))
# quick peek
for r in rows:
    if r["rex_funds"]:
        print(" REX", r["name"], "->", [f["fund_name"][:42] for f in r["rex_funds"]])

"""Race explicit FOREIGN-LISTED underliers (REX foreign 2x filings) against
rex_products + live fund_status -> actual fund names + EXACT filing dates +
issuer-convention leverage. Authoritative REX side.
"""
import json, re
from screener.li_engine.analysis import trex_combined_v9 as t

rexp, fs = t._load_race_sources()   # rexp: name,status,initial_filing_date,est_eff,listed ; fs: fund_name,status,effective_date,latest_filing_date

def lev_dir(name):
    s = str(name).upper()
    direction = "Inverse" if re.search(r"\b(INVERSE|SHORT|BEAR|ULTRASHORT)\b", s) else "Long"
    m = re.search(r"(\d(?:\.\d)?)\s*X", s)
    if m:
        lev = m.group(1) + "x"
    elif "ULTRAPRO" in s:            # ProShares UltraPro / UltraPro Short = 3x
        lev = "3x"
    elif "ULTRA" in s:               # ProShares Ultra / UltraShort = 2x
        lev = "2x"
    else:
        lev = "?"
    return lev, direction

TARGETS = [
    ("Samsung Electronics", "South Korea", "KRX (Korea Exchange)", "Foreign-listed", ["SAMSUNG"]),
    ("SK Hynix", "South Korea", "KRX (Korea Exchange)", "Foreign-listed", ["SK HYNIX", "HYNIX", "SKHY"]),
    ("Hyundai Motor", "South Korea", "KRX (Korea Exchange)", "Foreign-listed", ["HYUNDAI"]),
    ("Hanwha Aerospace", "South Korea", "KRX (Korea Exchange)", "Foreign-listed", ["HANWHA AEROSPACE", "HANWHA"]),
    ("Kioxia Holdings", "Japan", "Tokyo SE (JPX)", "Foreign-listed", ["KIOXIA"]),
    ("SoftBank Group", "Japan", "Tokyo SE (JPX)", "Foreign-listed", ["SOFTBANK"]),
    ("Nintendo", "Japan", "Tokyo SE (JPX)", "Foreign-listed", ["NINTENDO"]),
    ("Metaplanet", "Japan", "Tokyo SE (JPX)", "Foreign-listed", ["METAPLANET"]),
    ("Sivers Semiconductors", "Sweden", "Nasdaq Stockholm", "Foreign-listed", ["SIVERS", "SIVE"]),
    ("BYD Company", "China / Hong Kong", "HKEX + Shenzhen", "US OTC ADR", ["BYD", "BYDDY"]),
    ("Lynas Rare Earths", "Australia", "ASX (Australia)", "US OTC ADR", ["LYNAS", "LYSDY"]),
]

def keys_norm(kw):
    return [re.sub(r"[^A-Z0-9]+", " ", k.upper()).strip() for k in kw]

out = []
for disp, country, exch, listing, kw in TARGETS:
    keys = keys_norm(kw)
    rex_f, comp_f = [], []
    sr, sc = set(), set()
    for nm, st, init_fd, est_eff, listed in rexp.itertuples(index=False):
        if not t._name_match(keys, nm):
            continue
        nm = str(nm)
        if "ELECTRO" in nm.upper():   # Samsung Electro-Mechanics is a different company
            continue
        if nm in sr:
            continue
        sr.add(nm)
        lev, d = lev_dir(nm)
        rex_f.append({"fund": nm, "issuer": "REX (T-REX)", "dir": d, "lev": lev,
                      "status": t._race_collapse(st), "filed": t._race_eff(init_fd),
                      "eff": t._race_eff(listed, est_eff)})
    for nm, st, eff, filed in fs.itertuples(index=False):
        nm = str(nm)
        if not t._LI_NAME_RE.search(nm) or t._REX_NAME_RE.search(nm):
            continue
        if not t._name_match(keys, nm):
            continue
        if "ELECTRO" in nm.upper():
            continue
        if nm in sc:
            continue
        iss = t._comp_issuer_of(nm)
        if not iss:
            continue
        sc.add(nm)
        lev, d = lev_dir(nm)
        comp_f.append({"fund": nm, "issuer": iss, "dir": d, "lev": lev,
                       "status": t._race_collapse(st), "filed": t._race_eff(filed),
                       "eff": t._race_eff(eff)})
    out.append({"company": disp, "country": country, "exchange": exch, "listing": listing,
                "rex": rex_f, "comp": comp_f})

json.dump(out, open("/home/jarvis/rexfinhub/outputs/foreign_race_v2.json", "w"), indent=1)
print("WROTE foreign_race_v2.json")
for r in out:
    print(f"\n{r['company']} ({r['exchange']})")
    for f in r["rex"] + r["comp"]:
        tag = "REX " if f["issuer"].startswith("REX") else "COMP"
        print(f"   {tag} {f['lev']:3s} {f['dir']:7s} {f['status']:9s} filed={f['filed'] or '—':10s} {f['issuer'][:14]:14s} {f['fund'][:46]}")

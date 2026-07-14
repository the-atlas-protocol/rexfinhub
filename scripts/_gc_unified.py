"""ABSOLUTE foreign 2x filings extractor. Every REX + competitor L&I filing on a
foreign-listed underlier, assigned by MOST-SPECIFIC keyword (longest match wins) so
Samsung Electronics != Samsung Electro-Mechanics, Siemens != Siemens Energy, etc.
Exact filing dates + issuer-convention leverage. Live fund_status (scraped today).
"""
import json, re
from screener.li_engine.analysis import trex_combined_v9 as t

rexp, fs = t._load_race_sources()

# disp, country, exchange, listing, isg, region, [keywords]
U = [
 # --- REX-filed foreign-listed ---
 ("Samsung Electronics","South Korea","KRX (Korea Exchange)","Foreign-listed","Yes","Asia",["SAMSUNG"]),
 ("SK Hynix","South Korea","KRX (Korea Exchange)","Foreign-listed","Yes","Asia",["SK HYNIX","HYNIX","SKHY"]),
 ("Hyundai Motor","South Korea","KRX (Korea Exchange)","Foreign-listed","Yes","Asia",["HYUNDAI"]),
 ("Hanwha Aerospace","South Korea","KRX (Korea Exchange)","Foreign-listed","Yes","Asia",["HANWHA AEROSPACE"]),
 ("Kioxia Holdings","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["KIOXIA"]),
 ("SoftBank Group","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["SOFTBANK"]),
 ("Nintendo","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["NINTENDO"]),
 ("Metaplanet","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["METAPLANET"]),
 ("Sivers Semiconductors","Sweden","Nasdaq Stockholm","Foreign-listed","Yes","Europe",["SIVERS","SIVE"]),
 ("BYD Company","China / Hong Kong","HKEX + Shenzhen","US OTC ADR","Yes","Asia",["BYD","BYDDY"]),
 ("Lynas Rare Earths","Australia","ASX (Australia)","US OTC ADR","Yes","Asia-Pacific",["LYNAS","LYSDY"]),
 # --- affiliate split-outs (more specific than the parent) ---
 ("Samsung Electro-Mechanics","South Korea","KRX (Korea Exchange)","Foreign-listed","Yes","Asia",["SAMSUNG ELECTRO-MECHANICS","ELECTRO-MECHANICS","ELECTRO MECHANICS"]),
 # --- competitor-only ISG names ---
 ("ASML Holding","Netherlands","Euronext Amsterdam","Foreign-listed","Yes","Europe",["ASML"]),
 ("Tencent","Hong Kong","HKEX (Hong Kong)","Foreign-listed","Yes","Asia",["TENCENT"]),
 ("Roche Holding","Switzerland","SIX Swiss","Foreign-listed","Yes","Europe",["ROCHE"]),
 ("Novartis","Switzerland","SIX Swiss","Foreign-listed","Yes","Europe",["NOVARTIS"]),
 ("Siemens AG","Germany","Deutsche Börse (Xetra)","Foreign-listed","Yes","Europe",["SIEMENS"]),
 ("Siemens Energy","Germany","Deutsche Börse (Frankfurt)","Foreign-listed","Yes","Europe",["SIEMENS ENERGY"]),
 ("Tokyo Electron","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["TOKYO ELECTRON"]),
 ("MediaTek","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["MEDIATEK"]),
 ("ABB","Switzerland","SIX Swiss","Foreign-listed","Yes","Europe",["ABB"]),
 ("Schneider Electric","France","Euronext Paris","Foreign-listed","Yes","Europe",["SCHNEIDER"]),
 ("Delta Electronics","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["DELTA ELECTRONICS"]),
 ("Advantest","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["ADVANTEST"]),
 ("SK Square","South Korea","KRX (Korea Exchange)","Foreign-listed","Yes","Asia",["SK SQUARE"]),
 ("Sony Group","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["SONY"]),
 ("Infineon","Germany","Deutsche Börse (Xetra)","Foreign-listed","Yes","Europe",["INFINEON"]),
 ("Mitsubishi Corporation","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["MITSUBISHI CORP"]),
 ("Mitsubishi Heavy Industries","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["MITSUBISHI HEAVY"]),
 ("Xiaomi","Hong Kong","HKEX (Hong Kong)","Foreign-listed","Yes","Asia",["XIAOMI"]),
 ("Fujikura","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["FUJIKURA"]),
 ("Disco Corporation","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["DISCO"]),
 ("Quanta Computer","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["QUANTA"]),
 ("BE Semiconductor (BESI)","Netherlands","Euronext Amsterdam","Foreign-listed","Yes","Europe",["BE SEMICONDUCTOR","BESI"]),
 ("Hanmi Semiconductor","South Korea","KRX (Korea Exchange)","Foreign-listed","Yes","Asia",["HANMI"]),
 ("Lasertec","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["LASERTEC"]),
 ("Accton Technology","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["ACCTON"]),
 ("IBIDEN","Japan","Tokyo SE (JPX)","Foreign-listed","Yes","Asia",["IBIDEN"]),
 ("Alchip Technologies","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["ALCHIP"]),
 ("Nanya Technology","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["NANYA"]),
 ("Leeno Industrial","South Korea","KOSDAQ (Korea)","Foreign-listed","Yes","Asia",["LEENO"]),
 ("Unimicron Technology","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["UNIMICRON"]),
 ("Global Unichip","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["GLOBAL UNICHIP","UNICHIP"]),
 ("Winbond Electronics","Taiwan","Taiwan SE (TWSE)","Foreign-listed","Yes","Asia",["WINBOND"]),
 # --- EXCLUDED: mainland China (NOT ISG) ---
 ("CATL (Contemporary Amperex)","China","Shenzhen SE","Foreign-listed","No","China-mainland",["CATL","CONTEMPORARY AMPEREX"]),
 ("Foxconn Industrial Internet","China","Shanghai SE","Foreign-listed","No","China-mainland",["FOXCONN INDUSTRIAL"]),
 ("Zhongji Innolight","China","Shenzhen SE","Foreign-listed","No","China-mainland",["ZHONGJI","INNOLIGHT"]),
 ("Luxshare Precision","China","Shenzhen SE","Foreign-listed","No","China-mainland",["LUXSHARE"]),
 ("Hygon Information Tech","China","Shanghai SE","Foreign-listed","No","China-mainland",["HYGON"]),
 ("Dongshan Precision","China","Shenzhen SE","Foreign-listed","No","China-mainland",["DONGSHAN"]),
 ("Montage Technology","China","Shanghai SE","Foreign-listed","No","China-mainland",["MONTAGE"]),
 ("Wuhan Huagong (HGTech)","China","Shenzhen SE","Foreign-listed","No","China-mainland",["WUHAN HUAGONG","HGTECH"]),
 ("Victory Giant Technology","China","Shenzhen SE","Foreign-listed","No","China-mainland",["VICTORY GIANT"]),
 ("BIWIN Storage","China","Shenzhen SE","Foreign-listed","No","China-mainland",["BIWIN"]),
 # --- EXCLUDED: Middle East (Tadawul not a confirmed ISG member) ---
 ("Saudi Aramco","Saudi Arabia","Saudi Exchange (Tadawul)","Foreign-listed","No","Middle East",["ARAMCO"]),
 # --- EXCLUDED: Americas ---
 ("Kraken Robotics","Canada","TSX Venture","Foreign-listed","No","Americas",["KRAKEN ROBOTICS","KRAKEN"]),
]

def norm(s):
    return " " + re.sub(r"[^A-Z0-9]+", " ", str(s).upper()).strip() + " "

def lev_dir(name):
    s = str(name).upper()
    direction = "Inverse" if re.search(r"\b(INVERSE|SHORT|BEAR|ULTRASHORT)\b", s) else "Long"
    m = re.search(r"(\d(?:\.\d)?)\s*X", s)
    if m: lev = m.group(1) + "x"
    elif "ULTRAPRO" in s: lev = "3x"
    elif "ULTRA" in s: lev = "2x"
    else: lev = "?"
    return lev, direction

# pre-norm keywords
for u in U:
    u_keys = [(" " + re.sub(r"[^A-Z0-9]+", " ", k.upper()).strip() + " ") for k in u[6]]
    u_dict = {"disp": u[0], "country": u[1], "exch": u[2], "listing": u[3], "isg": u[4],
              "region": u[5], "nkeys": u_keys, "rex": [], "comp": []}
    U[U.index(u)] = u_dict

def best_underlier(name):
    nm = norm(name)
    best, blen = None, 0
    for u in U:
        for k in u["nkeys"]:
            if k in nm and len(k) > blen:
                best, blen = u, len(k)
    return best

seen = set()
# REX
for nm, st, init_fd, est_eff, listed in rexp.itertuples(index=False):
    nm = str(nm)
    if not t._LI_NAME_RE.search(nm):
        continue
    u = best_underlier(nm)
    if not u or nm in seen:
        continue
    seen.add(nm)
    lev, d = lev_dir(nm)
    u["rex"].append({"fund": nm, "issuer": "REX (T-REX)", "dir": d, "lev": lev,
                     "status": t._race_collapse(st), "filed": t._race_eff(init_fd),
                     "eff": t._race_eff(listed, est_eff)})
# Competitors
for nm, st, eff, filed in fs.itertuples(index=False):
    nm = str(nm)
    if not t._LI_NAME_RE.search(nm) or t._REX_NAME_RE.search(nm):
        continue
    u = best_underlier(nm)
    if not u or nm in seen:
        continue
    iss = t._comp_issuer_of(nm)
    if not iss:
        continue
    seen.add(nm)
    lev, d = lev_dir(nm)
    u["comp"].append({"fund": nm, "issuer": iss, "dir": d, "lev": lev,
                      "status": t._race_collapse(st), "filed": t._race_eff(filed),
                      "eff": t._race_eff(eff)})

out = [{k: u[k] for k in ("disp","country","exch","listing","isg","region","rex","comp")} for u in U]
json.dump(out, open("/home/jarvis/rexfinhub/outputs/gc_unified.json", "w"), indent=1)
print("WROTE gc_unified.json |", len(out), "underliers")
for u in out:
    n = len(u["rex"]) + len(u["comp"])
    if n:
        print(f"\n{u['disp']} [{u['isg']}/{u['region']}] {u['exch']} — {len(u['rex'])} REX + {len(u['comp'])} comp")
        for f in u["rex"] + u["comp"]:
            tag = "REX " if f["issuer"].startswith("REX") else "COMP"
            print(f"   {tag} {f['lev']:3s} {f['dir']:7s} {f['status']:9s} filed={f['filed'] or '—':10s} {f['issuer'][:14]:14s} {f['fund'][:50]}")

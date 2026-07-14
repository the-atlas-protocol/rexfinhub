from screener.li_engine.analysis import trex_combined_v9 as t
ss = t._single_stock_set()
ig = t.load_inverse_gap(ss)
print("CORRECTED inverse-gap (true open seats):", len(ig))
for _, r in ig.iterrows():
    print(f"  {r['underlier']:6} top long ${r['top_aum']:,.0f}M  {str(r['top_issuer'])[:20]}  ({r['top_fund'][:36]})")
# Show what dropped + why (the registered_inv map), by replaying the exclusion
import re
fs = t._df("SELECT fund_name,status FROM fund_status WHERE fund_name IS NOT NULL")
_u = re.compile(r"(?:SHORT|INVERSE|ULTRASHORT)\s+([A-Z]{1,6})\b|\b([A-Z]{1,6})\s+(?:BEAR|SHORT)\b")
reg = {}
for nm, st in fs.itertuples(index=False):
    s = str(nm)
    if not t._INV_RE.search(s) or t._REX_NAME_RE.search(s): continue
    for m in _u.finditer(s.upper()):
        cu = t._canon(m.group(1) or m.group(2))
        if cu in ss and cu not in reg: reg[cu] = (s, str(st))
print("\nDROPPED (registered inverse already exists) — sample of the 12:")
for u in ["MRVL","INTC","AAOI","WDC","DELL","CRDO","HIMS","COHR","AXTI","ARM","UNH","APP"]:
    if u in reg: print(f"  {u:6} -> {reg[u][0][:46]:46} [{reg[u][1]}]")

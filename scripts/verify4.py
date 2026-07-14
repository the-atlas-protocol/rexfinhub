from screener.li_engine.analysis import trex_combined_v9 as t
from screener.li_engine.analysis.foreign_filings import load_foreign_universe
uni = load_foreign_universe()
rexp, fs = t._load_race_sources()
import re
for target in ["Hygon","Quanta","Cambricon","Accton","Winbond"]:
    row = uni[uni['name'].str.contains(target, case=False, na=False)]
    if row.empty: 
        print(f"{target}: not in universe"); continue
    r = row.iloc[0]
    kws = list(r.get('name_keywords') or [])
    keys = [re.sub(r"[^A-Z0-9]+"," ",str(k).upper()).strip() for k in kws if k]
    print(f"\n{target}: keywords={keys}")
    # which fund_status names match?
    for nm, st, eff, fd in fs.itertuples(index=False):
        if not t._LI_NAME_RE.search(str(nm)) or t._REX_NAME_RE.search(str(nm)): continue
        if t._name_match(keys, nm):
            print(f"    MATCHED: {nm}  [{st}] issuer={t._comp_issuer_of(nm)}")

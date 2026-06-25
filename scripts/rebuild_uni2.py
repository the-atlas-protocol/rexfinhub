import pandas as pd, re, json
from pathlib import Path
from screener.li_engine.analysis.foreign_filings import SEED_FOREIGN_UNIVERSE, FOREIGN_UNIVERSE
def canon(n): return re.sub(r'[^A-Z0-9]','', str(n).upper())
SUFFIX = r'\b(INC|CORP|CORPORATION|LIMITED|LTD|HOLDINGS|HOLDING|GROUP|COMPANY|CO|SA|AG|NV|PLC|SE|AB|ADR|THE|LP|LLC)\b'
def derive_kw(name):
    n = re.sub(SUFFIX,'',str(name).upper())
    n = re.sub(r'[^A-Z0-9 ]',' ',n); n = re.sub(r'\s+',' ',n).strip()
    return [n] if len(n)>=3 else [str(name).upper()]

seed_kw = {canon(n): kws for (t,n,m,s,cap,kws) in SEED_FOREIGN_UNIVERSE if kws}
data = json.load(open('/home/jarvis/rexfinhub/data/foreign/noadr.json'))
all_status = {canon(k): v for k,v in data['all_status'].items()}

rows, seen = [], set()
# 1) authoritative no-ADR names with REAL caps
for r in data['noadr']:
    cn = canon(r['name'])
    if cn in seen: continue
    seen.add(cn)
    rows.append({"foreign_ticker": r.get('cmc_ticker') or r['name'],
                 "name": r['name'], "market": r.get('home_exchange') or r['country'],
                 "sector": "", "market_cap_usd": float(r['market_cap'] or 0),
                 "name_keywords": seed_kw.get(cn) or derive_kw(r['name'])})
# 2) keep small foreign raced names from the prior universe that have NO US access
bak = Path(str(FOREIGN_UNIVERSE)+".bak")
old = pd.read_parquet(bak) if bak.exists() else pd.read_parquet(FOREIGN_UNIVERSE)
for _, r in old.iterrows():
    cn = canon(r['name'])
    if cn in seen: continue
    st = all_status.get(cn)
    if st in ('DIRECT_US','ADR_LISTED'):   # has real US access -> not whitespace
        continue
    seen.add(cn)
    kws = list(r['name_keywords']) if r['name_keywords'] is not None else derive_kw(r['name'])
    rows.append({"foreign_ticker": r['foreign_ticker'], "name": r['name'],
                 "market": r['market'], "sector": r.get('sector',''),
                 "market_cap_usd": float(r['market_cap_usd'] or 0), "name_keywords": kws})

out = pd.DataFrame(rows).sort_values("market_cap_usd", ascending=False)
out.to_parquet(FOREIGN_UNIVERSE, compression="snappy")
print(f"noadr={len(data['noadr'])} +old_kept={len(out)-len(data['noadr'])} -> universe={len(out)}")
for _,r in out.head(8).iterrows(): print(f"  {r['name'][:28]:28} ${r['market_cap_usd']/1e9:,.0f}B {r['market']}")

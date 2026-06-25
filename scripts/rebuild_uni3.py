import pandas as pd, re, json
from pathlib import Path
from screener.li_engine.analysis.foreign_filings import FOREIGN_UNIVERSE
def canon(n): return re.sub(r'[^A-Z0-9]','', str(n).upper())
SUF = r'\b(INC|CORP|CORPORATION|LIMITED|LTD|HOLDINGS|HOLDING|GROUP|COMPANY|CO|SA|AG|NV|PLC|SE|AB|ADR|THE|LP|LLC)\b'
def strip_core(name):
    n=re.sub(SUF,'',str(name).upper()); return canon(n)
def derive_kw(name):
    n=re.sub(SUF,'',str(name).upper()); n=re.sub(r'[^A-Z0-9 ]',' ',n); n=re.sub(r'\s+',' ',n).strip()
    return [n] if len(n)>=3 else [str(name).upper()]

data=json.load(open('/home/jarvis/rexfinhub/data/foreign/noadr.json'))
usaccess={canon(k) for k,v in data['all_status'].items() if v in ('DIRECT_US','ADR_LISTED')}

rows, core_index = [], set()
def add(ft,name,market,sector,cap,kws):
    rows.append({"foreign_ticker":ft,"name":name,"market":market,"sector":sector,
                 "market_cap_usd":float(cap or 0),"name_keywords":kws})
    core_index.add(strip_core(name))
    for k in kws: core_index.add(canon(k))

for r in data['noadr']:                       # 1) authoritative, real caps
    if strip_core(r['name']) in core_index: continue
    add(r.get('cmc_ticker') or r['name'], r['name'], r.get('home_exchange') or r['country'],
        "", r['market_cap'], derive_kw(r['name']))

bak=Path(str(FOREIGN_UNIVERSE)+".bak")          # 2) small raced foreign, no US access
old=pd.read_parquet(bak)
for _,r in old.iterrows():
    nm=str(r['name'])
    if ' ADR' in nm.upper(): continue
    kws=list(r['name_keywords']) if r['name_keywords'] is not None else derive_kw(nm)
    keys={strip_core(nm)} | {canon(k) for k in kws}
    if keys & core_index: continue            # dup of a noadr company
    if canon(nm) in usaccess or strip_core(nm) in usaccess: continue
    add(r['foreign_ticker'], nm, r['market'], r.get('sector',''), r['market_cap_usd'], kws)

out=pd.DataFrame(rows).sort_values("market_cap_usd",ascending=False)
out.to_parquet(FOREIGN_UNIVERSE,compression="snappy")
print(f"universe={len(out)} (noadr-based + raced small-caps)")
# dup check by stripped core
import collections
dup=[k for k,c in collections.Counter(strip_core(n) for n in out['name']).items() if c>1]
print("residual dup cores:",dup[:10] or "none")
for _,r in out.head(10).iterrows(): print(f"  {r['name'][:30]:30} ${r['market_cap_usd']/1e9:,.0f}B {r['market']}")

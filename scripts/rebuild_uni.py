import pandas as pd, re
from pathlib import Path
from screener.li_engine.analysis.foreign_filings import SEED_FOREIGN_UNIVERSE, FOREIGN_UNIVERSE
def canon(n): return re.sub(r'[^A-Z0-9]','', str(n).upper())
seed = pd.DataFrame([{"foreign_ticker":t,"name":n,"market":m,"sector":s,
                      "market_cap_usd":cap*1e9,"name_keywords":kws}
                     for (t,n,m,s,cap,kws) in SEED_FOREIGN_UNIVERSE if kws])
bak = Path(str(FOREIGN_UNIVERSE)+".bak")
old = pd.read_parquet(bak) if bak.exists() else (pd.read_parquet(FOREIGN_UNIVERSE) if Path(FOREIGN_UNIVERSE).exists() else pd.DataFrame())
if not old.empty:
    seed_names = {canon(n) for n in seed["name"]}
    seed_tk = {str(t) for t in seed["foreign_ticker"]}
    keep_old = old[~(old["name"].map(canon).isin(seed_names) | old["foreign_ticker"].astype(str).isin(seed_tk))]
    out = pd.concat([seed, keep_old], ignore_index=True)
else:
    out = seed
out["_c"] = out["name"].map(canon)
out = out.drop_duplicates(subset="foreign_ticker").drop_duplicates(subset="_c").drop(columns="_c")
out.to_parquet(FOREIGN_UNIVERSE, compression="snappy")
print(f"seed={len(seed)} old={len(old)} -> universe={len(out)}")
for _,r in out.sort_values("market_cap_usd",ascending=False).head(6).iterrows():
    print(f"  {r['name'][:28]:28} {r['market_cap_usd']/1e9:,.0f}B {r['market']}")

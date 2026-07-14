import sqlite3, re, json
from screener.li_engine.analysis import trex_combined_v9 as t
con = sqlite3.connect("/home/jarvis/rexfinhub/data/etp_tracker.db")
c = con.cursor()

print("=== 1. rex_products status values + date columns ===")
for st,n in c.execute("SELECT COALESCE(status_cached,status),COUNT(*) FROM rex_products WHERE product_suite='T-REX' GROUP BY 1 ORDER BY 2 DESC").fetchall():
    print(f"  {str(st):20} {n}")
print("  -- sample rows (name/status/filing/est_eff/listed):")
for nm,st,fd,ee,ld in c.execute("""SELECT name,COALESCE(status_cached,status),initial_filing_date,estimated_effective_date,official_listed_date
    FROM rex_products WHERE product_suite='T-REX' AND (name LIKE '%SK HYNIX%' OR name LIKE '%SAMSUNG%' OR name LIKE '%OPENAI%' OR name LIKE '%NOW%' OR name LIKE '%GLW%') LIMIT 8""").fetchall():
    print(f"    {nm[:42]:42} | {str(st):10} | filed={fd} eff={ee} listed={ld}")

print("\n=== 2. DISCO — what filing matched? (keyword-collision check) ===")
for fn,st in c.execute("SELECT fund_name,status FROM fund_status WHERE UPPER(fund_name) LIKE '%DISCO%'").fetchall():
    print(f"    fund_status: {fn[:55]:55} [{st}]")
# what keywords does the foreign universe carry for DISCO?
from screener.li_engine.analysis.foreign_filings import load_foreign_universe
uni = load_foreign_universe()
print("  foreign universe cols:", list(uni.columns))
print("  universe size:", len(uni))
d = uni[uni['name'].astype(str).str.upper().str.contains('DISCO')]
for _,r in d.iterrows():
    print(f"    DISCO row: name={r['name']!r} kw={list(r.get('name_keywords') or [])} cap={r.get('market_cap_usd')} mkt={r.get('market')}")

print("\n=== 3. Foreign universe market caps (0B issue) ===")
print("  market_cap_usd describe:")
mc = uni['market_cap_usd'].astype(float)
print(f"    min={mc.min():,.0f} max={mc.max():,.0f} median={mc.median():,.0f}")
print("  sample (name, market_cap_usd, market):")
for _,r in uni.sort_values('market_cap_usd',ascending=False).head(8).iterrows():
    print(f"    {str(r['name'])[:30]:30} cap={r['market_cap_usd']:,.0f} mkt={r['market']}")
for _,r in uni.sort_values('market_cap_usd').head(5).iterrows():
    print(f"    [low] {str(r['name'])[:26]:26} cap={r['market_cap_usd']:,.0f} mkt={r['market']}")

print("\n=== 4. SpaceX in IPO watchlist? ===")
data = t.load_ipo_yaml()
for r in (data.get('high_profile') or []):
    print(f"    {r.get('company'):22} val={r.get('valuation_usd')} s1={r.get('s1_filed')}")
con.close()

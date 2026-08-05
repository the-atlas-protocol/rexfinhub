import sqlite3
c = sqlite3.connect("/home/jarvis/rexfinhub/data/etp_tracker.db")
base = "COALESCE(is_rex,0)=1 AND etp_category='LI' AND market_status='ACTV'"
SE = "underlier_type IN ('Stock','ETF','Crypto') AND COALESCE(rex_suite,'')!='MicroSectors'"

print("REX LI funds where DERIVED != CURRENT:")
for r in c.execute(f"""SELECT ticker, underlier_type, category_display, rex_suite, fund_name
  FROM mkt_master_data WHERE {base}
    AND ( ({SE} AND category_display NOT LIKE '%Single Stock%')
       OR (NOT ({SE}) AND category_display LIKE '%Single Stock%') )"""):
    print(f"   {r[0]:9} type={str(r[1]):9} disp={str(r[2])[-16:]:16} suite={str(r[3]):12} {str(r[4])[:36]}")

print("\nCounts:")
now = list(c.execute(f"SELECT COUNT(DISTINCT REPLACE(ticker,' US','')) FROM mkt_master_data WHERE {base} AND category_display LIKE '%Single Stock%'"))[0][0]
new = list(c.execute(f"SELECT COUNT(DISTINCT REPLACE(ticker,' US','')) FROM mkt_master_data WHERE {base} AND {SE}"))[0][0]
print(f"   current category_display : {now}")
print(f"   derived (Stock/ETF/Crypto): {new}")

print("\nMarket-wide movement with crypto counted as single:")
live = "market_status='ACTV' AND etp_category IN ('LI','CC')"
SE2 = "underlier_type IN ('Stock','ETF','Crypto') AND COALESCE(rex_suite,'')!='MicroSectors'"
for lbl, cond in (("move OUT of single", f"NOT ({SE2}) AND category_display LIKE '%Single Stock%'"),
                  ("move IN to single", f"{SE2} AND category_display LIKE '%Index%'")):
    rows = list(c.execute(f"SELECT underlier_type, COUNT(*) FROM mkt_master_data WHERE {live} AND {cond} GROUP BY 1 ORDER BY 2 DESC"))
    print(f"   {lbl}: {sum(k for _,k in rows)}  {dict(rows)}")

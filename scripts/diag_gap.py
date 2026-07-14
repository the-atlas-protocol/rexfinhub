import sqlite3, re
con = sqlite3.connect("/home/jarvis/rexfinhub/data/etp_tracker.db")
c = con.cursor()
# Find the specific short ETFs in mkt_master_data — why aren't they caught as ACTV inverse?
SHORTS = ["WDC","DELL","CRDO","HIMS","MRVL","COHR","AAOI","AXTI"]
print("=== mkt_master_data: every row whose fund_name contains 'SHORT'/'BEAR'/'INVERSE' for these underliers ===")
for u in SHORTS:
    rows = c.execute("""SELECT fund_name, market_status, primary_category, map_li_direction, map_li_underlier, aum
        FROM mkt_master_data WHERE (fund_name LIKE ? OR fund_name LIKE ? OR fund_name LIKE ?)
        """,(f"%SHORT {u}%",f"%BEAR {u}%",f"%INVERSE {u}%")).fetchall()
    if not rows:
        print(f"\n{u}: NOT in mkt_master_data at all (short fund missing from Bloomberg feed)")
    for fn,ms,pc,d,mu,a in rows:
        print(f"\n{u}: {fn[:48]:48} status={ms} cat={pc} dir={d!r} und={mu!r} aum={a}")
# What does fund_status 'Effective' really mean here — is there a listed/trading flag?
print("\n\n=== fund_status schema + sample inverse rows ===")
cols=[r[1] for r in c.execute("PRAGMA table_info(fund_status)").fetchall()]
print("cols:",cols)
for fn,st in c.execute("SELECT fund_name,status FROM fund_status WHERE fund_name LIKE '%2X Short WDC%' OR fund_name LIKE '%Short DELL%' OR fund_name LIKE '%Short CRDO%'").fetchall():
    print(f"  {st:12} {fn}")
con.close()

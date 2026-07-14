import sqlite3
con=sqlite3.connect("/home/jarvis/rexfinhub/data/etp_tracker.db")
c=con.cursor()
# Show the actual fund_status names matched for suspicious obscure names
for kw in ["QUANTA","HYGON","CAMBRICON","ACCTON","WINBOND","NANYA","IBIDEN","FUJIKURA","LUXSHARE"]:
    rows=c.execute("SELECT fund_name,status FROM fund_status WHERE UPPER(' '||fund_name||' ') LIKE ?",(f"% {kw} %",)).fetchall()
    # only leveraged ones
    lev=[(n,s) for n,s in rows if any(w in n.upper() for w in ['2X','3X','LONG','SHORT','BULL','BEAR','LEVERAG','DAILY'])]
    print(f"{kw:10}: {lev[:2] if lev else '(no leveraged match)'}")
con.close()

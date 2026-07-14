"""Verify the inverse board loaders against the live DB.
A) For each Table-A underlier: count ACTV long + ACTV inverse, AND any FILED inverse
   in fund_status (pending = seat not fully open).
B/C) For sample foreign/IPO names: dump the REX rows the race matched.
"""
import sqlite3, re
con = sqlite3.connect("/home/jarvis/rexfinhub/data/etp_tracker.db")
c = con.cursor()

TABLE_A = ["MRVL","INTC","AAOI","WDC","NOW","AXTI","UNH","DELL","CRDO","ARM","COHR","HIMS","GLW","APP"]
print("=== TABLE A: inverse-existence audit (ACTV mkt_master + FILED fund_status) ===")
print(f"{'Und':6} {'ACTVlong':>8} {'ACTVinv':>7} {'topLongAUM':>11}  filed_inverse_in_fund_status")
for u in TABLE_A:
    rows = c.execute("""SELECT map_li_direction, aum, fund_name FROM mkt_master_data
        WHERE primary_category='LI' AND market_status='ACTV' AND UPPER(map_li_underlier)=?""",(u,)).fetchall()
    nl = sum(1 for d,a,f in rows if 'long' in str(d).lower())
    ni = sum(1 for d,a,f in rows if re.search('short|inv',str(d).lower()))
    topl = max([a or 0 for d,a,f in rows if 'long' in str(d).lower()] or [0])
    # any inverse filing (pending or live) in fund_status mentioning this underlier
    fs = c.execute("""SELECT fund_name,status FROM fund_status WHERE fund_name LIKE ?""",(f"%{u}%",)).fetchall()
    finv = [f"{n} [{s}]" for n,s in fs if re.search(r'inverse|short|bear|-\dx',str(n),re.I)]
    print(f"{u:6} {nl:>8} {ni:>7} {topl:>11,.0f}  {('; '.join(finv)[:60]) or '(none)'}")

print("\n=== TABLES B/C: REX rows matched per sample name ===")
def race_strip(s): return re.sub(r"\(.*?\)","",str(s)).upper().replace(" ","")
INV=re.compile(r"inverse|short|bear|-\dx",re.I)
rexp = c.execute("SELECT name,status FROM rex_products WHERE product_suite='T-REX'").fetchall()
SAMPLES = {
 "SK Hynix":["SKHYNIX","HYNIX"], "ASML":["ASML"], "Samsung":["SAMSUNG"],
 "ASM International":["ASMINTERNATIONAL","ASMI"], "OpenAI":["OPENAI"],
 "Anthropic":["ANTHROPIC"], "Stripe":["STRIPE"], "Databricks":["DATABRICKS"],
}
for label,keys in SAMPLES.items():
    matched=[]
    for nm,st in rexp:
        snm=race_strip(nm)
        if any(k in snm for k in keys):
            matched.append(f"{('INV' if INV.search(nm) else 'LONG')}/{st}: {nm}")
    print(f"\n{label}: {'  |  '.join(matched) if matched else '(NO REX MATCH)'}")
con.close()

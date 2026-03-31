"""Final quality check: source files -> DB -> enriched JSON -> output."""
import psycopg2, psycopg2.extras, openpyxl, json

conn = psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia")
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=" * 70)
print("  FINAL QUALITY CHECK -- INPUT TO OUTPUT")
print("=" * 70)

# 1. KSD
print("\n--- 1. KSD Korea ---")
wb = openpyxl.load_workbook("attachments/2026 02 28 Korea REX report.xlsx", data_only=True)
ws = wb.active
ksd_total = sum(float(r[2]) for r in ws.iter_rows(min_row=2, values_only=True)
                if r[0] and isinstance(r[0], str) and r[0].isupper() and len(r[0]) <= 6
                and r[2] and isinstance(r[2], (int, float)) and r[2] > 0)
wb.close()
cur.execute("SELECT SUM(exchange_aum_usd) FROM etp_exchange_monthly_aum ea JOIN exchange ex ON ea.exchange_id=ex.exchange_id WHERE ex.name='KSD (Korea Securities Depository) - Retail' AND ea.month_id=13")
db_ksd = float(cur.fetchone()["sum"])
print(f"  Excel: ${ksd_total/1e6:,.1f}M | DB: ${db_ksd/1e6:,.1f}M | Diff: ${abs(db_ksd-ksd_total):,.0f}")

# 2. DRNZ Thailand
print("\n--- 2. DRNZ Thailand ---")
cur.execute("""
    SELECT ea.exchange_aum_usd, ea.shares_outstanding
    FROM etp_exchange_monthly_aum ea JOIN etp e ON ea.etp_id=e.etp_id
    JOIN exchange ex ON ea.exchange_id=ex.exchange_id
    WHERE e.ticker='DRNZ' AND ex.name='Asset Plus Asset Management' AND ea.month_id=13
""")
drnz = cur.fetchone()
drnz_aum = float(drnz["exchange_aum_usd"])
drnz_shares = float(drnz["shares_outstanding"])

BBG = r"C:\Users\RyuEl-Asmar\REX Financial LLC\REX Financial LLC - MasterFiles\MASTER Data\bloomberg_daily_file.xlsm"
wb2 = openpyxl.load_workbook(BBG, data_only=True, read_only=False)
ws2 = wb2["data_price"]
header2 = [str(c.value or "") for c in ws2[1]]
bbg_price = None
for row in ws2.iter_rows(min_row=2, max_row=4000):
    d = row[0].value
    if d and hasattr(d, "month") and d.year == 2026 and d.month == 2 and d.day == 28:
        for i, col in enumerate(header2[1:], 1):
            if "DRNZ" in str(col):
                bbg_price = float(row[i].value) if row[i].value else None
        break
wb2.close()

correct_aum = 16000 * bbg_price if bbg_price else 0
print(f"  Grace: 16,000 shares")
print(f"  Bloomberg price (Feb 28): ${bbg_price:.2f}")
print(f"  Correct AUM: 16,000 x ${bbg_price:.2f} = ${correct_aum:,.0f}")
print(f"  DB AUM: ${drnz_aum:,.0f}")
if abs(drnz_aum - correct_aum) > 100:
    print(f"  *** MISMATCH: DB=${drnz_aum:,.0f} vs correct=${correct_aum:,.0f}, diff=${abs(drnz_aum-correct_aum):,.0f} ***")
    # Check all DRNZ positions
    cur.execute("""
        SELECT ex.name, c.name as country, ea.exchange_aum_usd
        FROM etp_exchange_monthly_aum ea JOIN etp e ON ea.etp_id=e.etp_id
        JOIN exchange ex ON ea.exchange_id=ex.exchange_id
        JOIN country c ON ex.country_id=c.country_id
        WHERE e.ticker='DRNZ' AND ea.month_id=13
    """)
    print(f"  DRNZ all positions:")
    drnz_total = 0
    for r in cur.fetchall():
        v = float(r["exchange_aum_usd"])
        drnz_total += v
        print(f"    {r['name']} ({r['country']}): ${v:,.0f}")
    print(f"  DRNZ total Asia: ${drnz_total:,.0f}")
    print(f"  Original report DRNZ: $560,344")
else:
    print(f"  MATCH")

# 3. Enriched JSON
print("\n--- 3. ENRICHED JSON BALANCE ---")
enriched = json.load(open("enriched_report_data.json"))
h = enriched["headlines"]
dc = h["dollar_change"]
fl = h["total_flows"]
mm = h["total_market_move"]
print(f"  DC: ${dc/1e6:+,.1f}M")
print(f"  Flows: ${fl/1e6:+,.1f}M")
print(f"  MM: ${mm/1e6:+,.1f}M")
print(f"  FL+MM: ${(fl+mm)/1e6:+,.1f}M")
print(f"  Balanced: {abs(dc-(fl+mm)) < 0.01}")

# 4. Country totals
print("\n--- 4. COUNTRY TOTALS ---")
ORIG = {"Korea":951589017,"Hong Kong":219135176,"Japan":112487138,"Singapore":16397198,"Taiwan":14537031,"Malaysia":5293935,"Thailand":413760}
cur.execute("""
    SELECT c.name, SUM(ea.exchange_aum_usd) as aum
    FROM etp_exchange_monthly_aum ea JOIN exchange ex ON ea.exchange_id=ex.exchange_id
    JOIN country c ON ex.country_id=c.country_id WHERE ea.month_id=13
    GROUP BY c.name ORDER BY aum DESC
""")
all_ok = True
for r in cur.fetchall():
    db = float(r["aum"])
    orig = ORIG.get(r["name"], 0)
    diff = abs(db - orig)
    status = "OK" if diff < 1000 else f"DIFF ${diff:,.0f}"
    if diff >= 1000: all_ok = False
    print(f"  {r['name']:<12} DB=${db/1e6:,.1f}M  Orig=${orig/1e6:,.1f}M  {status}")
print(f"  {'ALL MATCH' if all_ok else '*** MISMATCHES FOUND ***'}")

# 5. Grand total
print("\n--- 5. GRAND TOTAL ---")
cur.execute("SELECT SUM(exchange_aum_usd) FROM etp_exchange_monthly_aum WHERE month_id=13")
total = float(cur.fetchone()["sum"])
print(f"  DB: ${total/1e6:,.4f}M")
print(f"  Original: $1,319.8533M")
print(f"  Diff: ${abs(total-1319853254):,.0f}")

# 6. Key report values
print("\n--- 6. REPORT VALUES ---")
print(f"  Total Asia AUM: ${h['total_asia_aum']/1e6:,.1f}M")
print(f"  Global AUM: ${h['total_global_aum']/1e9:.1f}B")
print(f"  % in Asia: {h['pct_in_asia']*100:.1f}%")
print(f"  Flows: ${h['total_flows']/1e6:+,.1f}M")
print(f"  Market Move: ${h['total_market_move']/1e6:+,.1f}M")

print("\n" + "=" * 70)
conn.close()

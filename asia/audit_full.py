"""Full audit of report_v15.html data vs PostgreSQL database."""
import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

# Get all fund data from DB for Jan and Feb 2026
cur.execute("""
    WITH feb_asia AS (
        SELECT e.ticker, ea.etp_id, SUM(ea.exchange_aum_usd) as aum
        FROM etp_exchange_monthly_aum ea
        JOIN etp e ON ea.etp_id = e.etp_id
        JOIN calendar_month cm ON ea.month_id = cm.month_id
        WHERE cm.month_end = '2026-02-28'
        GROUP BY e.ticker, ea.etp_id
    ),
    jan_asia AS (
        SELECT ea.etp_id, SUM(ea.exchange_aum_usd) as aum
        FROM etp_exchange_monthly_aum ea
        JOIN calendar_month cm ON ea.month_id = cm.month_id
        WHERE cm.month_end = '2026-01-31'
        GROUP BY ea.etp_id
    ),
    feb_global AS (
        SELECT mf.etp_id, mf.total_aum_usd
        FROM etp_monthly_fund mf
        JOIN calendar_month cm ON mf.month_id = cm.month_id
        WHERE cm.month_end = '2026-02-28'
    ),
    jan_global AS (
        SELECT mf.etp_id, mf.total_aum_usd
        FROM etp_monthly_fund mf
        JOIN calendar_month cm ON mf.month_id = cm.month_id
        WHERE cm.month_end = '2026-01-31'
    )
    SELECT f.ticker, f.aum as feb_asia,
           COALESCE(j.aum, 0) as jan_asia,
           COALESCE(fg.total_aum_usd, 0) as feb_global,
           COALESCE(jg.total_aum_usd, 0) as jan_global
    FROM feb_asia f
    LEFT JOIN jan_asia j ON f.etp_id = j.etp_id
    LEFT JOIN feb_global fg ON f.etp_id = fg.etp_id
    LEFT JOIN jan_global jg ON f.etp_id = jg.etp_id
    ORDER BY f.aum DESC
""")

db = {}
for r in cur.fetchall():
    ticker = r[0]
    feb_a, jan_a, feb_g, jan_g = [float(x) for x in r[1:]]
    dollar_chg = feb_a - jan_a
    mom = (feb_a / jan_a - 1) if jan_a > 0 else 0
    if jan_a > 0 and jan_g > 0 and feb_g > 0:
        global_mom = feb_g / jan_g - 1
        mkt_move = jan_a * global_mom
        flows = dollar_chg - mkt_move
    else:
        mkt_move = 0
        flows = dollar_chg
    pct = feb_a / feb_g if feb_g > 0 else 0
    db[ticker] = dict(aum=feb_a, total=feb_g, pct=pct, mom=mom, flows=flows,
                      mkt_move=mkt_move, jan_asia=jan_a, jan_global=jan_g)

# Report APPENDIX values
report = [
    ('FNGU','MicroSectors',177736000,853900000,0.208,-0.190,12900000),
    ('TSLT','T-REX',163864000,266900000,0.614,-0.087,11600000),
    ('MSTU','T-REX',155938000,307000000,0.508,0.055,72400000),
    ('BMNU','T-REX',145855000,220300000,0.662,-0.252,45400000),
    ('GDXU','MicroSectors',136545000,1278000000,0.107,0.891,23500000),
    ('BULZ','MicroSectors',114045000,342900000,0.333,-0.065,101800000),
    ('NVDX','T-REX',70480000,474400000,0.149,-0.136,5500000),
    ('AIPI','Income',40717000,376100000,0.108,-0.047,900000),
    ('FEPI','Income',37298000,582300000,0.064,0.020,2900000),
    ('SHNY','MicroSectors',37136000,162400000,0.229,0.603,29200000),
    ('CEPI','Income',35420000,83900000,0.422,-0.045,1400000),
    ('MSTZ','T-REX',25190000,93600000,0.269,-0.179,2000000),
    ('ROBN','T-REX',23787000,92700000,0.257,-0.359,3800000),
    ('TSLZ','T-REX',18312000,68200000,0.269,-0.094,-3300000),
    ('CCUP','T-REX',17963000,20800000,0.863,1.448,7400000),
    ('GOOX','T-REX',17598000,61800000,0.285,0.144,3400000),
    ('NVDQ','T-REX',13619000,35800000,0.381,-0.066,-500000),
    ('FNGS','MicroSectors',9368000,287400000,0.033,-0.143,100000),
    ('TSII','Income',8338000,45200000,0.184,-0.084,200000),
    ('NVII','Income',7535000,72600000,0.104,-0.168,-500000),
    ('SMUP','T-REX',5336000,14100000,0.377,-0.221,1200000),
    ('NFLU','T-REX',4826000,54000000,0.089,0.134,-500000),
    ('CRCD','T-REX',4394000,17100000,0.257,20.058,4400000),
    ('NRGU','MicroSectors',3967000,19100000,0.208,2.355,2400000),
    ('GMEU','T-REX',3736000,22400000,0.166,0.007,100000),
    ('FNGO','MicroSectors',3710000,76800000,0.048,-0.347,-1200000),
    ('RBLU','T-REX',3080000,11500000,0.267,0.037,0),
    ('CRWU','T-REX',3000000,12500000,0.240,-0.181,600000),
    ('DJTU','T-REX',2736000,10100000,0.271,-0.350,200000),
    ('MSFX','T-REX',2491000,20100000,0.124,1.067,1300000),
    ('CORD','T-REX',2242000,22800000,0.098,0.155,-200000),
    ('OILU','MicroSectors',2236000,29100000,0.077,-0.292,-1300000),
    ('BTCL','T-REX',2134000,25300000,0.084,-0.115,700000),
    ('RDWU','T-REX',2017000,4800000,0.421,0,2000000),
    ('BNKU','MicroSectors',1943000,11000000,0.176,-0.090,200000),
    ('BTCZ','T-REX',1938000,16400000,0.118,2.339,1200000),
    ('SNOU','T-REX',1476000,11400000,0.130,-0.204,200000),
    ('ETU','T-REX',1398000,10800000,0.129,-0.474,200000),
    ('ARMU','T-REX',1205000,3400000,0.351,0.323,-100000),
    ('KTUP','T-REX',1104000,5800000,0.192,0.798,600000),
]

print("=" * 100)
print("MATERIAL DISCREPANCIES: Report vs Database")
print("=" * 100)
print(f"{'FUND':<8} {'FIELD':<12} {'REPORT':>14} {'DATABASE':>14} {'DIFF':>14} {'SEV'}")
print("-" * 100)

total_rpt_flows = 0
total_db_flows = 0
material_count = 0

for (ticker, family, r_aum, r_total, r_pct, r_mom, r_flows) in report:
    total_rpt_flows += r_flows
    d = db.get(ticker)
    if not d:
        print(f"{ticker:<8} MISSING IN DB")
        continue
    total_db_flows += d['flows']

    aum_diff = r_aum - d['aum']
    if abs(aum_diff) > 100000:
        material_count += 1
        print(f"{ticker:<8} {'Asia AUM':<12} ${r_aum:>13,} ${d['aum']:>13,.0f} ${aum_diff:>13,.0f}")

    total_diff = r_total - d['total']
    if abs(total_diff) > 1000000:
        material_count += 1
        print(f"{ticker:<8} {'Global AUM':<12} ${r_total:>13,} ${d['total']:>13,.0f} ${total_diff:>13,.0f}")

    mom_diff = r_mom - d['mom']
    if abs(mom_diff) > 0.01:
        material_count += 1
        print(f"{ticker:<8} {'MoM %':<12} {r_mom*100:>13.1f}% {d['mom']*100:>13.1f}% {mom_diff*100:>13.1f}pp")

    flows_diff = r_flows - d['flows']
    if abs(flows_diff) > 100000:
        material_count += 1
        sev = 'CRIT' if abs(flows_diff) > 10000000 else 'HIGH' if abs(flows_diff) > 1000000 else 'MED'
        print(f"{ticker:<8} {'Flows':<12} ${r_flows:>13,} ${d['flows']:>13,.0f} ${flows_diff:>13,.0f} {sev}")

print("=" * 100)
print(f"\nMaterial discrepancies: {material_count}")
print(f"\n--- HEADLINE NUMBERS ---")
print(f"Report total flows (top 40): ${total_rpt_flows:>14,}")
print(f"DB total flows (top 40):     ${total_db_flows:>14,.0f}")
print(f"Difference:                  ${total_rpt_flows - total_db_flows:>14,.0f}")

# Full portfolio totals
total_all_flows = sum(d['flows'] for d in db.values())
total_all_mkt = sum(d['mkt_move'] for d in db.values())
total_feb_aum = sum(d['aum'] for d in db.values())
total_jan_aum = sum(d['jan_asia'] for d in db.values())

print(f"\n--- FULL PORTFOLIO (ALL {len(db)} FUNDS) ---")
print(f"Feb Asia AUM:    ${total_feb_aum:>14,.0f}")
print(f"Jan Asia AUM:    ${total_jan_aum:>14,.0f}")
print(f"Dollar change:   ${total_feb_aum - total_jan_aum:>14,.0f}")
print(f"Market move:     ${total_all_mkt:>14,.0f}")
print(f"Est. flows:      ${total_all_flows:>14,.0f}")

# Suite-level breakdown
suites = {}
cur.execute("SELECT e.ticker, pf.name FROM etp e JOIN product_family pf ON e.family_id = pf.family_id")
family_map = {r[0]: r[1] for r in cur.fetchall()}

for ticker, d in db.items():
    fam = family_map.get(ticker, 'Other')
    if fam not in suites:
        suites[fam] = {'flows': 0, 'mkt_move': 0, 'aum': 0}
    suites[fam]['flows'] += d['flows']
    suites[fam]['mkt_move'] += d['mkt_move']
    suites[fam]['aum'] += d['aum']

print(f"\n--- SUITE BREAKDOWN ---")
print(f"{'Suite':<20} {'Asia AUM':>14} {'Flows':>14} {'Mkt Move':>14}")
for suite in sorted(suites.keys()):
    s = suites[suite]
    print(f"{suite:<20} ${s['aum']:>13,.0f} ${s['flows']:>13,.0f} ${s['mkt_move']:>13,.0f}")

conn.close()

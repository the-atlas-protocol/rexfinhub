"""
[RETIRED 2026-04-22]

DO NOT RUN.

This script is a regex patcher that mutates report_v15.html directly.
The current pipeline uses:
  generate_report_data.py  -> report_data.json
  enrich_report_data.py    -> enriched_report_data.json
  build_reports.js         -> injects REPORT_DATA into HTML at render time

Running this script will OVERWRITE cleanly-injected data with regex substitutions,
re-creating the Feb 2026 post-ship incident where shipped HTML had mixed sources.
Kept for historical reference only.
"""
import sys
sys.exit("fix_flows.py is RETIRED — use enrich_report_data.py + build_reports.js pipeline")

import psycopg2
import re
import json

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

# Build complete fund data from DB
cur.execute("""
    WITH feb_asia AS (
        SELECT ea.etp_id, e.ticker, SUM(ea.exchange_aum_usd) as aum
        FROM etp_exchange_monthly_aum ea
        JOIN etp e ON ea.etp_id = e.etp_id
        JOIN calendar_month cm ON ea.month_id = cm.month_id
        WHERE cm.month_end = '2026-02-28'
        GROUP BY ea.etp_id, e.ticker
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
""")

db = {}
for r in cur.fetchall():
    ticker = r[0]
    feb_a, jan_a, feb_g, jan_g = [float(x) for x in r[1:]]
    mom = (feb_a / jan_a - 1) if jan_a > 0 else 0
    if jan_a > 0 and jan_g > 0 and feb_g > 0:
        global_mom = feb_g / jan_g - 1
        mkt_move = jan_a * global_mom
        flows = (feb_a - jan_a) - mkt_move
    else:
        mkt_move = 0
        flows = feb_a - jan_a if jan_a > 0 else 0
    pct = round(feb_a / feb_g, 3) if feb_g > 0 else 0
    db[ticker] = {
        'aum': round(feb_a),
        'total': round(feb_g),
        'pct': pct,
        'mom': round(mom, 3),
        'globalMom': round(global_mom, 3) if jan_g > 0 else 0,
        'flows': round(flows),
    }

conn.close()

# Read report
with open('C:/Foundry/Rexfinhub/asia/report_v15.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Fix APPENDIX entries - match pattern: {fund:'TICKER',...,flows:NUMBER}
def fix_appendix_entry(match):
    full = match.group(0)
    ticker_m = re.search(r"fund:'(\w+)'", full)
    if not ticker_m:
        return full
    ticker = ticker_m.group(1)
    d = db.get(ticker)
    if not d:
        return full

    # Replace all fields
    full = re.sub(r'aum:\d+', f'aum:{d["aum"]}', full)
    full = re.sub(r'total:\d+', f'total:{d["total"]}', full)
    full = re.sub(r'pct:[\d.]+', f'pct:{d["pct"]}', full)
    full = re.sub(r'mom:([-\d.]+)', f'mom:{d["mom"]}', full)
    full = re.sub(r'flows:([-\d]+)', f'flows:{d["flows"]}', full)
    return full

# Fix APPENDIX array entries
html = re.sub(
    r"\{fund:'[A-Z]+',family:'[^']*',aum:\d+,total:\d+,pct:[\d.]+,mom:[-\d.]+,flows:[-\d]+\}",
    fix_appendix_entry, html
)

# Fix suite fund arrays (TREX_FUNDS, MICRO_FUNDS, EPI_FUNDS, GI_FUNDS)
def fix_suite_entry(match):
    full = match.group(0)
    ticker_m = re.search(r"fund:'(\w+)'", full)
    if not ticker_m:
        return full
    ticker = ticker_m.group(1)
    d = db.get(ticker)
    if not d:
        return full

    # Replace all fields
    full = re.sub(r'aum:\d+', f'aum:{d["aum"]}', full)
    full = re.sub(r'total:\d+', f'total:{d["total"]}', full)
    full = re.sub(r'mom:([-\d.]+)', f'mom:{d["mom"]}', full)
    full = re.sub(r'globalMom:([-\d.]+)', f'globalMom:{d["globalMom"]}', full)
    full = re.sub(r'flows:([-\d]+)', f'flows:{d["flows"]}', full)
    return full

html = re.sub(
    r"\{fund:'[A-Z]+',aum:\d+,total:\d+,flows:[-\d]+,mom:[-\d.]+,globalMom:[-\d.]+\}",
    fix_suite_entry, html
)

# Fix OTHER_FUNDS entries (they have different format: {fund:'X',aum:N,total:N,pct:N,mom:N})
def fix_other_entry(match):
    full = match.group(0)
    ticker_m = re.search(r"fund:'(\w+)'", full)
    if not ticker_m:
        return full
    ticker = ticker_m.group(1)
    d = db.get(ticker)
    if not d:
        return full
    full = re.sub(r'aum:\d+', f'aum:{d["aum"]}', full)
    full = re.sub(r'total:\d+', f'total:{d["total"]}', full)
    full = re.sub(r'pct:[\d.]+', f'pct:{d["pct"]}', full)
    full = re.sub(r'mom:([-\d.]+)', f'mom:{d["mom"]}', full)
    return full

html = re.sub(
    r"\{fund:'[A-Z]+',aum:\d+,total:\d+,pct:[\d.]+,mom:[-\d.]+\}",
    fix_other_entry, html
)

# Write updated file
with open('C:/Foundry/Rexfinhub/asia/report_v15.html', 'w', encoding='utf-8') as f:
    f.write(html)

# Verify by re-reading and checking a few values
with open('C:/Foundry/Rexfinhub/asia/report_v15.html', 'r', encoding='utf-8') as f:
    verify = f.read()

# Check BULZ
bulz_m = re.search(r"fund:'BULZ',family:'MicroSectors',aum:\d+,total:\d+,pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)", verify)
if bulz_m:
    print(f"BULZ: mom={bulz_m.group(1)}, flows={bulz_m.group(2)}")
    print(f"  Expected: mom={db['BULZ']['mom']}, flows={db['BULZ']['flows']}")

# Check SHNY
shny_m = re.search(r"fund:'SHNY',family:'MicroSectors',aum:\d+,total:\d+,pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)", verify)
if shny_m:
    print(f"SHNY: mom={shny_m.group(1)}, flows={shny_m.group(2)}")
    print(f"  Expected: mom={db['SHNY']['mom']}, flows={db['SHNY']['flows']}")

# Check NVDQ
nvdq_m = re.search(r"fund:'NVDQ',family:'T-REX',aum:\d+,total:\d+,pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)", verify)
if nvdq_m:
    print(f"NVDQ: mom={nvdq_m.group(1)}, flows={nvdq_m.group(2)}")
    print(f"  Expected: mom={db['NVDQ']['mom']}, flows={db['NVDQ']['flows']}")

# Check OILU
oilu_m = re.search(r"fund:'OILU',family:'MicroSectors',aum:\d+,total:\d+,pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)", verify)
if oilu_m:
    print(f"OILU: mom={oilu_m.group(1)}, flows={oilu_m.group(2)}")
    print(f"  Expected: mom={db['OILU']['mom']}, flows={db['OILU']['flows']}")

# Calculate new totals
total_flows = 0
total_mkt = 0
for ticker, d in db.items():
    total_flows += d['flows']

print(f"\nTotal corrected flows (all funds): ${total_flows:,.0f}")

# Check suite entries
for suite_name in ['TSLT', 'MSTU', 'FNGU', 'GDXU', 'AIPI']:
    m = re.search(rf"fund:'{suite_name}',aum:\d+,total:\d+,flows:([-\d]+),mom:([-\d.]+),globalMom:([-\d.]+)", verify)
    if m:
        print(f"{suite_name} suite: flows={m.group(1)}, mom={m.group(2)}, globalMom={m.group(3)}")
        print(f"  Expected: flows={db[suite_name]['flows']}, mom={db[suite_name]['mom']}, globalMom={db[suite_name]['globalMom']}")

print("\nDone. All flows, mom, and globalMom values updated from database.")

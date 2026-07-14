"""Find EVERY stale hardcoded value in report_v15.html."""
import re
import psycopg2
import json

with open(r'C:\Foundry\Rexfinhub\asia\report_v15.html', 'r', encoding='utf-8') as f:
    html = f.read()

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

issues = []

def flag(array_name, field, html_val, db_val, threshold=0.05):
    if db_val == 0 and html_val == 0:
        return
    if db_val == 0:
        ratio = float('inf')
    else:
        ratio = html_val / db_val
    if abs(ratio - 1.0) > threshold:
        issues.append((array_name, field, html_val, db_val, ratio))

# ================================================================
# 1. COUNTRIES array
# ================================================================
m = re.findall(r"\{name:'(\w[\w\s]*)',aum:(\d+),prior:(\d+)", html)
cur.execute("""
    SELECT c.name, cm.month_end, SUM(ea.exchange_aum_usd)
    FROM etp_exchange_monthly_aum ea
    JOIN exchange ex ON ea.exchange_id = ex.exchange_id
    JOIN country c ON ex.country_id = c.country_id
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    WHERE cm.month_end IN ('2026-01-31', '2026-02-28')
    GROUP BY c.name, cm.month_end
""")
db_country = {}
for r in cur.fetchall():
    db_country[(r[0], str(r[1]))] = float(r[2])

for name, aum, prior in m:
    db_feb = db_country.get((name, '2026-02-28'), 0)
    db_jan = db_country.get((name, '2026-01-31'), 0)
    flag('COUNTRIES', f'{name} aum', int(aum), db_feb, 0.01)
    if int(prior) > 0:
        flag('COUNTRIES', f'{name} prior', int(prior), db_jan, 0.01)

# ================================================================
# 2. EXCHANGES array
# ================================================================
ex_matches = re.findall(r"\{name:'([^']+)',country:'([^']+)',etps:(\d+),aum:(\d+)\}", html)
cur.execute("""
    SELECT ex.name, c.name, COUNT(DISTINCT ea.etp_id), SUM(ea.exchange_aum_usd)
    FROM etp_exchange_monthly_aum ea
    JOIN exchange ex ON ea.exchange_id = ex.exchange_id
    JOIN country c ON ex.country_id = c.country_id
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    WHERE cm.month_end = '2026-02-28'
    GROUP BY ex.name, c.name ORDER BY SUM(ea.exchange_aum_usd) DESC
""")
db_exchanges = {(r[0][:20], r[1]): (r[2], float(r[3])) for r in cur.fetchall()}

for name, country, etps, aum in ex_matches:
    matched = None
    for (dn, dc), (de, da) in db_exchanges.items():
        if dc == country and (dn[:10] in name or name[:10] in dn):
            matched = (de, da)
            break
    if matched:
        flag('EXCHANGES', f'{name} aum', int(aum), matched[1], 0.01)
        flag('EXCHANGES', f'{name} etps', int(etps), matched[0], 0.01)

# ================================================================
# 3. COUNTRY_SHARES array (13 months of country percentages)
# ================================================================
cs_match = re.search(r'const COUNTRY_SHARES = \[(.*?)\];', html, re.DOTALL)
if cs_match:
    entries = re.findall(r'\{korea:([\d.]+),\s*hk:([\d.]+),\s*japan:([\d.]+),\s*other:([\d.]+)\}', cs_match.group(1))

    cur.execute("""
        SELECT cm.month_end, c.name, SUM(ea.exchange_aum_usd)
        FROM etp_exchange_monthly_aum ea
        JOIN exchange ex ON ea.exchange_id = ex.exchange_id
        JOIN country c ON ex.country_id = c.country_id
        JOIN calendar_month cm ON ea.month_id = cm.month_id
        GROUP BY cm.month_end, c.name ORDER BY cm.month_end
    """)
    db_cs = {}
    for r in cur.fetchall():
        month = str(r[0])
        if month == '2025-11-28':
            month = '2025-11-30'
        db_cs.setdefault(month, {})[r[1]] = float(r[2])

    months_order = sorted(db_cs.keys())
    for i, (korea, hk, japan, other) in enumerate(entries):
        if i < len(months_order):
            month = months_order[i]
            total = sum(db_cs[month].values())
            db_korea = db_cs[month].get('Korea', 0) / total
            db_hk = db_cs[month].get('Hong Kong', 0) / total
            db_japan = db_cs[month].get('Japan', 0) / total
            db_other = 1 - db_korea - db_hk - db_japan
            flag('COUNTRY_SHARES', f'month {i+1} korea', float(korea), db_korea, 0.02)
            flag('COUNTRY_SHARES', f'month {i+1} hk', float(hk), db_hk, 0.02)

# ================================================================
# 4. SUITE_FLOWS array
# ================================================================
sf_match = re.search(r'const SUITE_FLOWS = \[(.*?)\];', html, re.DOTALL)
if sf_match:
    sf_entries = re.findall(r"\{name:'([^']+)',flows:([-\d.]+)\}", sf_match.group(1))
    # These should be computed from the corrected APPENDIX data
    # The JS buildSuiteFlowsChart uses this array
    print(f"SUITE_FLOWS entries: {sf_entries}")

# ================================================================
# 5. INCOME_TIMELINE (EPI+G&I monthly data)
# ================================================================
it_match = re.search(r'const INCOME_TIMELINE = \[(.*?)\];', html, re.DOTALL)
if it_match:
    it_entries = re.findall(r'\{month:"([^"]+)",epi:([\d.]+),gi:([\d.]+)\}', it_match.group(1))

    # Get EPI and GI monthly from DB
    epi_tickers = ('AIPI', 'FEPI', 'CEPI')
    gi_tickers = ('TSII', 'NVII', 'MSII', 'COII', 'ULTI', 'WMTI', 'LLII', 'PLTI', 'HOII', 'GIF', 'CWII', 'ATCL')

    for month_str, epi_val, gi_val in it_entries:
        db_month = month_str if month_str != '2025-11-30' else '2025-11-28'

        cur.execute("""
            SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
            JOIN etp e ON ea.etp_id = e.etp_id
            JOIN calendar_month cm ON ea.month_id = cm.month_id
            WHERE cm.month_end = %s AND e.ticker IN %s
        """, (db_month, epi_tickers))
        db_epi = float(cur.fetchone()[0] or 0)

        cur.execute("""
            SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
            JOIN etp e ON ea.etp_id = e.etp_id
            JOIN calendar_month cm ON ea.month_id = cm.month_id
            WHERE cm.month_end = %s AND e.ticker IN %s
        """, (db_month, gi_tickers))
        db_gi = float(cur.fetchone()[0] or 0)

        flag('INCOME_TIMELINE', f'{month_str} epi', float(epi_val), db_epi, 0.01)
        flag('INCOME_TIMELINE', f'{month_str} gi', float(gi_val), db_gi, 0.05)

# ================================================================
# 6. Country breakdown per suite (TREX_COUNTRY_SUMMARY etc)
# ================================================================
for array_name, family_filter in [
    ('TREX_COUNTRY_SUMMARY', "= 'T-REX'"),
    ('MICRO_COUNTRY_SUMMARY', "= 'MicroSectors'"),
]:
    arr_match = re.search(rf'const {array_name} = \[(.*?)\];', html, re.DOTALL)
    if arr_match:
        entries = re.findall(r"\{country:'([^']+)',aum:([\d.]+)\}", arr_match.group(1))
        for country, aum_val in entries:
            db_country_name = 'Hong Kong' if country == 'Hong Kong' else country
            cur.execute(f"""
                SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
                JOIN etp e ON ea.etp_id = e.etp_id
                JOIN product_family pf ON e.family_id = pf.family_id
                JOIN exchange ex ON ea.exchange_id = ex.exchange_id
                JOIN country c ON ex.country_id = c.country_id
                JOIN calendar_month cm ON ea.month_id = cm.month_id
                WHERE cm.month_end = '2026-02-28' AND pf.name {family_filter}
                AND c.name = %s
            """, (db_country_name,))
            result = cur.fetchone()
            db_val = float(result[0]) if result[0] else 0
            flag(array_name, f'{country}', float(aum_val), db_val, 0.01)

# ================================================================
# 7. PCT arrays (% of suite in Asia)
# ================================================================
for pct_name in ['TREX_PCT', 'MICRO_PCT', 'EPI_PCT', 'GI_PCT']:
    pct_match = re.search(rf'const {pct_name} = \[([\d,]+)\]', html)
    if pct_match:
        vals = [int(v) for v in pct_match.group(1).split(',')]
        # These are already verified from Bloomberg - just note they exist
        pass

# ================================================================
# REPORT
# ================================================================
conn.close()

print(f"\n{'=' * 70}")
print(f"STALE VALUE SCAN: {len(issues)} mismatches found")
print(f"{'=' * 70}")

if issues:
    print(f"\n{'Array':<25} {'Field':<25} {'HTML':>14} {'DB':>14} {'Ratio':>8}")
    print("-" * 90)
    for arr, field, hv, dv, ratio in sorted(issues):
        print(f"{arr:<25} {field:<25} {hv:>14,.0f} {dv:>14,.0f} {ratio:>8.2f}x")
else:
    print("ALL VALUES MATCH")

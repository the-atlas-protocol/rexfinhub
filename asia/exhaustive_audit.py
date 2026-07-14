"""Exhaustive audit of every remaining hardcoded data array and text value."""
import re
import psycopg2

with open(r'C:\Projects\rexfinhub\asia\report_v15.html', 'r', encoding='utf-8') as f:
    html = f.read()

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

epi_tickers = ('AIPI', 'FEPI', 'CEPI')
gi_tickers = ('TSII', 'NVII', 'MSII', 'COII', 'ULTI', 'WMTI', 'LLII', 'PLTI', 'HOII', 'GIF', 'CWII', 'ATCL')

issues = []

# 1. Suite 6-month country bars
print("1. SUITE COUNTRY 6M BARS")
month_map = {
    "Sep '25": '2025-09-30', "Oct '25": '2025-10-31',
    "Nov '25": '2025-11-28', "Dec '25": '2025-12-31',
    "Jan '26": '2026-01-31', "Feb '26": '2026-02-28',
}
country_map = {'korea': 'Korea', 'hk': 'Hong Kong', 'japan': 'Japan', 'sg': 'Singapore'}

for array_name, pf_name, tix in [
    ('TREX_COUNTRY_6M', 'T-REX', None),
    ('MICRO_COUNTRY_6M', 'MicroSectors', None),
    ('EPI_COUNTRY_6M', None, epi_tickers),
    ('GI_COUNTRY_6M', None, gi_tickers),
]:
    m = re.search(rf'const {array_name} = \[(.*?)\];', html, re.DOTALL)
    if not m:
        continue
    entries = re.findall(r"\{month:'([^']+)',(.*?)\}", m.group(1))
    bad = 0
    for ml, cs in entries:
        db_month = month_map.get(ml)
        if not db_month:
            continue
        for ckey, cval in re.findall(r'(\w+):([\d.]+)', cs):
            rc = country_map.get(ckey)
            if not rc or float(cval) == 0:
                continue
            hv = float(cval) * 1e6
            if pf_name:
                cur.execute("""SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
                    JOIN etp e ON ea.etp_id = e.etp_id JOIN product_family pf ON e.family_id = pf.family_id
                    JOIN exchange ex ON ea.exchange_id = ex.exchange_id JOIN country c ON ex.country_id = c.country_id
                    JOIN calendar_month cm ON ea.month_id = cm.month_id
                    WHERE cm.month_end = %s AND pf.name = %s AND c.name = %s""", (db_month, pf_name, rc))
            else:
                cur.execute("""SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
                    JOIN etp e ON ea.etp_id = e.etp_id
                    JOIN exchange ex ON ea.exchange_id = ex.exchange_id JOIN country c ON ex.country_id = c.country_id
                    JOIN calendar_month cm ON ea.month_id = cm.month_id
                    WHERE cm.month_end = %s AND e.ticker IN %s AND c.name = %s""", (db_month, tix, rc))
            dv = float(cur.fetchone()[0] or 0)
            if dv > 10000 and abs(hv/dv - 1) > 0.05:
                bad += 1
                issues.append((array_name, f"{ml} {ckey}", hv/1e6, dv/1e6))
    print(f"  {array_name}: {bad} mismatches")

# 2. PI_TOP and GI_TOP flows
print("\n2. PI_TOP / GI_TOP FLOWS")
for arr_name in ['PI_TOP', 'GI_TOP']:
    m = re.search(rf'const {arr_name} = \[(.*?)\];', html, re.DOTALL)
    if not m:
        continue
    entries = re.findall(r"fund:'(\w+)',aum:(\d+),total:(\d+),flows:([-\d]+),mom:([-\d.]+),globalMom:([-\d.]+)", m.group(1))
    for ticker, aum, total, flows, mom, gmom in entries:
        # Check flows match what fix_flows.py computed
        cur.execute("""
            WITH feb AS (SELECT ea.etp_id, SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
                JOIN etp e ON ea.etp_id = e.etp_id JOIN calendar_month cm ON ea.month_id = cm.month_id
                WHERE cm.month_end = '2026-02-28' AND e.ticker = %s GROUP BY ea.etp_id),
            jan AS (SELECT ea.etp_id, SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
                JOIN etp e ON ea.etp_id = e.etp_id JOIN calendar_month cm ON ea.month_id = cm.month_id
                WHERE cm.month_end = '2026-01-31' AND e.ticker = %s GROUP BY ea.etp_id),
            fg AS (SELECT mf.etp_id, mf.total_aum_usd FROM etp_monthly_fund mf
                JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = '2026-02-28'),
            jg AS (SELECT mf.etp_id, mf.total_aum_usd FROM etp_monthly_fund mf
                JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = '2026-01-31')
            SELECT (f.aum - COALESCE(j.aum,0)) - COALESCE(j.aum,0) * (fg2.total_aum_usd/NULLIF(jg2.total_aum_usd,0) - 1)
            FROM feb f LEFT JOIN jan j ON f.etp_id = j.etp_id
            LEFT JOIN fg fg2 ON f.etp_id = fg2.etp_id LEFT JOIN jg jg2 ON f.etp_id = jg2.etp_id
        """, (ticker, ticker))
        r = cur.fetchone()
        db_flows = round(float(r[0])) if r and r[0] else 0
        if abs(int(flows) - db_flows) > 2:
            issues.append((arr_name, f"{ticker} flows", int(flows), db_flows))
            print(f"  {arr_name} {ticker}: HTML flows={flows} DB flows={db_flows}")
    if not any(a[0] == arr_name and 'flows' in a[1] for a in issues):
        print(f"  {arr_name}: all flows match")

# 3. OTHER_TOP
print("\n3. OTHER_TOP")
m = re.search(r'const OTHER_TOP = \[(.*?)\];', html, re.DOTALL)
if m:
    entries = re.findall(r"fund:'(\w+)',aum:(\d+),total:(\d+),pct:[\d.]+,mom:([-\d.]+)", m.group(1))
    for ticker, aum, total, mom in entries:
        cur.execute("""SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
            JOIN etp e ON ea.etp_id = e.etp_id JOIN calendar_month cm ON ea.month_id = cm.month_id
            WHERE cm.month_end = '2026-02-28' AND e.ticker = %s""", (ticker,))
        db_aum = float(cur.fetchone()[0] or 0)
        if abs(int(aum) - db_aum) > 1000:
            issues.append(('OTHER_TOP', f'{ticker} aum', int(aum), db_aum))
    print(f"  {len(entries)} entries checked")

# 4. TREX_TOP / MICRO_TOP
print("\n4. TREX_TOP / MICRO_TOP")
for arr_name in ['TREX_TOP', 'MICRO_TOP']:
    m = re.search(rf'const {arr_name} = \[(.*?)\];', html, re.DOTALL)
    if not m:
        continue
    entries = re.findall(r"fund:'(\w+)',aum:(\d+),total:(\d+),flows:([-\d]+)", m.group(1))
    bad = 0
    for ticker, aum, total, flows in entries:
        cur.execute("""SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
            JOIN etp e ON ea.etp_id = e.etp_id JOIN calendar_month cm ON ea.month_id = cm.month_id
            WHERE cm.month_end = '2026-02-28' AND e.ticker = %s""", (ticker,))
        db_aum = float(cur.fetchone()[0] or 0)
        if abs(int(aum) - db_aum) > 1000:
            bad += 1
            issues.append((arr_name, f'{ticker} aum', int(aum), db_aum))
    print(f"  {arr_name}: {len(entries)} entries, {bad} AUM mismatches")

# 5. Hardcoded text percentages
print("\n5. HARDCODED PERCENTAGES IN TEXT")
pct_checks = [
    ('T-REX 78.5% Korea', 'T-REX', 'Korea', 78.5),
    ('T-REX 12.6% HK', 'T-REX', 'Hong Kong', 12.6),
    ('MSEC 73% Korea', 'MicroSectors', 'Korea', 73.0),
    ('MSEC 26% HK', 'MicroSectors', 'Hong Kong', 26.0),
    ('EPI 69% Japan', None, 'Japan', 69.0),
    ('EPI 28% Korea', None, 'Korea', 28.0),
    ('G&I 92.2% Korea', None, 'Korea', 92.2),
]

for label, pf, country, html_pct in pct_checks:
    if pf:
        cur.execute("""
            SELECT SUM(ea.exchange_aum_usd) * 100.0 /
                (SELECT SUM(ea2.exchange_aum_usd) FROM etp_exchange_monthly_aum ea2
                 JOIN etp e2 ON ea2.etp_id = e2.etp_id JOIN product_family pf2 ON e2.family_id = pf2.family_id
                 JOIN calendar_month cm2 ON ea2.month_id = cm2.month_id
                 WHERE cm2.month_end = '2026-02-28' AND pf2.name = %s)
            FROM etp_exchange_monthly_aum ea
            JOIN etp e ON ea.etp_id = e.etp_id JOIN product_family pf ON e.family_id = pf.family_id
            JOIN exchange ex ON ea.exchange_id = ex.exchange_id JOIN country c ON ex.country_id = c.country_id
            JOIN calendar_month cm ON ea.month_id = cm.month_id
            WHERE cm.month_end = '2026-02-28' AND pf.name = %s AND c.name = %s
        """, (pf, pf, country))
    else:
        tix = epi_tickers if 'EPI' in label else gi_tickers
        cur.execute("""
            SELECT SUM(ea.exchange_aum_usd) * 100.0 /
                (SELECT SUM(ea2.exchange_aum_usd) FROM etp_exchange_monthly_aum ea2
                 JOIN etp e2 ON ea2.etp_id = e2.etp_id
                 JOIN calendar_month cm2 ON ea2.month_id = cm2.month_id
                 WHERE cm2.month_end = '2026-02-28' AND e2.ticker IN %s)
            FROM etp_exchange_monthly_aum ea
            JOIN etp e ON ea.etp_id = e.etp_id
            JOIN exchange ex ON ea.exchange_id = ex.exchange_id JOIN country c ON ex.country_id = c.country_id
            JOIN calendar_month cm ON ea.month_id = cm.month_id
            WHERE cm.month_end = '2026-02-28' AND e.ticker IN %s AND c.name = %s
        """, (tix, tix, country))

    db_pct = float(cur.fetchone()[0] or 0)
    diff = abs(html_pct - db_pct)
    status = "OK" if diff < 1.0 else "MISMATCH"
    if status == "MISMATCH":
        issues.append(('TEXT_%', label, html_pct, db_pct))
    print(f"  {label}: HTML={html_pct}% DB={db_pct:.1f}% {status}")

# 6. MoM dollar change text
print("\n6. MON DOLLAR CHANGE TEXT (KPI subs)")
# These are overwritten by JS, but let's verify the JS computes correctly
# by checking the APPENDIX-derived values
for pf_name, expected_text in [('T-REX', '-$71.0M'), ('MicroSectors', '+$17.0M')]:
    cur.execute("""
        SELECT SUM(ea_feb.aum) - SUM(ea_jan.aum) FROM
        (SELECT ea.etp_id, SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
         JOIN etp e ON ea.etp_id = e.etp_id JOIN product_family pf ON e.family_id = pf.family_id
         JOIN calendar_month cm ON ea.month_id = cm.month_id
         WHERE cm.month_end = '2026-02-28' AND pf.name = %s GROUP BY ea.etp_id) ea_feb
        JOIN
        (SELECT ea.etp_id, SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
         JOIN etp e ON ea.etp_id = e.etp_id JOIN product_family pf ON e.family_id = pf.family_id
         JOIN calendar_month cm ON ea.month_id = cm.month_id
         WHERE cm.month_end = '2026-01-31' AND pf.name = %s GROUP BY ea.etp_id) ea_jan
        ON ea_feb.etp_id = ea_jan.etp_id
    """, (pf_name, pf_name))
    db_change = float(cur.fetchone()[0] or 0)
    print(f"  {pf_name}: HTML={expected_text} DB=${db_change/1e6:+,.1f}M")

conn.close()

print(f"\n{'='*80}")
print(f"EXHAUSTIVE AUDIT COMPLETE: {len(issues)} issues")
if issues:
    for arr, field, hv, dv in issues:
        print(f"  {arr} {field}: HTML={hv} DB={dv}")
else:
    print("  ALL VALUES VERIFIED")

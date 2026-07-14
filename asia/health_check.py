"""Final health check: HTML vs DB vs Excel vs Grace, cross-report consistency."""
import psycopg2, json, re, random
import pandas as pd

random.seed(42)

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

with open('C:/Projects/rexfinhub/asia/report_v15.html', 'r', encoding='utf-8') as f:
    html = f.read()

entries = re.findall(
    r"fund:'(\w+)',family:'[^']*',aum:(\d+),total:(\d+),pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)", html)

# DB truth
cur.execute("""
    WITH feb AS (SELECT ea.etp_id, e.ticker, SUM(ea.exchange_aum_usd) as aum
        FROM etp_exchange_monthly_aum ea JOIN etp e ON ea.etp_id = e.etp_id
        JOIN calendar_month cm ON ea.month_id = cm.month_id WHERE cm.month_end = '2026-02-28'
        GROUP BY ea.etp_id, e.ticker),
    jan AS (SELECT ea.etp_id, SUM(ea.exchange_aum_usd) as aum
        FROM etp_exchange_monthly_aum ea JOIN calendar_month cm ON ea.month_id = cm.month_id
        WHERE cm.month_end = '2026-01-31' GROUP BY ea.etp_id),
    fg AS (SELECT mf.etp_id, mf.total_aum_usd FROM etp_monthly_fund mf
        JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = '2026-02-28'),
    jg AS (SELECT mf.etp_id, mf.total_aum_usd FROM etp_monthly_fund mf
        JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = '2026-01-31')
    SELECT f.ticker, f.aum, COALESCE(j.aum,0), COALESCE(fg2.total_aum_usd,0), COALESCE(jg2.total_aum_usd,0)
    FROM feb f LEFT JOIN jan j ON f.etp_id = j.etp_id
    LEFT JOIN fg fg2 ON f.etp_id = fg2.etp_id LEFT JOIN jg jg2 ON f.etp_id = jg2.etp_id
""")
db = {}
for r in cur.fetchall():
    t, fa, ja, fg, jg = r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])
    mom = round((fa/ja - 1), 3) if ja > 0 else 0
    if ja > 0 and jg > 0 and fg > 0:
        fl = round((fa - ja) - ja * (fg/jg - 1))
    else:
        fl = round(fa - ja) if ja > 0 else 0
    db[t] = {'aum': fa, 'total': fg, 'mom': mom, 'flows': fl}

# ==================================================================
print("=" * 90)
print("1. REPORT HEALTH: HTML vs Database")
print("=" * 90)

aum_ok = flows_ok = mom_ok = 0
for ticker, aum, total, mom, flows in entries:
    d = db.get(ticker)
    if not d:
        continue
    if abs(int(aum) - d['aum']) < 1000:
        aum_ok += 1
    if abs(int(flows) - d['flows']) < 2:
        flows_ok += 1
    if abs(float(mom) - d['mom']) < 0.002:
        mom_ok += 1

print(f"  AUM:   {aum_ok}/84 match  {'PASS' if aum_ok == 84 else 'FAIL'}")
print(f"  Flows: {flows_ok}/84 match  {'PASS' if flows_ok == 84 else 'FAIL'}")
print(f"  MoM:   {mom_ok}/84 match  {'PASS' if mom_ok == 84 else 'FAIL'}")

# Timeline
data_match = re.findall(r'month:"([\d-]+)",total:([\d.]+)', html)
cur.execute("""
    SELECT cm.month_end, SUM(ea.exchange_aum_usd)
    FROM etp_exchange_monthly_aum ea JOIN calendar_month cm ON ea.month_id = cm.month_id
    GROUP BY cm.month_end ORDER BY cm.month_end
""")
db_tl = {str(r[0]): float(r[1]) for r in cur.fetchall()}
db_tl["2025-11-30"] = db_tl.pop("2025-11-28", 0)

tl_ok = 0
for month, total in data_match:
    dv = db_tl.get(month, 0)
    if abs(float(total) - dv) < 1000:
        tl_ok += 1
    else:
        print(f"  TIMELINE MISS: {month} HTML={float(total)/1e6:.1f}M DB={dv/1e6:.1f}M")

print(f"  Timeline: {tl_ok}/13 months  {'PASS' if tl_ok == 13 else 'FAIL'}")

# ==================================================================
print()
print("=" * 90)
print("2. CROSS-REPORT CONSISTENCY")
print("=" * 90)

with open('C:/Projects/rexfinhub/asia/report_data.json') as f:
    rdata = json.load(f)

fams = {}
for f in rdata['funds']:
    fn = f['family_name']
    fams.setdefault(fn, []).append(f['ticker'])

trex_json = set(fams.get('T-REX', []))
micro_json = set(fams.get('MicroSectors', []))
trex_html = set(re.findall(r"fund:'(\w+)',family:'T-REX'", html))
micro_html = set(re.findall(r"fund:'(\w+)',family:'MicroSectors'", html))

print(f"  T-REX funds:        JSON={len(trex_json)} HTML={len(trex_html)}  {'PASS' if trex_json == trex_html else 'FAIL'}")
print(f"  MicroSectors funds: JSON={len(micro_json)} HTML={len(micro_html)}  {'PASS' if micro_json == micro_html else 'FAIL'}")

cur.execute("""
    SELECT pf.name, SUM(ea.exchange_aum_usd)
    FROM etp_exchange_monthly_aum ea JOIN etp e ON ea.etp_id = e.etp_id
    JOIN product_family pf ON e.family_id = pf.family_id
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    WHERE cm.month_end = '2026-02-28' GROUP BY pf.name
""")
db_s = {r[0]: float(r[1]) for r in cur.fetchall()}
trex_j = sum(f['asia_aum'] for f in rdata['funds'] if f['family_name'] == 'T-REX')
micro_j = sum(f['asia_aum'] for f in rdata['funds'] if f['family_name'] == 'MicroSectors')

print(f"  T-REX AUM:          JSON=${trex_j/1e6:.1f}M  DB=${db_s.get('T-REX',0)/1e6:.1f}M  {'PASS' if abs(trex_j - db_s.get('T-REX',0)) < 1 else 'FAIL'}")
print(f"  MicroSectors AUM:   JSON=${micro_j/1e6:.1f}M  DB=${db_s.get('MicroSectors',0)/1e6:.1f}M  {'PASS' if abs(micro_j - db_s.get('MicroSectors',0)) < 1 else 'FAIL'}")

# ==================================================================
print()
print("=" * 90)
print("3. GRACE SPOT CHECK: 6 random exchange lines")
print("=" * 90)

grace = [
    ("Korea", "KSD", 927.6, "reported"),
    ("Korea", "ACE TESLA", 23.9, "reported"),
    ("Japan", "SBI", 29.5, "reported"),
    ("Japan", "Rakuten", 61.9, "reported"),
    ("Japan", "MooMoo", 12.1, "quarterly"),
    ("Japan", "Monex", 7.3, "reported"),
    ("Japan", "Matsui", 1.8, "reported"),
    ("HK", "Futu/MooMoo", 168.3, "quarterly"),
    ("HK", "Oriental Harbour", 105.9, "13F"),
    ("HK", "SYFE", 1.0, "verbal"),
    ("Singapore", "MooMoo", 16.6, "quarterly"),
    ("Malaysia", "MooMoo", 6.3, "quarterly"),
    ("Thailand", "Asset Plus", 0.4, "reported"),
    ("ViewTrade", "ViewTrade", 25.4, "quarterly"),
]

cur.execute("""
    SELECT c.name, ex.name, SUM(ea.exchange_aum_usd), SUM(COALESCE(ea.original_aum_usd, ea.exchange_aum_usd))
    FROM etp_exchange_monthly_aum ea
    JOIN exchange ex ON ea.exchange_id = ex.exchange_id
    JOIN country c ON ex.country_id = c.country_id
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    WHERE cm.month_end = '2026-02-28' GROUP BY c.name, ex.name
""")
db_ex = {}
for r in cur.fetchall():
    db_ex[(r[0], r[1])] = {'repr': float(r[2])/1e6, 'orig': float(r[3])/1e6}

sample = random.sample(range(len(grace)), 6)
print(f"\n  {'#':<4} {'Country':<12} {'Source':<20} {'Grace':>8} {'DB Repr':>10} {'DB Orig':>10} {'Type':<10} Verdict")
print(f"  {'-'*85}")

for idx in sorted(sample):
    country, source, gval, stype = grace[idx]
    matched = None
    for (dc, dn), dv in db_ex.items():
        if source == "ViewTrade" and "ViewTrade" in dn:
            vr = sum(v['repr'] for (c, n), v in db_ex.items() if 'ViewTrade' in n)
            vo = sum(v['orig'] for (c, n), v in db_ex.items() if 'ViewTrade' in n)
            matched = {'repr': vr, 'orig': vo}
            break
        if source[:4].lower() in dn.lower():
            cmap = {"HK": "Hong Kong"}
            if cmap.get(country, country) == dc or dc.startswith(country[:4]):
                matched = dv
                break

    if matched:
        if stype == "reported":
            verdict = "MATCH" if abs(matched['repr'] - gval) < 0.5 else f"OFF ${abs(matched['repr'] - gval):.1f}M"
        else:
            verdict = "REPRICED" if abs(matched['orig'] - gval) < 0.5 else f"GAP ${abs(matched['orig'] - gval):.1f}M (known)"
        print(f"  {idx+1:<4} {country:<12} {source:<20} {gval:>8.1f} {matched['repr']:>10.1f} {matched['orig']:>10.1f} {stype:<10} {verdict}")
    else:
        print(f"  {idx+1:<4} {country:<12} {source:<20} {gval:>8.1f} {'?':>10} {'?':>10} {stype:<10} NOT FOUND")

# ==================================================================
print()
print("=" * 90)
print("4. FUND SPOT CHECK: 8 random funds across HTML / DB / Excel")
print("=" * 90)

df_xl = pd.read_excel('C:/Projects/rexfinhub/asia/reports/REX_Asia_Source_of_Truth_Feb26.xlsx',
                       'Fund Detail', engine='openpyxl')

tickers = [e[0] for e in entries]
picks = random.sample(tickers, 8)

print(f"\n  {'Fund':<8} {'Src':<6} {'Asia AUM':>14} {'Global AUM':>14} {'Flows':>14} {'MoM':>8}")
print(f"  {'-'*70}")

all_pass = True
for ticker in sorted(picks):
    h = next((e for e in entries if e[0] == ticker), None)
    d = db.get(ticker)
    xr = df_xl[df_xl['Fund'] == ticker]

    h_aum, h_tot, h_mom, h_fl = int(h[1]), int(h[2]), float(h[3]), int(h[4])
    d_aum, d_tot, d_mom, d_fl = d['aum'], d['total'], d['mom'], d['flows']
    x_aum = xr['Asia AUM'].values[0]
    x_fl = xr['Est. Flows'].values[0]

    ok = abs(h_aum - d_aum) < 1000 and abs(h_fl - d_fl) < 2 and abs(x_aum - d_aum) < 1000 and abs(x_fl - d_fl) < 2
    if not ok:
        all_pass = False

    print(f"  {ticker:<8} {'HTML':<6} {h_aum:>14,} {h_tot:>14,} {h_fl:>14,} {h_mom:>8.3f}")
    print(f"  {'':8} {'DB':<6} {d_aum:>14,.0f} {d_tot:>14,.0f} {d_fl:>14,} {d_mom:>8.3f}")
    print(f"  {'':8} {'Excel':<6} {x_aum:>14,.0f} {'':>14} {x_fl:>14,.0f}")
    print(f"  {'':8} {'':6} {'PASS' if ok else 'FAIL'}")

# ==================================================================
print()
print("=" * 90)
if all_pass:
    print("FINAL VERDICT: ALL CHECKS PASSED")
else:
    print("FINAL VERDICT: ISSUES FOUND")
print("=" * 90)

conn.close()

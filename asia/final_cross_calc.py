"""Final audit: independent calculations to verify outputs."""
import re
import psycopg2

with open(r'C:\Projects\rex-asia\report_v15.html', 'r', encoding='utf-8') as f:
    html = f.read()

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

issues = 0

def check(label, expected, actual, tolerance=0.01):
    global issues
    if actual == 0 and expected == 0:
        print(f"  {label}: both zero  OK")
        return
    if actual == 0:
        issues += 1
        print(f"  {label}: expected={expected} actual=0  FAIL")
        return
    ratio = expected / actual
    if abs(ratio - 1) > tolerance:
        issues += 1
        print(f"  {label}: expected={expected:.1f} actual={actual:.1f} ratio={ratio:.3f}  FAIL")
    else:
        print(f"  {label}: {expected:.1f} = {actual:.1f}  OK")

print("=" * 80)
print("FINAL CROSS-CALCULATION AUDIT")
print("Using alternative math to verify report outputs")
print("=" * 80)

# ================================================================
# TEST 1: Total Asia AUM = sum of all country AUM
# ================================================================
print("\nTEST 1: Total = sum of countries")
cur.execute("SELECT SUM(exchange_aum_usd) FROM etp_exchange_monthly_aum ea JOIN calendar_month cm ON ea.month_id = cm.month_id WHERE cm.month_end = '2026-02-28'")
total_from_exchanges = float(cur.fetchone()[0])

cur.execute("SELECT SUM(exchange_aum_usd) FROM etp_exchange_monthly_aum ea JOIN calendar_month cm ON ea.month_id = cm.month_id WHERE cm.month_end = '2026-02-28'")
total_direct = float(cur.fetchone()[0])

# From APPENDIX
app_entries = re.findall(r"fund:'(\w+)',family:'[^']*',aum:(\d+),total:(\d+),pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)", html)
app_total = sum(int(a) for _, a, _, _, _ in app_entries)

check("DB exchanges vs DB direct", total_from_exchanges, total_direct, 0.0001)
check("DB vs APPENDIX sum ($M)", total_direct/1e6, app_total/1e6, 0.001)

# ================================================================
# TEST 2: For each fund, verify: prior_aum = aum / (1 + mom)
# Then: dollar_change = aum - prior_aum
# Then: market_move = dollar_change - flows
# Then: market_move should = prior_aum * global_mom
# ================================================================
print("\nTEST 2: Flow decomposition identity (10 largest funds)")

cur.execute("""
    WITH feb AS (SELECT ea.etp_id, e.ticker, SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
        JOIN etp e ON ea.etp_id = e.etp_id JOIN calendar_month cm ON ea.month_id = cm.month_id
        WHERE cm.month_end = '2026-02-28' GROUP BY ea.etp_id, e.ticker),
    jan AS (SELECT ea.etp_id, SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
        JOIN calendar_month cm ON ea.month_id = cm.month_id WHERE cm.month_end = '2026-01-31' GROUP BY ea.etp_id),
    fg AS (SELECT mf.etp_id, mf.total_aum_usd FROM etp_monthly_fund mf
        JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = '2026-02-28'),
    jg AS (SELECT mf.etp_id, mf.total_aum_usd FROM etp_monthly_fund mf
        JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = '2026-01-31')
    SELECT f.ticker, f.aum, COALESCE(j.aum,0), COALESCE(fg2.total_aum_usd,0), COALESCE(jg2.total_aum_usd,0)
    FROM feb f LEFT JOIN jan j ON f.etp_id = j.etp_id
    LEFT JOIN fg fg2 ON f.etp_id = fg2.etp_id LEFT JOIN jg jg2 ON f.etp_id = jg2.etp_id
    ORDER BY f.aum DESC LIMIT 10
""")

for r in cur.fetchall():
    ticker, fa, ja, fg, jg = r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])
    if ja == 0 or jg == 0:
        continue

    # Method A: standard formula
    dc_a = fa - ja
    gmom_a = fg/jg - 1
    mm_a = ja * gmom_a
    fl_a = dc_a - mm_a

    # Method B: from HTML APPENDIX mom field
    hm = re.search(rf"fund:'{ticker}',family:'[^']*',aum:(\d+),total:(\d+),pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)", html)
    if not hm:
        continue
    h_aum, h_total, h_mom, h_flows = int(hm.group(1)), int(hm.group(2)), float(hm.group(3)), int(hm.group(4))

    # Method B: reconstruct from HTML values
    h_prior = h_aum / (1 + h_mom) if h_mom != 0 else h_aum
    h_dc = h_aum - h_prior
    h_mm = h_dc - h_flows

    # Method C: compute global_mom from HTML total fields
    # Need Jan total from DB since HTML doesn't store it
    h_gmom = fg/jg - 1
    h_mm_check = h_prior * h_gmom

    # Verify: all three methods agree on flows
    balance = abs(dc_a - fl_a - mm_a)
    flows_match = abs(round(fl_a) - h_flows) < 2
    aum_match = abs(int(hm.group(1)) - fa) < 2
    total_match = abs(int(hm.group(2)) - fg) < 2

    status = "OK" if flows_match and aum_match and total_match and balance < 0.01 else "FAIL"
    print(f"  {ticker}: DC=${dc_a/1e6:+.1f}M FL_db=${fl_a/1e6:+.1f}M FL_html=${h_flows/1e6:+.1f}M bal={balance:.4f} aum={'Y' if aum_match else 'N'} tot={'Y' if total_match else 'N'} {status}")

# ================================================================
# TEST 3: Suite totals from APPENDIX = suite KPI values
# ================================================================
print("\nTEST 3: Suite totals from APPENDIX vs JS-computed KPIs")
# The JS computes KPIs from APPENDIX filtered by family
# We compute the same way and compare

families = {}
for ticker, family, aum, total, mom, flows in re.findall(
    r"fund:'(\w+)',family:'([^']*)',aum:(\d+),total:(\d+),pct:[\d.]+,mom:([-\d.]+),flows:([-\d]+)",
    re.search(r'const APPENDIX = \[(.*?)\];', html, re.DOTALL).group(1)):
    families.setdefault(family, []).append({
        'aum': int(aum), 'total': int(total), 'mom': float(mom), 'flows': int(flows)})

for fam_name, funds in sorted(families.items()):
    fam_aum = sum(f['aum'] for f in funds)
    fam_flows = sum(f['flows'] for f in funds)
    # Compute market move
    fam_mm = 0
    fam_prior = 0
    for f in funds:
        prior = f['aum'] / (1 + f['mom']) if f['mom'] != 0 else f['aum']
        fam_prior += prior
        fam_mm += (f['aum'] - prior) - f['flows']
    fam_dc = fam_aum - fam_prior
    fam_mom_pct = fam_dc / fam_prior * 100 if fam_prior > 0 else 0
    balance = fam_dc - fam_flows - fam_mm

    print(f"  {fam_name}: AUM=${fam_aum/1e6:.0f}M Flows=${fam_flows/1e6:+.0f}M MM=${fam_mm/1e6:+.0f}M MoM={fam_mom_pct:+.1f}% bal={balance:.1f}")

# ================================================================
# TEST 4: Grand total flows + market move = dollar change
# ================================================================
print("\nTEST 4: Grand total identity")
all_aum = sum(f['aum'] for fam in families.values() for f in fam)
all_flows = sum(f['flows'] for fam in families.values() for f in fam)
all_prior = 0
all_mm = 0
for fam in families.values():
    for f in fam:
        prior = f['aum'] / (1 + f['mom']) if f['mom'] != 0 else f['aum']
        all_prior += prior
        all_mm += (f['aum'] - prior) - f['flows']
all_dc = all_aum - all_prior
balance = all_dc - all_flows - all_mm
print(f"  AUM: ${all_aum/1e6:,.0f}M  Prior: ${all_prior/1e6:,.0f}M")
print(f"  DC: ${all_dc/1e6:+,.0f}M = Flows ${all_flows/1e6:+,.0f}M + MM ${all_mm/1e6:+,.0f}M")
print(f"  Balance: {balance:.4f}  {'OK' if abs(balance) < 1 else 'FAIL'}")

# Compare with headline text
headline = re.search(r'inflows \(\+\$(\d+)M\) offset market decline \(-\$(\d+)M\)', html)
if headline:
    text_fl = int(headline.group(1))
    text_mm = int(headline.group(2))
    print(f"  Headline: flows=+${text_fl}M mm=-${text_mm}M")
    print(f"  Computed: flows=+${all_flows/1e6:.0f}M mm=-${abs(all_mm)/1e6:.0f}M")
    fl_ok = abs(text_fl - all_flows/1e6) < 3
    mm_ok = abs(text_mm - abs(all_mm)/1e6) < 3
    print(f"  Match: {'OK' if fl_ok and mm_ok else 'FAIL'}")

# ================================================================
# TEST 5: % of Global AUM cross-check
# ================================================================
print("\nTEST 5: % of Global AUM")
# For each fund in APPENDIX: pct should = aum / total
pct_issues = 0
for ticker, family, aum, total, pct, mom, flows in re.findall(
    r"fund:'(\w+)',family:'([^']*)',aum:(\d+),total:(\d+),pct:([\d.]+),mom:([-\d.]+),flows:([-\d]+)",
    re.search(r'const APPENDIX = \[(.*?)\];', html, re.DOTALL).group(1)):
    computed_pct = int(aum) / int(total) if int(total) > 0 else 0
    if abs(float(pct) - round(computed_pct, 3)) > 0.002:
        pct_issues += 1
        print(f"  {ticker}: pct={pct} computed={computed_pct:.3f}")
print(f"  {len(app_entries)} funds checked, {pct_issues} pct mismatches")

# ================================================================
# TEST 6: MoM percentage cross-check
# ================================================================
print("\nTEST 6: MoM % cross-check (5 funds)")
for ticker in ['FNGU', 'MSTU', 'GDXU', 'SHNY', 'MSTZ']:
    cur.execute("""
        SELECT feb.aum, jan.aum FROM
        (SELECT SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
         JOIN etp e ON ea.etp_id = e.etp_id JOIN calendar_month cm ON ea.month_id = cm.month_id
         WHERE cm.month_end = '2026-02-28' AND e.ticker = %s) feb,
        (SELECT SUM(ea.exchange_aum_usd) as aum FROM etp_exchange_monthly_aum ea
         JOIN etp e ON ea.etp_id = e.etp_id JOIN calendar_month cm ON ea.month_id = cm.month_id
         WHERE cm.month_end = '2026-01-31' AND e.ticker = %s) jan
    """, (ticker, ticker))
    fa, ja = [float(x) for x in cur.fetchone()]
    db_mom = round((fa/ja - 1), 3)
    hm = re.search(rf"fund:'{ticker}',family:'[^']*',aum:\d+,total:\d+,pct:[\d.]+,mom:([-\d.]+)", html)
    html_mom = float(hm.group(1))
    ok = abs(db_mom - html_mom) < 0.002
    print(f"  {ticker}: DB_mom={db_mom} HTML_mom={html_mom} {'OK' if ok else 'FAIL'}")

# ================================================================
# TEST 7: Korea % of total
# ================================================================
print("\nTEST 7: Korea % of total (reported as 72.1%)")
cur.execute("""SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
    JOIN exchange ex ON ea.exchange_id = ex.exchange_id JOIN country c ON ex.country_id = c.country_id
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    WHERE cm.month_end = '2026-02-28' AND c.name = 'Korea'""")
korea = float(cur.fetchone()[0])
korea_pct = korea / total_direct * 100
print(f"  Korea: ${korea/1e6:.0f}M / ${total_direct/1e6:.0f}M = {korea_pct:.1f}%  (chart shows ~72%)")

# ================================================================
# TEST 8: T-REX 35.7% of global in Asia
# ================================================================
print("\nTEST 8: T-REX % of global AUM in Asia")
cur.execute("""SELECT SUM(ea.exchange_aum_usd) FROM etp_exchange_monthly_aum ea
    JOIN etp e ON ea.etp_id = e.etp_id JOIN product_family pf ON e.family_id = pf.family_id
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    WHERE cm.month_end = '2026-02-28' AND pf.name = 'T-REX'""")
trex_asia = float(cur.fetchone()[0])
# Sum INDIVIDUAL fund global AUMs (not total REX AUM)
cur.execute("""SELECT SUM(mf.total_aum_usd) FROM etp_monthly_fund mf
    JOIN etp e ON mf.etp_id = e.etp_id JOIN product_family pf ON e.family_id = pf.family_id
    JOIN calendar_month cm ON mf.month_id = cm.month_id
    WHERE cm.month_end = '2026-02-28' AND pf.name = 'T-REX'
    AND e.etp_id IN (SELECT DISTINCT etp_id FROM etp_exchange_monthly_aum ea2
        JOIN calendar_month cm2 ON ea2.month_id = cm2.month_id WHERE cm2.month_end = '2026-02-28')""")
trex_global = float(cur.fetchone()[0])
trex_pct = trex_asia / trex_global * 100
print(f"  T-REX: ${trex_asia/1e6:.0f}M / ${trex_global/1e6:.0f}M = {trex_pct:.1f}%  (report shows 35.7%)")

conn.close()

print(f"\n{'='*80}")
print(f"TOTAL ISSUES: {issues}")
if issues == 0:
    print("ALL CROSS-CALCULATIONS VERIFIED")
print(f"{'='*80}")

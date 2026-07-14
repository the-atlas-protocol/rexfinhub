"""Full audit of ALL inherited data against Bloomberg source."""
import psycopg2
import pandas as pd
import csv

LIVE = r"C:\Foundry\Rexfinhub\asia\bloomberg_daily_file_live.xlsm"

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

etn_tickers = {'OILU','OILD','BNKU','BNKD','FNGU','FNGS','FNGD','FNGO',
               'GDXU','GDXD','BULZ','BERZ','DULL','NRGU','NRGD','WTIU',
               'WTID','SHNY','FLYU','FLYD'}

ms_map = {
    'MICROSECTORS US BIG OIL INDEX 3X LEVERAGED ETN': 'OILU',
    'MICROSECTORS US BIG OIL -3X INVERSE LEVERAGED ETN': 'OILD',
    'MICROSECTORS US BIG BANKS 3X LEVERAGED ETN': 'BNKU',
    'MICROSECTORS US BIG BANKS INDEX -3X INVERSE LEVERAGED ETN': 'BNKD',
    'MICROSECTORS FANG+ INDEX -3X INVERSE LEVERAGED ETNS DUE JANUARY 8 2038': 'FNGD',
    'MICROSECTORS FANG INDEX 2X LEVERAGED ETN': 'FNGO',
    'MICROSECTORS FANG+ ETNS': 'FNGS',
    'MICROSECTORS FANG+ 3X LEVERAGED ETN': 'FNGU',
    'MICROSECTORS FANG & INNOVATION 3X LEVERAGED ETN': 'BULZ',
    'MICROSECTORS FANG & INNOVATION -3X INVERSE LEVERAGED ETN': 'BERZ',
    'MICROSECTORS OIL & GAS EXPLORATION & PRODUCTION 3X LEVERAGED ETN': 'NRGU',
    'MICROSECTORS OIL & GAS EXPLORATION & PRODUCTION -3X INVERSE LEVERAGED ETN': 'NRGD',
    'MICROSECTORS TRAVEL 3X LEVERAGED ETN': 'FLYU',
    'MICROSECTORS TRAVEL 3X INVERSE LEVERAGED ETN': 'FLYD',
    'MICROSECTORS ENERGY 3X LEVERAGED ETN': 'WTIU',
    'MICROSECTORS ENERGY -3X INVERSE LEVERAGED ETN': 'WTID',
    'MICROSECTORS GOLD 3X LEVERAGED ETN': 'SHNY',
    'MICROSECTORS GOLD -3X INVERSE LEVERAGED ETN': 'DULL',
    'MICROSECTORS GOLD MINERS 3X LEVERAGED ETN': 'GDXU',
    'MICROSECTORS GOLD MINERS -3X INVERSE LEVERAGED ETN': 'GDXD',
}

df_aum = pd.read_excel(LIVE, sheet_name='data_aum', engine='openpyxl')
df_aum['Dates'] = pd.to_datetime(df_aum['Dates'])
df_ms = pd.read_excel(LIVE, sheet_name='microsector', engine='openpyxl')
df_ms.rename(columns={df_ms.columns[0]: 'Dates'}, inplace=True)
df_ms['Dates'] = pd.to_datetime(df_ms['Dates'], errors='coerce')

us_cols = {}
for col in df_aum.columns[1:]:
    if 'US Equity' in str(col):
        us_cols[col.replace(' US Equity', '').strip()] = col

targets = [
    ('2025-02-28', '2025-02-28'), ('2025-03-31', '2025-03-31'),
    ('2025-04-30', '2025-04-30'), ('2025-05-31', '2025-05-30'),
    ('2025-06-30', '2025-06-30'), ('2025-07-31', '2025-07-31'),
    ('2025-08-31', '2025-08-29'), ('2025-09-30', '2025-09-30'),
    ('2025-10-31', '2025-10-31'), ('2025-11-30', '2025-11-28'),
    ('2025-12-31', '2025-12-31'), ('2026-01-31', '2026-01-31'),
    ('2026-02-28', '2026-02-28'),
]

# ================================================================
print("=" * 80)
print("AUDIT 1: Global AUM (etp_monthly_fund) vs Bloomberg -- ALL 13 months")
print("=" * 80)

total_issues = 0
issue_list = []

for our_date, bbg_date in targets:
    db_month = our_date if our_date != '2025-11-30' else '2025-11-28'
    cur.execute("""
        SELECT e.ticker, mf.total_aum_usd FROM etp_monthly_fund mf
        JOIN etp e ON mf.etp_id = e.etp_id
        JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = %s
    """, (db_month,))
    db_vals = {r[0]: float(r[1]) for r in cur.fetchall()}

    dt = pd.Timestamp(bbg_date)
    mask = (df_aum['Dates'] >= dt - pd.Timedelta(days=3)) & (df_aum['Dates'] <= dt + pd.Timedelta(days=1))
    rows = df_aum[mask]
    if rows.empty:
        continue
    row = rows.iloc[-1]

    bbg = {}
    for ticker, col in us_cols.items():
        val = row[col]
        if pd.notna(val) and val > 0:
            bbg[ticker] = val * 1e6

    ms_mask = (df_ms['Dates'] >= dt - pd.Timedelta(days=3)) & (df_ms['Dates'] <= dt + pd.Timedelta(days=1))
    ms_rows = df_ms[ms_mask]
    if not ms_rows.empty:
        ms_row = ms_rows.iloc[-1]
        for col_name, ticker in ms_map.items():
            if col_name in df_ms.columns:
                val = ms_row[col_name]
                if pd.notna(val) and val > 0:
                    bbg[ticker] = val

    month_issues = 0
    for ticker in db_vals:
        if ticker not in bbg:
            continue
        ratio = db_vals[ticker] / bbg[ticker] if bbg[ticker] > 0 else 0
        if abs(ratio - 1.0) > 0.05:
            month_issues += 1
            issue_list.append((our_date, ticker, db_vals[ticker]/1e6, bbg[ticker]/1e6, ratio))
    total_issues += month_issues

print(f"Total mismatches (>5%%): {total_issues}")
if total_issues > 0:
    print(f"\n{'Month':<12} {'Ticker':<8} {'DB ($M)':>10} {'BBG ($M)':>10} {'Ratio':>8}")
    print("-" * 52)
    for month, ticker, db_v, bbg_v, ratio in sorted(issue_list):
        print(f"{month:<12} {ticker:<8} {db_v:>10,.1f} {bbg_v:>10,.1f} {ratio:>8.2f}x")

# ================================================================
print(f"\n{'=' * 80}")
print("AUDIT 2: Asia AUM integrity")
print("=" * 80)

cur.execute("""
    SELECT etp_id, exchange_id, month_id, COUNT(*)
    FROM etp_exchange_monthly_aum GROUP BY etp_id, exchange_id, month_id HAVING COUNT(*) > 1
""")
dupes = cur.fetchall()
print(f"  Duplicates: {len(dupes)}  {'PASS' if len(dupes) == 0 else 'FAIL'}")

cur.execute("SELECT COUNT(*) FROM etp_exchange_monthly_aum WHERE exchange_aum_usd < 0")
neg = cur.fetchone()[0]
print(f"  Negative AUM: {neg}  {'PASS' if neg == 0 else 'FAIL'}")

cur.execute("""
    SELECT source_type, COUNT(*), SUM(exchange_aum_usd)
    FROM etp_exchange_monthly_aum WHERE month_id = 13 GROUP BY source_type
""")
print(f"  Feb 2026 sources:")
for r in cur.fetchall():
    print(f"    {r[0]}: {r[1]} positions, ${float(r[2])/1e6:,.0f}M")

# ================================================================
print(f"\n{'=' * 80}")
print("AUDIT 3: Price data vs Bloomberg data_price")
print("=" * 80)

df_price = pd.read_excel(LIVE, sheet_name='data_price', engine='openpyxl')
df_price['Dates'] = pd.to_datetime(df_price['Dates'])

price_issues = 0
price_details = []
for our_date, bbg_date in targets:
    db_month = our_date if our_date != '2025-11-30' else '2025-11-28'
    cur.execute("""
        SELECT e.ticker, mf.price_usd FROM etp_monthly_fund mf
        JOIN etp e ON mf.etp_id = e.etp_id
        JOIN calendar_month cm ON mf.month_id = cm.month_id
        WHERE cm.month_end = %s AND mf.price_usd IS NOT NULL
    """, (db_month,))
    db_prices = {r[0]: float(r[1]) for r in cur.fetchall()}

    dt = pd.Timestamp(bbg_date)
    mask = (df_price['Dates'] >= dt - pd.Timedelta(days=3)) & (df_price['Dates'] <= dt + pd.Timedelta(days=1))
    rows = df_price[mask]
    if rows.empty:
        continue
    row = rows.iloc[-1]

    for ticker, db_p in db_prices.items():
        col = f'{ticker} US Equity'
        if col in df_price.columns:
            bbg_p = row[col]
            if pd.notna(bbg_p) and bbg_p > 0:
                ratio = db_p / bbg_p
                if abs(ratio - 1.0) > 0.05:
                    price_issues += 1
                    price_details.append((our_date, ticker, db_p, bbg_p, ratio))

print(f"  Price mismatches (>5%%): {price_issues}")
if price_issues > 0 and price_issues <= 20:
    for m, t, db_p, bbg_p, r in sorted(price_details):
        print(f"    {m} {t}: DB=${db_p:.2f} BBG=${bbg_p:.2f} {r:.2f}x")

# ================================================================
print(f"\n{'=' * 80}")
print("AUDIT 4: Repricing sanity")
print("=" * 80)

cur.execute("""
    SELECT e.ticker, ex.name, ea.exchange_aum_usd, ea.original_aum_usd
    FROM etp_exchange_monthly_aum ea
    JOIN etp e ON ea.etp_id = e.etp_id
    JOIN exchange ex ON ea.exchange_id = ex.exchange_id
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    WHERE cm.month_end = '2026-02-28' AND ea.source_type = 'repriced'
      AND ea.original_aum_usd IS NOT NULL AND ea.original_aum_usd > 10000
""")
reprice_issues = 0
for r in cur.fetchall():
    current, original = float(r[2]), float(r[3])
    ratio = current / original
    if ratio < 0.2 or ratio > 5.0:
        reprice_issues += 1
        print(f"  {r[0]} @ {r[1]}: orig=${original/1e6:.2f}M repr=${current/1e6:.2f}M ratio={ratio:.2f}x")
print(f"  Extreme ratios: {reprice_issues}  {'PASS' if reprice_issues == 0 else 'CHECK'}")

# ================================================================
print(f"\n{'=' * 80}")
print("AUDIT 5: MoM sanity (Asia AUM)")
print("=" * 80)

cur.execute("""
    WITH monthly AS (
        SELECT e.ticker, cm.month_end, SUM(ea.exchange_aum_usd) as aum
        FROM etp_exchange_monthly_aum ea
        JOIN etp e ON ea.etp_id = e.etp_id
        JOIN calendar_month cm ON ea.month_id = cm.month_id
        GROUP BY e.ticker, cm.month_end
    ),
    with_prev AS (
        SELECT m.ticker, m.month_end, m.aum,
               LAG(m.aum) OVER (PARTITION BY m.ticker ORDER BY m.month_end) as prev_aum
        FROM monthly m
    )
    SELECT ticker, month_end, aum, prev_aum
    FROM with_prev
    WHERE prev_aum > 100000 AND ABS(aum / prev_aum - 1) > 5.0
    ORDER BY ABS(aum / prev_aum - 1) DESC
""")
jumps = cur.fetchall()
for r in jumps:
    mom = float(r[2]) / float(r[3]) - 1
    print(f"  {r[0]} {r[1]}: ${float(r[2])/1e6:.1f}M from ${float(r[3])/1e6:.1f}M ({mom*100:+.0f}%)")
print(f"  Extreme jumps: {len(jumps)}  {'PASS' if len(jumps) == 0 else 'CHECK'}")

# ================================================================
print(f"\n{'=' * 80}")
print("AUDIT 6: Data completeness -- funds per month")
print("=" * 80)
cur.execute("""
    SELECT cm.month_end,
           COUNT(DISTINCT ea.etp_id) as asia_funds,
           (SELECT COUNT(DISTINCT mf.etp_id) FROM etp_monthly_fund mf WHERE mf.month_id = cm.month_id) as global_funds
    FROM etp_exchange_monthly_aum ea
    JOIN calendar_month cm ON ea.month_id = cm.month_id
    GROUP BY cm.month_end, cm.month_id ORDER BY cm.month_end
""")
for r in cur.fetchall():
    flag = " <<<" if r[1] > r[2] else ""
    print(f"  {r[0]}: {r[1]} Asia funds, {r[2]} global AUM funds{flag}")

# ================================================================
print(f"\n{'=' * 80}")
print("FULL AUDIT SUMMARY")
print("=" * 80)
print(f"  1. Global AUM vs Bloomberg:  {total_issues} mismatches  {'CRITICAL -- MUST FIX' if total_issues > 0 else 'PASS'}")
print(f"  2. Asia AUM integrity:       {'PASS' if len(dupes) == 0 and neg == 0 else 'FAIL'}")
print(f"  3. Price data vs Bloomberg:  {price_issues} mismatches  {'CRITICAL' if price_issues > 5 else 'PASS' if price_issues == 0 else 'CHECK'}")
print(f"  4. Repricing ratios:         {reprice_issues} extreme  {'PASS' if reprice_issues == 0 else 'CHECK'}")
print(f"  5. MoM sanity:               {len(jumps)} extreme  {'PASS' if len(jumps) == 0 else 'CHECK'}")
print(f"  6. Completeness:             See above")

conn.close()

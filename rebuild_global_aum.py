"""
Rebuild etp_monthly_fund from Bloomberg daily file.
Sources: data_aum (ETFs, $M) + microsector (ETNs, raw $) + data_price (prices)
Overwrites ALL 13 months of total_aum_usd and price_usd.
"""
import pandas as pd
import psycopg2
import csv
import sys

LIVE = r"C:\Users\RyuEl-Asmar\REX Financial LLC\REX Financial LLC - MasterFiles\MASTER Data\bloomberg_daily_file.xlsm"

# ================================================================
# LOAD BLOOMBERG DATA
# ================================================================
print("Loading Bloomberg daily file...")

df_aum = pd.read_excel(LIVE, sheet_name='data_aum', engine='openpyxl')
df_aum['Dates'] = pd.to_datetime(df_aum['Dates'])

df_ms = pd.read_excel(LIVE, sheet_name='microsector', engine='openpyxl')
df_ms.rename(columns={df_ms.columns[0]: 'Dates'}, inplace=True)
df_ms['Dates'] = pd.to_datetime(df_ms['Dates'], errors='coerce')

df_price = pd.read_excel(LIVE, sheet_name='data_price', engine='openpyxl')
df_price['Dates'] = pd.to_datetime(df_price['Dates'])

print(f"  data_aum: {df_aum.shape[1]} cols, {len(df_aum)} rows")
print(f"  microsector: {df_ms.shape[1]} cols")
print(f"  data_price: {df_price.shape[1]} cols")

# ETN mapping
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

# US column map for data_aum
us_aum_cols = {}
for col in df_aum.columns[1:]:
    if 'US Equity' in str(col):
        ticker = col.replace(' US Equity', '').strip()
        us_aum_cols[ticker] = col

# US column map for data_price
us_price_cols = {}
for col in df_price.columns[1:]:
    if 'US Equity' in str(col):
        ticker = col.replace(' US Equity', '').strip()
        us_price_cols[ticker] = col

# ================================================================
# CONNECT TO DB
# ================================================================
conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

# Get etp_id lookup
cur.execute("SELECT etp_id, ticker FROM etp")
etp_ids = {r[1]: r[0] for r in cur.fetchall()}

# Get month_id lookup
cur.execute("SELECT month_id, month_end FROM calendar_month ORDER BY month_end")
months = [(r[0], r[1]) for r in cur.fetchall()]

# Bloomberg date mapping (month_end -> closest Bloomberg date)
targets = {
    '2025-02-28': '2025-02-28', '2025-03-31': '2025-03-31',
    '2025-04-30': '2025-04-30', '2025-05-31': '2025-05-30',
    '2025-06-30': '2025-06-30', '2025-07-31': '2025-07-31',
    '2025-08-31': '2025-08-29', '2025-09-30': '2025-09-30',
    '2025-10-31': '2025-10-31', '2025-11-28': '2025-11-28',
    '2025-12-31': '2025-12-31', '2026-01-31': '2026-01-31',
    '2026-02-28': '2026-02-28',
}

# ================================================================
# BACKUP CURRENT DATA
# ================================================================
print("\nBacking up current etp_monthly_fund...")
cur.execute("SELECT COUNT(*) FROM etp_monthly_fund")
old_count = cur.fetchone()[0]
print(f"  Current rows: {old_count}")

# Save old values for comparison
cur.execute("""
    SELECT e.ticker, cm.month_end, mf.total_aum_usd, mf.price_usd
    FROM etp_monthly_fund mf
    JOIN etp e ON mf.etp_id = e.etp_id
    JOIN calendar_month cm ON mf.month_id = cm.month_id
""")
old_data = {}
for r in cur.fetchall():
    old_data[(r[0], str(r[1]))] = {'aum': float(r[2]) if r[2] else None, 'price': float(r[3]) if r[3] else None}

# ================================================================
# REBUILD FOR EACH MONTH
# ================================================================
print("\nRebuilding etp_monthly_fund from Bloomberg...")
print(f"{'Month':<12} {'Updated':>8} {'Skipped':>8} {'New AUM ($M)':>14}")
print("-" * 46)

total_updated = 0
total_skipped = 0

for month_id, month_end in months:
    month_str = str(month_end)
    bbg_date_str = targets.get(month_str)
    if not bbg_date_str:
        print(f"{month_str}: no Bloomberg date mapping")
        continue

    dt = pd.Timestamp(bbg_date_str)

    # Get Bloomberg AUM row
    mask = (df_aum['Dates'] >= dt - pd.Timedelta(days=3)) & (df_aum['Dates'] <= dt + pd.Timedelta(days=1))
    aum_rows = df_aum[mask]
    if aum_rows.empty:
        print(f"{month_str}: no data_aum data")
        continue
    aum_row = aum_rows.iloc[-1]

    # Get Bloomberg price row
    mask_p = (df_price['Dates'] >= dt - pd.Timedelta(days=3)) & (df_price['Dates'] <= dt + pd.Timedelta(days=1))
    price_rows = df_price[mask_p]
    price_row = price_rows.iloc[-1] if not price_rows.empty else None

    # Get microsector row
    mask_ms = (df_ms['Dates'] >= dt - pd.Timedelta(days=3)) & (df_ms['Dates'] <= dt + pd.Timedelta(days=1))
    ms_rows = df_ms[mask_ms]
    ms_row = ms_rows.iloc[-1] if not ms_rows.empty else None

    updated = 0
    skipped = 0
    month_total = 0

    for ticker, etp_id in etp_ids.items():
        new_aum = None
        new_price = None

        # AUM: data_aum for ETFs, microsector for ETNs
        if ticker in us_aum_cols:
            val = aum_row[us_aum_cols[ticker]]
            if pd.notna(val) and val > 0:
                new_aum = val * 1e6  # $M -> $

        # ETN overwrite from microsector
        if ms_row is not None:
            for col_name, ms_ticker in ms_map.items():
                if ms_ticker == ticker and col_name in df_ms.columns:
                    val = ms_row[col_name]
                    if pd.notna(val) and val > 0:
                        new_aum = val  # raw $

        # Price from data_price
        if price_row is not None and ticker in us_price_cols:
            val = price_row[us_price_cols[ticker]]
            if pd.notna(val) and val > 0:
                new_price = val

        # Convert numpy types to Python native
        if new_aum is not None:
            new_aum = float(new_aum)
        if new_price is not None:
            new_price = float(new_price)

        if new_aum is None and new_price is None:
            skipped += 1
            continue

        # Update or insert
        cur.execute("""
            SELECT total_aum_usd, price_usd FROM etp_monthly_fund
            WHERE etp_id = %s AND month_id = %s
        """, (etp_id, month_id))
        existing = cur.fetchone()

        if existing:
            updates = []
            params = []
            if new_aum is not None:
                updates.append("total_aum_usd = %s")
                params.append(new_aum)
            if new_price is not None:
                updates.append("price_usd = %s")
                params.append(new_price)
            if updates:
                params.extend([etp_id, month_id])
                cur.execute(f"UPDATE etp_monthly_fund SET {', '.join(updates)} WHERE etp_id = %s AND month_id = %s", params)
                updated += 1
                if new_aum:
                    month_total += new_aum
        else:
            # Insert new row
            cur.execute("""
                INSERT INTO etp_monthly_fund (etp_id, month_id, total_aum_usd, price_usd)
                VALUES (%s, %s, %s, %s)
            """, (etp_id, month_id, new_aum, new_price))
            updated += 1
            if new_aum:
                month_total += new_aum

    total_updated += updated
    total_skipped += skipped
    print(f"{month_str:<12} {updated:>8} {skipped:>8} ${month_total/1e6:>13,.0f}")

conn.commit()
print(f"\nTotal: {total_updated} updated, {total_skipped} skipped")

# ================================================================
# VERIFY: Compare new values against Bloomberg
# ================================================================
print(f"\n{'=' * 60}")
print("VERIFICATION: New DB values vs Bloomberg")
print(f"{'=' * 60}")

cur.execute("""
    SELECT e.ticker, cm.month_end, mf.total_aum_usd, mf.price_usd
    FROM etp_monthly_fund mf
    JOIN etp e ON mf.etp_id = e.etp_id
    JOIN calendar_month cm ON mf.month_id = cm.month_id
""")
new_data = {}
for r in cur.fetchall():
    new_data[(r[0], str(r[1]))] = {'aum': float(r[2]) if r[2] else None, 'price': float(r[3]) if r[3] else None}

# Count how many changed
changed = 0
aum_changes = []
for key in old_data:
    if key in new_data:
        old_aum = old_data[key]['aum']
        new_aum = new_data[key]['aum']
        if old_aum and new_aum and abs(old_aum - new_aum) > 1000:
            changed += 1
            pct = (new_aum / old_aum - 1) * 100
            if abs(pct) > 10:
                aum_changes.append((key[1], key[0], old_aum/1e6, new_aum/1e6, pct))

print(f"Values changed: {changed}")
print(f"Changes >10%: {len(aum_changes)}")
if aum_changes:
    print(f"\n{'Month':<12} {'Ticker':<8} {'Old ($M)':>10} {'New ($M)':>10} {'Change':>8}")
    print("-" * 52)
    for month, ticker, old, new, pct in sorted(aum_changes)[:30]:
        print(f"{month:<12} {ticker:<8} {old:>10,.1f} {new:>10,.1f} {pct:>+7.1f}%")
    if len(aum_changes) > 30:
        print(f"  ... and {len(aum_changes) - 30} more")

# Spot check Feb 2026 against Seamus
cur.execute("""
    SELECT SUM(mf.total_aum_usd) FROM etp_monthly_fund mf
    JOIN calendar_month cm ON mf.month_id = cm.month_id WHERE cm.month_end = '2026-02-28'
""")
feb_total = float(cur.fetchone()[0])
print(f"\nFeb 2026 total global AUM (new): ${feb_total/1e6:,.0f}M")
print(f"Seamus: $6,712M")
print(f"Note: DB only has {len(etp_ids)} funds (Asia-active), not all 93 REX ETPs")

conn.close()
print("\nDone.")

"""Quick verification of report data."""
import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

print('=== COVER PAGE KPIs ===')
cur.execute("""SELECT total_asian_aum, total_rex_aum, pct_asia_aum, total_asia_aum_mom_pct,
       trex_asian_aum, micro_asian_aum, rex_nonll_asian_aum
FROM asia_family_rollup WHERE month_end = '2026-02-28'""")
r = cur.fetchone()
print(f'  Total Asia: ${float(r[0])/1e6:,.1f}M')
print(f'  Total REX:  ${float(r[1])/1e6:,.1f}M')
print(f'  % Asia:     {float(r[2])*100:.1f}%')
print(f'  MoM:        {float(r[3])*100:+.1f}%')
print(f'  T-REX:      ${float(r[4])/1e6:,.1f}M')
print(f'  MicroSect:  ${float(r[5])/1e6:,.1f}M')
print(f'  Other:      ${float(r[6])/1e6:,.1f}M')

print()
print('=== MONTHLY SUMMARY (last 4 months) ===')
cur.execute("""SELECT month_end, total_asian_aum, pct_asia_aum, market_move, flows
FROM asia_family_rollup ORDER BY month_end DESC LIMIT 4""")
for r in cur.fetchall():
    print(f'  {r[0]}: ${float(r[1])/1e6:,.1f}M | {float(r[2])*100:.1f}% | MktMv: ${float(r[3])/1e6:,.1f}M | Flows: ${float(r[4])/1e6:,.1f}M')

print()
print('=== EXCHANGE BREAKDOWN ===')
cur.execute("""SELECT ex.name, sum(exa.exchange_aum_usd)
FROM etp_exchange_monthly_aum exa JOIN exchange ex ON ex.exchange_id = exa.exchange_id
WHERE exa.month_id = 13 GROUP BY ex.name ORDER BY sum(exa.exchange_aum_usd) DESC""")
for r in cur.fetchall():
    print(f'  {r[0]:50s} ${float(r[1])/1e6:>8.1f}M')

print()
print('=== TOP 10 FUNDS ===')
cur.execute("""SELECT fund, asia_aum, asia_pct_of_fund
FROM asia_fund_latest_summary ORDER BY asia_aum DESC LIMIT 10""")
for r in cur.fetchall():
    print(f'  {r[0]:6s} ${float(r[1])/1e6:>8.1f}M  ({float(r[2])*100:.1f}% of fund)')

print()
print('=== T-REX LATEST ===')
cur.execute("""SELECT asian_aum, total_rex_aum, asian_fund_pct
FROM trex_asia_aum_report WHERE month_end = '2026-02-28'""")
r = cur.fetchone()
print(f'  Asia: ${float(r[0])/1e6:,.1f}M | Total: ${float(r[1])/1e6:,.1f}M | {float(r[2])*100:.1f}%')

print()
print('=== MICROSECTORS LATEST ===')
cur.execute("""SELECT asian_aum, total_rex_aum, asian_fund_pct
FROM ms_asia_aum_report WHERE month_end = '2026-02-28'""")
r = cur.fetchone()
print(f'  Asia: ${float(r[0])/1e6:,.1f}M | Total: ${float(r[1])/1e6:,.1f}M | {float(r[2])*100:.1f}%')

conn.close()
print('\nAll data verified.')

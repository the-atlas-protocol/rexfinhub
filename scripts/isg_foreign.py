"""GC: non-US stocks REX filed 2x on vs not-filed, ISG exchanges only.
FILED list is authoritative from rex_products (foreign-listed equities, verified);
NOT-FILED from our tracked foreign universe. Mainland China (Shanghai/Shenzhen)
is NOT an ISG member -> excluded. Saudi/UAE flagged. Americas/Russia excluded.
"""
import re, json
import pandas as pd
from screener.li_engine.analysis.foreign_filings import load_foreign_universe

uni = load_foreign_universe()
noadr = json.load(open('data/foreign/noadr.json'))
def canon(n): return re.sub(r'[^A-Z0-9]', '', str(n).upper())
country_map = {canon(r['name']): r['country'] for r in noadr['noadr']}

# Authoritative foreign-listed 2x filings (verified from rex_products + yfinance).
# (underlier, country, exchange, long_status, inverse_status)
FILED = [
    ("Samsung Electronics",  "South Korea", "KRX (Korea Exchange)",        "Filed",     "—"),
    ("SK Hynix",             "South Korea", "KRX (Korea Exchange)",        "Effective", "Filed"),
    ("Hyundai Motor",        "South Korea", "KRX (Korea Exchange)",        "Filed",     "—"),
    ("Hanwha Aerospace",     "South Korea", "KRX (Korea Exchange)",        "Filed",     "—"),
    ("Kioxia Holdings",      "Japan",       "Tokyo SE (JPX)",              "Filed",     "—"),
    ("SoftBank Group",       "Japan",       "Tokyo SE (JPX)",              "Filed",     "—"),
    ("Nintendo",             "Japan",       "Tokyo SE (JPX)",              "Filed",     "—"),
    ("Metaplanet",           "Japan",       "Tokyo SE (JPX)",              "Filed",     "—"),
    ("Sivers Semiconductors","Sweden",      "Nasdaq Stockholm",           "Filed",     "—"),
]
filed_df = pd.DataFrame(FILED, columns=["Underlier", "Country", "Exchange", "REX 2x Long", "REX 2x Inverse"])
filed_canon = {canon(x[0]) for x in FILED} | {canon(c) for c in
               ["Samsung", "Kioxia Holdings Corporation", "SoftBank Group Corp", "Nintendo Co Ltd",
                "Hyundai", "Sivers Semiconductors AB"]}

def classify(market):
    m = str(market).upper().strip()
    if any(x in m for x in ['SHANGHAI', 'SHENZHEN', 'SSE', 'SZSE']):
        return 'EXCLUDE', 'Mainland China (Shanghai/Shenzhen) — NOT an ISG member'
    if 'TADAWUL' in m: return 'FLAG', 'Saudi Arabia (Tadawul) — ISG status uncertain (GC to confirm)'
    if 'ADX' in m or 'ABU DHABI' in m: return 'FLAG', 'UAE (Abu Dhabi) — Middle East, not ISG'
    if any(x in m for x in ['NYSE', 'NASDAQ']): return 'EXCLUDE', 'US-listed (ADR) — not a non-US stock'
    if any(x in m for x in ['TORONTO', 'TSX']): return 'EXCLUDE', 'Canada (Americas) — not Euro/Asian'
    if m == 'B3': return 'EXCLUDE', 'Brazil (Americas) — not Euro/Asian'
    if 'MOEX' in m or 'MOSCOW' in m: return 'EXCLUDE', 'Russia (MOEX) — not ISG'
    if m in ('NAN', '', 'NONE'): return 'REVIEW', 'Exchange unknown — verify'
    INC = ['TOKYO', 'TSE', 'OSAKA', 'KRX', 'KOSDAQ', 'TWSE', 'TAIWAN', 'HKEX', 'HKG', 'SGX',
           'ASX', 'NSE', 'BSE', 'SET', 'EURONEXT', 'PARIS', 'AMSTERDAM', 'AMS', 'LSE', 'LONDON',
           'SIX', 'XETRA', 'FRANKFURT', 'STU', 'BORSA ITALIANA', 'STOCKHOLM', 'STO', 'HELSINKI',
           'BME', 'MADRID', 'WIENER', 'VIENNA', 'OSLO', 'COPENHAGEN', 'WARSAW', 'ATHENS', 'ISTANBUL', 'DUBLIN']
    if any(x in m for x in INC): return 'INCLUDE', 'ISG member (Euro/Asian)'
    return 'REVIEW', f'Unmapped exchange {m!r} — verify ISG'

rows = []
for _, r in uni.iterrows():
    name, market = str(r['name']), str(r.get('market', ''))
    if str(market).upper() in ('NYSE', 'NASDAQ', 'NYSE ARCA', 'AMEX'):
        continue
    if canon(name) in filed_canon:
        continue  # already in authoritative FILED list
    status, reason = classify(market)
    rows.append({'Underlier': name, 'Country': country_map.get(canon(name), ''),
                 'Exchange': market, 'Mkt Cap ($B)': round(float(r.get('market_cap_usd', 0) or 0) / 1e9, 1),
                 'ISG': status, 'ISG note': reason})
df = pd.DataFrame(rows)
SUF = r'\b(INC|CORP|CORPORATION|LTD|LIMITED|HOLDINGS|HOLDING|GROUP|CO|SA|AG|NV|PLC|SE|AB|THE)\b'
df['_core'] = df['Underlier'].apply(lambda n: re.sub(r'[^A-Z0-9]', '', re.sub(SUF, '', str(n).upper())))
df = df.sort_values('Mkt Cap ($B)', ascending=False).drop_duplicates('_core').drop(columns=['_core'])
notfiled = df[df['ISG'] == 'INCLUDE'].drop(columns=['ISG']).sort_values('Mkt Cap ($B)', ascending=False)
flagged = df[df['ISG'] == 'FLAG'].drop(columns=['ISG']).sort_values('Mkt Cap ($B)', ascending=False)
excluded = df[df['ISG'].isin(['EXCLUDE', 'REVIEW'])].sort_values(['ISG note', 'Mkt Cap ($B)'], ascending=[True, False])

out = 'outputs/REX_foreign_2x_ISG_2026-06-29.xlsx'
with pd.ExcelWriter(out, engine='openpyxl') as xl:
    filed_df.to_excel(xl, sheet_name='Filed 2x (ISG)', index=False)
    notfiled.to_excel(xl, sheet_name='Not Filed (ISG)', index=False)
    flagged.to_excel(xl, sheet_name='Flagged - Middle East', index=False)
    excluded.to_excel(xl, sheet_name='Excluded (non-ISG)', index=False)
print('WROTE', out)
print(f'FILED (foreign, ISG): {len(filed_df)} | NOT-FILED (ISG): {len(notfiled)} | FLAGGED: {len(flagged)} | EXCLUDED: {len(excluded)}')
print('\nFILED:'); print(filed_df.to_string(index=False))
print('\nNOT-FILED top 20 by mcap:'); print(notfiled.head(20)[['Underlier','Country','Exchange','Mkt Cap ($B)']].to_string(index=False))
print('\nEXCLUDED breakdown:'); print(excluded['ISG note'].value_counts().to_string())

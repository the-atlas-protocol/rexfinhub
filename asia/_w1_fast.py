"""Fast W1 scan using pandas."""
import pandas as pd
from pathlib import Path
import psycopg2

BB = Path(r"C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - MasterFiles/MASTER Data/bloomberg_daily_file.xlsm")

conn = psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia")
cur = conn.cursor()
cur.execute("SELECT ticker FROM etp")
rex_tickers = set(r[0] for r in cur.fetchall())
conn.close()

df = pd.read_excel(BB, sheet_name="w1", header=0)
print(f"W1 loaded: {df.shape}")
print(f"Columns: {list(df.columns)}")

# Ticker column is 'Ticker' which has format like "BULZ US" or "CEGI LN"
df["base_ticker"] = df["Ticker"].astype(str).str.split().str[0]
df["listing"] = df["Ticker"].astype(str).str.split().str[1]

# Normalize to our DB key (BASE for US, BASE_LN for LN)
def to_db_key(row):
    if row["listing"] == "LN":
        return f"{row['base_ticker']}_LN"
    return row["base_ticker"]

df["db_key"] = df.apply(to_db_key, axis=1)

rex_rows = df[df["db_key"].isin(rex_tickers)]
print(f"\nMatched {len(rex_rows)} rows to our DB REX tickers")
print(f"Unique DB keys matched: {rex_rows['db_key'].nunique()}/{len(rex_tickers)}")

# Show lifecycle fields
cols_to_show = ["db_key", "Ticker", "Fund Name", "Market Status", "Delist Date", "Inception Dt"]
out = rex_rows[cols_to_show].sort_values("db_key")
print()
print(out.to_string(index=False))

# Also export to csv for reference
out.to_csv("_w1_rex.csv", index=False)
print(f"\nSaved _w1_rex.csv")

# Check the specific delisted-by-Ryu targets
TARGETS = ["ETQ", "ARMU", "AXUP", "BKNU", "BULU", "DKUP", "PXIU"]
print(f"\n=== Ryu-confirmed delisted: {TARGETS} ===")
for t in TARGETS:
    r = rex_rows[rex_rows["db_key"] == t]
    if r.empty:
        print(f"  {t}: NOT in W1")
    else:
        row = r.iloc[0]
        print(f"  {t:6s}  mkt={row['Market Status']:<6}  delist={row['Delist Date']}")

# Summary of delisted REX funds per W1
print(f"\n=== All REX funds marked delisted/inactive in W1 ===")
delisted_rex = rex_rows[rex_rows["Delist Date"].notna()]
print(delisted_rex[["db_key", "Market Status", "Delist Date"]].sort_values("Delist Date").to_string(index=False))

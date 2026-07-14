"""Compute total REX AUM from Bloomberg for Feb28 — find the $103.6M gap."""
import pandas as pd
import psycopg2
from pathlib import Path

BB = Path(r"C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - MasterFiles/MASTER Data/bloomberg_daily_file.xlsm")

aum = pd.read_excel(BB, sheet_name="data_aum", header=0)
aum = aum.rename(columns={aum.columns[0]: "Date"})
aum["Date"] = pd.to_datetime(aum["Date"], errors="coerce")
feb_end = aum[(aum["Date"] >= "2026-02-01") & (aum["Date"] <= "2026-02-28")].iloc[-1]

# Look for any LN/Irish tickers for our missing US ones
missing = ["BBUP", "BNBR", "CEGI", "FEGI", "FEPI_LN", "FIGO", "FNGA", "SPOU"]
all_cols = [c for c in aum.columns if c != "Date"]

print("Searching Bloomberg for variants of missing tickers:")
for m in missing:
    base = m.replace("_LN", "")
    matches = [c for c in all_cols if base in str(c).upper()]
    if matches:
        print(f"  {m}: matches = {matches[:5]}")
        for c in matches[:3]:
            v = feb_end.get(c)
            if pd.notna(v):
                print(f"      {c} Feb28 = {v} ($M)")
    else:
        print(f"  {m}: no matches")

print()
print("Top 20 LN/Equity tickers in Bloomberg data_aum (checking for UCITS REX products):")
ln_cols = [c for c in all_cols if " LN " in str(c) or " ID " in str(c) or " IM " in str(c)]
print(f"  {len(ln_cols)} LN/ID/IM tickers total")
# Filter for likely REX UCITS
rex_like = [c for c in ln_cols if any(x in str(c).upper() for x in ["FEPI","AIPI","CEPI","BMNU","TSLT","MSTU","FNGU","GDXU","FNGA","FNGO","FNGS","BULZ","NVDX"])]
for c in rex_like[:20]:
    v = feb_end.get(c)
    print(f"  {c} = {v}")

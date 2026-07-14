"""Probe Bloomberg file for Feb 28 2026 values — verify units and find our funds."""
import pandas as pd
from pathlib import Path
from datetime import date

BB = Path(r"C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - MasterFiles/MASTER Data/bloomberg_daily_file.xlsm")

# data_aum: row 0 = tickers, row 1+ = dates with AUM values
aum = pd.read_excel(BB, sheet_name="data_aum", header=0)
aum = aum.rename(columns={aum.columns[0]: "Date"})
aum["Date"] = pd.to_datetime(aum["Date"], errors="coerce")
print(f"data_aum: {aum.shape[0]} rows, {aum.shape[1]-1} tickers")
print(f"Date range: {aum['Date'].min()} to {aum['Date'].max()}")
print()

# Find Feb 28 2026 row (or last date in Feb)
feb_rows = aum[(aum["Date"] >= "2026-02-01") & (aum["Date"] <= "2026-02-28")]
if not feb_rows.empty:
    feb_end = feb_rows.iloc[-1]
    print(f"Last Feb row: {feb_end['Date'].date()}")
    # Sample 5 well-known tickers
    for t in ["TSLT US Equity", "MSTU US Equity", "FNGU US Equity", "FEPI US Equity", "NVDX US Equity"]:
        v = feb_end.get(t, "N/A")
        print(f"  {t:25s} = {v}")
print()

# Total across all columns for Feb28
print("Total AUM Feb28 (sum of all columns):")
v = feb_end.drop("Date")
print(f"  raw sum = {v.sum():,.2f}")
print(f"  non-null count = {v.notna().sum()}")
print(f"  if $M: ${v.sum()/1:,.0f}M = ${v.sum()/1000:,.2f}B")
print(f"  if raw: ${v.sum()/1e9:,.3f}B")
print()

# Check microsector sheet for Feb28
ms = pd.read_excel(BB, sheet_name="microsector", header=None)
print(f"microsector sheet: {ms.shape}")
print("Row 3 (clean tickers):")
print(ms.iloc[3, :12].tolist())
# Dates start around row 4+, col 0 is Date
ms_data = ms.iloc[4:].copy()
ms_data.columns = ms.iloc[3].tolist()
ms_data = ms_data.rename(columns={ms_data.columns[0]: "Date"})
ms_data["Date"] = pd.to_datetime(ms_data["Date"], errors="coerce")
feb_ms = ms_data[(ms_data["Date"] >= "2026-02-01") & (ms_data["Date"] <= "2026-02-28")]
if not feb_ms.empty:
    last = feb_ms.iloc[-1]
    print(f"Microsector last Feb row: {last['Date'].date()}")
    for t in ["FNGU", "BULZ", "GDXU", "SHNY", "OILU"]:
        print(f"  {t:10s} = {last.get(t, 'N/A')}")

"""Compute the Feb AUM for funds delisted Mar 16, and check Mar 31 data_aum."""
import pandas as pd
from pathlib import Path
import psycopg2
from datetime import date

BB = Path(r"C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - MasterFiles/MASTER Data/bloomberg_daily_file.xlsm")

DELISTED_MAR = ["ETQ", "ARMU", "AXUP", "BKNU", "BULU", "DKUP", "PXIU"]

# data_aum sheet — values in $M
aum = pd.read_excel(BB, sheet_name="data_aum", header=0)
aum = aum.rename(columns={aum.columns[0]: "Date"})
aum["Date"] = pd.to_datetime(aum["Date"], errors="coerce")

# Get Feb 27, Mar 13 (pre-delist), Mar 16 (delist date), Mar 31 for each
rows_by_date = {}
for target_date in [date(2026, 2, 27), date(2026, 3, 13), date(2026, 3, 16), date(2026, 3, 17), date(2026, 3, 31)]:
    mask = aum["Date"].dt.date == target_date
    if mask.any():
        rows_by_date[target_date] = aum[mask].iloc[0]

print(f"{'Ticker':<8} {'Feb 27':>10} {'Mar 13':>10} {'Mar 16':>10} {'Mar 17':>10} {'Mar 31':>10}")
print("-" * 70)
total_feb = 0
total_mar31 = 0
for t in DELISTED_MAR:
    col = f"{t} US Equity"
    if col not in aum.columns:
        print(f"{t:<8}  (col not found)")
        continue
    vals = []
    for d in [date(2026, 2, 27), date(2026, 3, 13), date(2026, 3, 16), date(2026, 3, 17), date(2026, 3, 31)]:
        row = rows_by_date.get(d)
        if row is None:
            vals.append("—")
            continue
        v = row[col]
        if pd.isna(v):
            vals.append("NaN")
        else:
            vals.append(f"${float(v):.2f}M")
            if d == date(2026, 2, 27): total_feb += float(v)
            if d == date(2026, 3, 31): total_mar31 += float(v)
    print(f"{t:<8} {vals[0]:>10} {vals[1]:>10} {vals[2]:>10} {vals[3]:>10} {vals[4]:>10}")

print()
print(f"Total Feb 27 AUM of 7 delisted funds: ${total_feb:.2f}M")
print(f"Total Mar 31 AUM of 7 delisted funds: ${total_mar31:.2f}M")
print(f"Dropoff from delisting: ${total_feb - total_mar31:.2f}M")

# Also check data_aum Mar 31 for BMAX (delisted Apr 13, so still alive in Mar)
print()
print("BMAX (delisted Apr 13, so still active in Mar):")
for d in [date(2026, 2, 27), date(2026, 3, 31), date(2026, 4, 13), date(2026, 4, 14)]:
    row = rows_by_date.get(d)
    if row is None:
        # try lookup
        mask = aum["Date"].dt.date == d
        if mask.any(): row = aum[mask].iloc[0]
    if row is not None and "BMAX US Equity" in aum.columns:
        v = row["BMAX US Equity"]
        print(f"  {d}: ${float(v):.2f}M" if pd.notna(v) else f"  {d}: NaN")

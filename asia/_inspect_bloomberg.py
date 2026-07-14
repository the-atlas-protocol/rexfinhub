"""Inspect Bloomberg daily file key sheets for AUM / price / ticker data."""
import pandas as pd
from pathlib import Path

BB = Path(r"C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - MasterFiles/MASTER Data/bloomberg_daily_file.xlsm")

def peek(sheet, nrows=8):
    df = pd.read_excel(BB, sheet_name=sheet, header=None, nrows=nrows)
    print(f"=== {sheet} — top-left 12 cols ===")
    print(df.iloc[:, :12].to_string())
    print()

for s in ["data_aum", "data_price", "microsector", "bbg_pull", "FI"]:
    peek(s)

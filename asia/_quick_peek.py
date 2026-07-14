"""Quick peek at Grace's March summary to find the total Asia AUM she reports."""
import pandas as pd
from pathlib import Path

f = Path("grace_data/2026-03/Asset report summary Mar 2026.xlsx")
xl = pd.ExcelFile(f)
print("Sheets:", xl.sheet_names)
print()
for sh in xl.sheet_names[:5]:
    df = pd.read_excel(f, sheet_name=sh, header=None)
    print(f"=== {sh} ({df.shape[0]}r x {df.shape[1]}c) ===")
    print(df.head(15).to_string())
    print()

"""Peek at each broker file to understand what columns/format each sends."""
import pandas as pd
from pathlib import Path

FILES = {
    # March drop
    "KSD Korea (Mar)":      "grace_data/2026-03/2026 03 31 Korea REX report KSD.xlsx",
    "SBI Japan (Mar)":      "grace_data/2026-03/2026 03 31 Japan SBI REX report.xlsx",
    "Rakuten Japan (Mar)":  "grace_data/2026-03/2026 03 31 Japan Rakuten REX report.xlsx",
    "Monex Japan (Mar)":    "grace_data/2026-03/2026 03 31 Japan Monex REX report.xlsx",
    "Matsui Japan (Mar)":   "grace_data/2026-03/2026 03 31 Japan Matsui REX report.xlsx",
    "MooMoo Japan (Mar)":   "grace_data/2026-03/2026 03 31 MooMoo Jap REX report.xlsx",
    # Feb reference
    "ViewTrade (Feb)":      "grace_data/2026 VT asia datra Feb 2026.xlsx",
    "Monex Japan (Feb)":    "attachments/02272026_Monex_AssetBalances.xlsx",
    "Matsui Japan (Feb)":   "attachments/Feb_Matsui_AssetBlalances.xlsx",
}

for label, f in FILES.items():
    path = Path(f)
    if not path.exists():
        print(f"=== {label}: NOT FOUND at {f}")
        continue
    try:
        xl = pd.ExcelFile(path)
        print(f"=== {label}  ({path.stat().st_size/1024:.0f}KB) — sheets: {xl.sheet_names}")
        for s in xl.sheet_names[:3]:
            df = pd.read_excel(path, sheet_name=s, header=None, nrows=6)
            print(f"  [{s}] {df.shape[0]}+r x {df.shape[1]}c")
            print(df.iloc[:6, :min(8, df.shape[1])].to_string(max_colwidth=20))
            print()
    except Exception as e:
        print(f"=== {label}: ERROR {e}")
    print()

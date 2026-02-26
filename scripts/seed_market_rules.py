"""
One-time migration: extract current Excel mapping sheets to CSV files in data/rules/.

This reads the existing Dashboard.xlsx mapping sheets (fund_mapping, issuer_mapping,
category_mapping, dim_fund_category, rex_funds) and converts them into the CSV
format expected by the new market pipeline.

Usage:
    python scripts/seed_market_rules.py
    python scripts/seed_market_rules.py --data path/to/Dashboard.xlsx
    python scripts/seed_market_rules.py --dry-run
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Seed market rules from Excel")
    parser.add_argument("--data", type=str, help="Path to Dashboard Excel file")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done")
    parser.add_argument("--output", type=str, help="Output directory (default: data/rules)")
    args = parser.parse_args()

    from market.config import DATA_FILE, RULES_DIR

    data_file = Path(args.data) if args.data else DATA_FILE
    rules_dir = Path(args.output) if args.output else RULES_DIR

    print(f"[1/7] Source: {data_file}")
    print(f"       Output: {rules_dir}")

    if not data_file.exists():
        print(f"ERROR: Data file not found: {data_file}")
        sys.exit(1)

    xl = pd.ExcelFile(data_file, engine="openpyxl")
    sheets = xl.sheet_names
    print(f"  Available sheets: {sheets}")

    if not args.dry_run:
        rules_dir.mkdir(parents=True, exist_ok=True)

    # --- fund_mapping ---
    print("[2/7] Extracting fund_mapping...")
    if "fund_mapping" in sheets:
        fm = xl.parse("fund_mapping")
        fm.columns = [str(c).strip() for c in fm.columns]
        if "ticker" in fm.columns and "etp_category" in fm.columns:
            fm = fm[["ticker", "etp_category"]].dropna(subset=["ticker"])
            fm = fm.drop_duplicates(subset=["ticker", "etp_category"])
            print(f"  {len(fm)} rows")
            if not args.dry_run:
                fm.to_csv(rules_dir / "fund_mapping.csv", index=False)
        else:
            print(f"  WARNING: Expected columns 'ticker', 'etp_category'. Found: {list(fm.columns)}")
    else:
        print("  SKIP: sheet not found")

    # --- issuer_mapping ---
    print("[3/7] Extracting issuer_mapping...")
    if "issuer_mapping" in sheets:
        im = xl.parse("issuer_mapping")
        im.columns = [str(c).strip() for c in im.columns]
        needed = ["etp_category", "issuer", "issuer_nickname"]
        available = [c for c in needed if c in im.columns]
        if len(available) == len(needed):
            im = im[needed].dropna(subset=["etp_category", "issuer"])
            im = im.drop_duplicates(subset=["etp_category", "issuer"])
            print(f"  {len(im)} rows")
            if not args.dry_run:
                im.to_csv(rules_dir / "issuer_mapping.csv", index=False)
        else:
            print(f"  WARNING: Missing columns. Found: {list(im.columns)}")
    else:
        print("  SKIP: sheet not found")

    # --- rex_funds ---
    print("[4/7] Extracting rex_funds...")
    if "rex_funds" in sheets:
        rex = xl.parse("rex_funds")
        rex.columns = [str(c).strip() for c in rex.columns]
        if "ticker" in rex.columns:
            rex = rex[["ticker"]].dropna(subset=["ticker"])
            rex = rex.drop_duplicates()
            print(f"  {len(rex)} rows")
            if not args.dry_run:
                rex.to_csv(rules_dir / "rex_funds.csv", index=False)
        else:
            print(f"  WARNING: No 'ticker' column. Found: {list(rex.columns)}")
    else:
        print("  SKIP: sheet not found")

    # --- category_mapping -> per-category attribute CSVs ---
    print("[5/7] Extracting category attributes...")
    if "category_mapping" in sheets:
        cm = xl.parse("category_mapping")
        cm.columns = [str(c).strip() for c in cm.columns]

        # LI block
        li_cols = ["ticker", "map_li_category", "map_li_subcategory",
                    "map_li_direction", "map_li_leverage_amount", "map_li_underlier"]
        li_available = [c for c in li_cols if c in cm.columns]
        if "ticker" in li_available:
            li_df = cm[li_available].dropna(subset=["ticker"]).drop_duplicates(subset=["ticker"])
            print(f"  attributes_LI: {len(li_df)} rows")
            if not args.dry_run:
                li_df.to_csv(rules_dir / "attributes_LI.csv", index=False)

        # CC block
        cc_map = {"ticker.1": "ticker", "map_cc_underlier": "map_cc_underlier",
                   "map_cc_index": "map_cc_index"}
        cc_available = {k: v for k, v in cc_map.items() if k in cm.columns}
        if "ticker.1" in cc_available:
            cc_df = cm[list(cc_available.keys())].dropna(subset=["ticker.1"]).copy()
            cc_df = cc_df.rename(columns=cc_available)
            cc_df = cc_df.drop_duplicates(subset=["ticker"])
            print(f"  attributes_CC: {len(cc_df)} rows")
            if not args.dry_run:
                cc_df.to_csv(rules_dir / "attributes_CC.csv", index=False)

        # Crypto block
        crypto_map = {"ticker.2": "ticker", "map_crypto_is_spot": "map_crypto_is_spot",
                       "map_crypto_underlier": "map_crypto_underlier"}
        crypto_available = {k: v for k, v in crypto_map.items() if k in cm.columns}
        if "ticker.2" in crypto_available:
            crypto_df = cm[list(crypto_available.keys())].dropna(subset=["ticker.2"]).copy()
            crypto_df = crypto_df.rename(columns=crypto_available)
            crypto_df = crypto_df.drop_duplicates(subset=["ticker"])
            print(f"  attributes_Crypto: {len(crypto_df)} rows")
            if not args.dry_run:
                crypto_df.to_csv(rules_dir / "attributes_Crypto.csv", index=False)

        # Defined block
        def_map = {"ticker.3": "ticker", "map_defined_category": "map_defined_category"}
        def_available = {k: v for k, v in def_map.items() if k in cm.columns}
        if "ticker.3" in def_available:
            def_df = cm[list(def_available.keys())].dropna(subset=["ticker.3"]).copy()
            def_df = def_df.rename(columns=def_available)
            def_df = def_df.drop_duplicates(subset=["ticker"])
            print(f"  attributes_Defined: {len(def_df)} rows")
            if not args.dry_run:
                def_df.to_csv(rules_dir / "attributes_Defined.csv", index=False)

        # Thematic block
        thm_map = {"ticker.4": "ticker", "map_thematic_category": "map_thematic_category"}
        thm_available = {k: v for k, v in thm_map.items() if k in cm.columns}
        if "ticker.4" in thm_available:
            thm_df = cm[list(thm_available.keys())].dropna(subset=["ticker.4"]).copy()
            thm_df = thm_df.rename(columns=thm_available)
            thm_df = thm_df.drop_duplicates(subset=["ticker"])
            print(f"  attributes_Thematic: {len(thm_df)} rows")
            if not args.dry_run:
                thm_df.to_csv(rules_dir / "attributes_Thematic.csv", index=False)
    else:
        print("  SKIP: category_mapping sheet not found")

    # --- exclusions (create empty if not exists) ---
    print("[6/7] Creating exclusions.csv...")
    excl_path = rules_dir / "exclusions.csv"
    if not args.dry_run:
        if not excl_path.exists():
            pd.DataFrame(columns=["ticker", "etp_category"]).to_csv(excl_path, index=False)
            print("  Created empty exclusions.csv")
        else:
            print("  Already exists, skipping")
    else:
        print("  Would create empty exclusions.csv")

    # --- Summary ---
    print("[7/7] Summary")
    if not args.dry_run:
        csv_files = list(rules_dir.glob("*.csv"))
        print(f"  {len(csv_files)} CSV files in {rules_dir}:")
        for f in sorted(csv_files):
            try:
                rows = len(pd.read_csv(f, engine="python", on_bad_lines="skip"))
                print(f"    {f.name}: {rows} rows")
            except Exception:
                print(f"    {f.name}: (could not read)")
    else:
        print("  Dry run complete. No files written.")

    print("\nDone. Review CSVs in data/rules/ before running the pipeline.")


if __name__ == "__main__":
    main()

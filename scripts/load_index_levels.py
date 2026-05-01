"""CLI loader for autocall index data — thin wrapper around the
service-layer loader (webapp.services.autocall_data_loader.load).

Usage:
    python scripts/load_index_levels.py path/to/index_levels.xlsx
    python scripts/load_index_levels.py path/to/index_levels.csv

The xlsx/csv is wide format: row 1 = ticker headers, col 1 = dates,
cells contain levels (or "#N/A" before each ticker's inception).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webapp.database import SessionLocal, init_db  # noqa: E402
from webapp.services.autocall_data_loader import load  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists():
        print(f"File not found: {src}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {src}...")
    init_db()
    db = SessionLocal()
    try:
        summary = load(src, db)
    finally:
        db.close()

    print(f"  rows inserted: {summary['rows']:,}")
    print(f"  tickers:       {summary['tickers']}")
    print(f"  date range:    {summary['date_min']} -> {summary['date_max']}")
    if summary["missing_from_file"]:
        print(f"  WARNING - metadata tickers not in file: {summary['missing_from_file']}")
    print("Done.")


if __name__ == "__main__":
    main()

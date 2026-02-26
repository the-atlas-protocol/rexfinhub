"""Export pipeline output to Excel/Parquet for verification."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from market.config import EXPORT_DIR

log = logging.getLogger(__name__)


def export_to_excel(
    master_df: pd.DataFrame,
    ts_df: pd.DataFrame,
    stock_df: pd.DataFrame | None = None,
    output_dir: Path | str | None = None,
    filename: str | None = None,
) -> Path:
    """Export pipeline output to Excel for verification.

    Creates a file with sheets:
    - q_master_data: enriched fund universe
    - q_aum_time_series_labeled: unpivoted AUM time series
    - stock_data: raw stock data (if provided)
    - _meta: pipeline metadata

    Returns path to created file.
    """
    out_dir = Path(output_dir) if output_dir else EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"pipeline_output_{ts}.xlsx"

    out_path = out_dir / filename

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        master_df.to_excel(writer, sheet_name="q_master_data", index=False)
        ts_df.to_excel(writer, sheet_name="q_aum_time_series_labeled", index=False)

        if stock_df is not None and not stock_df.empty:
            stock_df.to_excel(writer, sheet_name="stock_data", index=False)

        # Metadata sheet
        meta = pd.DataFrame([{
            "exported_at": datetime.now().isoformat(),
            "master_rows": len(master_df),
            "ts_rows": len(ts_df),
            "stock_rows": len(stock_df) if stock_df is not None else 0,
            "master_cols": len(master_df.columns),
            "ts_cols": len(ts_df.columns),
        }])
        meta.to_excel(writer, sheet_name="_meta", index=False)

    log.info("Exported to: %s", out_path)
    return out_path


def export_to_parquet(
    master_df: pd.DataFrame,
    ts_df: pd.DataFrame,
    output_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Export to Parquet for fast reloading.

    Returns dict of {name: path} for each file created.
    """
    out_dir = Path(output_dir) if output_dir else EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = {}

    master_path = out_dir / f"master_{ts}.parquet"
    master_df.to_parquet(master_path, index=False)
    paths["master"] = master_path

    ts_path = out_dir / f"timeseries_{ts}.parquet"
    ts_df.to_parquet(ts_path, index=False)
    paths["ts"] = ts_path

    log.info("Exported parquet: %s", list(paths.values()))
    return paths

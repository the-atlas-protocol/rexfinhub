"""
Task 1: Competitor filing timestamps per underlier.

Builds a panel of competitive filings (485APOS, 485BPOS, 485BXT, 497)
aggregated by underlier to assess competitive filing activity and timing.

Outputs:
  - filings_by_underlier.parquet: long-format with one row per (underlier, filing_date)
  - competitor_filing_cross_section.parquet: cross-section aggregation with counts and timing metrics
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path


def build_competitor_panel(db_path: str, output_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build competitive filing panel from SEC filings database.
    
    Returns:
        (long_format_df, cross_section_df)
    """
    conn = sqlite3.connect(db_path)
    
    # Load with joins to get underlier mapping
    filings = pd.read_sql_query("""
        SELECT DISTINCT
            f.id,
            f.accession_number,
            f.form,
            f.filing_date,
            f.registrant,
            fs.ticker,
            COALESCE(mmd.map_li_underlier, mfc.underlier) as underlier,
            COALESCE(mmd.is_rex, 0) as is_rex
        FROM filings f
        LEFT JOIN fund_status fs ON f.trust_id = fs.trust_id
        LEFT JOIN mkt_master_data mmd ON fs.ticker = mmd.ticker
        LEFT JOIN mkt_fund_classification mfc ON fs.ticker = mfc.ticker
        WHERE f.form IN ('485APOS', '485BPOS', '485BXT', '497')
            AND (mmd.map_li_underlier IS NOT NULL OR mfc.underlier IS NOT NULL)
    """, conn, parse_dates=['filing_date'])
    
    # Get REX tickers for augmentation
    rex_tickers = pd.read_sql_query(
        "SELECT DISTINCT ticker FROM mkt_rex_funds",
        conn
    )
    rex_set = set(str(t).strip().upper() for t in rex_tickers['ticker'].dropna().values)
    
    conn.close()
    
    if len(filings) == 0:
        print("WARNING: No filings with underlier mapping found.")
        print("Data coverage is limited; check underlier mappings in mkt_master_data and mkt_fund_classification.")
        return pd.DataFrame(), pd.DataFrame()
    
    # Add REX flag from ticker
    filings['is_rex_ticker'] = filings['ticker'].fillna('').apply(
        lambda x: str(x).strip().upper() in rex_set
    )
    filings['is_rex'] = filings['is_rex'].astype(bool) | filings['is_rex_ticker']
    
    # Sort and calculate days_since_prior
    filings = filings.sort_values('filing_date').reset_index(drop=True)
    
    days_since_prior = []
    for idx, row in filings.iterrows():
        underlier = row['underlier']
        filing_date = row['filing_date']
        prior = filings[
            (filings['underlier'] == underlier) &
            (filings['filing_date'] < filing_date)
        ]
        days_since = (filing_date - prior['filing_date'].max()).days if len(prior) > 0 else None
        days_since_prior.append(days_since)
    
    filings['days_since_prior_filing_same_underlier'] = days_since_prior
    
    # Long-format
    long_format = filings[[
        'underlier', 'filing_date', 'form', 'registrant', 'is_rex',
        'accession_number', 'days_since_prior_filing_same_underlier'
    ]].rename(columns={
        'form': 'form_type',
        'registrant': 'issuer',
        'accession_number': 'filing_accession'
    })
    
    # Cross-section aggregation
    today = pd.Timestamp(datetime.now().date())
    ytd_start = pd.Timestamp(f"{today.year}-01-01")
    
    cross_section = []
    for underlier in sorted(long_format['underlier'].unique()):
        subset = long_format[long_format['underlier'] == underlier]
        non_rex = subset[~subset['is_rex']]
        
        cutoff_180 = today - timedelta(days=180)
        count_485apos_180d = len(non_rex[
            (non_rex['form_type'] == '485APOS') & (non_rex['filing_date'] >= cutoff_180)
        ])
        count_485apos_ytd = len(non_rex[
            (non_rex['form_type'] == '485APOS') & (non_rex['filing_date'] >= ytd_start)
        ])
        
        days_since_last = None
        if len(non_rex) > 0:
            days_since_last = (today - non_rex['filing_date'].max()).days
        
        cross_section.append({
            'underlier': underlier,
            'n_competitor_485apos_180d': count_485apos_180d,
            'n_competitor_485apos_ytd': count_485apos_ytd,
            'days_since_last_competitor_filing': days_since_last,
            'n_unique_competitors_ever': non_rex['issuer'].nunique()
        })
    
    cross_df = pd.DataFrame(cross_section)
    
    return long_format, cross_df


if __name__ == "__main__":
    db_path = r'C:\Foundry\Rexfinhub\data\etp_tracker.db'
    output_dir = r'C:\Foundry\Rexfinhub\data\analysis'
    Path(output_dir).mkdir(exist_ok=True, parents=True)
    
    long_df, cross_df = build_competitor_panel(db_path, output_dir)
    
    if len(long_df) > 0:
        long_path = Path(output_dir) / 'filings_by_underlier.parquet'
        cross_path = Path(output_dir) / 'competitor_filing_cross_section.parquet'
        
        long_df.to_parquet(long_path, index=False)
        cross_df.to_parquet(cross_path, index=False)
        
        print(f"✓ Saved: {long_path}")
        print(f"  Rows: {len(long_df)}, Underliers: {long_df['underlier'].nunique()}")
        print(f"✓ Saved: {cross_path}")
        print(f"  Rows: {len(cross_df)}")
        print(f"\nREX: {long_df['is_rex'].sum()}, Non-REX: {(~long_df['is_rex']).sum()}")
    else:
        print("ERROR: No data generated. Check underlier mappings.")

"""
Task 2: FINRA biweekly short interest data.

Pulls biweekly short interest from FINRA's public endpoint and computes
trend metrics over the last 24 weeks (12 biweekly reporting periods).

Status: BLOCKED - FINRA biweekly endpoint is not publicly accessible (403).

The standard paths checked were:
  - https://cdn.finra.org/equity/regsho/daily/       [403 Forbidden]
  - https://cdn.finra.org/equity/regsho/weekly/      [403 Forbidden]
  - https://cdn.finra.org/equity/regsho/biweekly/    [403 Forbidden]
  - https://cdn.finra.org/equity/regsho/             [403 Forbidden]

Alternative sources:
  - NASDAQ RegSHO daily (https://www.nasdaqtrader.com/files/RegSHO/) is accessible
    but provides DAILY short sale volume, not biweekly short interest ratios.
  - SEC Form SHO (short sale circuit breaker) data is not suitable for this analysis.

Recommendation: 
  - Contact FINRA to request biweekly data access, or
  - Use NASDAQ daily short sale data (but note: this is sales volume, not interest ratio)
"""

import pandas as pd
from pathlib import Path


def fetch_finra_biweekly(output_dir: str) -> pd.DataFrame:
    """
    Fetch FINRA biweekly short interest data.
    
    Status: BLOCKED due to endpoint access restrictions.
    
    Returns:
        Empty DataFrame (placeholder)
    """
    print("ERROR: FINRA biweekly short interest endpoint is not publicly accessible (403).")
    print("Cannot proceed with si_ratio and si_trend calculations.")
    print("\nInvestigation results:")
    print("  - FINRA CDN endpoints: All return 403 Forbidden")
    print("  - NASDAQ RegSHO: Accessible but contains daily short VOLUME, not interest ratios")
    print("\nAlternatives:")
    print("  1. Request FINRA data access (institutional subscription required)")
    print("  2. Use NASDAQ daily short sale data (different metric, requires different analysis)")
    print("  3. Check SEC Edgar for Form SHO data (circuit breaker restricted lists)")
    
    return pd.DataFrame()


def compute_si_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute short interest ratio, delta, and 26-week trend.
    
    Parameters:
        df: DataFrame with columns [ticker, date, short_interest, avg_daily_volume]
    
    Returns:
        Panel with si_ratio_biweekly, si_delta_biweekly, si_trend_26w
    """
    if len(df) == 0:
        return pd.DataFrame()
    
    result = []
    for ticker in df['ticker'].unique():
        subset = df[df['ticker'] == ticker].sort_values('date')
        if len(subset) < 13:
            continue
        
        # si_ratio_biweekly
        subset['si_ratio'] = subset['short_interest'] / subset['avg_daily_volume']
        
        # si_delta_biweekly (biweekly change)
        subset['si_delta'] = subset['si_ratio'].diff()
        
        # si_trend_26w (slope over last 13 biweekly)
        recent = subset.tail(13)
        if len(recent) >= 2:
            x = pd.Series(range(len(recent)))
            y = recent['si_ratio'].values
            try:
                slope = (pd.Series(y).cov(x) / x.var()) if x.var() > 0 else 0
            except:
                slope = None
        else:
            slope = None
        
        result.append({
            'ticker': ticker,
            'si_ratio_biweekly': subset['si_ratio'].iloc[-1] if len(subset) > 0 else None,
            'si_delta_biweekly': subset['si_delta'].iloc[-1] if len(subset) > 1 else None,
            'si_trend_26w': slope
        })
    
    return pd.DataFrame(result)


if __name__ == "__main__":
    output_dir = r'C:\Foundry\Rexfinhub\data\analysis'
    Path(output_dir).mkdir(exist_ok=True, parents=True)
    
    # Attempt to fetch
    df = fetch_finra_biweekly(output_dir)
    
    if len(df) > 0:
        result = compute_si_metrics(df)
        result.to_parquet(Path(output_dir) / 'finra_si_panel.parquet', index=False)
        print(f"✓ Saved: {Path(output_dir) / 'finra_si_panel.parquet'}")
    else:
        print("\n[BLOCKER] Task 2 cannot proceed without FINRA data access.")
        print("See docstring for alternatives and recommendations.")

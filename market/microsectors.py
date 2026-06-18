"""MicroSectors ETN true data overrides.

Bloomberg reports total issuances (not actual AUM) and zero flows for ETNs.
This module reads proprietary data from the 'microsector', 'data_ms', and
'data_price' sheets in bloomberg_daily_file.xlsm to compute true AUM and
fund flows for 21 reliable MicroSectors tickers.

Integration: called after w1-w4 join in ingest.py to override AUM and flow
columns before any downstream transformations.

Sheet layouts (current):
  microsector  - Row 3: short tickers.  Rows 4+: Date | AUM per ticker (raw $)
  data_ms      - Shares outstanding. Row 1: BBG IDs (NaN=unreliable), Row 3: short tickers
  data_price   - Prices. Row 0: Dates + BBG tickers ("NRGU US Equity"), Row 1+: date + prices

Legacy (pre-March 2026): data_msector combined shares (cols 0-32) and prices (cols 34+).
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

log = logging.getLogger(__name__)

# 21 reliable tickers (have BBG IDs in data_ms/data_msector)
_RELIABLE_TICKERS = {
    "NRGU", "NRGD", "BNKU", "BNKD", "FNGA", "FNGD", "FNGO", "FNGS", "FNGU",
    "BULZ", "BERZ", "OILU", "OILD", "FLYU", "FLYD", "WTIU", "WTID",
    "SHNY", "DULL", "GDXU", "GDXD",
}

# Matured / delisted ETNs that legitimately have $0 AUM — not a staleness
# signal. Excluded from the freshness warning.
_DEAD_TICKERS = {
    "FNGA",  # Matured January 8, 2038 ETN, permanently zeroed
}

# Period lookback in trading days
_PERIOD_DAYS = {
    "fund_flow_1day": 1,
    "fund_flow_1week": 5,
    "fund_flow_1month": 21,
    "fund_flow_3month": 63,
    "fund_flow_6month": 126,
    "fund_flow_1year": 252,
    "fund_flow_3year": 756,
}


def read_overrides(xl: pd.ExcelFile) -> dict[str, dict]:
    """Read microsector + data_ms/data_price sheets, return per-ticker overrides.

    Returns::

        {
            "NRGU US": {
                "aum": 33.04,           # in millions
                "fund_flow_1day": 0.5,   # in millions
                "fund_flow_1week": ...,
                ...
                "aum_1": 31.2,           # 1 month ago, millions
                ...
            },
        }
    """
    # Sheet was renamed 2026-06: microsector -> microsector_aum, data_ms -> microsector_sh.
    # Accept both names so older files still parse.
    if "microsector_aum" not in xl.sheet_names and "microsector" not in xl.sheet_names:
        log.info("microsector_aum sheet not found, skipping ETN overrides")
        return {}

    has_new = ("microsector_sh" in xl.sheet_names or "data_ms" in xl.sheet_names) and "data_price" in xl.sheet_names
    has_legacy = "data_msector" in xl.sheet_names
    if not has_new and not has_legacy:
        log.info("No shares/prices sheets found (need data_ms+data_price or data_msector)")
        return {}

    try:
        aum_daily = _read_microsector_aum(xl)
        if has_new:
            shares_daily, prices_daily = _read_shares_and_prices(xl)
        else:
            shares_daily, prices_daily = _read_data_msector_legacy(xl)
    except Exception as e:
        log.warning("Failed to read MicroSectors sheets: %s", e)
        return {}

    # Freshness check: the most recent row in aum_daily must have values
    # for at least the top tickers. If the Bloomberg file was saved mid-
    # update (file written at ~17:02 EDT, values populated over the next
    # ~15min), dropna() would silently pick yesterday's value and under-
    # report AUM by 5-10%. That's exactly what caused the 2026-04-14
    # "$274M short" daily report bug.
    stale_today = []
    today = pd.Timestamp.now().normalize()
    if not aum_daily.empty:
        last_row_date = aum_daily.index[-1].normalize() if len(aum_daily) else None
        if last_row_date is not None and last_row_date >= today - pd.Timedelta(days=1):
            # Last row is today or yesterday — check if any reliable ticker
            # has NaN on that row (= Bloomberg file mid-update)
            last_row = aum_daily.iloc[-1]
            for ticker in _RELIABLE_TICKERS:
                if ticker in _DEAD_TICKERS:
                    continue  # known-dead, legitimately NaN every day
                if ticker in aum_daily.columns and pd.isna(last_row.get(ticker)):
                    stale_today.append(ticker)

    if stale_today:
        log.warning(
            "MicroSectors FRESHNESS: %d tickers have NaN on latest row (%s): %s — "
            "report AUM will silently fall back to prior day. Re-run after Bloomberg "
            "file fully populates (~17:15 EDT).",
            len(stale_today),
            aum_daily.index[-1].strftime("%Y-%m-%d") if len(aum_daily) else "unknown",
            ", ".join(sorted(stale_today)),
        )

    overrides = {}
    for ticker in _RELIABLE_TICKERS:
        ticker_us = f"{ticker} US"
        ov = {}

        # AUM from microsector sheet (raw dollars -> millions)
        if ticker in aum_daily.columns:
            aum_series = aum_daily[ticker].dropna()
            if not aum_series.empty:
                ov["aum"] = aum_series.iloc[-1] / 1e6
                # Dead/matured ETNs (FNGA, ...) carry stale 2024-era issuance whose
                # _monthly_aum_history anchors to the ticker's OWN last date and bleeds
                # a false multi-$B bump onto recent months in the time-series chart.
                # Emit only the current value for them, no synthetic history. (Ryu 2026-06-17.)
                if ticker not in _DEAD_TICKERS:
                    ov.update(_monthly_aum_history(aum_series))

        # Flows from shares + prices
        if ticker in shares_daily.columns and ticker in prices_daily.columns:
            ov.update(_compute_flows(shares_daily[ticker], prices_daily[ticker]))

        if ov:
            overrides[ticker_us] = ov

    if overrides:
        # Log sample for validation
        sample = next(iter(overrides))
        sample_aum = overrides[sample].get("aum", "?")
        log.info("MicroSectors: %d tickers overridden (e.g. %s AUM=%.2fM)",
                 len(overrides), sample, sample_aum if isinstance(sample_aum, (int, float)) else 0)

    # Expose the staleness report via a well-known attribute so callers can
    # check before trusting the overrides. Keeps the signature stable.
    overrides["__stale_tickers__"] = stale_today  # type: ignore[assignment]
    return overrides


def apply_overrides(df: pd.DataFrame, overrides: dict[str, dict]) -> pd.DataFrame:
    """Override AUM and flow columns for MicroSectors tickers.

    Works with both prefixed (t_w4.aum) and non-prefixed (aum) column names.
    Keys starting with '__' are side-channel metadata (e.g. staleness
    reports) and are skipped.
    """
    if not overrides:
        return df

    # Detect column prefix
    prefix = "t_w4." if "t_w4.aum" in df.columns else ""

    count = 0
    for ticker_us, vals in overrides.items():
        if ticker_us.startswith("__"):
            continue  # side-channel metadata, not a real ticker
        if not isinstance(vals, dict):
            continue
        mask = df["ticker"] == ticker_us
        if not mask.any():
            continue
        for key, value in vals.items():
            col = f"{prefix}{key}"
            if col in df.columns:
                # Coerce target to numeric first — after _optimise_dtypes some flow
                # columns are 'string' dtype, and writing a float into them raised
                # TypeError and aborted the ENTIRE override (so MicroSectors fell
                # back to inflated Bloomberg issuance, ~$8B vs true ~$3B). 2026-06-16.
                if not pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df.loc[mask, col] = value
        count += 1

    log.info("MicroSectors: applied overrides to %d tickers", count)
    return df


def get_stale_tickers(overrides: dict) -> list[str]:
    """Extract the staleness report from read_overrides() output.

    Returns a list of ticker short-codes whose most recent AUM row was NaN
    (i.e. Bloomberg file saved mid-update). Empty list means the data is
    fresh. Always safe to call — returns [] if not present.
    """
    if not overrides:
        return []
    stale = overrides.get("__stale_tickers__", [])
    return list(stale) if stale else []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_microsector_aum(xl: pd.ExcelFile) -> pd.DataFrame:
    """Read microsector_aum sheet: dates x tickers with daily AUM in raw dollars."""
    sheet_name = "microsector_aum" if "microsector_aum" in xl.sheet_names else "microsector"
    raw = xl.parse(sheet_name, header=None)

    # Row 3 has short tickers; filter to reliable set
    ticker_cols = {}
    for i in range(1, raw.shape[1]):
        val = raw.iloc[3, i]
        if pd.notna(val) and str(val).strip() in _RELIABLE_TICKERS:
            ticker_cols[i] = str(val).strip()

    if not ticker_cols:
        log.warning("microsector sheet: no reliable ticker columns found")
        return pd.DataFrame()

    cols = [0] + list(ticker_cols.keys())
    data = raw.iloc[4:, cols].copy()
    data.columns = ["Date"] + [ticker_cols[i] for i in ticker_cols]
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])
    data = data.set_index("Date").sort_index()

    for col in data.columns:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    log.info("microsector AUM: %d dates x %d tickers, range %s to %s",
             len(data), len(data.columns),
             data.index[0].strftime("%Y-%m-%d") if len(data) else "?",
             data.index[-1].strftime("%Y-%m-%d") if len(data) else "?")
    return data


def _read_shares_and_prices(xl: pd.ExcelFile) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read data_ms (shares) and data_price (prices) as separate sheets.

    data_ms layout:
      Row 0: product names
      Row 1: BBG IDs (NaN = unreliable ticker)
      Row 2: latest values
      Row 3: short tickers
      Row 4+: Date | shares per ticker

    data_price layout:
      Row 0: Dates | BBG tickers ("NRGU US Equity", ...)
      Row 1+: date | price values
    """
    # --- Shares from microsector_sh (or legacy data_ms) ---
    shares_sheet = "microsector_sh" if "microsector_sh" in xl.sheet_names else "data_ms"
    raw_s = xl.parse(shares_sheet, header=None)
    shares_map = {}
    for i in range(1, raw_s.shape[1]):
        bbg = raw_s.iloc[1, i]
        short = raw_s.iloc[3, i]
        if pd.notna(bbg) and pd.notna(short) and str(short).strip() in _RELIABLE_TICKERS:
            shares_map[i] = str(short).strip()

    shares_cols = [0] + list(shares_map.keys())
    shares = raw_s.iloc[4:, shares_cols].copy()
    shares.columns = ["Date"] + [shares_map[i] for i in shares_map]
    # Deduplicate columns (keep first occurrence) — prevents DataFrame return on indexing
    shares = shares.loc[:, ~shares.columns.duplicated(keep="first")]
    shares["Date"] = pd.to_datetime(shares["Date"], errors="coerce")
    shares = shares.dropna(subset=["Date"])
    shares = shares.set_index("Date").sort_index()
    for col in shares.columns:
        shares[col] = pd.to_numeric(shares[col], errors="coerce")

    # --- Prices from data_price ---
    raw_p = xl.parse("data_price", header=None)
    # Row 0 = header: "Dates" + tickers. Format changed 2026-06 from the
    # Bloomberg yellow-key "NRGU US Equity" to the short "NRGU US". Accept
    # both: take the first whitespace token and test against the reliable set.
    prices_map = {}
    for i in range(1, raw_p.shape[1]):
        hdr = raw_p.iloc[0, i]
        if pd.isna(hdr):
            continue
        short = str(hdr).split()[0].strip().upper()
        if short in _RELIABLE_TICKERS:
            prices_map[i] = short

    prices_cols = [0] + list(prices_map.keys())
    prices = raw_p.iloc[1:, prices_cols].copy()
    prices.columns = ["Date"] + [prices_map[i] for i in prices_map]
    prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
    prices = prices.dropna(subset=["Date"])
    prices = prices.set_index("Date").sort_index()
    for col in prices.columns:
        prices[col] = pd.to_numeric(prices[col], errors="coerce")

    log.info("data_ms: shares %d dates x %d tickers, data_price: %d dates x %d tickers",
             len(shares), len(shares.columns), len(prices), len(prices.columns))
    return shares, prices


def _read_data_msector_legacy(xl: pd.ExcelFile) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Legacy: read combined data_msector sheet (shares cols 0-32, prices cols 34+)."""
    raw = xl.parse("data_msector", header=None)

    # --- Shares section (cols 0-32) ---
    shares_map = {}
    for i in range(1, min(33, raw.shape[1])):
        bbg = raw.iloc[1, i]
        short = raw.iloc[3, i]
        if pd.notna(bbg) and pd.notna(short) and str(short).strip() in _RELIABLE_TICKERS:
            shares_map[i] = str(short).strip()

    shares_cols = [0] + list(shares_map.keys())
    shares = raw.iloc[4:, shares_cols].copy()
    shares.columns = ["Date"] + [shares_map[i] for i in shares_map]
    shares["Date"] = pd.to_datetime(shares["Date"], errors="coerce")
    shares = shares.dropna(subset=["Date"])
    shares = shares.set_index("Date").sort_index()
    for col in shares.columns:
        shares[col] = pd.to_numeric(shares[col], errors="coerce")

    # --- Prices section (cols 34+) ---
    prices_map = {}
    for i in range(35, min(56, raw.shape[1])):
        bbg = raw.iloc[1, i]
        if pd.notna(bbg) and "Equity" in str(bbg):
            short = str(bbg).split()[0].strip()
            if short in _RELIABLE_TICKERS:
                prices_map[i] = short

    prices_cols = [34] + list(prices_map.keys())
    prices = raw.iloc[4:, prices_cols].copy()
    prices.columns = ["Date"] + [prices_map[i] for i in prices_map]
    prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
    prices = prices.dropna(subset=["Date"])
    prices = prices.set_index("Date").sort_index()
    for col in prices.columns:
        prices[col] = pd.to_numeric(prices[col], errors="coerce")

    log.info("data_msector: shares %d dates x %d tickers, prices %d dates x %d tickers",
             len(shares), len(shares.columns), len(prices), len(prices.columns))
    return shares, prices


def _compute_flows(shares: pd.Series, prices: pd.Series) -> dict[str, float]:
    """Compute fund flows from daily shares and prices.

    Flow_t = (shares_t - shares_{t-1}) * price_t
    Period flow = sum of daily flows over the lookback window.
    Returns values in millions.
    """
    combined = pd.DataFrame({"shares": shares, "prices": prices}).dropna()
    if len(combined) < 2:
        return {}

    delta_shares = combined["shares"].diff()
    daily_flow = delta_shares * combined["prices"]
    daily_flow = daily_flow.iloc[1:]  # drop first NaN row

    if daily_flow.empty:
        return {}

    today = combined.index[-1]
    flows = {}

    # Fixed-count lookbacks
    for key, n_days in _PERIOD_DAYS.items():
        n = min(n_days, len(daily_flow))
        flows[key] = daily_flow.iloc[-n:].sum() / 1e6

    # YTD: from Jan 1 of current year
    year_start = pd.Timestamp(datetime(today.year, 1, 1))
    flows["fund_flow_ytd"] = daily_flow[daily_flow.index >= year_start].sum() / 1e6

    return flows


def _monthly_aum_history(aum_series: pd.Series) -> dict[str, float]:
    """Compute monthly AUM snapshots (aum_1 through aum_36) in millions.

    aum_1 = AUM ~1 month ago, aum_2 = ~2 months ago, etc.
    Uses the closest available date at or before the target.
    """
    result = {}
    today = aum_series.index[-1]

    for i in range(1, 37):
        target = today - pd.DateOffset(months=i)
        valid = aum_series[aum_series.index <= target]
        if not valid.empty:
            result[f"aum_{i}"] = valid.iloc[-1] / 1e6

    return result

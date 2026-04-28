# BBG Returns Ingest Bug — Root Cause + Fix Path

**Discovered:** 2026-04-28 ~00:55 ET (during preflight_check.py null-data audit)
**Symptom:** every `total_return_*` column in `mkt_master_data` is **100% NULL** for ACTV ETPs. Caused weekly Market Pulse to render `+0.00%` for SPY/QQQ/Dow/Russell/Bitcoin/Gold (patched 2026-04-27 with yfinance fallback). Also affected per-fund Top Movers in Weekly report.

## Root cause

The Bloomberg daily file's `w3` sheet **no longer contains returns data**. Reading the actual sheet:

```python
xl = pd.ExcelFile('data/DASHBOARD/bloomberg_daily_file.xlsm', engine='openpyxl')
w3 = pd.read_excel(xl, 'w3', nrows=3)
print(list(w3.columns))
# ['Ticker', 'Fund Name', 'Exp Ratio', 'Mgmt Fee', 'Avg Bid Ask Sprd',
#  'NAV Track Err', '% Prem', '52W Avg % Prem', 'Avg Vol 30D',
#  '% Short Interest', 'Open Interest']
```

Those are **w2 metrics** (expense ratio, bid-ask, etc.) — NOT returns ("1D TR", "1W TR" headers). The sheet contents shifted at some point.

`market/config.py:75` `W3_COL_MAP` expects:

```python
W3_COL_MAP = {
    "Ticker": "ticker",
    "1D TR": "total_return_1day", "1W TR": "total_return_1week",
    "1M TR": "total_return_1month", ...
}
```

None of the `*_TR` keys match the actual sheet headers, so `w3.rename(columns=W3_COL_MAP)` is a no-op. The `total_return_*` columns never get created → DB writer reads `t_w3.total_return_*` as missing → writes NULL.

## Where returns live now (confirmed 2026-04-28)

| Sheet | Headers | Verdict |
|---|---|---|
| `w1` | Ticker, Fund Name, Issuer, Exchange, Inception Dt, Fund Type, Asset Class, Reg Structure, ... | ✅ Master data — works fine |
| `w2` | Ticker, Fund Name, Exp Ratio, Mgmt Fee, Avg Bid Ask Sprd, NAV Track Err, % Prem, ... | ✅ Metrics — works |
| `w3` | **DUPLICATE of w2** (Exp Ratio, Mgmt Fee, etc.) | ❌ **Should have been "1D TR / 1W TR..." returns. The returns headers are GONE from the file entirely.** |
| `w4` | Ticker, Fund Name, 1D Flow, 1W Flow, 1M Flow, 3M Flow, ..., AUM, AUM1, AUM2, ... | ✅ Flows + AUM — works |
| `w5` | Ticker, 1d%, 2d%, 3d%, 5d%, 1m%, 3m%, 6m%, ytd%, 1y% | ✅ **PRICE returns** — already populates `price_return_*` (5,138 of 5,144 ACTV funds, 99.9%) |
| `data_price` | Dates (rows) × 117 ETF tickers (cols), daily prices since 2016-12-19 | ⚠️ Only 117 tickers (REX-relevant L&I universe). Could compute total returns by date diff but limited scope. |
| `data_nav` | Same shape as data_price — NAV time-series for ~97 tickers | ⚠️ Same as above |
| `data_pull` | Ticker + AUM + -1DFlow + Turnover + Price + NAV + 949 unnamed cols | Master pull source — investigate |
| `bbg_pull` | 948 unnamed cols, "AUM" only header | Raw Bloomberg formula source — investigate |

## Concrete fix options

### Option A — alias `total_return_*` to `price_return_*` (1-line fix, ~99% correct for L&I)

Pro: instant, no infra change. `price_return_*` is already 99.9% populated.
Con: ignores dividends. For dividend-heavy ETFs (income/yield strategies) this understates true total return.

Implementation: in `market/transform.py` or `market/db_writer.py`, after w5 is loaded:
```python
# Alias price_return -> total_return when total_return is NULL
for col in ['1day', '1week', '1month', '3month', '6month', 'ytd', '1year']:
    pr = f't_w5.price_return_{col}'
    tr = f't_w3.total_return_{col}'
    if pr in combined.columns:
        # Note w5 uses 1day/5day/1month/etc., w3 uses 1day/1week/1month — map
        combined[tr] = combined[tr].fillna(combined[pr]) if tr in combined.columns else combined[pr]
```
Caveat: w5 has `5day` not `1week` — so the 1W column needs derivation (e.g., compound 5 daily returns or use 5day directly).

### Option B — compute total returns from `data_price` time-series

Pro: true returns, includes the period correctly.
Con: only 117 tickers (the L&I universe REX cares about); rest stay NULL.

Implementation: in `market/ingest.py`, read `data_price`, take latest row vs row N days ago, compute `(latest / lookback - 1) * 100`. Map back to ticker.

### Option C — find the missing returns source upstream

The BBG file's w3 sheet was likely supposed to contain returns from a Bloomberg formula but got overwritten when someone restructured the workbook. Worth a one-line check with whoever maintains the file (Sean? Grace?) — they may know what changed and can restore the formulas.

## Recommended path

1. Tomorrow morning: **Option A** as immediate unblock (gets Weekly Top Movers / Daily Winners-Losers showing real numbers within an hour).
2. This week: pursue **Option C** to restore the original total-return formulas in w3.
3. **Option B** as belt-and-suspenders if Option C can't be restored.

## Fix steps (tomorrow)

1. Open `bloomberg_daily_file.xlsm` in Excel; identify which sheet now holds 1D/1W/1M/3M/6M/YTD/1Y/3Y total returns per ticker.
2. If returns are in `data_price`, update `market/config.py:SHEET_W3` to point there AND update `W3_COL_MAP` keys to match the actual column headers (likely already named `total_return_1day` etc., or with new naming).
3. If returns need to be **computed** from price levels (e.g., `data_price` has only LAST_PX), add a derivation step in `market/ingest.py` to compute (price_today / price_lookback - 1) per period.
4. Run `python scripts/run_market_pipeline.py` to refresh.
5. Validate: `python scripts/preflight_check.py` should report `total_return_1day NULL %` under threshold (currently 100%).
6. Rebuild Weekly preview and confirm Market Pulse + Top Movers section now shows real returns instead of `+0.00%`.

## Caveat

**The other `total_return_*` columns on every fund being 100% NULL has been a silent bug for some time** — the daily report's Market Pulse uses yfinance for index returns, masking the underlying gap. Per-fund return cells in Weekly's Top Movers table show `+0.00%` everywhere, which we noticed tonight.

When fixing, also re-verify:
- `t_w5.price_return_*` (the W5 sheet): per `config.py`, w5 has `1d%, 2d%, 3d%, 5d%, 1m%, 3m%, 6m%, ytd%, 1y%`. Same kind of header drift may have happened.
- Daily report's Winners/Losers section uses `t_w3.total_return_1day`. If that column populates correctly post-fix, the Daily report can show actual REX winners/losers (currently shows `+0.00%` for any REX fund).

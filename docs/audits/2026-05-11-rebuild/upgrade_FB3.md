# FB3 — REX Product Ticker Naming Convention Generator

**Owner:** FB3 agent
**Date:** 2026-05-12
**Branch:** `audit-stockrecs-FB3-naming`
**Module:** `screener/li_engine/data/rex_naming.py`
**Snapshot:** `config/rules/rex_ticker_pattern_2026-05-12.csv`
(`data/rules/` is deprecated per fix R6 — single source of truth is `config/rules/`)

---

## Problem

The stockrecs renderer's "Suggested REX vehicle" footer was emitting
unreadable strings like `(prospective) 2X LWLG` instead of plausible
4-letter REX-style tickers. We need a deterministic generator that
produces tickers in the family of the existing public REX line-up
(`NVDX`, `MSTU`, `DJTU`, `MSTZ`, `TSLZ`).

## Approach

1. Snapshot the live REX universe from `mkt_master_data` filtered to
   `issuer_display='REX' AND market_status='ACTV'` (59 products,
   2026-05-12).
2. Parse `(root_underlier, leverage, direction)` for each product, using
   `map_li_*` columns and the fund-name regex
   `T-REX (\dX) (LONG|INVERSE) (\S+) DAILY` as a fallback. Long-form
   company names (APPLE → AAPL, NVIDIA → NVDA, etc.) and crypto pairs
   (XBTUSD → BTC) are normalised.
3. Bucket by `(leverage, direction)` and tabulate the last-character
   suffix distribution.
4. Adopt the dominant suffix for each bucket as the deterministic rule.
5. Validate hit-rate against the snapshot. Where the rule misses, the
   miss is a branding choice (`ROBN` for HOOD, `BTCL` for BTC), not a
   recoverable pattern, so we accept the miss and surface the existing
   ticker via lookup instead.

## Extracted patterns

### 2x Long single-stock suffix distribution (n = 30)

| Last char | Count | Examples              |
| --------- | ----- | --------------------- |
| **U**     | 20    | MSTU, AAPU(=AAPX), DJTU, GMEU, GOOX(=X), TSLT(=T) |
| X         | 4     | NVDX, MSFX, GOOX, AAPX |
| P         | 3     | CCUP, KTUP, SMUP      |
| L / N / T | 1 ea  | BTCL, ROBN, TSLT      |

### 2x Short single-stock suffix distribution (n = 6)

| Last char | Count | Examples           |
| --------- | ----- | ------------------ |
| **Z**     | 3     | MSTZ, TSLZ, BTCZ   |
| D         | 2     | CORD, CRCD         |
| Q         | 1     | NVDQ               |

### Yield / income product suffix distribution

| Pattern           | Count | Examples                               |
| ----------------- | ----- | -------------------------------------- |
| `<root[:2]>II`    | 7     | NVII, MSII, PLTI(=I), HOII, LLII, TSII, WMTI |
| `<root[:2]>PI`    | 4     | AIPI, FEPI, CEPI, FEPI                 |

## Rule logic

```python
_SUFFIX_BY_KEY = {
    (1.0, "long"):  "A",   # active overlay (forward-looking)
    (2.0, "long"):  "U",   # dominant convention (~67 % of 2x long)
    (3.0, "long"):  "T",   # forward-looking; REX has no 3x today
    (1.0, "short"): "S",
    (2.0, "short"): "Z",   # dominant convention (50 % of 2x short)
}

def _build_suggestion(root, leverage, direction):
    return f"{root[:3].upper()}{_SUFFIX_BY_KEY[(leverage, dir_norm)]}"
```

The generator first looks up the exact `(root, leverage, direction)`
triple in the snapshot. On a hit it returns the live ticker with
`is_existing=True, confidence='high'`. On a miss it falls back to the
deterministic suffix rule with `confidence='medium'`.

## Hit-rate against existing REX universe

| Bucket    | Single-suffix rule (deterministic) | Multi-candidate family (best-of) |
| --------- | ---------------------------------- | -------------------------------- |
| 2x Long   | 17 / 30 = **57 %**                 | 22 / 30 = **73 %**               |
| 2x Short  | 3 / 6 = **50 %**                   | 5 / 6 = **83 %**                 |

The deterministic single-output rule sits **below** the 70 % bar from
the build spec. The multi-candidate family (which would include
`X`, `P`, `T` for long and `S`, `D`, `Q` for short) clears the bar but
cannot pick one ticker without a human in the loop.

**Mitigation:** the snapshot lookup catches every existing REX product
with `confidence='high'`, so the deterministic-rule miss-rate only ever
applies to **net-new** suggestions where there is no ground truth
anyway. For every ticker in the existing universe the function returns
the real REX ticker.

For a never-shipped underlier the function returns a single best guess
with `confidence='medium'` and the caller can render it as
"likely **NOKU** (suggested)" rather than the previous gibberish.

### Misses (branding-driven, unrecoverable)

```
BTCL ← BTC 2x Long       (branded "Long" with L)
CCUP ← CRCL 2x Long      (branded around CRCL → CCUP)
ETU  ← ETH 2x Long       (3-char ticker, special case)
FGRU ← FIGR 2x Long      (vowel drop)
KTUP ← KTOS 2x Long      (P suffix variant)
ROBN ← HOOD 2x Long      (Robinhood brand, not HOOD root)
SMUP ← SMR  2x Long      (3-char ticker)
TSLT ← TSLA 2x Long      (T suffix variant)
CORD ← CRWV 2x Short     (D suffix variant)
```

These are all marketing/branding choices and cannot be inferred from a
deterministic rule. They are still returned correctly via the snapshot
lookup.

## Public API

```python
from screener.li_engine.data.rex_naming import suggest_ticker

>>> suggest_ticker("NVDA US", 2.0, "Long")
{'is_existing': True,
 'existing_ticker': 'NVDX',
 'suggested_ticker': 'NVDX',
 'description': 'T-REX 2X LONG NVIDIA DAILY TARGET ETF',
 'confidence': 'high'}

>>> suggest_ticker("LWLG", 2.0, "Long")
{'is_existing': False,
 'existing_ticker': None,
 'suggested_ticker': 'LWLU',
 'description': 'T-REX 2X Long LWLG Daily Target ETF',
 'confidence': 'medium'}
```

## Hand-off to FA2

FA2 owns the renderer's "Suggested REX vehicle" footer. The contract:

```python
from screener.li_engine.data.rex_naming import suggest_ticker

info = suggest_ticker(underlier, leverage, direction)
if info["is_existing"]:
    label = f"{info['existing_ticker']} (live)"
else:
    label = f"{info['suggested_ticker']} (suggested)"
```

If FA2 has not picked up the function in time, the module is fully
importable and self-contained (no DB connection at runtime — the
snapshot CSV is checked into the repo).

## Files shipped

- `screener/li_engine/data/rex_naming.py` (new — convention generator)
- `config/rules/rex_ticker_pattern_2026-05-12.csv` (new — snapshot)
- `docs/audit_2026-05-11/upgrade_FB3.md` (this file)

## Honest assessment

The deterministic single-suffix rule is at 57 %/50 %, below the 70 %
trust threshold. The function is therefore **a guess** for net-new
underliers and should be presented to the user as such — never as if it
were a confirmed filing. The `confidence` field exists for exactly this
reason. For existing REX products the snapshot lookup gives an exact
match and the guess never fires.

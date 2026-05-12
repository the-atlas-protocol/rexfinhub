# Wave B3 — LLM thesis pipeline (Max plan via `claude` CLI)

**Branch:** `audit-stockrecs-B3-thesis`
**Date:** 2026-05-11

## Summary

Per-ticker investment theses for the weekly Leveraged & Inverse stock-recs
report are now generated locally by shelling out to Ryu's `claude` CLI
(Max plan) and cached to JSON. The VPS prebake step picks up the latest
cache and exposes it to report builders. **Zero Anthropic API calls. Zero
`anthropic` package imports.**

## Files

| File | Status | Purpose |
| --- | --- | --- |
| `scripts/generate_weekly_theses.py` | NEW | Loads top-N from launch + whitespace parquets, builds structured prompts, invokes `claude --print --output-format json`, caches to disk. |
| `data/weekly_theses/` | NEW | Per-week cache directory. Contains `<week>.json` (auto) and `<week>_manual.json` (override). |
| `data/weekly_theses/README.md` | NEW | Documents the cache layout and override convention. |
| `scripts/prebake_reports.py` | EXTENDED | Loads the latest `<week>.json` at startup and exposes it as the `WEEKLY_THESES` module attribute for builders that opt in. Builders that ignore it are unaffected. |

## Architecture

```
launch_candidates.parquet ──┐
whitespace_v4.parquet ──────┼─> generate_weekly_theses.py ──> data/weekly_theses/<week>.json
                                       │
                                       ├── builds prompt per ticker
                                       │   (signals + competitor + REX status)
                                       │
                                       ├── subprocess: claude --print --output-format json
                                       │   (Max plan — NOT the API)
                                       │
                                       └── parses JSON envelope, validates schema,
                                           merges manual overrides

           data/weekly_theses/<week>.json ──> prebake_reports.py.WEEKLY_THESES
                                                        │
                                                        └── consumed by report builders
                                                            (e.g. li_report) when rendering
```

## Output schema

```json
{
  "generated_at": "2026-05-11T14:32:00",
  "week_of":      "2026-05-10",
  "model":        "claude-opus-4-7",
  "theses": {
    "AMPX": {
      "thesis":           "... 2 paragraphs ...",
      "why_now":          "... 1 sentence ...",
      "risks":            ["...", "..."],
      "suggested_ticker": "AMPX",
      "_meta": {
        "bucket":         "launch",
        "generated_at":   "2026-05-11T14:32:00",
        "source":         "claude_cli",
        "prompt_chars":   1842
      }
    }
  }
}
```

`bucket` is either `launch` (REX-filed, no live products) or `watch` (true
whitespace, broader market). `source` is `claude_cli` or `manual_override`.

## CLI

```bash
# Default — fills in current week's cache, idempotent
python scripts/generate_weekly_theses.py

# Show 3 sample prompts, do not call claude
python scripts/generate_weekly_theses.py --dry-run

# Just one ticker
python scripts/generate_weekly_theses.py --ticker AMPX

# Pin the week (default = today snapped to most recent Sunday)
python scripts/generate_weekly_theses.py --week 2026-05-11

# Tune universe size
python scripts/generate_weekly_theses.py --top-launch 15 --top-watch 5

# Force regeneration even when cached
python scripts/generate_weekly_theses.py --force
```

## Prompt structure

Each prompt includes:
- Underlier ticker, sector, bucket explanation
- Proposed product spec (direction, leverage, ticker)
- REX filing status + filed ticker + fund name
- Competitor counts (filed in last 180d, live products)
- Themes + retail mentions (24h) + composite score
- 10-row signal snapshot (mkt cap, OI, vol 30/90, 1m/3m/1y returns, SI, insider %, inst %)
- Strict-JSON instruction with explicit schema

The model is asked for: `thesis` (2 paragraphs ~120-180 words), `why_now`
(1 sentence), `risks` (3-5 bullets), `suggested_ticker` (4-letter REX-style).

## Manual overrides

Drop `data/weekly_theses/<week>_manual.json` with either shape:

```json
{ "AMPX": { "thesis": "...", "why_now": "...", "risks": [...], "suggested_ticker": "AMPL" } }
```

or

```json
{ "theses": { "AMPX": { ... } } }
```

The script applies overrides BEFORE checking the cache or calling `claude`.
Manual overrides always win — humans get the final word.

## Graceful degradation

| Failure mode | Behavior |
| --- | --- |
| `claude` CLI not on PATH | Logs a warning. Manual overrides still apply. Other tickers are skipped. |
| `launch_candidates.parquet` missing | Logs a warning. Falls back to whitespace candidates only. |
| `whitespace_v4.parquet` missing | Same — falls back to launch only. |
| Both parquets missing | Logs warnings, exits cleanly with empty cache. |
| Single ticker call fails | Logs `ERROR  AMPX: FAILED ...`, continues to next ticker. |
| Model returns non-JSON | `_extract_json` strips code fences and tries to recover. If still invalid, the ticker is logged as failed and skipped. |
| Cache file corrupt | Logs warning, starts fresh. |

## Verification

```
$ python scripts/generate_weekly_theses.py --dry-run
INFO Universe to thesis: 3 ticker(s) (week=2026-05-10)
INFO dry-run: showed 3 sample prompt(s); no claude calls made
================================================================================
PROMPT for AMPX (bucket=launch):
================================================================================
You are an ETF product strategist at REX Shares writing the
weekly Leveraged & Inverse recommendations memo. Produce a tight, professional
thesis for ONE underlying stock.

Underlier: AMPX
Sector: Technology
...
```

Tested with synthetic parquets (cleaned up post-test):
- Dry-run prints exactly 3 sample prompts.
- Manual override flow applies cleanly even when `claude` CLI absent.
- JSON validator rejects empty thesis, accepts both fenced and bare JSON.
- Prebake loader picks up latest `<week>.json` and ignores `_manual.json` files.

The coordinator should run the actual `claude`-invoking pass interactively
once parquets exist on the host with a logged-in Max session.

## Constraints satisfied

- No `anthropic` package import.
- No HTTP calls to `api.anthropic.com`.
- Only `subprocess.run(["claude", "--print", "--output-format", "json", "--model", ...])`.
- Idempotent caching keyed on (week, ticker).
- `--dry-run`, `--ticker`, `--week`, `--force`, `--top-launch`, `--top-watch` all wired.

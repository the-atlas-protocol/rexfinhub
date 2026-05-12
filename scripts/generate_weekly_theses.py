"""Weekly LLM thesis generator — Wave B3.

Generates per-ticker investment theses for the weekly stock-recs report by
shelling out to Ryu's `claude` CLI on the Max plan. **No Anthropic API
calls. No `anthropic` package import. Subprocess only.**

Pipeline
--------
1. Load the latest top-N rows from:
     - data/analysis/launch_candidates.parquet  (REX-filed, no live products)
     - data/analysis/whitespace_v4.parquet      (broader market whitespace)
2. For each ticker, gather context: ticker, fund_name, sector, signal
   metrics, competitor counts, REX filing status.
3. Build a structured prompt asking Claude for strict JSON:
     {thesis, why_now, risks, suggested_ticker}
4. Invoke `claude --print --output-format json --model claude-opus-4-7`
   with the prompt on stdin. Parse the JSON envelope, then parse the
   inner JSON the model produced.
5. Cache per (week_of, ticker) inside data/weekly_theses/<week>.json so
   reruns are idempotent. Re-running with --force regenerates everything.
6. Optional manual override: data/weekly_theses/<week>_manual.json. Any
   ticker present in the manual file replaces the LLM output for that
   ticker (per the B3 spec — humans get the final word).

CLI
---
    # Default — generate (or update) the current week's theses
    python scripts/generate_weekly_theses.py

    # Print the prompts for the first 3 tickers, do not call claude
    python scripts/generate_weekly_theses.py --dry-run

    # Just one ticker
    python scripts/generate_weekly_theses.py --ticker AMPX

    # Pin the week (default = today, snapped to the most recent Sunday)
    python scripts/generate_weekly_theses.py --week 2026-05-11

    # Tune how many tickers to generate
    python scripts/generate_weekly_theses.py --top-launch 15 --top-watch 5

Output schema (data/weekly_theses/<week>.json)
----------------------------------------------
    {
      "generated_at": "2026-05-11T14:32:00",
      "week_of":      "2026-05-10",
      "model":        "claude-opus-4-7",
      "theses": {
        "AMPX": {
          "thesis":           "... 2 paragraphs ...",
          "why_now":          "... 1 sentence ...",
          "risks":            ["...", "..."],
          "suggested_ticker": "AMPL",
          "_meta": {
            "bucket":         "launch",     # or "watch"
            "generated_at":   "2026-05-11T14:32:00",
            "source":         "claude_cli", # or "manual_override"
            "prompt_chars":   1842
          }
        },
        ...
      }
    }
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LAUNCH_PARQUET = PROJECT_ROOT / "data" / "analysis" / "launch_candidates.parquet"
WHITESPACE_PARQUET = PROJECT_ROOT / "data" / "analysis" / "whitespace_v4.parquet"
THESES_DIR = PROJECT_ROOT / "data" / "weekly_theses"

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_TOP_LAUNCH = 15
DEFAULT_TOP_WATCH = 5
CLAUDE_TIMEOUT_SEC = 180

log = logging.getLogger("weekly_theses")


# --------------------------------------------------------------------------- #
#  Week / cache helpers
# --------------------------------------------------------------------------- #
def week_of(d: date | None = None) -> str:
    """Snap any date to the start of its ISO week (Sunday). Returns YYYY-MM-DD."""
    d = d or date.today()
    # Sunday = 6 in isoweekday(); snap back to the previous Sunday
    delta = (d.isoweekday() % 7)
    return (d - timedelta(days=delta)).isoformat()


def cache_path(week: str) -> Path:
    return THESES_DIR / f"{week}.json"


def manual_override_path(week: str) -> Path:
    return THESES_DIR / f"{week}_manual.json"


def load_cache(week: str) -> dict:
    path = cache_path(week)
    if not path.exists():
        return {
            "generated_at": None,
            "week_of": week,
            "model": DEFAULT_MODEL,
            "theses": {},
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("cache at %s is corrupt (%s); starting fresh", path, exc)
        return {
            "generated_at": None,
            "week_of": week,
            "model": DEFAULT_MODEL,
            "theses": {},
        }


def save_cache(week: str, payload: dict) -> Path:
    THESES_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(week)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_manual_overrides(week: str) -> dict[str, dict]:
    """Load data/weekly_theses/<week>_manual.json if present.

    The override file uses the same per-ticker shape:
        {"AMPX": {"thesis": "...", "why_now": "...", "risks": [...], "suggested_ticker": "..."}}
    """
    path = manual_override_path(week)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("manual override at %s is invalid (%s)", path, exc)
        return {}
    # Accept either {"theses": {...}} or {"AMPX": {...}, "AMPL": {...}}
    if isinstance(raw, dict) and "theses" in raw and isinstance(raw["theses"], dict):
        return {k.upper(): v for k, v in raw["theses"].items()}
    if isinstance(raw, dict):
        return {k.upper(): v for k, v in raw.items() if isinstance(v, dict)}
    log.warning("manual override at %s is not a dict; ignoring", path)
    return {}


# --------------------------------------------------------------------------- #
#  Data loading
# --------------------------------------------------------------------------- #
def _safe_read_parquet(path: Path) -> "pd.DataFrame | None":  # noqa: F821
    if not path.exists():
        log.warning("parquet missing: %s", path)
        return None
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas not installed; cannot read parquet")
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        log.error("failed to read %s: %s", path, exc)
        return None


def load_top_candidates(top_launch: int, top_watch: int) -> list[dict]:
    """Return ordered list of {ticker, bucket, row} for the universe to thesis."""
    out: list[dict] = []
    seen: set[str] = set()

    launch_df = _safe_read_parquet(LAUNCH_PARQUET)
    if launch_df is not None and not launch_df.empty:
        if "composite_score" in launch_df.columns:
            launch_df = launch_df.sort_values("composite_score", ascending=False)
        for ticker, row in launch_df.head(top_launch).iterrows():
            t = str(ticker).upper().strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append({"ticker": t, "bucket": "launch", "row": row.to_dict()})

    watch_df = _safe_read_parquet(WHITESPACE_PARQUET)
    if watch_df is not None and not watch_df.empty:
        if "composite_score" in watch_df.columns:
            watch_df = watch_df.sort_values("composite_score", ascending=False)
        for ticker, row in watch_df.head(top_watch).iterrows():
            t = str(ticker).upper().strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append({"ticker": t, "bucket": "watch", "row": row.to_dict()})

    return out


# --------------------------------------------------------------------------- #
#  Prompt construction
# --------------------------------------------------------------------------- #
def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(v: Any, suffix: str = "") -> str:
    try:
        return f"{float(v):,.1f}{suffix}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_mcap(v: Any) -> str:
    try:
        x = float(v)
        if x >= 1000:
            return f"${x/1000:.1f}B"
        return f"${x:,.0f}M"
    except (TypeError, ValueError):
        return "n/a"


def build_prompt(ticker: str, bucket: str, row: dict) -> str:
    """Build a structured prompt for one ticker."""
    fund_name = row.get("rex_fund_name") or row.get("fund_name") or "—"
    sector = row.get("sector") or "—"
    direction = row.get("direction") or "Long"
    leverage = row.get("leverage") or "2.0"
    rex_status = row.get("rex_market_status") or ("FILED" if bucket == "launch" else "NOT_FILED")
    rex_ticker = row.get("rex_ticker") or "—"

    n_filed_comp = (
        row.get("competitor_filed_total")
        or row.get("n_competitor_filings_180d")
        or 0
    )
    n_active_comp = row.get("n_active_competitor_products", 0)

    metrics_lines = [
        f"  - Market cap:        {_fmt_mcap(row.get('market_cap'))}",
        f"  - Total OI:          {_fmt_num(row.get('total_oi'))}",
        f"  - 30d realized vol:  {_fmt_pct(row.get('rvol_30d'))}",
        f"  - 90d realized vol:  {_fmt_pct(row.get('rvol_90d'))}",
        f"  - 1m total return:   {_fmt_pct(row.get('ret_1m'))}",
        f"  - 3m total return:   {_fmt_pct(row.get('ret_3m'))}",
        f"  - 1y total return:   {_fmt_pct(row.get('ret_1y'))}",
        f"  - Short interest:    {_fmt_num(row.get('si_ratio'), ' days')}",
        f"  - Insider %:         {_fmt_pct(row.get('insider_pct'))}",
        f"  - Institutional %:   {_fmt_pct(row.get('inst_own_pct'))}",
    ]

    themes = row.get("themes") or row.get("themes_str")
    mentions_24h = row.get("mentions_24h")
    composite = row.get("composite_score")

    bucket_blurb = {
        "launch": (
            "REX has already FILED for this underlier (no live REX product yet, "
            "and no competitor has launched one). The thesis should explain why "
            "REX should prioritize launching this product."
        ),
        "watch": (
            "This is a true whitespace underlier — no live products from anyone "
            "and no recent competitor 485APOS. The thesis should explain why "
            "REX should consider FILING for a leveraged product on this name."
        ),
    }.get(bucket, "")

    proposed_ticker_hint = ""
    if bucket == "launch" and rex_ticker and rex_ticker != "—":
        proposed_ticker_hint = (
            f"The REX-filed ticker is `{rex_ticker}`. Suggest that or a "
            "tighter alternative."
        )
    else:
        proposed_ticker_hint = (
            "Propose a 4-letter REX-style ticker (e.g. AAPL -> AAPX, NVDA -> NVDX)."
        )

    prompt = f"""You are an ETF product strategist at REX Shares writing the
weekly Leveraged & Inverse recommendations memo. Produce a tight, professional
thesis for ONE underlying stock.

Underlier: {ticker}
Sector: {sector}
Bucket: {bucket} ({bucket_blurb})
Proposed product: {direction} {leverage}x {ticker}
REX status: {rex_status}  |  Filed REX ticker: {rex_ticker}  |  Fund name: {fund_name}
Competitor filings (last 180d): {n_filed_comp}  |  Competitor live products: {n_active_comp}
Themes: {themes or '—'}  |  Retail mentions (24h): {mentions_24h or '—'}  |  Composite score: {composite}

Signal snapshot:
{chr(10).join(metrics_lines)}

Write the thesis. Be specific to this name's catalysts, options liquidity,
and competitive position. Avoid boilerplate. {proposed_ticker_hint}

Respond with STRICT JSON ONLY (no prose around it, no markdown fences) using
exactly these keys:

{{
  "thesis":           "Two paragraphs (~120-180 words total). Paragraph 1: the underlying business / catalyst case. Paragraph 2: why a leveraged ETP fits — vol, liquidity, retail interest, competitive whitespace.",
  "why_now":          "One sentence. Specific catalyst or market condition that argues for launching this WEEK rather than next quarter.",
  "risks":            ["3 to 5 short bullets — concrete risks specific to this name (regulatory, liquidity, single-stock, sector, etc.)"],
  "suggested_ticker": "4-letter REX-style ticker for the proposed product"
}}
"""
    return prompt.strip()


# --------------------------------------------------------------------------- #
#  Claude CLI invocation (subprocess only — Max plan, NOT the API)
# --------------------------------------------------------------------------- #
def claude_available() -> bool:
    return shutil.which("claude") is not None


def call_claude_cli(prompt: str, model: str = DEFAULT_MODEL,
                    timeout: int = CLAUDE_TIMEOUT_SEC) -> dict:
    """Run `claude --print --output-format json` on the Max plan.

    Returns the parsed inner JSON the model produced (not the CLI envelope).
    Raises RuntimeError on any failure — caller decides how to log/skip.
    """
    if not claude_available():
        raise RuntimeError("claude CLI not on PATH")

    # --print runs non-interactively; --output-format json wraps the response
    # in a deterministic envelope. We read the model's reply from `result`.
    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", model,
    ]

    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI returned {proc.returncode}: {proc.stderr.strip()[:400]}"
        )

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RuntimeError("claude CLI returned empty stdout")

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude CLI envelope is not JSON: {exc}: {stdout[:200]}")

    # The CLI envelope shape: {"type": "result", "result": "<text>", ...}
    body = envelope.get("result") if isinstance(envelope, dict) else None
    if body is None:
        # Some CLI versions just return the text payload directly
        body = stdout

    inner = _extract_json(body)
    return _validate_thesis(inner)


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a string. Tolerant of code fences."""
    text = text.strip()
    if text.startswith("```"):
        # strip leading ```json and trailing ```
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back: find first `{` and last `}`
    lo = text.find("{")
    hi = text.rfind("}")
    if lo == -1 or hi == -1 or hi <= lo:
        raise RuntimeError(f"no JSON object found in model output: {text[:200]}")
    return json.loads(text[lo:hi + 1])


def _validate_thesis(obj: Any) -> dict:
    """Validate + normalize the thesis schema."""
    if not isinstance(obj, dict):
        raise RuntimeError(f"thesis is not a dict: {type(obj).__name__}")

    out = {
        "thesis": str(obj.get("thesis", "")).strip(),
        "why_now": str(obj.get("why_now", "")).strip(),
        "risks": obj.get("risks") or [],
        "suggested_ticker": str(obj.get("suggested_ticker", "")).strip().upper(),
    }
    if not out["thesis"]:
        raise RuntimeError("thesis field is empty")
    if not isinstance(out["risks"], list):
        out["risks"] = [str(out["risks"])]
    out["risks"] = [str(r).strip() for r in out["risks"] if str(r).strip()]
    return out


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def run(week: str, top_launch: int, top_watch: int,
        only_ticker: str | None, dry_run: bool, force: bool,
        model: str) -> dict:
    candidates = load_top_candidates(top_launch, top_watch)
    if only_ticker:
        only_ticker = only_ticker.upper()
        candidates = [c for c in candidates if c["ticker"] == only_ticker]
        if not candidates:
            log.error("ticker %s not found in launch/whitespace parquets", only_ticker)
            return {}

    log.info("Universe to thesis: %d ticker(s) (week=%s)", len(candidates), week)

    cache = load_cache(week)
    cache.setdefault("theses", {})
    cache["model"] = model

    overrides = load_manual_overrides(week)
    if overrides:
        log.info("Manual overrides present for: %s", ", ".join(sorted(overrides)))

    if dry_run:
        for c in candidates[:3]:
            print("=" * 80)
            print(f"PROMPT for {c['ticker']} (bucket={c['bucket']}):")
            print("=" * 80)
            print(build_prompt(c["ticker"], c["bucket"], c["row"]))
            print()
        log.info("dry-run: showed %d sample prompt(s); no claude calls made",
                 min(3, len(candidates)))
        return cache

    cli_ready = claude_available()
    if not cli_ready:
        log.warning("claude CLI not installed — only manual overrides will be applied")

    for c in candidates:
        ticker = c["ticker"]

        # Manual override always wins
        if ticker in overrides:
            cache["theses"][ticker] = {
                **overrides[ticker],
                "_meta": {
                    "bucket": c["bucket"],
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "source": "manual_override",
                },
            }
            log.info("  %s: manual override applied", ticker)
            continue

        # Skip if already cached and not forced
        if ticker in cache["theses"] and not force:
            log.info("  %s: cached, skip (use --force to regenerate)", ticker)
            continue

        if not cli_ready:
            log.info("  %s: skipped (no claude CLI)", ticker)
            continue

        prompt = build_prompt(ticker, c["bucket"], c["row"])
        try:
            thesis = call_claude_cli(prompt, model=model)
        except Exception as exc:
            log.error("  %s: FAILED — %s", ticker, exc)
            continue

        cache["theses"][ticker] = {
            **thesis,
            "_meta": {
                "bucket": c["bucket"],
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "source": "claude_cli",
                "prompt_chars": len(prompt),
            },
        }
        log.info("  %s: generated (%d chars)", ticker, len(thesis["thesis"]))

    cache["generated_at"] = datetime.now().isoformat(timespec="seconds")
    out_path = save_cache(week, cache)
    log.info("Wrote %s (%d theses total)", out_path, len(cache["theses"]))
    return cache


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--week", help="YYYY-MM-DD; default = today snapped to most recent Sunday")
    p.add_argument("--ticker", help="Generate just this single ticker")
    p.add_argument("--top-launch", type=int, default=DEFAULT_TOP_LAUNCH,
                   help=f"How many launch_candidates rows (default {DEFAULT_TOP_LAUNCH})")
    p.add_argument("--top-watch", type=int, default=DEFAULT_TOP_WATCH,
                   help=f"How many whitespace_v4 rows (default {DEFAULT_TOP_WATCH})")
    p.add_argument("--dry-run", action="store_true",
                   help="Print 3 sample prompts and exit; do not call claude")
    p.add_argument("--force", action="store_true",
                   help="Regenerate even if a ticker is already cached this week")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Model to pass to `claude --model` (default {DEFAULT_MODEL})")
    return p.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    if args.week:
        try:
            datetime.strptime(args.week, "%Y-%m-%d")
        except ValueError:
            log.error("--week must be YYYY-MM-DD")
            sys.exit(2)
        week = args.week
    else:
        week = week_of()

    run(
        week=week,
        top_launch=args.top_launch,
        top_watch=args.top_watch,
        only_ticker=args.ticker,
        dry_run=args.dry_run,
        force=args.force,
        model=args.model,
    )


if __name__ == "__main__":
    main()

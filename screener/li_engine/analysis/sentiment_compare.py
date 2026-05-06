"""Social-sentiment API comparison harness (Pillar 6 research).

Probes every free-or-claimed-free social sentiment source we might use in
production, pulls the 30-ticker test universe from each, and computes
cross-API Spearman correlation so we can pick a primary + secondary signal
without being misled by marketing.

APIs in scope:
    1. ApeWisdom                - no key, already in signals.py (baseline)
    2. Finnhub social-sentiment - free key REQUIRED (we skip if absent)
    3. Google Trends (pytrends) - no key, unofficial
    4. StockTwits trending      - now Cloudflare-blocked for anonymous
    5. Quiver Quant WSB         - premium, no free tier on /beta/live/
    6. Reddit via PRAW          - requires client_id/secret (skip if absent)

Run:
    python -m screener.li_engine.analysis.sentiment_compare

Outputs:
    reports/sentiment_api_comparison.csv    (per-ticker per-API detail)
    stdout: structured markdown summary (<=700 words)

No API is faked. If a source returns nothing in practice today, its column
is empty and the markdown says so explicitly - that IS the finding.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

# pytrends calls .fillna on object dtype; pandas 2.x emits a FutureWarning
# per call. We cannot fix the library, so silence it locally.
warnings.filterwarnings(
    "ignore",
    message="Downcasting object dtype arrays",
    category=FutureWarning,
)

log = logging.getLogger("sentiment_compare")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TEST_UNIVERSE: list[str] = [
    "TSLA", "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AMD",
    "AVGO", "PLTR", "MSTR", "HOOD", "COIN", "RDDT", "SOFI", "IONQ",
    "RGTI", "QBTS", "RKLB", "ASTS", "LUNR", "CRSP", "NTLA", "BEAM",
    "MU",  "SNDK", "TSM",  "ARM",  "SMCI", "DRAM",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "reports" / "sentiment_api_comparison.csv"

UA = "rexfinhub-sentiment-research/1.0 (+https://rexfinhub.com)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Per-API result container
# ---------------------------------------------------------------------------

@dataclass
class ApiResult:
    name: str
    status: str = "error"             # "ok" | "skipped" | "blocked" | "error"
    reason: str = ""
    data: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    # Metadata for the comparison table
    auth_required: str = ""
    rate_limit: str = ""
    fields_provided: str = ""
    update_frequency: str = ""
    historical: str = ""
    paid_cost: str = ""

    @property
    def coverage_pct(self) -> float:
        if not len(TEST_UNIVERSE):
            return 0.0
        hits = self.data.reindex(TEST_UNIVERSE).notna().sum()
        return 100.0 * hits / len(TEST_UNIVERSE)


# ---------------------------------------------------------------------------
# 1. ApeWisdom
# ---------------------------------------------------------------------------

def probe_apewisdom() -> ApiResult:
    r = ApiResult(
        name="apewisdom",
        status="ok",
        auth_required="None",
        rate_limit="Undocumented; community reports ~1 req/s safe",
        fields_provided="rank, mentions, mentions_24h_ago, upvotes, sentiment",
        update_frequency="~hourly (trailing 24h window)",
        historical="No - current snapshot only (sliding 24h)",
        paid_cost="N/A - free; consider donation/mirror if heavy use",
    )
    base = "https://apewisdom.io/api/v1.0/filter/{f}/page/{p}"
    records: dict[str, int] = {}
    try:
        for filt in ("all-stocks", "wallstreetbets"):
            for page in range(1, 11):
                resp = requests.get(
                    base.format(f=filt, p=page),
                    headers={"User-Agent": UA},
                    timeout=15,
                )
                if resp.status_code != 200:
                    log.warning("ApeWisdom %s p%d HTTP %d", filt, page, resp.status_code)
                    break
                items = resp.json().get("results") or []
                if not items:
                    break
                for it in items:
                    tkr = (it.get("ticker") or "").upper().strip()
                    if not tkr:
                        continue
                    m = int(it.get("mentions") or 0)
                    if m > records.get(tkr, 0):
                        records[tkr] = m
                time.sleep(0.25)
                if len(items) < 50:
                    break
    except Exception as e:
        r.status = "error"
        r.reason = f"{type(e).__name__}: {e}"
        return r

    if not records:
        r.status = "error"
        r.reason = "Empty response from all pages"
        return r

    r.data = pd.Series(records, name="apewisdom_mentions", dtype=float)
    return r


# ---------------------------------------------------------------------------
# 2. Finnhub
# ---------------------------------------------------------------------------

def probe_finnhub() -> ApiResult:
    r = ApiResult(
        name="finnhub",
        auth_required="API key required (free tier available)",
        rate_limit="60 req/min on free tier",
        fields_provided="reddit.mention, twitter.mention, positive/negative score",
        update_frequency="Daily aggregates per symbol",
        historical="Yes - /stock/social-sentiment accepts from/to date range",
        paid_cost="Premium plans from $60/mo (endpoint is often gated to paid)",
    )
    key = os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_KEY")
    if not key:
        r.status = "skipped"
        r.reason = "No FINNHUB_API_KEY in environment - not tested (per spec)."
        return r

    # GitHub issue #557: free tier often 403s on /stock/social-sentiment.
    records: dict[str, int] = {}
    blocked = False
    try:
        for tkr in TEST_UNIVERSE:
            resp = requests.get(
                "https://finnhub.io/api/v1/stock/social-sentiment",
                params={"symbol": tkr, "token": key},
                timeout=15,
            )
            if resp.status_code == 403:
                blocked = True
                break
            if resp.status_code != 200:
                log.warning("Finnhub %s HTTP %d", tkr, resp.status_code)
                continue
            payload = resp.json() or {}
            red = payload.get("reddit") or []
            twi = payload.get("twitter") or []
            mentions = sum(int(x.get("mention") or 0) for x in red) \
                     + sum(int(x.get("mention") or 0) for x in twi)
            if mentions:
                records[tkr] = mentions
            time.sleep(1.1)  # free tier = 60/min
    except Exception as e:
        r.status = "error"
        r.reason = f"{type(e).__name__}: {e}"
        return r

    if blocked:
        r.status = "blocked"
        r.reason = "Free tier returned 403 on /stock/social-sentiment (GH #557 confirmed)"
        return r
    if not records:
        r.status = "error"
        r.reason = "Key worked but every ticker returned zero mentions"
        return r

    r.status = "ok"
    r.data = pd.Series(records, name="finnhub_mentions", dtype=float)
    return r


# ---------------------------------------------------------------------------
# 3. Google Trends (pytrends)
# ---------------------------------------------------------------------------

def probe_google_trends() -> ApiResult:
    r = ApiResult(
        name="google_trends",
        auth_required="None (unofficial pytrends library)",
        rate_limit="Aggressive - 429s after ~5 consecutive requests; 60s cooldown",
        fields_provided="Relative search interest 0-100 (normalized per batch)",
        update_frequency="Daily (with slight lag); real-time trends separate",
        historical="Yes - up to 5 years via timeframe param",
        paid_cost="No official paid tier; SerpAPI mirrors it at $50/mo+",
    )
    try:
        from pytrends.request import TrendReq
    except ImportError:
        r.status = "skipped"
        r.reason = "pytrends not installed (pip install pytrends)"
        return r

    # Google Trends allows max 5 terms per request. Batch the 30 tickers
    # with a common anchor ('TSLA') so all rows are on a comparable scale.
    ANCHOR = "TSLA"
    batches: list[list[str]] = []
    for i in range(0, len(TEST_UNIVERSE), 4):
        group = TEST_UNIVERSE[i:i + 4]
        if ANCHOR not in group:
            group = [ANCHOR] + group
        batches.append(group)

    # NOTE: pytrends' Retry call uses 'method_whitelist' which urllib3 v2
    # removed in favor of 'allowed_methods'. Passing retries= triggers the
    # crash. Omit it; we handle retries ourselves per batch.
    try:
        pt = TrendReq(hl="en-US", tz=300, timeout=(10, 25))
    except Exception as e:
        r.status = "error"
        r.reason = f"pytrends init failed: {type(e).__name__}: {e}"
        return r

    interest: dict[str, float] = {}
    anchor_means: list[float] = []
    errors = 0
    for group in batches:
        try:
            pt.build_payload(group, timeframe="today 1-m", geo="US")
            df = pt.interest_over_time()
            if df.empty:
                continue
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])
            means = df.mean(numeric_only=True)
            anchor_val = means.get(ANCHOR)
            if anchor_val and anchor_val > 0:
                anchor_means.append(float(anchor_val))
            for t, v in means.items():
                if t == ANCHOR:
                    continue
                interest[t] = float(v)
            time.sleep(2.5)  # stay under rate limit
        except Exception as e:
            errors += 1
            log.warning("pytrends batch %s failed: %s", group, e)
            time.sleep(5.0)
            if errors >= 3:
                break

    # Restore TSLA (the anchor) as the avg of its observations across batches
    if anchor_means:
        interest[ANCHOR] = sum(anchor_means) / len(anchor_means)

    if not interest:
        r.status = "error"
        r.reason = f"No interest data returned ({errors} batch errors)"
        return r

    r.status = "ok"
    r.data = pd.Series(interest, name="google_trends_interest", dtype=float)
    if errors:
        r.reason = f"Partial: {errors} batches rate-limited"
    return r


# ---------------------------------------------------------------------------
# 4. StockTwits
# ---------------------------------------------------------------------------

def probe_stocktwits() -> ApiResult:
    r = ApiResult(
        name="stocktwits",
        auth_required="OAuth required (was public pre-2024)",
        rate_limit="200 req/hour unauth historically; now 0 (blocked)",
        fields_provided="watchlist_count, sentiment (bullish/bearish), message volume",
        update_frequency="Near real-time",
        historical="Limited - 30 messages per symbol endpoint, no deep history",
        paid_cost="StockTwits API Enterprise - contact sales (est. $1k+/mo)",
    )
    try:
        resp = requests.get(
            "https://api.stocktwits.com/api/2/streams/trending.json",
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code == 200:
            payload = resp.json()
            # If they ever unblock: extract symbols from messages
            msgs = payload.get("messages") or []
            counts: dict[str, int] = {}
            for m in msgs:
                for sym in m.get("symbols") or []:
                    t = (sym.get("symbol") or "").upper()
                    if t:
                        counts[t] = counts.get(t, 0) + 1
            if counts:
                r.status = "ok"
                r.data = pd.Series(counts, name="stocktwits_mentions", dtype=float)
                return r
            r.status = "error"
            r.reason = "HTTP 200 but no messages parsed"
            return r
        r.status = "blocked"
        r.reason = f"HTTP {resp.status_code} (Cloudflare challenge - unauth access revoked)"
        return r
    except Exception as e:
        r.status = "error"
        r.reason = f"{type(e).__name__}: {e}"
        return r


# ---------------------------------------------------------------------------
# 5. Quiver Quant WSB
# ---------------------------------------------------------------------------

def probe_quiver_wsb() -> ApiResult:
    r = ApiResult(
        name="quiver_wsb",
        auth_required="Bearer token required (subscription endpoint)",
        rate_limit="Plan-dependent",
        fields_provided="Mentions, sentiment, timestamps from r/wallstreetbets",
        update_frequency="Daily",
        historical="Yes - /historical/wallstreetbets/{ticker}",
        paid_cost="Premium $10/mo (Hobbyist); API access typically $50/mo+",
    )
    try:
        resp = requests.get(
            "https://api.quiverquant.com/beta/live/wallstreetbets",
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code == 200:
            payload = resp.json()
            counts: dict[str, int] = {}
            if isinstance(payload, list):
                for row in payload:
                    t = (row.get("Ticker") or "").upper()
                    m = row.get("Mentions") or row.get("mentions") or 0
                    if t:
                        counts[t] = max(counts.get(t, 0), int(m))
            if counts:
                r.status = "ok"
                r.data = pd.Series(counts, name="quiver_mentions", dtype=float)
                return r
            r.status = "error"
            r.reason = "HTTP 200 but empty payload"
            return r
        r.status = "blocked"
        r.reason = f"HTTP {resp.status_code} - beta/live endpoint is gated to paid accounts"
        return r
    except Exception as e:
        r.status = "error"
        r.reason = f"{type(e).__name__}: {e}"
        return r


# ---------------------------------------------------------------------------
# 6. Reddit via PRAW
# ---------------------------------------------------------------------------

def probe_reddit_praw() -> ApiResult:
    r = ApiResult(
        name="reddit_praw",
        auth_required="client_id + client_secret + user_agent (OAuth script app)",
        rate_limit="60 req/min authenticated (official)",
        fields_provided="Full post/comment text - DIY sentiment + mention count",
        update_frequency="Real-time stream available",
        historical="Hard-capped ~1000 posts per listing; Pushshift dead since 2023",
        paid_cost="Free for noncommercial <100 QPM; Reddit Enterprise is custom-quote",
    )
    cid = os.environ.get("REDDIT_CLIENT_ID")
    csec = os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and csec):
        r.status = "skipped"
        r.reason = "No REDDIT_CLIENT_ID/SECRET in environment - not tested (per spec)."
        return r
    try:
        import praw  # type: ignore
    except ImportError:
        r.status = "skipped"
        r.reason = "praw not installed; credentials present. pip install praw to test."
        return r
    try:
        reddit = praw.Reddit(
            client_id=cid, client_secret=csec,
            user_agent="rexfinhub-sentiment-research/1.0",
        )
        reddit.read_only = True
        sub = reddit.subreddit("wallstreetbets")
        counts: dict[str, int] = {}
        import re
        cashtag = re.compile(r"\$([A-Z]{1,5})\b")
        for post in sub.new(limit=500):
            text = f"{post.title or ''} {post.selftext or ''}".upper()
            for m in cashtag.findall(text):
                counts[m] = counts.get(m, 0) + 1
        if not counts:
            r.status = "error"
            r.reason = "PRAW auth worked but no cashtags found in 500 newest WSB posts"
            return r
        r.status = "ok"
        r.data = pd.Series(counts, name="reddit_praw_mentions", dtype=float)
        return r
    except Exception as e:
        r.status = "error"
        r.reason = f"{type(e).__name__}: {e}"
        return r


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

PROBES: list[tuple[str, Callable[[], ApiResult]]] = [
    ("apewisdom",     probe_apewisdom),
    ("finnhub",       probe_finnhub),
    ("google_trends", probe_google_trends),
    ("stocktwits",    probe_stocktwits),
    ("quiver_wsb",    probe_quiver_wsb),
    ("reddit_praw",   probe_reddit_praw),
]


def build_comparison_df(results: list[ApiResult]) -> pd.DataFrame:
    df = pd.DataFrame(index=pd.Index(TEST_UNIVERSE, name="ticker"))
    for r in results:
        col = f"{r.name}__value"
        if r.status == "ok":
            df[col] = r.data.reindex(TEST_UNIVERSE)
        else:
            df[col] = pd.NA
    return df


def compute_correlations(df: pd.DataFrame) -> pd.DataFrame:
    value_cols = [c for c in df.columns if c.endswith("__value")]
    usable = [c for c in value_cols if df[c].notna().sum() >= 3]
    if len(usable) < 2:
        return pd.DataFrame()
    # Spearman handles different scales and is robust to outliers -
    # correct choice for mentions vs. search-interest comparison.
    return df[usable].corr(method="spearman")


def format_markdown(results: list[ApiResult], corr: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# Social Sentiment API Comparison")
    lines.append("")
    lines.append(f"Test universe: {len(TEST_UNIVERSE)} tickers (mega-cap tech, retail-heavy, quantum, space, biotech, memory).")
    lines.append("")
    lines.append("## Status by source")
    lines.append("")
    lines.append("| API | Status | Coverage | Auth | Historical | Notes |")
    lines.append("|---|---|---|---|---|---|")
    status_icon = {"ok": "LIVE", "skipped": "SKIP", "blocked": "BLOCK", "error": "ERR"}
    for r in results:
        cov = f"{r.coverage_pct:.0f}%" if r.status == "ok" else "-"
        note = r.reason or "-"
        if len(note) > 80:
            note = note[:77] + "..."
        lines.append(
            f"| {r.name} | {status_icon.get(r.status, r.status)} | {cov} | "
            f"{r.auth_required} | {r.historical} | {note} |"
        )
    lines.append("")

    lines.append("## Cross-API Spearman correlation")
    lines.append("")
    if corr.empty:
        lines.append("Insufficient live sources (need >=2 with >=3 overlapping tickers). "
                     "Correlation matrix cannot be computed this run.")
    else:
        cols = [c.replace("__value", "") for c in corr.columns]
        lines.append("| | " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * (len(cols) + 1))
        for row_label, row in zip(cols, corr.values):
            vals = " | ".join(f"{v:.2f}" if pd.notna(v) else "-" for v in row)
            lines.append(f"| {row_label} | {vals} |")
    lines.append("")

    # Recommendation logic.
    # Priority ordering is NOT pure coverage -- mention-count signals (ApeWisdom,
    # Finnhub, Reddit, Quiver, StockTwits) are primary-class for retail attention;
    # Google Trends measures search interest, which is a different signal type
    # (useful as a non-redundant confirmator, not a replacement).
    live = [r for r in results if r.status == "ok"]
    MENTION_CLASS = {"apewisdom", "finnhub", "reddit_praw", "quiver_wsb", "stocktwits"}
    mention_live = [r for r in live if r.name in MENTION_CLASS]
    other_live = [r for r in live if r.name not in MENTION_CLASS]
    primary = None
    if mention_live:
        primary = sorted(mention_live, key=lambda r: r.coverage_pct, reverse=True)[0]
    elif other_live:
        primary = sorted(other_live, key=lambda r: r.coverage_pct, reverse=True)[0]

    lines.append("## Recommendation")
    lines.append("")
    if not primary:
        lines.append("**No live sources today.** Defer the decision - re-run when Finnhub key is available.")
    else:
        lines.append(f"- **Primary:** `{primary.name}` - coverage {primary.coverage_pct:.0f}%, "
                     f"auth: {primary.auth_required}. Mention-count signal, aligned with existing `load_sentiment_signals`.")
        secondary = None
        candidates = [r for r in live if r.name != primary.name]
        if not corr.empty and candidates:
            p_col = f"{primary.name}__value"
            best = None
            best_score = 1.0
            for cand in candidates:
                c_col = f"{cand.name}__value"
                if p_col in corr.columns and c_col in corr.columns:
                    rho = corr.loc[p_col, c_col]
                    if pd.notna(rho) and abs(rho) < best_score:
                        best_score = abs(rho)
                        best = (cand, rho)
            if best:
                cand, rho = best
                verdict = "complementary" if abs(rho) < 0.6 else "partially redundant"
                secondary = cand
                lines.append(f"- **Secondary:** `{cand.name}` - rho={rho:.2f} vs primary ({verdict}). "
                             f"Coverage {cand.coverage_pct:.0f}%.")
        if secondary is None and candidates:
            cand = sorted(candidates, key=lambda r: r.coverage_pct, reverse=True)[0]
            lines.append(f"- **Secondary:** `{cand.name}` - coverage {cand.coverage_pct:.0f}% "
                         f"(correlation vs primary not computable).")
        elif not candidates:
            lines.append("- **Secondary:** none available. Add Finnhub or Reddit creds to get a second leg.")

    lines.append("")
    lines.append("## Historical backfill")
    lines.append("")
    lines.append("- **ApeWisdom**: current snapshot only. We must capture daily going forward - no backfill.")
    lines.append("- **Google Trends**: up to 5 years of weekly/daily interest via pytrends `timeframe`. Best backfill path.")
    lines.append("- **Finnhub**: `/stock/social-sentiment?from=YYYY-MM-DD&to=YYYY-MM-DD` IF the free-tier key is granted access (often isn't).")
    lines.append("- **StockTwits / Quiver**: paywalled. Not viable without contract.")
    lines.append("- **Reddit**: no deep history - Pushshift effectively dead since 2023.")
    lines.append("")
    lines.append("## Operational notes")
    lines.append("")
    lines.append("- ApeWisdom is the existing baseline in `signals.py::load_sentiment_signals` - keep it as the primary mentions feed.")
    lines.append("- For a second, non-redundant retail signal we should stand up a nightly pytrends job (4-ticker batches with an anchor, stored in the DB).")
    lines.append("- Revisit StockTwits/Quiver only if a budget line is approved - no free production path today.")
    lines.append("- Start capturing daily ApeWisdom snapshots NOW so in 90 days we have our own history that nobody else sells.")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    results: list[ApiResult] = []
    for name, fn in PROBES:
        log.info("Probing %s", name)
        try:
            results.append(fn())
        except Exception as e:  # belt-and-braces - a probe must never take down the run
            results.append(ApiResult(name=name, status="error", reason=f"unhandled: {e}"))

    df = build_comparison_df(results)

    # Annotate per-API metadata as leading rows in the CSV for analyst context
    meta_rows = []
    for r in results:
        meta_rows.append({
            "ticker": f"__meta__:{r.name}",
            f"{r.name}__value": pd.NA,
            "status": r.status,
            "auth_required": r.auth_required,
            "rate_limit": r.rate_limit,
            "fields_provided": r.fields_provided,
            "update_frequency": r.update_frequency,
            "historical": r.historical,
            "paid_cost": r.paid_cost,
            "reason": r.reason,
            "coverage_pct": round(r.coverage_pct, 1),
        })
    meta_df = pd.DataFrame(meta_rows).set_index("ticker")

    # Append Spearman block
    corr = compute_correlations(df)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write three logical sections to one CSV, separated by blank lines
    with open(REPORT_PATH, "w", encoding="utf-8", newline="") as fh:
        fh.write("# Section 1: per-ticker values\n")
        df.to_csv(fh)
        fh.write("\n# Section 2: API metadata\n")
        meta_df.to_csv(fh)
        fh.write("\n# Section 3: Spearman correlation\n")
        if not corr.empty:
            corr.to_csv(fh)
        else:
            fh.write("correlation_not_computable,need_at_least_2_live_sources\n")

    md = format_markdown(results, corr)
    # Enforce the 700-word hard cap on the markdown block
    words = md.split()
    if len(words) > 700:
        md = " ".join(words[:700]) + " ..."
    print(md)
    print(f"\n[wrote CSV: {REPORT_PATH}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Catalyst calendar — maps launch_candidates tickers to upcoming events.

Each rec needs a "why now" justification. This module collects:
  1. Earnings dates (yfinance calendar)
  2. FDA press releases (FDA RSS — best-effort; recent + upcoming AdComs)
  3. Tech conferences (static calendar of well-known conferences)
  4. Regulatory deadlines (static + SEC filing notes — best-effort)

Per ticker we keep the next 2 catalysts within 90 days. The renderer
("why now" tag) consumes the soonest-upcoming row per ticker.

Output schema (long format, one row per catalyst):
    ticker            str
    catalyst_type     str   # earnings | fda | conference | regulatory
    catalyst_date     date
    source            str   # yfinance | fda_rss | conferences_2026 | sec
    description       str
    confidence        str   # high | medium | low

Cache: per-ticker JSON under data/analysis/catalyst_cache/ (24h TTL)
       to keep yfinance calls cheap on re-runs.
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCH_CANDIDATES = REPO_ROOT / "data" / "analysis" / "launch_candidates.parquet"
OUTPUT_PATH = REPO_ROOT / "data" / "analysis" / "catalyst_calendar.parquet"
CACHE_DIR = REPO_ROOT / "data" / "analysis" / "catalyst_cache"
CACHE_TTL_HOURS = 24

HORIZON_DAYS = 90
MAX_PER_TICKER = 2

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_FDA_PRESS_RSS = (
    "https://www.fda.gov/about-fda/contact-fda/stay-informed/"
    "rss-feeds/press-releases/rss.xml"
)


@dataclass
class Catalyst:
    ticker: str
    catalyst_type: str
    catalyst_date: date
    source: str
    description: str
    confidence: str = "medium"


# ---------------------------------------------------------------------------
# Static conference calendar (2026 — covers next 90d from typical run date)
# ---------------------------------------------------------------------------
# Best-effort dates for the well-known tech conferences. Used only for tech /
# semi tickers. Add/edit yearly. Dates are conference-start unless noted.
_CONFERENCES_2026: list[tuple[str, date, str]] = [
    # (name, date, sector_hint)
    # Sector hints are deliberately specific — only pinned tickers get attached
    # to vendor-specific events (Intel Vision, AMD Advancing AI, etc.). Generic
    # tech conferences (Goldman/JPM Tech, WWDC, Build) attach to broad-tech tickers.
    ("Computex 2026 (Taipei)", date(2026, 5, 19), "tech_hardware"),
    ("Apple WWDC 2026", date(2026, 6, 8), "tech_general"),
    ("Microsoft Build 2026", date(2026, 5, 19), "tech_general"),
    ("Snowflake Summit 2026", date(2026, 6, 1), "data"),
    ("Databricks Data+AI Summit 2026", date(2026, 6, 8), "data"),
    ("Cisco Live 2026", date(2026, 6, 7), "networking"),
    ("BIO International 2026", date(2026, 6, 15), "biotech"),
    ("Intel Vision 2026", date(2026, 5, 12), "semis"),
    ("AMD Advancing AI", date(2026, 6, 12), "semis"),
    ("Money 20/20 Europe", date(2026, 6, 2), "fintech"),
    ("Goldman Sachs Tech Conf", date(2026, 5, 27), "tech_general"),
    ("JPM Tech Conf", date(2026, 5, 19), "tech_general"),
]

# Sector / ticker → relevant conference hint.
_TECH_SECTORS = {"Information Technology", "Communication Services"}
_HEALTH_SECTORS = {"Health Care"}

# Tickers known to anchor specific conferences (best-effort cross-reference).
_TICKER_CONFERENCES: dict[str, list[str]] = {
    "NVDA": ["NVIDIA GTC Spring", "Computex 2026 (Taipei)"],
    "AMD": ["AMD Advancing AI", "Computex 2026 (Taipei)"],
    "INTC": ["Intel Vision 2026", "Computex 2026 (Taipei)"],
    "AAPL": ["Apple WWDC 2026"],
    "MSFT": ["Microsoft Build 2026"],
    "CSCO": ["Cisco Live 2026"],
    "SNOW": ["Snowflake Summit 2026"],
    "DDOG": ["Snowflake Summit 2026", "Databricks Data+AI Summit 2026"],
    "CRWD": ["RSA Conference 2026"],
    "PANW": ["RSA Conference 2026"],
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _cache_path(ticker: str) -> Path:
    safe = re.sub(r"[^A-Z0-9_-]", "_", ticker.upper())
    return CACHE_DIR / f"{safe}.json"


def _cache_read(ticker: str) -> list[Catalyst] | None:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        age_h = (time.time() - p.stat().st_mtime) / 3600.0
        if age_h > CACHE_TTL_HOURS:
            return None
        payload = json.loads(p.read_text(encoding="utf-8"))
        out: list[Catalyst] = []
        for row in payload:
            row["catalyst_date"] = date.fromisoformat(row["catalyst_date"])
            out.append(Catalyst(**row))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("cache read failed for %s: %s", ticker, exc)
        return None


def _cache_write(ticker: str, items: Iterable[Catalyst]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = []
    for c in items:
        d = asdict(c)
        d["catalyst_date"] = c.catalyst_date.isoformat()
        payload.append(d)
    _cache_path(ticker).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Source: yfinance earnings
# ---------------------------------------------------------------------------
def _earnings_for(ticker: str, today: date) -> list[Catalyst]:
    try:
        import yfinance as yf  # noqa: WPS433 — optional heavy import
    except ImportError:
        log.warning("yfinance not installed; skipping earnings")
        return []

    out: list[Catalyst] = []
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if not isinstance(cal, dict):
            return out
        raw = cal.get("Earnings Date")
        if not raw:
            return out
        # Normalise to list[date]
        if isinstance(raw, (list, tuple)):
            dates = raw
        else:
            dates = [raw]
        for d in dates:
            if isinstance(d, datetime):
                d = d.date()
            if not isinstance(d, date):
                continue
            delta = (d - today).days
            if delta < 0 or delta > HORIZON_DAYS:
                continue
            avg = cal.get("Earnings Average")
            desc = f"Q earnings"
            if isinstance(avg, (int, float)):
                desc = f"Q earnings (consensus EPS ~{avg:.2f})"
            out.append(
                Catalyst(
                    ticker=ticker,
                    catalyst_type="earnings",
                    catalyst_date=d,
                    source="yfinance",
                    description=desc,
                    confidence="high",
                )
            )
    except Exception as exc:  # noqa: BLE001
        log.debug("earnings fetch failed for %s: %s", ticker, exc)
    return out


# ---------------------------------------------------------------------------
# Source: FDA press-releases RSS (recent past — used as a proxy signal)
# ---------------------------------------------------------------------------
_FDA_CACHE: dict[str, object] = {"fetched_at": 0.0, "entries": []}


def _fetch_fda_entries() -> list[dict]:
    """Fetch FDA press release feed once per process (cached)."""
    if _FDA_CACHE["entries"] and (time.time() - float(_FDA_CACHE["fetched_at"])) < 3600:
        return list(_FDA_CACHE["entries"])  # type: ignore[arg-type]
    try:
        r = requests.get(
            _FDA_PRESS_RSS, timeout=15, headers={"User-Agent": _USER_AGENT}
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            link = (item.findtext("link") or "").strip()
            try:
                # RFC 822 — Wed, 30 Apr 2026 14:23:00 -0400
                dt = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S")
                d = dt.date()
            except Exception:  # noqa: BLE001
                d = date.today()
            items.append({"title": title, "date": d, "link": link})
        _FDA_CACHE["entries"] = items
        _FDA_CACHE["fetched_at"] = time.time()
        return items
    except Exception as exc:  # noqa: BLE001
        log.warning("FDA RSS fetch failed: %s", exc)
        return []


def _company_name_for(ticker: str, info_cache: dict) -> str:
    """Best-effort company name from yfinance info (used to match FDA items)."""
    if ticker in info_cache:
        return info_cache[ticker]
    try:
        import yfinance as yf  # noqa: WPS433
        info = yf.Ticker(ticker).info or {}
        name = info.get("longName") or info.get("shortName") or ""
        # Strip common suffixes
        name = re.sub(
            r"\b(Inc|Corp|Corporation|Holdings?|Limited|Ltd|plc|PLC|"
            r"Therapeutics|Pharmaceuticals|Pharma|Biosciences)\b\.?",
            "",
            name,
        ).strip()
        info_cache[ticker] = name
        return name
    except Exception:  # noqa: BLE001
        info_cache[ticker] = ""
        return ""


def _fda_for(
    ticker: str, sector: str | None, today: date, info_cache: dict
) -> list[Catalyst]:
    """Match FDA press releases to biotech tickers by company-name substring."""
    if sector not in _HEALTH_SECTORS:
        return []
    name = _company_name_for(ticker, info_cache)
    if not name or len(name) < 4:
        return []
    name_l = name.lower()
    out: list[Catalyst] = []
    for item in _fetch_fda_entries():
        if name_l in item["title"].lower():
            d = item["date"]
            # FDA RSS is past-only; treat very recent (<=14d) as a "fresh
            # catalyst" worth flagging in the why-now tag.
            delta_days = (today - d).days
            if 0 <= delta_days <= 14:
                out.append(
                    Catalyst(
                        ticker=ticker,
                        catalyst_type="fda",
                        catalyst_date=d,
                        source="fda_rss",
                        description=f"FDA: {item['title'][:140]}",
                        confidence="high",
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Source: conferences (static)
# ---------------------------------------------------------------------------
def _conferences_for(
    ticker: str, sector: str | None, today: date
) -> list[Catalyst]:
    """Conferences attach ONLY to tickers explicitly pinned via
    `_TICKER_CONFERENCES`, plus biotech tickers attaching to BIO International.

    A sector-wide fallback was rejected: it produced too much noise (e.g.,
    AMC getting Microsoft Build, AUR getting Intel Vision). Generic tech
    conferences are not a real catalyst for non-pinned tickers.
    """
    out: list[Catalyst] = []
    horizon = today + timedelta(days=HORIZON_DAYS)
    pinned = set(_TICKER_CONFERENCES.get(ticker, []))

    for name, d, hint in _CONFERENCES_2026:
        if not (today <= d <= horizon):
            continue
        if name in pinned:
            out.append(
                Catalyst(
                    ticker=ticker,
                    catalyst_type="conference",
                    catalyst_date=d,
                    source="conferences_2026",
                    description=f"Conference: {name}",
                    confidence="high",
                )
            )
        elif sector in _HEALTH_SECTORS and hint == "biotech":
            # Biotech tickers all get BIO International — it's the one
            # broadly-relevant biotech catalyst.
            out.append(
                Catalyst(
                    ticker=ticker,
                    catalyst_type="conference",
                    catalyst_date=d,
                    source="conferences_2026",
                    description=f"Conference: {name}",
                    confidence="medium",
                )
            )
    return out


# ---------------------------------------------------------------------------
# Source: regulatory (best-effort placeholder)
# ---------------------------------------------------------------------------
# 485APOS deadlines / SEC filing windows would require SEC submissions JSON
# parsing per ticker — heavy and out of scope for first pass. Empty by design;
# keep the source slot so downstream renderers don't break when populated.
def _regulatory_for(ticker: str, today: date) -> list[Catalyst]:
    return []


# ---------------------------------------------------------------------------
# Per-ticker collector
# ---------------------------------------------------------------------------
def _catalysts_for_ticker(
    ticker: str,
    sector: str | None,
    today: date,
    info_cache: dict,
    use_cache: bool = True,
) -> list[Catalyst]:
    if use_cache:
        cached = _cache_read(ticker)
        if cached is not None:
            return cached

    found: list[Catalyst] = []
    found.extend(_earnings_for(ticker, today))
    found.extend(_fda_for(ticker, sector, today, info_cache))
    found.extend(_conferences_for(ticker, sector, today))
    found.extend(_regulatory_for(ticker, today))

    # Keep recent FDA news (past) + future events within horizon.
    found = [
        c
        for c in found
        if (c.catalyst_type == "fda")
        or (0 <= (c.catalyst_date - today).days <= HORIZON_DAYS)
    ]
    # Rank: higher confidence wins ties on date; then soonest-first.
    _conf_rank = {"high": 0, "medium": 1, "low": 2}
    found.sort(
        key=lambda c: (
            _conf_rank.get(c.confidence, 3),
            c.catalyst_date,
            c.catalyst_type,
        )
    )
    found = found[:MAX_PER_TICKER]
    # Final sort by date for display
    found.sort(key=lambda c: c.catalyst_date)

    _cache_write(ticker, found)
    return found


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def build_catalyst_calendar(
    candidates: pd.DataFrame | None = None,
    use_cache: bool = True,
    today: date | None = None,
) -> pd.DataFrame:
    """Build the catalyst calendar parquet for all launch candidates.

    Returns a DataFrame with columns:
      ticker, catalyst_type, catalyst_date, source, description, confidence
    Sorted by ticker then catalyst_date.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    if candidates is None:
        if not LAUNCH_CANDIDATES.exists():
            raise FileNotFoundError(
                f"launch_candidates.parquet not found at {LAUNCH_CANDIDATES}"
            )
        candidates = pd.read_parquet(LAUNCH_CANDIDATES)

    # underlier may be the index or a column
    if "underlier" in candidates.columns:
        tickers = candidates["underlier"].astype(str).tolist()
        sectors = candidates.set_index("underlier")["sector"].to_dict()
    else:
        tickers = candidates.index.astype(str).tolist()
        sectors = candidates["sector"].to_dict()

    info_cache: dict[str, str] = {}
    rows: list[dict] = []
    for i, ticker in enumerate(tickers, 1):
        sector = sectors.get(ticker)
        try:
            cats = _catalysts_for_ticker(
                ticker, sector, today, info_cache, use_cache=use_cache
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("catalyst fetch failed for %s: %s", ticker, exc)
            cats = []
        for c in cats:
            rows.append(asdict(c))
        if i % 25 == 0:
            log.info("catalysts: processed %d/%d tickers", i, len(tickers))
        # be polite to yfinance
        time.sleep(0.05)

    df = pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "catalyst_type",
            "catalyst_date",
            "source",
            "description",
            "confidence",
        ],
    )
    if not df.empty:
        df["catalyst_date"] = pd.to_datetime(df["catalyst_date"])
        df = df.sort_values(["ticker", "catalyst_date"]).reset_index(drop=True)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    log.info("wrote %d catalyst rows -> %s", len(df), OUTPUT_PATH)
    return df


def soonest_catalyst_per_ticker(
    calendar: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Renderer helper — soonest upcoming catalyst per ticker.

    Returns a DataFrame indexed by ticker with the top catalyst row.
    """
    if calendar is None:
        calendar = pd.read_parquet(OUTPUT_PATH)
    if calendar.empty:
        return calendar
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    upcoming = calendar[calendar["catalyst_date"] >= today].copy()
    if upcoming.empty:
        # fall back to most-recent past (FDA recent-news case)
        upcoming = calendar.copy()
    upcoming = upcoming.sort_values(["ticker", "catalyst_date"])
    return upcoming.groupby("ticker", as_index=True).first()


def why_now_tag(
    ticker: str, calendar: pd.DataFrame | None = None
) -> str | None:
    """Renderer helper — single-string "why now" tag for a ticker."""
    soon = soonest_catalyst_per_ticker(calendar)
    if ticker not in soon.index:
        return None
    row = soon.loc[ticker]
    d = pd.Timestamp(row["catalyst_date"]).date()
    delta = (d - datetime.now(timezone.utc).date()).days
    when = (
        f"in {delta}d"
        if delta > 0
        else (f"{-delta}d ago" if delta < 0 else "today")
    )
    return f"{row['catalyst_type'].upper()} {when}: {row['description']}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    df = build_catalyst_calendar(use_cache=True)
    print(f"Built catalyst_calendar.parquet — {len(df)} rows")
    if not df.empty:
        print(df.head(20).to_string())

"""Notable Voices — curated thought-leader quote layer for stock recs.

First-pass static loader. Reads:
    data/notable_voices/voices_config.yaml      (voice roster)
    data/notable_voices/quotes_<DATE>.json      (curated quotes; latest wins)

Public surface:
    load_voices()                            -> dict[name -> voice meta]
    load_quotes(date_str=None)               -> list[quote dict]
    quotes_for_ticker(ticker, themes=None,
                      limit=2)               -> list[quote dict]
    format_voice_line(quote)                 -> short HTML snippet for cards

Design constraints:
- No network. Pure file IO + in-memory ranking.
- Tolerant of missing files (returns empty list, never raises).
- Cheap to call from the renderer per card (results are memoized).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from html import escape
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
VOICES_DIR = REPO_ROOT / "data" / "notable_voices"
VOICES_CONFIG_PATH = VOICES_DIR / "voices_config.yaml"

_QUOTES_FILENAME_RE = re.compile(r"^quotes_(\d{4}-\d{2}-\d{2})\.json$")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_voices() -> dict[str, dict]:
    """Return {voice_name: voice_meta}. Empty dict if config missing."""
    if not VOICES_CONFIG_PATH.exists():
        log.info("notable_voices: %s missing — voices disabled", VOICES_CONFIG_PATH)
        return {}
    if yaml is None:
        log.warning("notable_voices: PyYAML not installed; cannot read voices config")
        return {}
    try:
        with VOICES_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:  # pragma: no cover
        log.warning("notable_voices: failed to parse %s: %s", VOICES_CONFIG_PATH, e)
        return {}
    out: dict[str, dict] = {}
    for v in (data.get("voices") or []):
        name = (v or {}).get("name")
        if name:
            out[name] = v
    return out


def _resolve_quotes_path(date_str: str | None) -> Path | None:
    """Pick the quotes JSON to load. If date_str given, prefer that file;
    otherwise return the most recent `quotes_YYYY-MM-DD.json` in VOICES_DIR.
    """
    if not VOICES_DIR.exists():
        return None
    if date_str:
        candidate = VOICES_DIR / f"quotes_{date_str}.json"
        if candidate.exists():
            return candidate
    # Glob and take the lexicographically largest (ISO dates sort correctly)
    candidates = []
    for p in VOICES_DIR.glob("quotes_*.json"):
        m = _QUOTES_FILENAME_RE.match(p.name)
        if m:
            candidates.append((m.group(1), p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


@lru_cache(maxsize=4)
def load_quotes(date_str: str | None = None) -> tuple[dict, ...]:
    """Return a tuple of quote dicts (tuple so it stays hashable for cache).
    Empty tuple on missing/malformed input.
    """
    path = _resolve_quotes_path(date_str)
    if path is None:
        log.info("notable_voices: no quotes_*.json found in %s", VOICES_DIR)
        return ()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:  # pragma: no cover
        log.warning("notable_voices: failed to parse %s: %s", path, e)
        return ()
    quotes = raw.get("quotes") if isinstance(raw, dict) else None
    if not isinstance(quotes, list):
        return ()
    cleaned: list[dict] = []
    for q in quotes:
        if not isinstance(q, dict):
            continue
        # Normalise keys we rely on
        q = dict(q)
        q["tickers"] = [str(t).upper().strip() for t in (q.get("tickers") or []) if t]
        q["themes"] = [str(t).strip().lower() for t in (q.get("themes") or []) if t]
        cleaned.append(q)
    return tuple(cleaned)


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


def _score_quote(q: dict, ticker: str, themes: set[str]) -> float:
    """Higher = better fit. Returns 0 if neither ticker nor theme matches —
    callers filter on > 0. Ticker hit dominates; theme overlap is secondary;
    recency is only a tiebreaker (added after a real hit).
    """
    tickers = set(q.get("tickers") or [])
    q_themes = set(q.get("themes") or [])
    score = 0.0
    matched = False
    if ticker and ticker in tickers:
        score += 10.0
        matched = True
    # Theme overlap (only counts if voice explicitly tagged with one)
    if themes and q_themes:
        overlap = len(themes & q_themes)
        if overlap:
            score += 2.0 * overlap
            matched = True
    if not matched:
        return 0.0
    # Recency tiebreaker — ISO date string sorts naturally
    date = str(q.get("date") or "")
    if date:
        try:
            year = int(date[:4])
            month = int(date[5:7]) if len(date) >= 7 else 6
            score += min(1.0, max(0.0, (year - 2023) + month / 12.0)) * 0.05
        except (ValueError, IndexError):
            pass
    return score


def quotes_for_ticker(
    ticker: str,
    themes: list[str] | set[str] | None = None,
    limit: int = 2,
    date_str: str | None = None,
) -> list[dict]:
    """Return up to `limit` most-relevant quote dicts for `ticker`.

    A quote qualifies if its ticker list contains `ticker` OR (if no ticker
    hit) any of its themes overlap with `themes`. Pure-theme matches are
    ranked below ticker hits.
    """
    if not ticker:
        return []
    ticker = ticker.upper().strip()
    themes_set = {str(t).strip().lower() for t in (themes or []) if t}

    quotes = load_quotes(date_str)
    if not quotes:
        return []

    scored: list[tuple[float, int, dict]] = []
    for idx, q in enumerate(quotes):
        s = _score_quote(q, ticker, themes_set)
        if s <= 0:
            continue
        scored.append((s, -idx, q))  # idx tiebreaker keeps stable ordering

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [q for _, _, q in scored[:max(0, limit)]]


# ---------------------------------------------------------------------------
# Renderer helpers
# ---------------------------------------------------------------------------


def format_voice_line(quote: dict) -> str:
    """Compact single-line HTML snippet for a card.

    Example output:
        <div ...><strong>Aschenbrenner</strong> on AI infrastructure:
        "Power is the new bottleneck." <a href=...>source</a></div>
    """
    if not quote:
        return ""
    voice = str(quote.get("voice") or "").strip()
    last_name = voice.split()[-1] if voice else "Source"
    text = str(quote.get("quote") or "").strip()
    if not text:
        return ""
    url = str(quote.get("source_url") or "").strip()
    themes = quote.get("themes") or []
    theme_label = ""
    if themes:
        # Humanize "ai_infrastructure" -> "AI infrastructure",
        # "memory_hbm" -> "memory HBM", etc.
        _ACRONYMS = {"ai", "hbm", "ev", "etf", "tsm", "us", "uk", "llm"}
        words = str(themes[0]).replace("_", " ").strip().split()
        if words:
            out = []
            for i, w in enumerate(words):
                if w.lower() in _ACRONYMS:
                    out.append(w.upper())
                elif i == 0:
                    out.append(w[:1].upper() + w[1:])
                else:
                    out.append(w)
            theme_label = " ".join(out)

    label_html = f"<strong>{escape(last_name)}</strong>"
    if theme_label:
        label_html += f" on {escape(theme_label)}"
    src_html = (
        f' <a href="{escape(url)}" style="color:#7f8c8d;text-decoration:none;'
        f'border-bottom:1px dotted #7f8c8d;">source</a>'
        if url else ""
    )
    return (
        '<div style="font-size:11px;color:#566573;line-height:1.5;'
        'margin-top:8px;padding-top:8px;border-top:1px dashed #ecf0f1;">'
        f'{label_html}: '
        f'<span style="color:#1a1a2e;font-style:italic;">"{escape(text)}"</span>'
        f'{src_html}'
        '</div>'
    )


def render_voices_for_ticker(
    ticker: str,
    themes: list[str] | set[str] | None = None,
    limit: int = 1,
) -> str:
    """Convenience: lookup + format. Returns "" if no match."""
    matches = quotes_for_ticker(ticker, themes=themes, limit=limit)
    if not matches:
        return ""
    return "".join(format_voice_line(q) for q in matches)


__all__ = [
    "load_voices",
    "load_quotes",
    "quotes_for_ticker",
    "format_voice_line",
    "render_voices_for_ticker",
]

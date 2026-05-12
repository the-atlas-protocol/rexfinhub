# Upgrade FB2 — Notable Voices qualitative layer

**Branch:** `audit-stockrecs-FB2-voices`
**Worktree:** `C:/Projects/rexfinhub-FB2`
**Owner:** stockrecs FB2 wave
**Status:** first-pass curated quote layer (no RSS ingestion yet)

## What this adds

A small, config-driven layer that surfaces a single named-thought-leader
quote on each per-ticker recommendation card. The quote is selected by
matching the card's ticker (and, where available, its theme tags from
`screener/li_engine/themes.yaml`) against a curated JSON of attributable
quotes.

The default layer ships with **9 voices** and **36 quotes**, weighted
toward AI / compute / power / nuclear / energy / crypto-equity themes —
i.e. the secular trades currently driving REX product placement.

Leopold Aschenbrenner ("Situational Awareness") gets the largest
allocation: **8 quotes** covering scaling laws, compute centralization,
power/grid constraints, sovereign-AI race, China, model-weight security,
and HBM as the binding constraint on training.

## Files

| Path | Role |
|---|---|
| `data/notable_voices/voices_config.yaml` | Voice roster (name, affiliation, focus, URL, handle) |
| `data/notable_voices/quotes_2026-05-12.json` | Curated quote pool (the matcher reads the lexicographically latest `quotes_*.json`) |
| `screener/li_engine/data/notable_voices.py` | Loader + matcher + HTML helper |
| `screener/li_engine/analysis/weekly_v2_report.py` | Renderer touch — voice line appended inside `_render_thesis_panel` |

## Voice roster

| Voice | Affiliation | Quotes |
|---|---|---:|
| Leopold Aschenbrenner | Former OpenAI; *Situational Awareness* essay series | 8 |
| Ben Thompson | Stratechery | 4 |
| Dwarkesh Patel | Dwarkesh Podcast | 4 |
| Cathie Wood | ARK Invest | 4 |
| Chamath Palihapitiya | Social Capital; All-In Podcast | 3 |
| Jensen Huang | NVIDIA | 3 |
| Sam Altman | OpenAI | 3 |
| Lyn Alden | Lyn Alden Investment Strategy | 3 |
| Doomberg | Doomberg Substack | 4 |

Total: **36 quotes** across **9 voices** (config gate ≥5; quotes gate ≥30).

## Sourcing notes

All quotes are tagged `"kind": "paraphrase"` — they are faithful
short-form restatements of public essays / podcast appearances /
keynotes, not verbatim transcripts. Each entry carries a real
`source_url` pointing at the original artifact (essay page, podcast
episode, NVIDIA newsroom release, Sam Altman blog post, etc.).

Aschenbrenner's quotes are drawn from the *Situational Awareness* series
sub-pages (`from-gpt-4-to-agi`, `racing-to-the-trillion-dollar-cluster`,
`the-free-world-must-prevail`, `lock-down-the-labs`,
`from-agi-to-superintelligence`, `the-project`).

Themes referenced map onto the existing `themes.yaml` taxonomy where
possible: `ai_infrastructure`, `ai_applications`, `semiconductors`,
`memory`, `memory_hbm`, `nuclear`, `power`, `energy`, `crypto_equity`,
`ev_battery`, `biotech_gene`. A few new tags were added that the
recommender does not yet rank on but which the matcher uses for
quote-to-ticker fit: `sovereign_ai`, `cybersecurity`, `commodities`,
`autonomous`, `robotics`, `crypto`.

## Matcher behaviour

`quotes_for_ticker(ticker, themes, limit=2)`

- Ticker hit = +10 (dominant signal)
- Theme overlap = +2 per matched theme tag
- Recency = small tiebreaker only (≤ +0.05)
- Returns `[]` when neither ticker nor theme matches — never invents a
  match for unrelated names

Verified on the spec's sample tickers:

| Ticker | Top voice |
|---|---|
| NVDA | Aschenbrenner ("Counting the OOMs…") |
| TSLA | Wood (autonomous / robotaxi) |
| MSTR | Wood (bitcoin as monetary network) |
| VST  | Aschenbrenner (trillion-dollar clusters / power) |
| OKLO | Aschenbrenner (compute clusters / power) |
| MU   | Aschenbrenner (HBM is the binding constraint) |
| AAPL | Thompson (on-device AI distribution) |
| XYZ_NOPE | (no match — empty list) |

## Renderer integration

One helper, one call site, fail-soft:

```python
from screener.li_engine.data.notable_voices import render_voices_for_ticker
# Inside _render_thesis_panel:
voice_html = render_voices_for_ticker(
    ticker, themes=_ticker_themes(ticker), limit=1,
)
```

The voice line renders below the thesis paragraphs, separated by a
dashed top border, sized 11px / muted color so it doesn't compete
visually with the thesis itself. Format:

> **Aschenbrenner** on AI infrastructure: *"Power is the new
> bottleneck."* [source]

If quotes load fails, voices are missing, or the ticker has no match,
the card renders identically to the pre-FB2 version (no empty div, no
exception bubble — wrapped in `try/except`).

## Smoke-test results

Five sample cards verified to surface a voice line:
NVDA, TSLA, MSTR, VST, OKLO — all match. XYZ_NOPE correctly renders
without a voice line (empty HTML).

```
NVDA         voice=True  len=704
TSLA         voice=True  len=701
MSTR         voice=True  len=665
VST          voice=True  len=708
OKLO         voice=True  len=694
XYZ_NOPE     voice=False  len=110
```

## Known limitations / next-job items

- **No RSS ingestion** — every quote is a hand-curated paraphrase. Full
  freshness pipeline (Substack RSS, podcast transcript ingestion, X
  thread capture) is the FB3 / FB4 follow-up.
- **No quote rotation** — the matcher returns the same top quote every
  week. A "do not repeat within N weeks" diversity layer would prevent
  the same Aschenbrenner line from showing on NVDA cards forever.
- **No verbatim quotes** — paraphrase only, by design (cleaner attribution
  + shorter card footprint). When transcript ingestion lands we can
  switch to direct quotes with timestamp anchors.
- **Theme taxonomy drift** — six tags used by quotes (`sovereign_ai`,
  `power`, `cybersecurity`, `commodities`, `autonomous`, `robotics`)
  are not in `themes.yaml`. Either add them to `themes.yaml` or accept
  that those quotes can only match by ticker.
- **No editorial review gate** — a voice could conceivably show up on
  a card whose recommendation contradicts the quote's spirit. Tonight's
  curated set was authored to be broadly directional — but a future
  scorer should weight quote-thesis sentiment alignment.

"""Recommendation history & self-grading (Wave E1, 2026-05-11).

Three responsibilities:

  1. ``append_weekly_recommendations`` — called by `weekly_v2_report.render`
     after a successful build. One row per (week, ticker, tier).
  2. ``grade_open_recommendations`` — invoked weekly by
     ``scripts/grade_recommendations.py``. Walks open recs and updates
     outcome columns by comparing against current state of `filings`,
     `mkt_master_data`, `mkt_time_series`.
  3. ``hit_rate_stats`` — read-only aggregator. Returns a dict consumed
     by the B-renderer "Track Record" footer. Keep cheap (single SQL
     pass) so it can run inline during render.

Namespace note: this is intentionally separate from the `mkt_*` tables.
The market-data pipeline owns `mkt_*` and rebuilds them daily; the rec
history is append-only and survives every DB swap (so don't put it in a
table the upload route truncates).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB = _ROOT / "data" / "etp_tracker.db"

# Statuses are sticky: once set, the grader only refines AUM, never
# walks them back to a softer state.
_TERMINAL_STATUSES = {"launched", "killed", "abandoned"}

# How long a "filing" recommendation gets to play out before we mark it
# abandoned (in days). 365d covers the typical 75-day SEC review +
# extensions + the issuer's actual launch decision window.
_ABANDON_AFTER_DAYS = 365


# ---------------------------------------------------------------------------
# Row dataclass — what the renderer hands us per recommendation.
# ---------------------------------------------------------------------------
@dataclass
class RecRow:
    """One recommendation, ready for insert.

    week_of is normalized to the Monday of the report's week so that
    the (week_of, ticker, tier) UNIQUE constraint is stable across
    same-week reruns.
    """
    week_of: date
    ticker: str
    confidence_tier: str               # HIGH | MEDIUM | WATCH
    composite_score: float | None
    fund_name: str | None
    thesis_snippet: str | None
    suggested_rex_ticker: str | None
    section: str | None                # launch | filing | money_flow

    def normalized(self) -> "RecRow":
        """Return a copy with ticker upper-cased and trimmed."""
        t = (self.ticker or "").strip().upper().split()[0] if self.ticker else ""
        thesis = (self.thesis_snippet or "")[:280] or None
        return RecRow(
            week_of=self.week_of,
            ticker=t,
            confidence_tier=(self.confidence_tier or "WATCH").upper().strip(),
            composite_score=self.composite_score,
            fund_name=(self.fund_name or None),
            thesis_snippet=thesis,
            suggested_rex_ticker=(self.suggested_rex_ticker or None),
            section=(self.section or None),
        )


def monday_of(d: date) -> date:
    """Return the Monday of the week containing *d* (UTC date logic)."""
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------------------
# 1. Append
# ---------------------------------------------------------------------------
def append_weekly_recommendations(
    rows: Iterable[RecRow],
    db_path: Path | str | None = None,
) -> dict:
    """Insert a batch of recommendations for the current week.

    Idempotent: relies on the UNIQUE(week_of, ticker, confidence_tier)
    constraint. If the same week is rerun, existing rows are kept and
    only new (ticker, tier) combinations are added. This means a single
    rerun never duplicates entries, but it ALSO means a re-tier (e.g.
    HIGH → MEDIUM in a later rerun) creates a second row — that's
    intentional, the audit trail wants to see the tier walk.

    Returns ``{"inserted": N, "skipped": M}``.
    """
    db = Path(db_path) if db_path else _DB
    if not db.exists():
        log.warning("DB does not exist at %s — skipping append", db)
        return {"inserted": 0, "skipped": 0}

    inserted = 0
    skipped = 0
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        # Defensive: make sure the table exists (in case create_all hasn't
        # run yet in a fresh dev DB). The schema mirrors the ORM model.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS recommendation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at DATETIME NOT NULL,
                week_of DATE NOT NULL,
                ticker VARCHAR(30) NOT NULL,
                fund_name VARCHAR(300),
                confidence_tier VARCHAR(10) NOT NULL,
                composite_score FLOAT,
                thesis_snippet TEXT,
                suggested_rex_ticker VARCHAR(30),
                section VARCHAR(20),
                outcome_status VARCHAR(20),
                outcome_at DATETIME,
                outcome_aum_6mo FLOAT,
                outcome_aum_12mo FLOAT,
                graded_at DATETIME,
                matched_product_ticker VARCHAR(30),
                grading_note TEXT,
                CONSTRAINT uq_recommendation_history_week_ticker_tier
                    UNIQUE (week_of, ticker, confidence_tier)
            )
            """
        )

        for raw in rows:
            r = raw.normalized()
            if not r.ticker:
                skipped += 1
                continue
            try:
                cur.execute(
                    """
                    INSERT INTO recommendation_history (
                        generated_at, week_of, ticker, fund_name,
                        confidence_tier, composite_score, thesis_snippet,
                        suggested_rex_ticker, section
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        r.week_of.isoformat(),
                        r.ticker,
                        r.fund_name,
                        r.confidence_tier,
                        r.composite_score,
                        r.thesis_snippet,
                        r.suggested_rex_ticker,
                        r.section,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # Same (week, ticker, tier) already present — idempotent skip.
                skipped += 1
        conn.commit()
    finally:
        conn.close()

    log.info(
        "recommendation_history: inserted=%d skipped=%d (week=%s)",
        inserted, skipped,
        rows[0].week_of.isoformat() if isinstance(rows, list) and rows else "n/a",
    )
    return {"inserted": inserted, "skipped": skipped}


# ---------------------------------------------------------------------------
# Helpers used by both grading and hit-rate.
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def _safe_query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception as e:
        log.warning("safe_query failed: %s | %s", e, sql[:120])
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# 2. Grade
# ---------------------------------------------------------------------------
def grade_open_recommendations(
    db_path: Path | str | None = None,
    today: date | None = None,
) -> dict:
    """Walk every open rec and update its outcome columns.

    Logic per row (in priority order):
      1. If a REX product on this underlier is now ACTV in mkt_master_data
         → outcome_status='launched', outcome_at=today.
      2. Else if a REX 485APOS exists for this underlier filed AFTER the
         rec's week_of → outcome_status='rex_filed'.
      3. Else if any non-REX issuer 485APOS for this underlier filed
         AFTER the rec's week_of → outcome_status='competitor_filed'.
      4. Else if rec is older than _ABANDON_AFTER_DAYS and still nothing
         observed → outcome_status='abandoned'.
      5. Otherwise leave NULL (still open).

    For 'launched' rows, also fill outcome_aum_6mo and outcome_aum_12mo
    when the AUM history reaches that age (read from `mkt_master_data.aum`
    or, if available, `mkt_time_series.aum_value` at the right months_ago).

    Idempotent: re-running the grader on already-graded rows updates
    `graded_at` and refines AUM, but never reverts a terminal status.
    A status that was 'rex_filed' may upgrade to 'launched' on a later
    pass — that's correct behaviour, the funnel only moves forward.
    """
    db = Path(db_path) if db_path else _DB
    today = today or date.today()
    if not db.exists():
        log.warning("DB does not exist at %s — skipping grade", db)
        return {"graded": 0, "newly_terminal": 0}

    conn = sqlite3.connect(str(db))
    graded = 0
    newly_terminal = 0
    try:
        if not _table_exists(conn, "recommendation_history"):
            log.info("recommendation_history table not yet created — nothing to grade")
            return {"graded": 0, "newly_terminal": 0}

        # Open rows = anything not in a terminal state. We re-check
        # 'rex_filed' / 'competitor_filed' rows because they may upgrade
        # to 'launched'.
        open_df = _safe_query(
            conn,
            """
            SELECT id, week_of, ticker, confidence_tier, outcome_status,
                   suggested_rex_ticker, matched_product_ticker
            FROM recommendation_history
            WHERE outcome_status IS NULL
               OR outcome_status NOT IN ('launched','killed','abandoned')
            """,
        )
        if open_df.empty:
            log.info("No open recommendations to grade.")
            return {"graded": 0, "newly_terminal": 0}

        # Pre-load REX-related state once (avoid N+1 queries).
        rex_active = _safe_query(
            conn,
            """
            SELECT ticker, map_li_underlier, market_status, aum,
                   inception_date
            FROM mkt_master_data
            WHERE is_rex = 1 AND map_li_underlier IS NOT NULL
            """,
        )
        if not rex_active.empty:
            rex_active["underlier_clean"] = (
                rex_active["map_li_underlier"]
                .astype(str).str.split().str[0].str.upper()
            )

        # Filings keyed by underlier — we need the date + whether it's REX.
        filings_df = _safe_query(
            conn,
            """
            SELECT f.id, f.filing_date, f.registrant, f.form, fe.series_name
            FROM filings f
            JOIN fund_extractions fe ON fe.filing_id = f.id
            WHERE f.form IN ('485APOS','485BPOS','N-1A')
            """,
        )
        if not filings_df.empty:
            try:
                from screener.li_engine.analysis.filed_underliers import extract_underlier
                filings_df["underlier"] = filings_df["series_name"].apply(extract_underlier)
            except Exception as e:
                log.warning("Could not import extract_underlier (%s) — falling back", e)
                filings_df["underlier"] = filings_df["series_name"].astype(str).str.extract(
                    r"\b([A-Z]{2,5})\b", expand=False
                )
            filings_df = filings_df.dropna(subset=["underlier"]).copy()
            filings_df["underlier"] = filings_df["underlier"].str.upper()
            filings_df["filing_date"] = pd.to_datetime(filings_df["filing_date"], errors="coerce")
            filings_df["is_rex"] = filings_df["registrant"].astype(str).str.contains(
                r"REX|ETF Opportunities", case=False, regex=True, na=False,
            )

        cur = conn.cursor()
        now_iso = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        for _, rec in open_df.iterrows():
            ticker = (rec["ticker"] or "").upper()
            week_of = pd.to_datetime(rec["week_of"]).date()
            previous_status = rec["outcome_status"]

            new_status: str | None = None
            matched_ticker: str | None = rec["matched_product_ticker"]
            note: str | None = None
            outcome_at_iso: str | None = None
            aum_6mo: float | None = None
            aum_12mo: float | None = None

            # --- Check 1: REX launched product on this underlier ---
            if not rex_active.empty:
                hit = rex_active[
                    (rex_active["underlier_clean"] == ticker)
                    & (rex_active["market_status"].astype(str).str.upper().isin(["ACTV", "ACTIVE"]))
                ]
                if not hit.empty:
                    new_status = "launched"
                    matched_ticker = str(hit.iloc[0]["ticker"]).split()[0]
                    note = "matched on map_li_underlier + ACTV"
                    # Inception date drives the +6mo / +12mo windows.
                    inc = hit.iloc[0].get("inception_date")
                    cur_aum = hit.iloc[0].get("aum")
                    try:
                        inc_dt = pd.to_datetime(inc).date() if pd.notna(inc) else None
                    except Exception:
                        inc_dt = None
                    if inc_dt and cur_aum is not None and not pd.isna(cur_aum):
                        age_days = (today - inc_dt).days
                        # Loose buckets: snapshot the current AUM into the
                        # appropriate window once it's mature.
                        if age_days >= 180:
                            aum_6mo = float(cur_aum)
                        if age_days >= 365:
                            aum_12mo = float(cur_aum)
                    outcome_at_iso = now_iso

            # --- Check 2/3: Filings observed after the rec week ---
            if new_status is None and not filings_df.empty:
                cand = filings_df[
                    (filings_df["underlier"] == ticker)
                    & (filings_df["filing_date"] >= pd.Timestamp(week_of))
                ]
                if not cand.empty:
                    rex_hits = cand[cand["is_rex"]]
                    if not rex_hits.empty:
                        new_status = "rex_filed"
                        first = rex_hits.sort_values("filing_date").iloc[0]
                        outcome_at_iso = first["filing_date"].strftime("%Y-%m-%d")
                        note = f"REX 485APOS by {first['registrant']}"
                    else:
                        new_status = "competitor_filed"
                        first = cand.sort_values("filing_date").iloc[0]
                        outcome_at_iso = first["filing_date"].strftime("%Y-%m-%d")
                        note = f"competitor 485APOS by {first['registrant']}"

            # --- Check 4: Aged out → abandoned ---
            if new_status is None:
                if (today - week_of).days > _ABANDON_AFTER_DAYS:
                    new_status = "abandoned"
                    outcome_at_iso = now_iso
                    note = f"no filing observed after {_ABANDON_AFTER_DAYS}d"

            # --- Sticky-status guard ---
            # If the prior status is already terminal, only refine AUM.
            if previous_status in _TERMINAL_STATUSES and new_status != "launched":
                new_status = previous_status

            # --- Persist ---
            if new_status is not None:
                # Track 'newly terminal' for the run summary.
                if (
                    new_status in _TERMINAL_STATUSES
                    and previous_status not in _TERMINAL_STATUSES
                ):
                    newly_terminal += 1

                cur.execute(
                    """
                    UPDATE recommendation_history
                    SET outcome_status = ?,
                        outcome_at = COALESCE(outcome_at, ?),
                        outcome_aum_6mo = COALESCE(?, outcome_aum_6mo),
                        outcome_aum_12mo = COALESCE(?, outcome_aum_12mo),
                        matched_product_ticker = COALESCE(?, matched_product_ticker),
                        grading_note = ?,
                        graded_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_status,
                        outcome_at_iso,
                        aum_6mo,
                        aum_12mo,
                        matched_ticker,
                        note,
                        now_iso,
                        int(rec["id"]),
                    ),
                )
                graded += 1

        conn.commit()
    finally:
        conn.close()

    log.info(
        "grade_open_recommendations: graded=%d, newly_terminal=%d (today=%s)",
        graded, newly_terminal, today.isoformat(),
    )
    return {"graded": graded, "newly_terminal": newly_terminal}


# ---------------------------------------------------------------------------
# 3. Hit-rate stats (read-only)
# ---------------------------------------------------------------------------
def hit_rate_stats(
    db_path: Path | str | None = None,
    rolling_days: int = 90,
    today: date | None = None,
) -> dict:
    """Return aggregate stats for the "Track Record" footer.

    Output keys:
      - rolling_days
      - high_total, high_hit, high_hit_rate
      - medium_total, medium_hit, medium_hit_rate
      - watch_total, watch_hit, watch_hit_rate
      - avg_aum_6mo (across launched recs with outcome_aum_6mo populated)
      - tier_accuracy: ratio of HIGH hits to (HIGH+MEDIUM+WATCH hits) — a
        proxy for whether we're tiering correctly (HIGH should over-index
        on actual launches/filings).
      - sample_size_warning: True if the rolling window has < 10 HIGH recs.

    A "hit" = outcome_status in {'launched', 'rex_filed'}. We treat
    'competitor_filed' as a half-signal but don't count it in the
    headline rate (a competitor filing means we spotted real demand,
    but REX missed the window).
    """
    db = Path(db_path) if db_path else _DB
    today = today or date.today()
    out: dict = {
        "rolling_days": rolling_days,
        "as_of": today.isoformat(),
        "high_total": 0, "high_hit": 0, "high_hit_rate": None,
        "medium_total": 0, "medium_hit": 0, "medium_hit_rate": None,
        "watch_total": 0, "watch_hit": 0, "watch_hit_rate": None,
        "avg_aum_6mo": None,
        "tier_accuracy": None,
        "sample_size_warning": True,
    }
    if not db.exists():
        return out

    conn = sqlite3.connect(str(db))
    try:
        if not _table_exists(conn, "recommendation_history"):
            return out

        cutoff = (today - timedelta(days=rolling_days)).isoformat()
        df = _safe_query(
            conn,
            """
            SELECT confidence_tier, outcome_status, outcome_aum_6mo
            FROM recommendation_history
            WHERE week_of >= ?
            """,
            (cutoff,),
        )
        if df.empty:
            return out

        df["confidence_tier"] = df["confidence_tier"].str.upper().str.strip()
        df["is_hit"] = df["outcome_status"].isin(["launched", "rex_filed"])

        for tier in ("HIGH", "MEDIUM", "WATCH"):
            sub = df[df["confidence_tier"] == tier]
            total = int(len(sub))
            hit = int(sub["is_hit"].sum())
            rate = (hit / total) if total else None
            out[f"{tier.lower()}_total"] = total
            out[f"{tier.lower()}_hit"] = hit
            out[f"{tier.lower()}_hit_rate"] = round(rate, 3) if rate is not None else None

        # Average AUM 6mo across launched recs that have matured.
        aum = df["outcome_aum_6mo"].dropna()
        if not aum.empty:
            out["avg_aum_6mo"] = round(float(aum.mean()), 2)

        # Tier accuracy: are HIGH-tier recs actually the ones that hit?
        total_hits = int(df["is_hit"].sum())
        if total_hits:
            high_hits = int(df[(df["confidence_tier"] == "HIGH") & df["is_hit"]].shape[0])
            out["tier_accuracy"] = round(high_hits / total_hits, 3)

        out["sample_size_warning"] = out["high_total"] < 10
    finally:
        conn.close()

    return out


def render_track_record_footer(stats: dict) -> str:
    """Tiny HTML snippet for the B-renderer "Track Record" footer.

    Keep inline-styled (the email pipeline strips <style> blocks). The
    snippet is meant to drop into the existing methodology footer, not
    replace it.
    """
    if not stats or stats.get("high_total", 0) == 0:
        return (
            '<div style="font-size:11px;color:#94a3b8;font-style:italic;">'
            'Track record: insufficient history yet.</div>'
        )
    warn = (
        ' <span style="color:#e67e22;font-weight:600;">(small sample)</span>'
        if stats.get("sample_size_warning") else ''
    )
    rate_str = lambda v: ("—" if v is None else f"{v*100:.0f}%")
    avg_aum = stats.get("avg_aum_6mo")
    aum_str = (f"${avg_aum:,.1f}M" if avg_aum is not None else "—")
    return f'''
<div style="font-size:11px;color:#566573;line-height:1.6;margin-top:8px;
            border-top:1px dashed #d4d8dd;padding-top:8px;">
  <strong style="color:#1a1a2e;">Track Record</strong>
  (last {stats.get("rolling_days", 90)} days){warn}<br>
  HIGH: {stats.get("high_hit", 0)}/{stats.get("high_total", 0)}
    ({rate_str(stats.get("high_hit_rate"))}) ·
  MEDIUM: {stats.get("medium_hit", 0)}/{stats.get("medium_total", 0)}
    ({rate_str(stats.get("medium_hit_rate"))}) ·
  WATCH: {stats.get("watch_hit", 0)}/{stats.get("watch_total", 0)}
    ({rate_str(stats.get("watch_hit_rate"))})<br>
  Avg AUM 6mo post-launch: <strong>{aum_str}</strong> ·
  Tier accuracy: {rate_str(stats.get("tier_accuracy"))}
</div>
'''.strip()


# ---------------------------------------------------------------------------
# Helper: build RecRow batch from the renderer's DataFrames.
# ---------------------------------------------------------------------------
def _tier_for(score: float | None, rank: int) -> str:
    """Default tiering rule when the renderer doesn't tag rows.

    Top-3 by score = HIGH, next 4 = MEDIUM, rest = WATCH. Rank is 0-based.
    """
    if rank < 3:
        return "HIGH"
    if rank < 7:
        return "MEDIUM"
    return "WATCH"


def build_rows_from_renderer(
    week_of: date,
    launch_df: pd.DataFrame | None,
    whitespace_df: pd.DataFrame | None,
    thesis_resolver=None,
) -> list[RecRow]:
    """Convert the renderer's launch + whitespace DataFrames into RecRow batch.

    ``thesis_resolver`` is an optional callable ``(ticker) -> str``
    (typically the renderer's `_resolve_company_line`). When omitted we
    just snapshot the ticker symbol.
    """
    out: list[RecRow] = []

    def _row(df: pd.DataFrame, section: str) -> None:
        if df is None or df.empty:
            return
        # Re-sort defensively so tier assignment matches what the
        # renderer actually showed.
        if "composite_score" in df.columns:
            df = df.sort_values("composite_score", ascending=False)
        for rank, (ticker, r) in enumerate(df.iterrows()):
            score = r.get("composite_score") if "composite_score" in df.columns else None
            try:
                score_f = float(score) if score is not None and not pd.isna(score) else None
            except Exception:
                score_f = None
            fund_name = (
                r.get("rex_fund_name")
                or r.get("fund_name")
                or None
            )
            thesis = None
            if thesis_resolver is not None:
                try:
                    thesis = thesis_resolver(ticker)
                except Exception:
                    thesis = None
            out.append(RecRow(
                week_of=week_of,
                ticker=str(ticker),
                confidence_tier=_tier_for(score_f, rank),
                composite_score=score_f,
                fund_name=str(fund_name) if fund_name else None,
                thesis_snippet=thesis,
                suggested_rex_ticker=(
                    str(r.get("suggested_rex_ticker"))
                    if r.get("suggested_rex_ticker") is not None
                    and not pd.isna(r.get("suggested_rex_ticker"))
                    else None
                ),
                section=section,
            ))

    _row(launch_df, "launch")
    _row(whitespace_df, "filing")
    return out

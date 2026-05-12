"""Launch candidates — stocks REX has FILED for but not yet launched.

Per Ryu's spec: "stocks that we have filed for which we identify as good and
deserving of a launch. As long as there isn't a live product already out
there. If there is a filing from a competitor of that stock it should be
indicated by the # Filed Competitors."

Pipeline:
    1. Find all underliers REX has filed for (master_data is_rex=1 + fund_extractions regex)
    2. Exclude underliers where we already have an active product
    3. Exclude underliers where ANY competitor has an active product
    4. Exclude paused filings (BBUP/FIGO/SPOU per Ryu — bbg leaves them as PEND but they won't launch)
    5. Score remaining underliers using whitespace_v4 composite (with hot-theme boost)
    6. Annotate # filed competitors per underlier

Wave A2 (2026-05-11) — TIME-DECAY LAYER
---------------------------------------
For each underlier, we now query the most-recent REX filing and the most-recent
competitor filing. Three decay components multiply into the final composite:

    decay_factor = mention_decay * rex_filing_decay * competitor_filing_decay

`rex_filing_decay`  : 0.50 if last REX filing >90d old without follow-up,
                      0.20 if >180d. AMC's last REX filing is 2024-09-19
                      (>600d ago) — its decay factor crushes the score.
`competitor_filing_decay` : recency-weighted average over a 180-day audit
                      window — fresh competitor filings count more.
`mention_decay`     : exponential past MENTION_FRESH_DAYS (handled in v3).

`is_stale_filing` and `display_effective_label` columns are emitted so the
report layer can render strikethrough / "(STALE — Nd ago)" annotations
without re-querying the DB.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from screener.li_engine.signals import (
    COMPETITOR_AUDIT_DAYS,
    REX_FILING_DEAD_DAYS,
    REX_FILING_STALE_DAYS,
    competitor_filing_recency_weight,
    rex_filing_decay_factor,
)

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT = _ROOT / "data" / "analysis" / "launch_candidates.parquet"

# Manual exclusion list — REX filings Ryu flagged as paused / not actually pursuing
PAUSED_TICKERS = {"BBUP", "FIGO", "SPOU"}


def _clean(t):
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


def _coerce(v):
    if v in (None, "", "#ERROR", "#N/A"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_rex_filed_underliers() -> dict[str, dict]:
    """Return {underlier: {direction, source, fund_name, status}}.
    Combines mkt_master_data is_rex=1 + fund-name regex on REX-registrant filings."""
    conn = sqlite3.connect(str(DB))
    try:
        master = pd.read_sql_query(
            """SELECT ticker, fund_name, market_status, map_li_underlier,
                      map_li_direction, map_li_leverage_amount, inception_date
               FROM mkt_master_data
               WHERE is_rex = 1 AND primary_category = 'LI'
                 AND map_li_underlier IS NOT NULL AND map_li_underlier != ''""",
            conn,
        )

        # All REX filings via registrant
        fe_rex = pd.read_sql_query(
            """
            SELECT fe.series_name, fe.class_contract_name, f.filing_date, f.form, f.registrant
            FROM fund_extractions fe
            JOIN filings f ON f.id = fe.filing_id
            WHERE f.registrant LIKE '%REX%' OR f.registrant LIKE '%ETF Opportunities%'
            """,
            conn,
        )
    finally:
        conn.close()

    out: dict[str, dict] = {}

    # From master_data
    master["underlier"] = master["map_li_underlier"].astype(str).map(_clean)
    master = master[master["underlier"] != ""]
    for _, r in master.iterrows():
        u = r["underlier"]
        if u in PAUSED_TICKERS:
            continue
        if u not in out:
            out[u] = {
                "underlier": u,
                "direction": (r.get("map_li_direction") or "").strip() or "Long",
                "leverage": r.get("map_li_leverage_amount") or "2.0",
                "rex_fund_name": r.get("fund_name"),
                "rex_ticker": r.get("ticker"),
                "rex_market_status": r.get("market_status"),
                "rex_inception": r.get("inception_date"),
                "source": "master_data",
            }

    # From regex on fund names
    from screener.li_engine.analysis.filed_underliers import extract_underlier
    fe_rex["underlier"] = fe_rex["series_name"].apply(extract_underlier)
    fb = fe_rex["underlier"].isna()
    fe_rex.loc[fb, "underlier"] = fe_rex.loc[fb, "class_contract_name"].apply(extract_underlier)
    fe_rex = fe_rex.dropna(subset=["underlier"])
    fe_rex["underlier"] = fe_rex["underlier"].str.upper()

    # Detect direction from name
    import re
    for _, r in fe_rex.iterrows():
        u = r["underlier"]
        if u in PAUSED_TICKERS or u in out:
            continue
        name = r.get("series_name") or r.get("class_contract_name") or ""
        direction = "Long"
        if re.search(r"\b(?:Inverse|Short|Bear)\b", name, re.IGNORECASE):
            direction = "Short"
        out[u] = {
            "underlier": u,
            "direction": direction,
            "leverage": "2.0",
            "rex_fund_name": name,
            "rex_ticker": None,
            "rex_market_status": "FILED",
            "rex_inception": None,
            "source": "fund_extractions_regex",
        }

    return out


def load_filing_dates_for_underliers() -> pd.DataFrame:
    """Per underlier, return:
        last_rex_filing_date  : max filing_date across all REX filings
        last_comp_filing_date : max filing_date across all competitor filings
        comp_filings_180d     : list of (filing_date, registrant) tuples within
                                the audit window for recency-weighted scoring
        closest_effective_date_raw : earliest known effective_date (real or null)

    Driven by the same fund_extractions + filings join the report uses, so the
    "Closest effective date" we emit lines up exactly with what
    `weekly_v2_report._section_card` renders.
    """
    from screener.li_engine.analysis.filed_underliers import extract_underlier

    conn = sqlite3.connect(str(DB))
    try:
        df = pd.read_sql_query(
            """
            SELECT fe.series_name, fe.class_contract_name, fe.effective_date,
                   f.filing_date, f.form, f.registrant
            FROM fund_extractions fe
            JOIN filings f ON f.id = fe.filing_id
            WHERE f.filing_date IS NOT NULL
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=[
            "last_rex_filing_date", "last_comp_filing_date",
            "closest_effective_date_raw", "n_comp_filings_180d",
            "competitor_filing_decay",
        ])

    df["underlier"] = df["series_name"].apply(extract_underlier)
    fb = df["underlier"].isna()
    df.loc[fb, "underlier"] = df.loc[fb, "class_contract_name"].apply(extract_underlier)
    df = df.dropna(subset=["underlier"])
    df["underlier"] = df["underlier"].str.upper()
    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["effective_date"] = pd.to_datetime(df["effective_date"], errors="coerce")
    df = df.dropna(subset=["filing_date"])

    df["is_rex"] = df["registrant"].astype(str).str.contains(
        r"REX|ETF Opportunities", case=False, na=False, regex=True
    )

    today = pd.Timestamp(datetime.now(timezone.utc).date())
    df["age_days"] = (today - df["filing_date"]).dt.days.astype(float)

    rex_only = df[df["is_rex"]]
    comp_only = df[~df["is_rex"]]

    # Per-underlier aggregates
    last_rex = rex_only.groupby("underlier")["filing_date"].max().rename("last_rex_filing_date")
    last_comp = comp_only.groupby("underlier")["filing_date"].max().rename("last_comp_filing_date")
    closest_eff = df.groupby("underlier")["effective_date"].min().rename("closest_effective_date_raw")

    # Competitor decay: recency-weighted average over audit window
    audit_window = comp_only[comp_only["age_days"] <= COMPETITOR_AUDIT_DAYS].copy()
    if not audit_window.empty:
        audit_window["recency_w"] = audit_window["age_days"].apply(
            lambda d: competitor_filing_recency_weight(d)
        )
        # Per underlier: mean recency weight across all in-window comp filings.
        # An underlier with one filing 7d ago scores ~0.96; one with several
        # filings 150d ago scores ~0.17; one with no in-window filings -> 0.
        comp_decay = audit_window.groupby("underlier")["recency_w"].mean().rename("competitor_filing_decay")
        comp_count = audit_window.groupby("underlier").size().rename("n_comp_filings_180d")
    else:
        comp_decay = pd.Series(dtype=float, name="competitor_filing_decay")
        comp_count = pd.Series(dtype=int, name="n_comp_filings_180d")

    out = pd.concat([last_rex, last_comp, closest_eff, comp_decay, comp_count], axis=1)
    out["n_comp_filings_180d"] = out["n_comp_filings_180d"].fillna(0).astype(int)
    # Underliers with comp filings outside window get 0.0; with no filings at
    # all in DB get NaN (treated as "no competitor pressure" at scoring time).
    return out


def load_competitor_status() -> pd.DataFrame:
    """Return per underlier: n_active_competitor, n_filed_competitor (filed not active)."""
    cc_path = _ROOT / "data" / "analysis" / "competitor_counts.parquet"
    if not cc_path.exists():
        log.warning("competitor_counts.parquet missing")
        return pd.DataFrame()
    return pd.read_parquet(cc_path)


def load_signal_data() -> pd.DataFrame:
    """Pull stock metrics for whichever tickers we need to score."""
    conn = sqlite3.connect(str(DB))
    try:
        run_id = conn.execute(
            "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
            "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?", (run_id,)
        ).fetchall()
    finally:
        conn.close()

    recs = []
    for ticker, blob in rows:
        if not blob:
            continue
        try:
            d = json.loads(blob)
            d = d[0] if isinstance(d, list) else d
        except json.JSONDecodeError:
            continue
        insider = _coerce(d.get("% Insider Shares Outstanding"))
        if insider is not None and insider > 100:
            insider = None

        recs.append({
            "ticker": _clean(ticker),
            "market_cap": _coerce(d.get("Mkt Cap")),
            "total_oi": _coerce(d.get("Total OI")),
            "rvol_30d": _coerce(d.get("Volatility 30D")),
            "rvol_90d": _coerce(d.get("Volatility 90D")),
            "ret_1m": _coerce(d.get("1M Total Return")),
            "ret_3m": _coerce(d.get("3M Total Return")),
            "ret_1y": _coerce(d.get("1Y Total Return")),
            "si_ratio": _coerce(d.get("Short Interest Ratio")),
            "insider_pct": insider,
            "inst_own_pct": _coerce(d.get("Institutional Owner % Shares Outstanding")),
            "sector": d.get("GICS Sector"),
        })
    df = pd.DataFrame(recs)
    df = df[df["ticker"] != ""].drop_duplicates("ticker").set_index("ticker")
    return df


def build() -> pd.DataFrame:
    rex_filed = get_rex_filed_underliers()
    competitor = load_competitor_status()
    signals = load_signal_data()

    log.info("REX-filed underliers (raw): %d", len(rex_filed))
    log.info("Competitor counts available: %d", len(competitor))

    rows = []
    for u, info in rex_filed.items():
        # Skip if REX has an active product
        rex_active_long = competitor.loc[u, "rex_active_long"] if u in competitor.index else 0
        rex_active_short = competitor.loc[u, "rex_active_short"] if u in competitor.index else 0
        if rex_active_long > 0 or rex_active_short > 0:
            continue  # already launched

        # Skip if any competitor has an active product
        comp_active_long = competitor.loc[u, "competitor_active_long"] if u in competitor.index else 0
        comp_active_short = competitor.loc[u, "competitor_active_short"] if u in competitor.index else 0
        if comp_active_long > 0 or comp_active_short > 0:
            continue  # market is taken

        # Build row
        row = dict(info)
        if u in competitor.index:
            row["competitor_filed_long"] = int(competitor.loc[u, "competitor_filed_long"])
            row["competitor_filed_short"] = int(competitor.loc[u, "competitor_filed_short"])
            row["competitor_filed_total"] = (
                int(competitor.loc[u].get("competitor_filed_long", 0))
                + int(competitor.loc[u].get("competitor_filed_short", 0))
                + int(competitor.loc[u].get("competitor_extra_long", 0))
                + int(competitor.loc[u].get("competitor_extra_short", 0))
            )
        else:
            row["competitor_filed_long"] = 0
            row["competitor_filed_short"] = 0
            row["competitor_filed_total"] = 0

        # Add bbg signal data — `has_signals` is now derived downstream by
        # annotate_signal_strength rather than being set here as a binary
        # "did bbg return a row?" flag. (See A3 upgrade.)
        if u in signals.index:
            sig = signals.loc[u]
            for col in ("market_cap", "total_oi", "rvol_30d", "rvol_90d",
                        "ret_1m", "ret_3m", "ret_1y", "si_ratio", "insider_pct",
                        "inst_own_pct", "sector"):
                row[col] = sig.get(col)

        rows.append(row)

    df = pd.DataFrame(rows).set_index("underlier")

    # Score using same methodology as v3
    if not df.empty:
        from screener.li_engine.analysis.whitespace_v3 import compute_score_v3
        from screener.li_engine.analysis.whitespace_v2 import (
            load_themes, load_apewisdom_full_map,
        )
        from screener.li_engine.analysis.signal_strength import (
            annotate_signal_strength, signal_strength_multiplier,
        )

        themes = load_themes()
        # Single ApeWisdom fetch — feed the rich blob to the strength
        # annotator and the legacy mentions-only int to the v3 scorer.
        ape_full = load_apewisdom_full_map(set(df.index))
        mentions = {t: blob["mentions_24h"] for t, blob in ape_full.items()}

        df = compute_score_v3(df, themes, mentions)

        # ------------------------------------------------------------------
        # WAVE A2: TIME-DECAY OVERRIDES (filing-driven, per-underlier)
        # whitespace_v3 set rex/competitor decay = 1.0 because it has no
        # filing context. We have it — apply real values now.
        # ------------------------------------------------------------------
        filing_dates = load_filing_dates_for_underliers()
        # whitespace_v3 set rex_filing_decay/competitor_filing_decay to 1.0 as
        # placeholders — drop them before the join so we get the real values.
        df = df.drop(columns=["rex_filing_decay", "competitor_filing_decay",
                              "days_since_rex_filing", "days_since_competitor_filing"],
                     errors="ignore")
        df = df.join(filing_dates, how="left")

        today = pd.Timestamp(datetime.now(timezone.utc).date())
        df["days_since_rex_filing"] = (today - df["last_rex_filing_date"]).dt.days
        df["days_since_competitor_filing"] = (today - df["last_comp_filing_date"]).dt.days

        # REX filing decay (step penalty for stale leads)
        df["rex_filing_decay"] = df["days_since_rex_filing"].apply(
            lambda d: rex_filing_decay_factor(d if pd.notna(d) else None)
        )

        # Competitor filing decay default = 1.0 (no in-window filings = no
        # decay penalty); real value comes from filing_dates join above.
        df["competitor_filing_decay"] = df["competitor_filing_decay"].fillna(1.0)

        # Stale flag — used by report layer to render "(STALE — Nd ago)"
        df["is_stale_filing"] = df["days_since_rex_filing"] >= REX_FILING_STALE_DAYS

        # Pre-built display label so the report can render staleness without
        # re-doing the math. None when no closest_effective_date_raw exists;
        # report falls back to its existing "Closest effective date: n/a"
        # branch in that case.
        def _label(row):
            ced = row.get("closest_effective_date_raw")
            if pd.isna(ced):
                return None
            base = pd.to_datetime(ced).date().isoformat()
            d = row.get("days_since_rex_filing")
            if pd.notna(d) and d >= REX_FILING_STALE_DAYS:
                tag = "DEAD" if d >= REX_FILING_DEAD_DAYS else "STALE"
                return f"{base} (~{tag} — {int(d)}d since last REX filing, no follow-up)"
            return base
        df["display_effective_label"] = df.apply(_label, axis=1)

        # Recompute final composite with full decay product
        df["decay_factor"] = (
            df["mention_decay"].fillna(1.0)
            * df["rex_filing_decay"].fillna(1.0)
            * df["competitor_filing_decay"].fillna(1.0)
        )
        df["composite_score"] = df["composite_score_pre_decay"] * df["decay_factor"]
        df["score_pct"] = df["composite_score"].rank(pct=True) * 100

        df = df.sort_values("composite_score", ascending=False)

    return df


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    df = build()
    df.to_parquet(OUT, compression="snappy")
    log.info("Wrote %s (%d rows)", OUT, len(df))

    print(f"\nLaunch candidates (REX filed, no live products anywhere): {len(df)}")
    if not df.empty:
        cols = ["sector", "rex_fund_name", "competitor_filed_total", "rvol_90d",
                "ret_1m", "ret_1y", "mentions_24h", "is_hot_theme",
                "days_since_rex_filing", "decay_factor", "composite_score"]
        cols = [c for c in cols if c in df.columns]
        print(df[cols].head(15).to_string())
        print()
        print(f"Stale (>{REX_FILING_STALE_DAYS}d since last REX filing): "
              f"{int(df['is_stale_filing'].sum())} of {len(df)} candidates")

        if "signal_strength" in df.columns:
            print("\nsignal_strength distribution (full result):")
            print(df["signal_strength"].value_counts(dropna=False).to_string())

    # Wave D2: also build the parallel foreign-underlier candidates table.
    # Keyed on foreign tickers (e.g. 000660.KS, ASML.AS) and consumed by
    # the B-renderer's "International" section. Failure here must not
    # block the US pipeline.
    try:
        from screener.li_engine.analysis import foreign_filings
        foreign_filings.main()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("foreign_filings build skipped: %s", exc)


if __name__ == "__main__":
    main()

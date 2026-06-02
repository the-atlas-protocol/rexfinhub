"""Multi-angle methodology analysis for the L&I recommender engine.

Every signal gets evaluated under 10 analytical angles × 5 target-variable
definitions = up to 50 views per signal. A signal earns weight only if it
survives a majority of views. Disagreement across angles is a flag — it
means we need to investigate, not average away.

Angles:
    1.  Spearman rank-IC           (monotonic, outlier-robust)
    2.  Pearson correlation         (linear strength)
    3.  Quintile spread             (top-20% minus bottom-20%)
    4.  Size-controlled regression  (coefficient after controlling for mkt cap)
    5.  Mutual information          (any relationship, linear or not)
    6.  Tree feature importance     (joint + interactions)
    7.  Lasso survival              (does it survive L1 regularization)
    8.  Bootstrap IC CI             (how certain are we)
    9.  Time stability              (deferred — need more dated history)
    10. Cross-sector consistency    (deferred — need sector labels)

Targets:
    - raw_3m_flow
    - rank_3m_flow
    - log_3m_flow
    - aum_growth (contaminated — kept for contrast)
    - flow_adj_perf (flow × perf decomposition)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, pearsonr

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.feature_selection import mutual_info_regression
    from sklearn.linear_model import LassoCV
    from sklearn.preprocessing import StandardScaler
except ImportError as e:
    raise RuntimeError(f"sklearn required: {e}")

from webapp.services.ticker_normalize import normalize_underlier as _clean

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

log = logging.getLogger(__name__)

_DB = Path(__file__).resolve().parent.parent.parent.parent / "data" / "etp_tracker.db"


# ---------------------------------------------------------------------------
# Data prep
# ---------------------------------------------------------------------------


def _coerce(v):
    if v in (None, "#ERROR", "#N/A", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_signal_panel(db: Path = _DB) -> pd.DataFrame:
    """Pull bbg stock metrics for the latest run with stock data, joined to
    fund-level aggregation (for underliers that have existing products)."""
    conn = sqlite3.connect(db)
    try:
        run_id = conn.execute(
            "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
            "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]

        stock_rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?",
            (run_id,),
        ).fetchall()

        fund_df = pd.read_sql_query(
            """
            SELECT map_li_underlier AS underlier,
                   SUM(aum) AS total_aum,
                   SUM(fund_flow_1month) AS flow_1m,
                   SUM(fund_flow_3month) AS flow_3m,
                   SUM(fund_flow_6month) AS flow_6m,
                   AVG(total_return_3month) AS prod_return_3m,
                   COUNT(*) AS n_products
            FROM mkt_master_data
            WHERE map_li_underlier IS NOT NULL
              AND map_li_underlier != ''
              AND primary_category = 'LI'
              AND aum IS NOT NULL
              AND aum > 0
            GROUP BY map_li_underlier
            """,
            conn,
        )

        underlier_returns = pd.read_sql_query(
            """
            SELECT ticker, total_return_1month, total_return_3month, total_return_6month,
                   total_return_1year
            FROM mkt_master_data
            """,
            conn,
        )
    finally:
        conn.close()

    stock_records = []
    for ticker, data_json in stock_rows:
        if not data_json:
            continue
        try:
            parsed = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        blob = parsed[0] if isinstance(parsed, list) else parsed

        mkt_cap = _coerce(blob.get("Mkt Cap"))
        adv = _coerce(blob.get("Avg Volume 30D"))
        last_price = _coerce(blob.get("Last Price"))
        turnover = adv * last_price if (adv and last_price) else None
        total_oi = _coerce(blob.get("Total OI"))
        call_oi = _coerce(blob.get("Total Call OI")) or 0
        put_oi = _coerce(blob.get("Total Put OI")) or 0
        skew = (call_oi - put_oi) / (call_oi + put_oi) if (call_oi + put_oi) > 0 else None

        stock_records.append({
            "ticker": _clean(ticker),
            "market_cap": mkt_cap,
            "adv_30d": adv,
            "turnover_30d": turnover,
            "total_oi": total_oi,
            "put_call_skew": skew,
            "realized_vol_30d": _coerce(blob.get("Volatility 30D")),
            "realized_vol_90d": _coerce(blob.get("Volatility 90D")),
            "short_interest": _coerce(blob.get("Short Interest")),
        })

    stock_df = pd.DataFrame.from_records(stock_records)
    stock_df = stock_df[stock_df["ticker"] != ""].drop_duplicates("ticker").set_index("ticker")

    fund_df["ticker"] = fund_df["underlier"].astype(str).map(_clean)
    fund_df = fund_df[fund_df["ticker"] != ""].groupby("ticker").agg({
        "total_aum": "sum",
        "flow_1m": "sum",
        "flow_3m": "sum",
        "flow_6m": "sum",
        "prod_return_3m": "mean",
        "n_products": "sum",
    })

    underlier_returns["ticker"] = underlier_returns["ticker"].astype(str).map(_clean)
    underlier_returns = underlier_returns[underlier_returns["ticker"] != ""]
    underlier_returns = underlier_returns.drop_duplicates("ticker").set_index("ticker")
    underlier_returns = underlier_returns.rename(columns={
        "total_return_1month": "underlier_ret_1m",
        "total_return_3month": "underlier_ret_3m",
        "total_return_6month": "underlier_ret_6m",
        "total_return_1year": "underlier_ret_1y",
    })

    panel = stock_df.join(fund_df, how="inner").join(underlier_returns, how="left")
    log.info("Panel: %d underliers with both signals and existing products", len(panel))
    return panel


# ---------------------------------------------------------------------------
# Target construction
# ---------------------------------------------------------------------------

SIGNALS = [
    "market_cap", "adv_30d", "turnover_30d",
    "total_oi", "put_call_skew",
    "realized_vol_30d", "realized_vol_90d",
    "short_interest",
]


def build_targets(panel: pd.DataFrame) -> dict[str, pd.Series]:
    """Five target definitions to test robustness across outcome framings."""
    t = {}

    t["raw_3m_flow"] = panel["flow_3m"]

    t["rank_3m_flow"] = panel["flow_3m"].rank(pct=True)

    t["log_3m_flow"] = np.sign(panel["flow_3m"]) * np.log1p(panel["flow_3m"].abs())

    start_aum = (panel["total_aum"] - panel["flow_3m"]).clip(lower=1e-6)
    t["aum_growth"] = (panel["flow_3m"] / start_aum).clip(-5, 5)

    market_pnl = panel["total_aum"] * panel["underlier_ret_3m"].fillna(0) / 100.0 * 2.0
    flow_adj = panel["flow_3m"] - market_pnl
    t["flow_adj_perf"] = flow_adj

    return {k: v.dropna() for k, v in t.items()}


# ---------------------------------------------------------------------------
# The 10 angles
# ---------------------------------------------------------------------------

def angle_spearman(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    idx = x.index.intersection(y.index)
    x, y = x.loc[idx], y.loc[idx]
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return float("nan"), int(m.sum())
    rho, _ = spearmanr(x[m], y[m])
    return float(rho), int(m.sum())


def angle_pearson(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    idx = x.index.intersection(y.index)
    x, y = x.loc[idx], y.loc[idx]
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return float("nan"), int(m.sum())
    r, _ = pearsonr(x[m], y[m])
    return float(r), int(m.sum())


def angle_quintile_spread(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    idx = x.index.intersection(y.index)
    x, y = x.loc[idx], y.loc[idx]
    m = x.notna() & y.notna()
    if m.sum() < 25:
        return float("nan"), int(m.sum())
    df = pd.DataFrame({"x": x[m], "y": y[m]})
    try:
        df["q"] = pd.qcut(df["x"], 5, labels=False, duplicates="drop")
    except ValueError:
        return float("nan"), int(m.sum())
    q_means = df.groupby("q")["y"].mean()
    if len(q_means) < 2:
        return float("nan"), int(m.sum())
    spread = q_means.iloc[-1] - q_means.iloc[0]
    return float(spread), int(m.sum())


def angle_size_controlled(x: pd.Series, y: pd.Series, size: pd.Series) -> tuple[float, int]:
    import statsmodels.api as sm
    idx = x.index.intersection(y.index).intersection(size.index)
    df = pd.DataFrame({"x": x.loc[idx], "y": y.loc[idx], "size": np.log1p(size.loc[idx].abs())}).dropna()
    if len(df) < 15:
        return float("nan"), len(df)
    X = sm.add_constant(df[["x", "size"]])
    try:
        model = sm.OLS(df["y"], X).fit()
        coef = model.params.get("x", float("nan"))
        return float(coef), len(df)
    except Exception:
        return float("nan"), len(df)


def angle_mutual_info(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    idx = x.index.intersection(y.index)
    m = x.loc[idx].notna() & y.loc[idx].notna()
    if m.sum() < 15:
        return float("nan"), int(m.sum())
    X = x.loc[idx][m].values.reshape(-1, 1)
    Y = y.loc[idx][m].values
    mi = mutual_info_regression(X, Y, random_state=42)[0]
    return float(mi), int(m.sum())


def angle_bootstrap_ic(x: pd.Series, y: pd.Series, n_boot: int = 500, seed: int = 42) -> tuple[float, float, int]:
    """Return (IC, CI_width, n)."""
    idx = x.index.intersection(y.index)
    x, y = x.loc[idx], y.loc[idx]
    m = x.notna() & y.notna()
    if m.sum() < 15:
        return float("nan"), float("nan"), int(m.sum())
    x, y = x[m].values, y[m].values
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        idx_b = rng.integers(0, len(x), len(x))
        rho, _ = spearmanr(x[idx_b], y[idx_b])
        if not np.isnan(rho):
            boots.append(rho)
    if not boots:
        return float("nan"), float("nan"), len(x)
    boots = np.array(boots)
    median = float(np.median(boots))
    low, high = np.percentile(boots, [2.5, 97.5])
    return median, float(high - low), len(x)


def angle_rf_importance(panel: pd.DataFrame, target: pd.Series, signals: list[str]) -> dict[str, float]:
    idx = target.index.intersection(panel.index)
    X = panel.loc[idx, signals].copy()
    y = target.loc[idx]
    df = pd.concat([X, y.rename("y")], axis=1).dropna()
    if len(df) < 30:
        return {s: float("nan") for s in signals}
    X = df[signals]
    y = df["y"]
    model = RandomForestRegressor(n_estimators=200, max_depth=6, random_state=42, n_jobs=-1)
    model.fit(X, y)
    return {s: float(imp) for s, imp in zip(signals, model.feature_importances_)}


def angle_lasso_survival(panel: pd.DataFrame, target: pd.Series, signals: list[str]) -> dict[str, float]:
    idx = target.index.intersection(panel.index)
    X = panel.loc[idx, signals].copy()
    y = target.loc[idx]
    df = pd.concat([X, y.rename("y")], axis=1).dropna()
    if len(df) < 30:
        return {s: float("nan") for s in signals}
    scaler = StandardScaler()
    Xs = scaler.fit_transform(df[signals])
    ys = (df["y"] - df["y"].mean()) / df["y"].std()
    try:
        model = LassoCV(cv=5, random_state=42, n_jobs=-1, max_iter=10000)
        model.fit(Xs, ys)
        return {s: float(c) for s, c in zip(signals, model.coef_)}
    except Exception:
        return {s: float("nan") for s in signals}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class SignalReport:
    signal: str
    n_total: int = 0
    per_angle: dict[str, Any] = field(default_factory=dict)
    consensus_positive: int = 0
    consensus_negative: int = 0
    consensus_unclear: int = 0

    def verdict(self) -> str:
        total = self.consensus_positive + self.consensus_negative + self.consensus_unclear
        if total == 0:
            return "insufficient-data"
        if self.consensus_positive >= 0.6 * total:
            return "keep (positive)"
        if self.consensus_negative >= 0.6 * total:
            return "flip-sign (negative)"
        return "ambiguous"


def run_all(panel: pd.DataFrame, signals: list[str]) -> dict[str, SignalReport]:
    targets = build_targets(panel)
    reports: dict[str, SignalReport] = {s: SignalReport(signal=s) for s in signals}

    for tgt_name, tgt in targets.items():
        log.info("=== target: %s ===", tgt_name)

        rf_imp = angle_rf_importance(panel, tgt, signals)
        lasso_coef = angle_lasso_survival(panel, tgt, signals)

        for sig in signals:
            r = reports[sig]
            x = panel[sig]

            sp_rho, sp_n = angle_spearman(x, tgt)
            pe_r, pe_n = angle_pearson(x, tgt)
            q_spread, q_n = angle_quintile_spread(x, tgt)
            sc_coef, sc_n = angle_size_controlled(x, tgt, panel["market_cap"])
            mi_val, mi_n = angle_mutual_info(x, tgt)
            boot_med, boot_ci, boot_n = angle_bootstrap_ic(x, tgt)

            r.per_angle[tgt_name] = {
                "spearman": sp_rho, "n_sp": sp_n,
                "pearson": pe_r, "n_pe": pe_n,
                "quintile_spread": q_spread,
                "size_controlled": sc_coef,
                "mutual_info": mi_val,
                "bootstrap_median": boot_med, "bootstrap_ci": boot_ci,
                "rf_importance": rf_imp.get(sig, float("nan")),
                "lasso_coef": lasso_coef.get(sig, float("nan")),
            }
            r.n_total = max(r.n_total, boot_n)

            sign_votes = []
            for k, v in r.per_angle[tgt_name].items():
                if k in ("mutual_info", "rf_importance", "quintile_spread", "bootstrap_ci"):
                    continue
                if k.startswith("n_"):
                    continue
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                sign_votes.append(np.sign(v))

            if len(sign_votes) >= 3:
                pos = sum(1 for s in sign_votes if s > 0)
                neg = sum(1 for s in sign_votes if s < 0)
                if pos > neg and pos / len(sign_votes) >= 0.6:
                    r.consensus_positive += 1
                elif neg > pos and neg / len(sign_votes) >= 0.6:
                    r.consensus_negative += 1
                else:
                    r.consensus_unclear += 1

    return reports


def derive_weights(reports: dict[str, SignalReport]) -> dict[str, float]:
    """Consensus-weighted magnitude. Signals that pass multi-angle get weight
    proportional to their median |Spearman IC| across targets. Signals that
    are ambiguous or flip-sign get zero weight (not negative — just out).

    5% floor, 35% cap."""
    magnitudes: dict[str, float] = {}
    for sig, r in reports.items():
        if "positive" not in r.verdict():
            magnitudes[sig] = 0.0
            continue
        ics = []
        for tgt_res in r.per_angle.values():
            val = tgt_res.get("spearman")
            if val is not None and not np.isnan(val):
                ics.append(abs(val))
        magnitudes[sig] = float(np.median(ics)) if ics else 0.0

    total = sum(magnitudes.values())
    if total == 0:
        return {s: 1.0 / len(magnitudes) for s in magnitudes}

    raw = {s: m / total for s, m in magnitudes.items()}
    floored = {s: max(0.05 if raw[s] > 0 else 0.0, raw[s]) for s in raw}
    total2 = sum(floored.values())
    normed = {s: v / total2 for s, v in floored.items()}
    capped = {s: min(0.35, v) for s, v in normed.items()}
    total3 = sum(capped.values())
    return {s: v / total3 for s, v in capped.items()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    panel = load_signal_panel()
    reports = run_all(panel, SIGNALS)
    for sig, r in reports.items():
        print(f"\n{sig}: {r.verdict()} (pos={r.consensus_positive} neg={r.consensus_negative} amb={r.consensus_unclear} n={r.n_total})")
    print("\n=== Suggested weights ===")
    for s, w in derive_weights(reports).items():
        print(f"  {s:20s} {w:.1%}")

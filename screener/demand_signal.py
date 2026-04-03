"""
Demand Signal — three independent dimensions of retail leverage demand.

1. Base Liquidity: can this stock support a leveraged product? (turnover percentile)
2. Momentum: does this stock excite people? (performance, not direction)
3. Attention: is something happening RIGHT NOW? (volume surge, recent acceleration)

These three dimensions are intentionally independent:
- High liquidity + flat momentum + no surge = stable but boring
- Low liquidity + huge momentum + surge = meme stock potential
- High liquidity + moderate momentum + surge = ideal candidate

Does NOT output a single score or recommendation.
Outputs a profile that a human evaluates.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "etp_tracker.db")


@dataclass
class DemandProfile:
    """Demand profile for a single stock."""
    ticker: str
    name: str
    sector: str
    market_cap: float  # millions

    # Dimension 1: Base Liquidity
    turnover: float
    turnover_pctl: float  # 0-100 within universe
    volume_30d: float
    options_oi: float
    call_oi: float
    put_oi: float
    liquidity_pass: bool  # minimum floor

    # Dimension 2: Momentum
    return_1m: float | None
    return_3m: float | None
    return_6m: float | None
    return_1y: float | None
    momentum_signal: str  # "strong", "moderate", "flat", "declining"

    # Dimension 3: Attention
    volume_surge: float  # 5D / 3M ratio
    attention_signal: str  # "surging", "elevated", "normal", "fading"

    # Competition
    comp_products: list  # [{ticker, issuer, aum, flow_ytd, exp_ratio, age_months}]
    comp_count: int
    comp_total_aum: float
    comp_net_flow_ytd: float
    recent_competitor_filing: bool

    # Context
    short_interest_ratio: float
    institutional_ownership: float
    volatility_30d: float
    news_sentiment: float | None

    # Flags
    flags_positive: list[str]
    flags_negative: list[str]
    flags_info: list[str]


def _safe_float(val) -> float:
    """Convert Bloomberg value to float, handling errors."""
    if val is None:
        return 0.0
    try:
        f = float(val)
        if str(val) in ('#ERROR', 'N/A', 'nan', ''):
            return 0.0
        return f
    except (ValueError, TypeError):
        return 0.0


def _safe_float_or_none(val):
    """Convert to float or None."""
    if val is None:
        return None
    try:
        f = float(val)
        if str(val) in ('#ERROR', 'N/A', 'nan', ''):
            return None
        return f
    except (ValueError, TypeError):
        return None


def build_profiles(tickers: list[str], db_path: str = DB_PATH) -> list[DemandProfile]:
    """Build demand profiles for a list of stock tickers."""
    conn = sqlite3.connect(db_path)

    # Load all stock data for percentile computation
    all_stocks = conn.execute("SELECT ticker, data_json FROM mkt_stock_data").fetchall()
    universe = {}
    all_turnovers = []
    for tk, dj in all_stocks:
        try:
            d = json.loads(dj)[0]
            turn = _safe_float(d.get('Turnover / Traded Value'))
            universe[tk] = d
            if turn > 0:
                all_turnovers.append(turn)
        except:
            pass

    all_turnovers_sorted = sorted(all_turnovers)
    n_universe = len(all_turnovers_sorted)

    # Load competition data
    comp_rows = conn.execute("""
        SELECT ticker, fund_name, issuer_display, aum, is_singlestock,
               expense_ratio, fund_flow_ytd, inception_date
        FROM mkt_master_data
        WHERE category_display LIKE '%Leverage%Single Stock%'
        AND market_status = 'ACTV' AND map_li_direction = 'Long'
    """).fetchall()

    # Build competition lookup by underlier
    comp_by_ul = {}
    for r in comp_rows:
        ul = r[4] or ''
        if ul not in comp_by_ul:
            comp_by_ul[ul] = []
        from datetime import datetime
        age = 0
        if r[7]:
            try:
                dt = datetime.fromisoformat(str(r[7]).split(' ')[0])
                age = (datetime.now() - dt).days / 30.44
            except:
                pass
        comp_by_ul[ul].append({
            'ticker': r[0], 'issuer': r[2],
            'aum': float(r[3] or 0), 'flow_ytd': float(r[6] or 0),
            'exp_ratio': float(r[5] or 0), 'age_months': round(age, 1),
        })

    conn.close()

    # Liquidity floor: very easy to pass
    # 10th percentile of the stock universe (not successful products)
    liq_floor_turnover = np.percentile(all_turnovers, 10) if all_turnovers else 0

    profiles = []
    for ticker in tickers:
        clean = ticker.upper().strip()
        if not clean.endswith(' US'):
            clean += ' US'

        d = universe.get(clean)
        if not d:
            log.warning("No data for %s", clean)
            continue

        # Extract metrics
        turnover = _safe_float(d.get('Turnover / Traded Value'))
        vol30d = _safe_float(d.get('Avg Volume 30D'))
        vol5d = _safe_float(d.get('Avg Volume 5D'))
        vol3m = _safe_float(d.get('Avg Volume 3M'))
        oi = _safe_float(d.get('Total OI'))
        coi = _safe_float(d.get('Total Call OI'))
        poi = _safe_float(d.get('Total Put OI'))
        mc = _safe_float(d.get('Mkt Cap'))
        v30 = _safe_float(d.get('Volatility 30D'))
        si = _safe_float(d.get('Short Interest Ratio'))
        inst = _safe_float(d.get('Institutional Owner % Shares Outstanding'))
        sentiment = _safe_float_or_none(d.get('News Sentiment Daily Avg'))
        ret1m = _safe_float_or_none(d.get('1M Total Return'))
        ret3m = _safe_float_or_none(d.get('3M Total Return'))
        ret6m = _safe_float_or_none(d.get('6M Total Return'))
        ret1y = _safe_float_or_none(d.get('1Y Total Return'))
        sector = d.get('GICS Sector', '')

        # Turnover percentile
        if turnover > 0 and n_universe > 0:
            rank = sum(1 for t in all_turnovers_sorted if t < turnover)
            turnover_pctl = round(rank / n_universe * 100, 1)
        else:
            turnover_pctl = 0

        # Liquidity floor
        liquidity_pass = turnover >= liq_floor_turnover or vol30d >= 100000

        # Momentum signal
        abs_ret = abs(ret1y) if ret1y is not None else 0
        if abs_ret > 100:
            momentum_signal = "strong"
        elif abs_ret > 30:
            momentum_signal = "moderate"
        elif abs_ret > 5:
            momentum_signal = "flat"
        else:
            momentum_signal = "minimal"

        # Attention signal
        surge = vol5d / vol3m if vol3m > 0 else 1.0
        if surge >= 2.0:
            attention_signal = "surging"
        elif surge >= 1.3:
            attention_signal = "elevated"
        elif surge >= 0.8:
            attention_signal = "normal"
        else:
            attention_signal = "fading"

        # Competition
        comp_list = comp_by_ul.get(clean, [])
        comp_count = len(comp_list)
        comp_total_aum = sum(c['aum'] for c in comp_list)
        comp_net_flow = sum(c['flow_ytd'] for c in comp_list)
        recent_filing = any(c['age_months'] < 3 for c in comp_list)

        # Flags
        pos = []
        neg = []
        info = []

        if ret1y is not None and ret1y > 100:
            pos.append(f"Strong 1Y performance ({ret1y:+.0f}%)")
        if ret1y is not None and ret1y > 500:
            pos.append(f"Extreme momentum — high retail attention likely")
        if surge >= 1.5:
            pos.append(f"Volume surge: {surge:.1f}x recent vs historical")
        if oi >= 100000:
            pos.append(f"Deep options market ({oi:,.0f} OI)")
        if vol30d >= 5000000:
            pos.append(f"High daily liquidity ({vol30d/1e6:.1f}M shares)")
        if mc >= 20000:
            pos.append(f"Large cap (${mc/1000:,.0f}B)")
        if comp_count > 0 and comp_net_flow > 0:
            pos.append(f"Competitor products attracting flows (${comp_net_flow:,.0f}M YTD)")
        if recent_filing:
            pos.append("Recent competitor filing — someone else sees opportunity")

        if mc < 3000:
            neg.append(f"Small cap (${mc:,.0f}M) — swap counterparty consideration")
        if oi < 5000:
            neg.append(f"Thin options market ({oi:,.0f} OI)")
        if si > 5:
            neg.append(f"High short interest ({si:.1f} days)")
        if v30 > 150:
            neg.append(f"Extreme volatility ({v30:.0f}%) — accelerated product decay")
        if ret1m is not None and ret1m < -20:
            neg.append(f"Sharp recent decline ({ret1m:+.1f}% 1M)")
        if comp_count >= 5:
            neg.append(f"Crowded space ({comp_count} existing products)")
        if comp_count > 0 and comp_net_flow < 0:
            neg.append(f"Competitor products losing assets (${comp_net_flow:,.0f}M YTD)")

        info.append(f"Sector: {sector}")
        if comp_count == 0:
            info.append("No existing 2x Long products — unvalidated whitespace")
        if coi > 0 and poi > 0:
            ratio = coi / poi
            if ratio > 2:
                info.append(f"Bullish options skew (C/P ratio {ratio:.1f})")
            elif ratio < 0.7:
                info.append(f"Bearish options skew (C/P ratio {ratio:.1f})")

        profiles.append(DemandProfile(
            ticker=clean,
            name=d.get('Ticker', clean),
            sector=sector,
            market_cap=mc,
            turnover=turnover,
            turnover_pctl=turnover_pctl,
            volume_30d=vol30d,
            options_oi=oi,
            call_oi=coi,
            put_oi=poi,
            liquidity_pass=liquidity_pass,
            return_1m=ret1m,
            return_3m=ret3m,
            return_6m=ret6m,
            return_1y=ret1y,
            momentum_signal=momentum_signal,
            volume_surge=surge,
            attention_signal=attention_signal,
            comp_products=comp_list,
            comp_count=comp_count,
            comp_total_aum=comp_total_aum,
            comp_net_flow_ytd=comp_net_flow,
            recent_competitor_filing=recent_filing,
            short_interest_ratio=si,
            institutional_ownership=inst,
            volatility_30d=v30,
            news_sentiment=sentiment,
            flags_positive=pos,
            flags_negative=neg,
            flags_info=info,
        ))

    return profiles

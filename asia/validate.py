"""
Post-load validation for Rex Asia AUM data.

Run after every monthly load to catch data quality issues before report generation.
Checks: staleness, outliers, Bloomberg cross-check, missing data, flow decomposition.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import psycopg2
from decimal import Decimal

MONTH_ID = 13  # Current month
PRIOR_MONTH_ID = 12
OUTLIER_THRESHOLD = 0.25  # Flag >25% MoM swings


def get_conn():
    return psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia")


def check_data_quality(conn):
    """Summary of reported vs estimated data."""
    cur = conn.cursor()
    cur.execute("""
        SELECT source_type, count(*) as rows,
               sum(exchange_aum_usd)::numeric(14,0) as aum
        FROM etp_exchange_monthly_aum
        WHERE month_id = %s
        GROUP BY source_type ORDER BY source_type
    """, (MONTH_ID,))
    rows = cur.fetchall()

    total_aum = sum(float(r[2]) for r in rows)
    print("=" * 70)
    print("DATA QUALITY SUMMARY")
    print("=" * 70)
    for src, count, aum in rows:
        pct = float(aum) / total_aum * 100 if total_aum else 0
        print(f"  {src:20s}  {count:4d} rows  ${float(aum)/1e6:>10.1f}M  ({pct:.1f}%)")
    print(f"  {'TOTAL':20s}  {sum(r[1] for r in rows):4d} rows  ${total_aum/1e6:>10.1f}M")
    print()
    return total_aum


def check_staleness(conn):
    """Flag exchanges with stale (carried-forward) data and show how old it is."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ex.name, exa.source_type,
               exa.month_id - exa.data_as_of_month_id as months_stale,
               count(*) as etps,
               sum(exa.exchange_aum_usd)::numeric(14,0) as aum
        FROM etp_exchange_monthly_aum exa
        JOIN exchange ex ON ex.exchange_id = exa.exchange_id
        WHERE exa.month_id = %s
          AND exa.source_type != 'reported'
        GROUP BY ex.name, exa.source_type, exa.month_id - exa.data_as_of_month_id
        ORDER BY sum(exa.exchange_aum_usd) DESC
    """, (MONTH_ID,))
    rows = cur.fetchall()

    if not rows:
        print("STALENESS CHECK: All data is fresh (reported)")
    else:
        print("STALENESS CHECK")
        print("-" * 70)
        print(f"  {'Exchange':30s} {'Type':16s} {'Stale':>5s} {'ETPs':>5s} {'AUM':>12s}")
        print(f"  {'-'*30} {'-'*16} {'-'*5} {'-'*5} {'-'*12}")
        for name, src, stale, etps, aum in rows:
            flag = " !! " if stale is not None and stale >= 3 else ""
            print(f"  {name:30s} {src:16s} {stale:>5d}m {etps:>5d} ${float(aum)/1e6:>10.1f}M{flag}")
    print()


def check_outliers(conn):
    """Flag ETPs with >25% MoM AUM swings at any exchange."""
    cur = conn.cursor()
    cur.execute("""
        SELECT e.ticker, ex.name,
               curr.exchange_aum_usd as current_aum,
               prev.exchange_aum_usd as prior_aum,
               CASE WHEN prev.exchange_aum_usd > 0
                    THEN (curr.exchange_aum_usd - prev.exchange_aum_usd) / prev.exchange_aum_usd
                    ELSE NULL END as pct_change,
               curr.source_type
        FROM etp_exchange_monthly_aum curr
        JOIN etp_exchange_monthly_aum prev
            ON prev.etp_id = curr.etp_id AND prev.exchange_id = curr.exchange_id
            AND prev.month_id = curr.month_id - 1
        JOIN etp e ON e.etp_id = curr.etp_id
        JOIN exchange ex ON ex.exchange_id = curr.exchange_id
        WHERE curr.month_id = %s
          AND prev.exchange_aum_usd > 100000  -- ignore tiny positions
          AND ABS(CASE WHEN prev.exchange_aum_usd > 0
                       THEN (curr.exchange_aum_usd - prev.exchange_aum_usd) / prev.exchange_aum_usd
                       ELSE 0 END) > %s
        ORDER BY ABS(curr.exchange_aum_usd - prev.exchange_aum_usd) DESC
    """, (MONTH_ID, Decimal(str(OUTLIER_THRESHOLD))))
    rows = cur.fetchall()

    if not rows:
        print(f"OUTLIER CHECK: No positions with >{OUTLIER_THRESHOLD*100:.0f}% MoM swing")
    else:
        print(f"OUTLIER CHECK (>{OUTLIER_THRESHOLD*100:.0f}% MoM swing, >$100K positions)")
        print("-" * 70)
        print(f"  {'Ticker':8s} {'Exchange':25s} {'Prior':>10s} {'Current':>10s} {'Change':>8s} {'Source':>10s}")
        print(f"  {'-'*8} {'-'*25} {'-'*10} {'-'*10} {'-'*8} {'-'*10}")
        for ticker, exchange, curr_aum, prior_aum, pct, src in rows[:20]:
            pct_str = f"{float(pct)*100:+.1f}%" if pct else "N/A"
            print(f"  {ticker:8s} {exchange:25s} ${float(prior_aum)/1e6:>8.1f}M "
                  f"${float(curr_aum)/1e6:>8.1f}M {pct_str:>8s} {src:>10s}")
    print()


def check_bloomberg_crosscheck(conn):
    """Compare sum of exchange-level AUM to Bloomberg fund-level AUM."""
    cur = conn.cursor()
    cur.execute("""
        SELECT e.ticker,
               f.total_aum_usd as bloomberg_total,
               ex_agg.asia_exchange_aum,
               CASE WHEN f.total_aum_usd > 0
                    THEN ex_agg.asia_exchange_aum / f.total_aum_usd
                    ELSE NULL END as asia_pct
        FROM etp_monthly_fund f
        JOIN etp e ON e.etp_id = f.etp_id
        JOIN (
            SELECT etp_id, sum(exchange_aum_usd) as asia_exchange_aum
            FROM etp_exchange_monthly_aum
            WHERE month_id = %s
            GROUP BY etp_id
        ) ex_agg ON ex_agg.etp_id = f.etp_id
        WHERE f.month_id = %s
          AND ex_agg.asia_exchange_aum > f.total_aum_usd * 1.01  -- Asia exceeds total = problem
        ORDER BY ex_agg.asia_exchange_aum - f.total_aum_usd DESC
    """, (MONTH_ID, MONTH_ID))
    rows = cur.fetchall()

    if not rows:
        print("BLOOMBERG CROSS-CHECK: No funds where Asia AUM > Total AUM (good)")
    else:
        print("BLOOMBERG CROSS-CHECK: Asia AUM exceeds Total AUM (!)")
        print("-" * 70)
        for ticker, total, asia, pct in rows:
            print(f"  {ticker:8s}  Total: ${float(total)/1e6:.1f}M  Asia: ${float(asia)/1e6:.1f}M  "
                  f"({float(pct)*100:.1f}%)")
    print()


def check_missing_data(conn):
    """ETPs that exist in etp_monthly_fund but have no exchange-level data."""
    cur = conn.cursor()
    cur.execute("""
        SELECT e.ticker, f.total_aum_usd
        FROM etp_monthly_fund f
        JOIN etp e ON e.etp_id = f.etp_id
        LEFT JOIN (
            SELECT DISTINCT etp_id FROM etp_exchange_monthly_aum WHERE month_id = %s
        ) ex ON ex.etp_id = f.etp_id
        WHERE f.month_id = %s AND ex.etp_id IS NULL
          AND f.total_aum_usd > 1000000  -- ignore tiny funds
        ORDER BY f.total_aum_usd DESC
    """, (MONTH_ID, MONTH_ID))
    rows = cur.fetchall()

    if not rows:
        print("MISSING DATA CHECK: All funds >$1M have exchange-level data")
    else:
        print(f"MISSING DATA CHECK: {len(rows)} funds >$1M with NO exchange-level data")
        print("-" * 70)
        for ticker, aum in rows[:10]:
            print(f"  {ticker:8s}  ${float(aum)/1e6:.1f}M")
    print()


def check_flow_decomposition(conn):
    """Show implied flows using price-return method for reported data."""
    cur = conn.cursor()
    cur.execute("""
        WITH aum_with_prices AS (
            SELECT e.ticker, ex.name as exchange,
                   curr.exchange_aum_usd as aum_t,
                   prev.exchange_aum_usd as aum_t1,
                   f_curr.price_usd as price_t,
                   f_prev.price_usd as price_t1,
                   curr.shares_outstanding as shares_t,
                   prev.shares_outstanding as shares_t1,
                   curr.source_type
            FROM etp_exchange_monthly_aum curr
            JOIN etp_exchange_monthly_aum prev
                ON prev.etp_id = curr.etp_id AND prev.exchange_id = curr.exchange_id
                AND prev.month_id = curr.month_id - 1
            JOIN etp_monthly_fund f_curr ON f_curr.etp_id = curr.etp_id AND f_curr.month_id = curr.month_id
            JOIN etp_monthly_fund f_prev ON f_prev.etp_id = curr.etp_id AND f_prev.month_id = curr.month_id - 1
            JOIN etp e ON e.etp_id = curr.etp_id
            JOIN exchange ex ON ex.exchange_id = curr.exchange_id
            WHERE curr.month_id = %s
              AND prev.exchange_aum_usd > 1000000
              AND f_prev.price_usd > 0
        )
        SELECT ticker, exchange, source_type,
               aum_t, aum_t1,
               -- Price-return method (works for all, preferred for no-share data)
               aum_t - aum_t1 * (price_t / price_t1) as implied_flows,
               aum_t1 * (price_t / price_t1 - 1) as market_move,
               -- Share method (exact when shares are accurate)
               (shares_t - shares_t1) * price_t as share_flows
        FROM aum_with_prices
        WHERE ABS(aum_t - aum_t1 * (price_t / price_t1)) > 100000  -- >$100K implied flows
        ORDER BY ABS(aum_t - aum_t1 * (price_t / price_t1)) DESC
        LIMIT 15
    """, (MONTH_ID,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]

    print("FLOW DECOMPOSITION (price-return method, positions >$1M with >$100K implied flows)")
    print("-" * 70)
    print(f"  {'Ticker':8s} {'Exchange':20s} {'Type':8s} {'Implied Flow':>12s} {'Mkt Move':>10s} {'Share Flow':>10s}")
    print(f"  {'-'*8} {'-'*20} {'-'*8} {'-'*12} {'-'*10} {'-'*10}")
    for row in rows:
        d = dict(zip(cols, row))
        impl = float(d['implied_flows'])
        mkt = float(d['market_move'])
        sh = float(d['share_flows']) if d['share_flows'] else 0
        print(f"  {d['ticker']:8s} {d['exchange']:20s} {d['source_type']:8s} "
              f"${impl/1e6:>+10.1f}M ${mkt/1e6:>+8.1f}M ${sh/1e6:>+8.1f}M")
    print()


def check_estimation_comparison(conn):
    """Compare mark-to-market vs fund-proportional estimation for repriced rows."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ex.name, e.ticker,
               exa.exchange_aum_usd as mtm_estimate,
               exa.original_aum_usd as stale_value,
               -- Fund-proportional estimate
               CASE WHEN f_orig.total_aum_usd > 0
                    THEN f_curr.total_aum_usd * (exa.original_aum_usd / f_orig.total_aum_usd)
                    ELSE NULL END as proportional_estimate,
               f_curr.total_aum_usd as fund_total_now,
               f_orig.total_aum_usd as fund_total_then
        FROM etp_exchange_monthly_aum exa
        JOIN exchange ex ON ex.exchange_id = exa.exchange_id
        JOIN etp e ON e.etp_id = exa.etp_id
        JOIN etp_monthly_fund f_curr ON f_curr.etp_id = exa.etp_id AND f_curr.month_id = exa.month_id
        LEFT JOIN etp_monthly_fund f_orig ON f_orig.etp_id = exa.etp_id AND f_orig.month_id = exa.data_as_of_month_id
        WHERE exa.month_id = %s
          AND exa.source_type = 'repriced'
          AND exa.exchange_aum_usd > 100000
        ORDER BY exa.exchange_aum_usd DESC
        LIMIT 20
    """, (MONTH_ID,))
    rows = cur.fetchall()

    print("ESTIMATION COMPARISON: Mark-to-Market vs Fund-Proportional (repriced rows >$100K)")
    print("-" * 70)
    print(f"  {'Ticker':8s} {'Exchange':20s} {'Stale':>10s} {'MtM':>10s} {'Proport.':>10s} {'Delta':>10s}")
    print(f"  {'-'*8} {'-'*20} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    for name, ticker, mtm, stale, prop, fund_now, fund_then in rows:
        mtm_f = float(mtm)
        stale_f = float(stale) if stale else 0
        prop_f = float(prop) if prop else 0
        delta = prop_f - mtm_f if prop else 0
        prop_str = f"${prop_f/1e6:>8.1f}M" if prop else "N/A"
        delta_str = f"${delta/1e6:>+8.1f}M" if prop else "N/A"
        print(f"  {ticker:8s} {name:20s} ${stale_f/1e6:>8.1f}M "
              f"${mtm_f/1e6:>8.1f}M {prop_str} {delta_str}")
    print()


if __name__ == "__main__":
    conn = get_conn()
    print()
    check_data_quality(conn)
    check_staleness(conn)
    check_outliers(conn)
    check_bloomberg_crosscheck(conn)
    check_missing_data(conn)
    check_flow_decomposition(conn)
    check_estimation_comparison(conn)
    conn.close()
    print("Validation complete.")

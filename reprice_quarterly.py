"""
Reprice quarterly carry-forward positions.

Two estimation methods available:
  1. Mark-to-Market (MtM): shares * current_price
     - Assumes zero flows since last report
     - Accurate for price moves, blind to flows

  2. Fund-Proportional: fund_total_now * (exchange_aum / fund_total_then)
     - Captures fund-level flows proportionally
     - More conservative, assumes constant exchange share of fund

Default: Fund-Proportional (more defensible estimate)
Fallback: MtM when fund-level data is unavailable

Affected exchanges:
  - Japan MooMoo (id=5)
  - HK Futu/MooMoo (id=9)
  - Oriental Harbour (id=10)
  - Singapore MooMoo (id=11)
  - Malaysia MooMoo (id=13)

Schema: sets source_type='repriced', preserves original_aum_usd, tracks data_as_of_month_id.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import psycopg2
from decimal import Decimal

MONTH_ID = 13  # Feb 2026
PRIOR_MONTH_ID = 12  # Jan 2026

QUARTERLY_EXCHANGES = [5, 9, 10, 11, 13]
EXCHANGE_NAMES = {5: 'Japan MooMoo', 9: 'HK Futu/MooMoo', 10: 'Oriental Harbour',
                  11: 'Singapore MooMoo', 13: 'Malaysia MooMoo'}


def reprice(conn, method='proportional'):
    """
    Reprice quarterly carry-forwards.

    method: 'proportional' (fund-proportional) or 'mtm' (mark-to-market)
    """
    cur = conn.cursor()

    # Get current Bloomberg prices and fund-level AUM
    cur.execute("""
        SELECT etp_id, price_usd, total_aum_usd
        FROM etp_monthly_fund WHERE month_id = %s
    """, (MONTH_ID,))
    fund_current = {r[0]: {'price': float(r[1]), 'total_aum': float(r[2])} for r in cur.fetchall()}

    # Get ALL months' fund-level AUM (for proportional method — need reporting-month AUM)
    cur.execute("""
        SELECT etp_id, month_id, total_aum_usd
        FROM etp_monthly_fund
    """)
    fund_by_month = {}
    for etp_id, mid, aum in cur.fetchall():
        fund_by_month[(etp_id, mid)] = float(aum)

    # Find the last reported month for each etp+exchange pair
    cur.execute("""
        SELECT etp_id, exchange_id, MAX(month_id) as last_reported
        FROM etp_exchange_monthly_aum
        WHERE month_id < %s AND source_type = 'reported'
          AND exchange_id = ANY(%s)
        GROUP BY etp_id, exchange_id
    """, (MONTH_ID, QUARTERLY_EXCHANGES))
    last_reported_by_pair = {(r[0], r[1]): r[2] for r in cur.fetchall()}

    method_label = "Fund-Proportional" if method == 'proportional' else "Mark-to-Market"
    print(f"=== REPRICING QUARTERLY CARRY-FORWARDS ({method_label}) ===")
    print(f"Current month_id={MONTH_ID}\n")

    grand_old = 0
    grand_new_mtm = 0
    grand_new_prop = 0
    grand_new = 0

    for ex_id in QUARTERLY_EXCHANGES:
        ex_name = EXCHANGE_NAMES[ex_id]

        cur.execute("""
            SELECT etp_id, exchange_aum_usd, shares_outstanding, original_aum_usd
            FROM etp_exchange_monthly_aum
            WHERE exchange_id = %s AND month_id = %s
        """, (ex_id, MONTH_ID))
        rows = cur.fetchall()

        old_total = sum(float(r[1]) for r in rows)
        new_total = 0
        mtm_total = 0
        prop_total = 0
        updated = 0
        kept = 0

        for etp_id, current_aum, shares, orig_aum in rows:
            shares_f = float(shares)
            current_aum_f = float(current_aum)
            # Use original_aum_usd if this was previously repriced, else use current
            carry_forward_aum = float(orig_aum) if orig_aum else current_aum_f

            fund = fund_current.get(etp_id, {})
            price = fund.get('price', 0)
            fund_aum_now = fund.get('total_aum', 0)

            # For proportional: use fund AUM at the time of the ORIGINAL report
            origin_month = last_reported_by_pair.get((etp_id, ex_id), PRIOR_MONTH_ID)
            fund_aum_at_report = fund_by_month.get((etp_id, origin_month), 0)

            # --- Mark-to-Market: shares * current price ---
            mtm_est = shares_f * price if (shares_f > 0 and price > 0) else None

            # --- Fund-Proportional: fund_total_now * (exchange_aum_at_report / fund_total_at_report) ---
            prop_est = None
            if fund_aum_at_report > 0 and fund_aum_now > 0 and carry_forward_aum > 0:
                exchange_share = carry_forward_aum / fund_aum_at_report
                prop_est = fund_aum_now * exchange_share

            if mtm_est:
                mtm_total += mtm_est
            if prop_est:
                prop_total += prop_est

            # Pick the estimate based on method
            if method == 'proportional' and prop_est is not None:
                new_aum = prop_est
            elif mtm_est is not None:
                new_aum = mtm_est
            else:
                new_aum = carry_forward_aum
                kept += 1
                new_total += new_aum
                continue

            new_total += new_aum
            # Only set original_aum_usd if not already set (preserve true original)
            update_original = carry_forward_aum if orig_aum is None else float(orig_aum)
            cur.execute("""
                UPDATE etp_exchange_monthly_aum
                SET exchange_aum_usd = %s,
                    source_type = 'repriced',
                    original_aum_usd = %s,
                    data_as_of_month_id = %s
                WHERE etp_id = %s AND exchange_id = %s AND month_id = %s
            """, (Decimal(str(round(new_aum, 4))),
                  Decimal(str(round(update_original, 4))),
                  origin_month,
                  etp_id, ex_id, MONTH_ID))
            updated += 1

        conn.commit()

        diff = new_total - old_total
        pct = (diff / old_total * 100) if old_total else 0
        print(f"{ex_name} (last reported: month {last_reported_by_pair.get((rows[0][0] if rows else 0, ex_id), '?')}):")
        print(f"  Carry-fwd:    ${old_total/1e6:>8.1f}M")
        print(f"  MtM estimate: ${mtm_total/1e6:>8.1f}M")
        print(f"  Prop estimate:${prop_total/1e6:>8.1f}M")
        print(f"  Applied:      ${new_total/1e6:>8.1f}M  (delta: ${diff/1e6:>+.1f}M)")
        print(f"  Updated: {updated}, kept: {kept}")
        print()

        grand_old += old_total
        grand_new_mtm += mtm_total
        grand_new_prop += prop_total
        grand_new += new_total

    diff = grand_new - grand_old
    print("=" * 60)
    print("TOTAL QUARTERLY:")
    print(f"  Stale:         ${grand_old/1e6:>8.1f}M")
    print(f"  MtM estimate:  ${grand_new_mtm/1e6:>8.1f}M")
    print(f"  Prop estimate: ${grand_new_prop/1e6:>8.1f}M")
    print(f"  Applied:       ${grand_new/1e6:>8.1f}M  (delta: ${diff/1e6:>+.1f}M)")
    print()

    # Show updated total Asia AUM
    cur.execute("""
        SELECT total_asian_aum, pct_asia_aum
        FROM asia_family_rollup WHERE month_end = '2026-02-28'
    """)
    r = cur.fetchone()
    if r:
        print(f"Updated Asia AUM: ${float(r[0])/1e6:,.1f}M ({float(r[1])*100:.1f}%% of total REX)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--method', choices=['mtm', 'proportional'], default='proportional',
                        help='Estimation method: mtm (mark-to-market) or proportional (fund-proportional)')
    args = parser.parse_args()

    conn = psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia")
    reprice(conn, method=args.method)
    conn.close()
    print("\nDone.")

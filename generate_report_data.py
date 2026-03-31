"""
generate_report_data.py
Queries PostgreSQL and outputs JSON with raw data for the Asia ETP report.

Usage:
    python generate_report_data.py --month 2026-02 --output report_data.json
"""

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal

import psycopg2
import psycopg2.extras


def get_connection():
    return psycopg2.connect(
        host="localhost",
        port=5433,
        user="postgres",
        dbname="rex_asia",
    )


def resolve_month_id(cur, month_str):
    """Resolve a YYYY-MM string to a month_id and its prior month_id."""
    # Parse to get the month_end date pattern
    year, month = map(int, month_str.split("-"))
    # Find the month_id for the given month
    cur.execute(
        """
        SELECT month_id, month_end
        FROM calendar_month
        WHERE EXTRACT(YEAR FROM month_end) = %s
          AND EXTRACT(MONTH FROM month_end) = %s
        """,
        (year, month),
    )
    row = cur.fetchone()
    if not row:
        print(f"ERROR: No calendar_month found for {month_str}")
        sys.exit(1)
    month_id = row["month_id"]
    month_end = row["month_end"]

    # Prior month
    if month == 1:
        prior_year, prior_month = year - 1, 12
    else:
        prior_year, prior_month = year, month - 1

    cur.execute(
        """
        SELECT month_id, month_end
        FROM calendar_month
        WHERE EXTRACT(YEAR FROM month_end) = %s
          AND EXTRACT(MONTH FROM month_end) = %s
        """,
        (prior_year, prior_month),
    )
    prior_row = cur.fetchone()
    if not prior_row:
        print(f"ERROR: No calendar_month found for prior month {prior_year}-{prior_month:02d}")
        sys.exit(1)

    return month_id, month_end, prior_row["month_id"], prior_row["month_end"]


def get_timeline_month_ids(cur, current_month_id, count=13):
    """Get the last `count` month_ids ending with current_month_id."""
    cur.execute(
        """
        SELECT month_id, month_end
        FROM calendar_month
        WHERE month_id <= %s
        ORDER BY month_id DESC
        LIMIT %s
        """,
        (current_month_id, count),
    )
    rows = cur.fetchall()
    # Return in chronological order
    return list(reversed(rows))


def fetch_fund_data(cur, month_id, prior_month_id):
    """Per-fund data: ticker, family, asia AUM current & prior, global AUM current & prior."""
    query = """
        SELECT
            e.etp_id,
            e.ticker,
            e.name AS fund_name,
            pf.name AS family_name,
            -- Current month Asia AUM (sum across exchanges)
            COALESCE(ea_cur.asia_aum, 0) AS asia_aum,
            -- Prior month Asia AUM
            COALESCE(ea_prior.asia_aum, 0) AS asia_aum_prior,
            -- Current month global AUM
            COALESCE(gf_cur.total_aum_usd, 0) AS global_aum,
            -- Prior month global AUM
            COALESCE(gf_prior.total_aum_usd, 0) AS global_aum_prior
        FROM etp e
        JOIN product_family pf ON pf.family_id = e.family_id
        LEFT JOIN (
            SELECT etp_id, SUM(exchange_aum_usd) AS asia_aum
            FROM etp_exchange_monthly_aum
            WHERE month_id = %(month_id)s
            GROUP BY etp_id
        ) ea_cur ON ea_cur.etp_id = e.etp_id
        LEFT JOIN (
            SELECT etp_id, SUM(exchange_aum_usd) AS asia_aum
            FROM etp_exchange_monthly_aum
            WHERE month_id = %(prior_month_id)s
            GROUP BY etp_id
        ) ea_prior ON ea_prior.etp_id = e.etp_id
        LEFT JOIN etp_monthly_fund gf_cur
            ON gf_cur.etp_id = e.etp_id AND gf_cur.month_id = %(month_id)s
        LEFT JOIN etp_monthly_fund gf_prior
            ON gf_prior.etp_id = e.etp_id AND gf_prior.month_id = %(prior_month_id)s
        WHERE COALESCE(ea_cur.asia_aum, 0) > 0
           OR COALESCE(ea_prior.asia_aum, 0) > 0
        ORDER BY pf.name, e.ticker
    """
    cur.execute(query, {"month_id": month_id, "prior_month_id": prior_month_id})
    return cur.fetchall()


def fetch_timeline(cur, timeline_months):
    """Monthly AUM timeline: total and per-suite for each month."""
    month_ids = [m["month_id"] for m in timeline_months]

    # Total AUM per month
    cur.execute(
        """
        SELECT
            cm.month_id,
            TO_CHAR(cm.month_end, 'YYYY-MM') AS month,
            COALESCE(SUM(ea.exchange_aum_usd), 0) AS total_aum
        FROM calendar_month cm
        LEFT JOIN etp_exchange_monthly_aum ea ON ea.month_id = cm.month_id
        WHERE cm.month_id = ANY(%s)
        GROUP BY cm.month_id, cm.month_end
        ORDER BY cm.month_id
        """,
        (month_ids,),
    )
    totals = {row["month_id"]: row for row in cur.fetchall()}

    # Per-suite AUM per month
    cur.execute(
        """
        SELECT
            cm.month_id,
            TO_CHAR(cm.month_end, 'YYYY-MM') AS month,
            pf.name AS suite,
            COALESCE(SUM(ea.exchange_aum_usd), 0) AS suite_aum
        FROM calendar_month cm
        CROSS JOIN product_family pf
        LEFT JOIN etp e ON e.family_id = pf.family_id
        LEFT JOIN etp_exchange_monthly_aum ea
            ON ea.etp_id = e.etp_id AND ea.month_id = cm.month_id
        WHERE cm.month_id = ANY(%s)
        GROUP BY cm.month_id, cm.month_end, pf.name
        ORDER BY cm.month_id, pf.name
        """,
        (month_ids,),
    )
    suite_rows = cur.fetchall()

    # Build timeline
    timeline = []
    for m in timeline_months:
        mid = m["month_id"]
        total_row = totals.get(mid)
        entry = {
            "month": total_row["month"] if total_row else m["month_end"].strftime("%Y-%m"),
            "total": float(total_row["total_aum"]) if total_row else 0.0,
            "suites": {},
        }
        for sr in suite_rows:
            if sr["month_id"] == mid:
                entry["suites"][sr["suite"]] = float(sr["suite_aum"])
        timeline.append(entry)

    return timeline


def fetch_country_breakdown(cur, month_id, prior_month_id=None):
    """AUM breakdown by country for the given month, optionally with prior."""
    cur.execute(
        """
        SELECT
            c.name AS country,
            COALESCE(SUM(ea.exchange_aum_usd), 0) AS aum
        FROM etp_exchange_monthly_aum ea
        JOIN exchange ex ON ex.exchange_id = ea.exchange_id
        JOIN country c ON c.country_id = ex.country_id
        WHERE ea.month_id = %s
        GROUP BY c.name
        ORDER BY aum DESC
        """,
        (month_id,),
    )
    countries = {r["country"]: {"country": r["country"], "aum": float(r["aum"]), "aum_prior": 0.0} for r in cur.fetchall()}

    if prior_month_id:
        cur.execute(
            """
            SELECT
                c.name AS country,
                COALESCE(SUM(ea.exchange_aum_usd), 0) AS aum
            FROM etp_exchange_monthly_aum ea
            JOIN exchange ex ON ex.exchange_id = ea.exchange_id
            JOIN country c ON c.country_id = ex.country_id
            WHERE ea.month_id = %s
            GROUP BY c.name
            """,
            (prior_month_id,),
        )
        for r in cur.fetchall():
            name = r["country"]
            if name in countries:
                countries[name]["aum_prior"] = float(r["aum"])
            else:
                countries[name] = {"country": name, "aum": 0.0, "aum_prior": float(r["aum"])}

    return sorted(countries.values(), key=lambda x: -x["aum"])


def fetch_exchange_breakdown(cur, month_id):
    """AUM breakdown by exchange for the given month."""
    cur.execute(
        """
        SELECT
            ex.name AS exchange,
            c.name AS country,
            COUNT(DISTINCT ea.etp_id) AS products,
            COALESCE(SUM(ea.exchange_aum_usd), 0) AS aum
        FROM etp_exchange_monthly_aum ea
        JOIN exchange ex ON ex.exchange_id = ea.exchange_id
        JOIN country c ON c.country_id = ex.country_id
        WHERE ea.month_id = %s
        GROUP BY ex.name, c.name
        ORDER BY aum DESC
        """,
        (month_id,),
    )
    return [
        {
            "exchange": r["exchange"],
            "country": r["country"],
            "products": r["products"],
            "aum": float(r["aum"]),
        }
        for r in cur.fetchall()
    ]


def fetch_global_aum_timeline(cur, timeline_months):
    """Total global AUM (all REX funds) for each month in the timeline."""
    month_ids = [m["month_id"] for m in timeline_months]
    cur.execute(
        """
        SELECT
            gf.month_id,
            TO_CHAR(cm.month_end, 'YYYY-MM') AS month,
            COALESCE(SUM(gf.total_aum_usd), 0) AS global_aum
        FROM etp_monthly_fund gf
        JOIN calendar_month cm ON cm.month_id = gf.month_id
        WHERE gf.month_id = ANY(%s)
        GROUP BY gf.month_id, cm.month_end
        ORDER BY gf.month_id
        """,
        (month_ids,),
    )
    return {r["month"]: float(r["global_aum"]) for r in cur.fetchall()}


def fetch_country_timeline(cur, timeline_months):
    """Per-country AUM for each month in the timeline."""
    month_ids = [m["month_id"] for m in timeline_months]
    cur.execute(
        """
        SELECT
            ea.month_id,
            TO_CHAR(cm.month_end, 'YYYY-MM') AS month,
            c.name AS country,
            COALESCE(SUM(ea.exchange_aum_usd), 0) AS aum
        FROM etp_exchange_monthly_aum ea
        JOIN exchange ex ON ex.exchange_id = ea.exchange_id
        JOIN country c ON c.country_id = ex.country_id
        JOIN calendar_month cm ON cm.month_id = ea.month_id
        WHERE ea.month_id = ANY(%s)
        GROUP BY ea.month_id, cm.month_end, c.name
        ORDER BY ea.month_id, c.name
        """,
        (month_ids,),
    )
    rows = cur.fetchall()

    result = []
    for m in timeline_months:
        mid = m["month_id"]
        month_label = m["month_end"].strftime("%Y-%m")
        countries = {}
        for r in rows:
            if r["month_id"] == mid:
                countries[r["country"]] = float(r["aum"])
        result.append({"month": month_label, "countries": countries})
    return result


def fetch_fund_country_allocation(cur, month_id):
    """Per-fund per-country AUM allocation for the current month."""
    cur.execute(
        """
        SELECT
            e.ticker,
            c.name AS country,
            SUM(ea.exchange_aum_usd) AS aum
        FROM etp_exchange_monthly_aum ea
        JOIN etp e ON e.etp_id = ea.etp_id
        JOIN exchange ex ON ex.exchange_id = ea.exchange_id
        JOIN country c ON c.country_id = ex.country_id
        WHERE ea.month_id = %s
        GROUP BY e.ticker, c.name
        ORDER BY e.ticker, aum DESC
        """,
        (month_id,),
    )
    return [{"ticker": r["ticker"], "country": r["country"], "aum": float(r["aum"])} for r in cur.fetchall()]


def fetch_suite_global_timeline(cur, timeline_months):
    """Per-fund global AUM for each month, grouped by DB family AND ticker.
    This allows the HTML to split Income into EPI vs G&I using ticker-level data."""
    month_ids = [m["month_id"] for m in timeline_months]
    # Per-DB-family totals
    cur.execute(
        """
        SELECT gf.month_id, TO_CHAR(cm.month_end, 'YYYY-MM') AS month,
               pf.name AS suite, COALESCE(SUM(gf.total_aum_usd), 0) AS global_aum
        FROM etp_monthly_fund gf
        JOIN etp e ON e.etp_id = gf.etp_id
        JOIN product_family pf ON pf.family_id = e.family_id
        JOIN calendar_month cm ON cm.month_id = gf.month_id
        WHERE gf.month_id = ANY(%s)
        GROUP BY gf.month_id, cm.month_end, pf.name
        ORDER BY gf.month_id, pf.name
        """,
        (month_ids,),
    )
    suite_rows = cur.fetchall()

    # Per-fund Asia AUM for Income family tickers (to split EPI vs G&I on timeline)
    cur.execute(
        """
        SELECT ea.month_id, TO_CHAR(cm.month_end, 'YYYY-MM') AS month,
               e.ticker, COALESCE(SUM(ea.exchange_aum_usd), 0) AS asia_aum
        FROM etp_exchange_monthly_aum ea
        JOIN etp e ON e.etp_id = ea.etp_id
        JOIN product_family pf ON pf.family_id = e.family_id
        JOIN calendar_month cm ON cm.month_id = ea.month_id
        WHERE ea.month_id = ANY(%s) AND pf.name = 'Income'
        GROUP BY ea.month_id, cm.month_end, e.ticker
        ORDER BY ea.month_id, e.ticker
        """,
        (month_ids,),
    )
    income_asia_rows = cur.fetchall()

    # Per-fund global AUM for Income family tickers (to split EPI vs G&I)
    cur.execute(
        """
        SELECT gf.month_id, TO_CHAR(cm.month_end, 'YYYY-MM') AS month,
               e.ticker, COALESCE(gf.total_aum_usd, 0) AS global_aum
        FROM etp_monthly_fund gf
        JOIN etp e ON e.etp_id = gf.etp_id
        JOIN product_family pf ON pf.family_id = e.family_id
        JOIN calendar_month cm ON cm.month_id = gf.month_id
        WHERE gf.month_id = ANY(%s) AND pf.name = 'Income'
        ORDER BY gf.month_id, e.ticker
        """,
        (month_ids,),
    )
    income_fund_rows = cur.fetchall()

    # Build result
    result = []
    current_mid = None
    entry = None
    for r in suite_rows:
        if r["month_id"] != current_mid:
            if entry:
                result.append(entry)
            current_mid = r["month_id"]
            entry = {"month": r["month"], "suites": {}, "income_funds": {}}
        entry["suites"][r["suite"]] = float(r["global_aum"])

    if entry:
        result.append(entry)

    # Attach per-fund Income data (global + asia)
    for r in income_fund_rows:
        month = r["month"]
        for e in result:
            if e["month"] == month:
                if r["ticker"] not in e["income_funds"]:
                    e["income_funds"][r["ticker"]] = {"global": 0, "asia": 0}
                e["income_funds"][r["ticker"]]["global"] = float(r["global_aum"])
                break
    for r in income_asia_rows:
        month = r["month"]
        for e in result:
            if e["month"] == month:
                if r["ticker"] not in e["income_funds"]:
                    e["income_funds"][r["ticker"]] = {"global": 0, "asia": 0}
                e["income_funds"][r["ticker"]]["asia"] = float(r["asia_aum"])
                break

    return result


def fetch_suite_country_6m(cur, current_month_id):
    """Per-suite per-country AUM for the last 6 months."""
    start_id = current_month_id - 5
    cur.execute(
        """
        SELECT cm.month_id, TO_CHAR(cm.month_end, 'Mon ''YY') as month_label,
               pf.name as suite, c.name as country,
               COALESCE(SUM(ea.exchange_aum_usd), 0) as aum
        FROM calendar_month cm
        CROSS JOIN product_family pf
        CROSS JOIN country c
        LEFT JOIN etp e ON e.family_id = pf.family_id
        LEFT JOIN exchange ex ON ex.country_id = c.country_id
        LEFT JOIN etp_exchange_monthly_aum ea
            ON ea.etp_id = e.etp_id AND ea.exchange_id = ex.exchange_id AND ea.month_id = cm.month_id
        WHERE cm.month_id BETWEEN %s AND %s
        GROUP BY cm.month_id, cm.month_end, pf.name, c.name
        HAVING COALESCE(SUM(ea.exchange_aum_usd), 0) > 0
        ORDER BY cm.month_id, pf.name, aum DESC
        """,
        (start_id, current_month_id),
    )
    rows = cur.fetchall()

    result = {}
    for r in rows:
        suite = r["suite"]
        if suite not in result:
            result[suite] = []
        mid = r["month_id"]
        # Find or create month entry
        month_entry = next((m for m in result[suite] if m["month_id"] == mid), None)
        if not month_entry:
            month_entry = {"month_id": mid, "month": r["month_label"], "countries": {}}
            result[suite].append(month_entry)
        month_entry["countries"][r["country"]] = float(r["aum"])

    return result


class ReportEncoder(json.JSONEncoder):
    """Handle Decimal, date, datetime serialization."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


def main():
    parser = argparse.ArgumentParser(description="Generate Asia ETP report data as JSON")
    parser.add_argument(
        "--month",
        required=True,
        help="Report month in YYYY-MM format (e.g. 2026-02)",
    )
    parser.add_argument(
        "--output",
        default="report_data.json",
        help="Output JSON file path (default: report_data.json)",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Resolve months
        month_id, month_end, prior_month_id, prior_month_end = resolve_month_id(cur, args.month)
        print(f"Report month: {args.month} (month_id={month_id}, end={month_end})")
        print(f"Prior month: month_id={prior_month_id}, end={prior_month_end}")

        # 1. Fund-level data
        print("Fetching fund data...")
        fund_rows = fetch_fund_data(cur, month_id, prior_month_id)
        funds = []
        for r in fund_rows:
            funds.append(
                {
                    "etp_id": r["etp_id"],
                    "ticker": r["ticker"],
                    "fund_name": r["fund_name"],
                    "family_name": r["family_name"],
                    "asia_aum": float(r["asia_aum"]),
                    "asia_aum_prior": float(r["asia_aum_prior"]),
                    "global_aum": float(r["global_aum"]),
                    "global_aum_prior": float(r["global_aum_prior"]),
                }
            )
        print(f"  {len(funds)} funds with Asia AUM")

        # 2. Timeline (13 months)
        print("Fetching timeline...")
        timeline_months = get_timeline_month_ids(cur, month_id, count=13)
        timeline = fetch_timeline(cur, timeline_months)
        print(f"  {len(timeline)} months in timeline")

        # 3. Country breakdown (with prior month)
        print("Fetching country breakdown...")
        countries = fetch_country_breakdown(cur, month_id, prior_month_id)
        print(f"  {len(countries)} countries")

        # 4. Exchange breakdown
        print("Fetching exchange breakdown...")
        exchanges = fetch_exchange_breakdown(cur, month_id)
        print(f"  {len(exchanges)} exchanges")

        # 5. Global AUM timeline (for % of AUM in Asia chart)
        print("Fetching global AUM timeline...")
        global_aum_timeline = fetch_global_aum_timeline(cur, timeline_months)
        print(f"  {len(global_aum_timeline)} months of global AUM")

        # 6. Country timeline (13 months)
        print("Fetching country timeline...")
        country_timeline = fetch_country_timeline(cur, timeline_months)
        print(f"  {len(country_timeline)} months of country data")

        # 7. Per-suite global AUM timeline (for % of AUM in Asia dashed line)
        print("Fetching suite global AUM timeline...")
        suite_global_timeline = fetch_suite_global_timeline(cur, timeline_months)
        print(f"  {len(suite_global_timeline)} months of suite global data")

        # 8. Per-suite per-country 6-month history
        print("Fetching suite country 6-month data...")
        suite_country_6m = fetch_suite_country_6m(cur, month_id)
        print(f"  {len(suite_country_6m)} suites with country data")

        # 8. Fund-country allocation
        print("Fetching fund-country allocation...")
        fund_countries = fetch_fund_country_allocation(cur, month_id)
        print(f"  {len(fund_countries)} fund-country rows")

        # Assemble output
        output = {
            "meta": {
                "report_month": args.month,
                "month_id": month_id,
                "month_end": month_end,
                "prior_month_id": prior_month_id,
                "prior_month_end": prior_month_end,
                "generated_at": datetime.now().isoformat(),
                "fund_count": len(funds),
            },
            "funds": funds,
            "timeline": timeline,
            "global_aum_timeline": global_aum_timeline,
            "countries": countries,
            "exchanges": exchanges,
            "country_timeline": country_timeline,
            "suite_global_timeline": suite_global_timeline,
            "suite_country_6m": suite_country_6m,
            "fund_countries": fund_countries,
        }

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, cls=ReportEncoder)

        print(f"\nOutput written to {args.output}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

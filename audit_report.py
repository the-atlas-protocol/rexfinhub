"""
audit_report.py
Reads report_data.json, computes all derived values, runs sanity checks.
Optionally cross-validates against Grace's Excel.

Usage:
    python audit_report.py [report_data.json]
"""

import json
import os
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Color helpers -- graceful fallback if colorama is missing
# ---------------------------------------------------------------------------
try:
    from colorama import Fore, Style, init as colorama_init

    colorama_init(autoreset=True)
    GREEN = Fore.GREEN
    RED = Fore.RED
    YELLOW = Fore.YELLOW
    CYAN = Fore.CYAN
    BOLD = Style.BRIGHT
    RESET = Style.RESET_ALL
except ImportError:
    GREEN = RED = YELLOW = CYAN = BOLD = RESET = ""


def ok(msg):
    return f"{GREEN}[OK]{RESET} {msg}"


def fail(msg):
    return f"{RED}[FAIL]{RESET} {msg}"


def warn(msg):
    return f"{YELLOW}[WARN]{RESET} {msg}"


def header(msg):
    width = 70
    print()
    print(f"{CYAN}{BOLD}{'=' * width}{RESET}")
    print(f"{CYAN}{BOLD}  {msg}{RESET}")
    print(f"{CYAN}{BOLD}{'=' * width}{RESET}")


def subheader(msg):
    print(f"\n{BOLD}--- {msg} ---{RESET}")


# ---------------------------------------------------------------------------
# Derived value computation
# ---------------------------------------------------------------------------
def compute_derived(fund):
    """Compute MoM, dollar change, market move, and flows for a single fund."""
    asia = fund["asia_aum"]
    asia_prior = fund["asia_aum_prior"]
    glob = fund["global_aum"]
    glob_prior = fund["global_aum_prior"]

    # MoM percentage changes
    mom = (asia / asia_prior - 1) if asia_prior else None
    global_mom = (glob / glob_prior - 1) if glob_prior else None

    # Dollar change
    dollar_change = asia - asia_prior

    # Market move: what Asia AUM would have done if it moved like the global fund
    market_move = asia_prior * global_mom if global_mom is not None else 0.0

    # Flows = residual after removing market effect
    flows = dollar_change - market_move

    return {
        "ticker": fund["ticker"],
        "family_name": fund["family_name"],
        "asia_aum": asia,
        "asia_aum_prior": asia_prior,
        "global_aum": glob,
        "global_aum_prior": glob_prior,
        "mom": mom,
        "global_mom": global_mom,
        "dollar_change": dollar_change,
        "market_move": market_move,
        "flows": flows,
    }


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
TOLERANCE = 0.01  # $0.01 tolerance for float rounding


def check_balance(enriched_funds):
    """Dollar Change = Flows + Market Move (per fund AND total)."""
    header("CHECK 1: Balance (Dollar Change = Flows + Market Move)")
    errors = 0
    total_dc = 0.0
    total_flows = 0.0
    total_mm = 0.0

    for f in enriched_funds:
        dc = f["dollar_change"]
        fl = f["flows"]
        mm = f["market_move"]
        total_dc += dc
        total_flows += fl
        total_mm += mm
        residual = abs(dc - (fl + mm))
        if residual > TOLERANCE:
            print(fail(f"  {f['ticker']}: DC={dc:,.2f}  Flows={fl:,.2f}  MM={mm:,.2f}  residual={residual:,.4f}"))
            errors += 1

    # Total check
    total_residual = abs(total_dc - (total_flows + total_mm))
    if total_residual > TOLERANCE:
        print(fail(f"  TOTAL: DC={total_dc:,.2f}  Flows={total_flows:,.2f}  MM={total_mm:,.2f}  residual={total_residual:,.4f}"))
        errors += 1
    else:
        print(ok(f"  TOTAL: DC={total_dc:,.2f}  Flows={total_flows:,.2f}  MM={total_mm:,.2f}  (balanced)"))

    if errors == 0:
        print(ok(f"All {len(enriched_funds)} funds balanced."))
    else:
        print(fail(f"{errors} fund(s) out of balance."))

    return errors


def check_aum_vs_timeline(data, enriched_funds):
    """Fund-level AUM sum should match the timeline total for the report month."""
    header("CHECK 2: AUM Match (fund sum vs timeline total)")
    fund_sum = sum(f["asia_aum"] for f in enriched_funds)

    # Last entry in timeline is the report month
    timeline = data["timeline"]
    if not timeline:
        print(warn("No timeline data to compare."))
        return 1

    timeline_total = timeline[-1]["total"]
    diff = abs(fund_sum - timeline_total)

    print(f"  Fund-level sum:  ${fund_sum:>18,.2f}")
    print(f"  Timeline total:  ${timeline_total:>18,.2f}")
    print(f"  Difference:      ${diff:>18,.2f}")

    if diff > 1.0:  # $1 tolerance for rounding across many funds
        print(fail("Mismatch exceeds $1.00"))
        return 1
    else:
        print(ok("Match within tolerance."))
        return 0


def check_absurd_flows(enriched_funds):
    """Flag funds where |flows| > 50% of AUM."""
    header("CHECK 3: Absurd Flows (|flows| > 50% of AUM)")
    flagged = []
    for f in enriched_funds:
        aum = max(f["asia_aum"], f["asia_aum_prior"], 1)  # avoid div by zero
        ratio = abs(f["flows"]) / aum
        if ratio > 0.50:
            flagged.append((f, ratio))

    if not flagged:
        print(ok("No absurd flows detected."))
    else:
        print(warn(f"{len(flagged)} fund(s) with flows > 50% of AUM:"))
        for f, ratio in sorted(flagged, key=lambda x: -x[1]):
            print(f"  {RED}{f['ticker']:>12}{RESET}  flows=${f['flows']:>14,.2f}  "
                  f"AUM=${f['asia_aum']:>14,.2f}  ratio={ratio:.1%}")

    return len(flagged)


def smell_test(enriched_funds):
    """Top 5 inflows and outflows with flows-as-%-of-AUM."""
    header("CHECK 4: Smell Test (Top 5 Inflows & Outflows)")

    # Only funds with non-zero flows
    with_flows = [f for f in enriched_funds if abs(f["flows"]) > 0.01]

    subheader("Top 5 Inflows")
    inflows = sorted(with_flows, key=lambda x: x["flows"], reverse=True)[:5]
    for f in inflows:
        aum = max(f["asia_aum"], f["asia_aum_prior"], 1)
        pct = f["flows"] / aum
        indicator = GREEN + "+" if f["flows"] > 0 else RED
        print(f"  {f['ticker']:>12}  {f['family_name']:<20}  "
              f"flows={indicator}${abs(f['flows']):>14,.2f}{RESET}  "
              f"({pct:>+7.1%} of AUM)")

    subheader("Top 5 Outflows")
    outflows = sorted(with_flows, key=lambda x: x["flows"])[:5]
    for f in outflows:
        aum = max(f["asia_aum"], f["asia_aum_prior"], 1)
        pct = f["flows"] / aum
        indicator = RED if f["flows"] < 0 else GREEN + "+"
        print(f"  {f['ticker']:>12}  {f['family_name']:<20}  "
              f"flows={indicator}${abs(f['flows']):>14,.2f}{RESET}  "
              f"({pct:>+7.1%} of AUM)")


def suite_totals(enriched_funds):
    """Flows and market move aggregated per suite."""
    header("CHECK 5: Suite Totals")
    suites = defaultdict(lambda: {"flows": 0.0, "market_move": 0.0, "aum": 0.0, "aum_prior": 0.0, "count": 0})
    for f in enriched_funds:
        s = suites[f["family_name"]]
        s["flows"] += f["flows"]
        s["market_move"] += f["market_move"]
        s["aum"] += f["asia_aum"]
        s["aum_prior"] += f["asia_aum_prior"]
        s["count"] += 1

    fmt = "  {suite:<25} {count:>3} funds  AUM=${aum:>16,.2f}  Flows={fl_color}${fl:>14,.2f}{reset}  MM=${mm:>14,.2f}"
    for suite_name in sorted(suites):
        s = suites[suite_name]
        fl_color = GREEN if s["flows"] >= 0 else RED
        print(fmt.format(
            suite=suite_name,
            count=s["count"],
            aum=s["aum"],
            fl_color=fl_color,
            fl=abs(s["flows"]),
            reset=RESET,
            mm=s["market_move"],
        ))

    # Grand total
    total_flows = sum(s["flows"] for s in suites.values())
    total_mm = sum(s["market_move"] for s in suites.values())
    total_aum = sum(s["aum"] for s in suites.values())
    total_aum_prior = sum(s["aum_prior"] for s in suites.values())
    total_dc = total_aum - total_aum_prior

    subheader("Grand Total")
    print(f"  AUM (current):  ${total_aum:>18,.2f}")
    print(f"  AUM (prior):    ${total_aum_prior:>18,.2f}")
    print(f"  Dollar Change:  ${total_dc:>18,.2f}")
    print(f"  Flows:          ${total_flows:>18,.2f}")
    print(f"  Market Move:    ${total_mm:>18,.2f}")
    print(f"  MoM:            {((total_aum / total_aum_prior - 1) if total_aum_prior else 0):>18.2%}")

    return suites


def headline_numbers(enriched_funds, data):
    """Print the exact headline numbers needed for the report."""
    header("CHECK 6: Headline Numbers")
    total_aum = sum(f["asia_aum"] for f in enriched_funds)
    total_aum_prior = sum(f["asia_aum_prior"] for f in enriched_funds)
    total_dc = total_aum - total_aum_prior
    total_flows = sum(f["flows"] for f in enriched_funds)
    total_mm = sum(f["market_move"] for f in enriched_funds)
    mom_pct = (total_aum / total_aum_prior - 1) if total_aum_prior else 0

    meta = data.get("meta", {})
    month = meta.get("report_month", "?")

    print(f"  Report Month:       {month}")
    print(f"  Total Asia AUM:     ${total_aum:>18,.2f}")
    print(f"  Prior Month AUM:    ${total_aum_prior:>18,.2f}")
    print(f"  Dollar Change:      ${total_dc:>18,.2f}")
    print(f"  MoM Change:         {mom_pct:>18.2%}")
    print(f"  Net Flows:          ${total_flows:>18,.2f}")
    print(f"  Market Move:        ${total_mm:>18,.2f}")
    print(f"  Fund Count:         {len(enriched_funds):>18d}")
    print(f"  Countries:          {len(data.get('countries', [])):>18d}")
    print(f"  Exchanges:          {len(data.get('exchanges', [])):>18d}")


# ---------------------------------------------------------------------------
# Grace Excel cross-validation
# ---------------------------------------------------------------------------
GRACE_PATH = os.path.join(os.path.dirname(__file__), "grace_data", "Asset report summary Feb 2026.xlsx")


def cross_validate_grace(enriched_funds, data):
    """Compare country-level AUM against Grace's source Excel."""
    header("CROSS-VALIDATION: Grace's Excel")

    if not os.path.exists(GRACE_PATH):
        print(warn(f"Grace file not found: {GRACE_PATH}"))
        print("  Skipping cross-validation.")
        return

    try:
        import pandas as pd
    except ImportError:
        print(warn("pandas not installed -- skipping cross-validation."))
        return

    try:
        # Grace's data is by exchange/country, not by ticker
        # Read raw with no header — row 1 has headers
        df = pd.read_excel(GRACE_PATH, engine="openpyxl", header=None)

        # Parse Grace's country-level data (units: $mil USD)
        # Format: Country | Source | Dec-25 | Jan-26 | Feb-26 | Remark
        grace_countries = {}
        for _, row in df.iterrows():
            country = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
            feb_val = row.iloc[4] if len(row) > 4 else None
            if not country or country in ("Country", "NaN", "nan", ""):
                continue
            if country.startswith("http"):
                continue
            try:
                val = float(feb_val) * 1e6  # Convert $mil to $
            except (ValueError, TypeError):
                continue
            # Normalize country names
            if "Korea" in country:
                country = "Korea"
            elif "Japan" in country:
                country = "Japan"
            elif "Hong Kong" in country:
                country = "Hong Kong"
            elif "Singapore" in country:
                country = "Singapore"
            elif "Malaysia" in country:
                country = "Malaysia"
            elif "Thailand" in country:
                country = "Thailand"
            elif "Taiwan" in country or "ViewTrade" in str(row.iloc[1]):
                country = "Taiwan/SG/HK (ViewTrade)"
            else:
                continue
            grace_countries[country] = grace_countries.get(country, 0) + val

        # Our country data from report_data.json
        our_countries = {c["country"]: c["aum"] for c in data.get("countries", [])}

        subheader("Country-Level Comparison (Grace raw vs DB repriced)")
        print(f"  {'Country':<25} {'Grace ($M)':>12} {'DB ($M)':>12} {'Diff ($M)':>12}  Note")
        print(f"  {'-'*75}")
        grace_total = 0
        db_total = 0
        for country in sorted(set(list(grace_countries.keys()) + list(our_countries.keys()))):
            g = grace_countries.get(country, 0)
            d = our_countries.get(country, 0)
            diff = d - g
            grace_total += g
            db_total += d
            note = ""
            if abs(diff) > 1e6:
                note = "repriced quarterly reporters" if g > d else ""
            status = ok("") if abs(diff) < 1e6 else warn("")
            print(f"  {country:<25} {g/1e6:>12.1f} {d/1e6:>12.1f} {diff/1e6:>+12.1f}  {note}")

        print(f"  {'-'*75}")
        print(f"  {'TOTAL':<25} {grace_total/1e6:>12.1f} {db_total/1e6:>12.1f} {(db_total-grace_total)/1e6:>+12.1f}")
        print()
        print(f"  Note: Differences are expected for quarterly reporters (Futu/MooMoo, Oriental Harbour)")
        print(f"  whose stale carry-forward values are repriced using fund-level NAV changes.")

    except Exception as e:
        print(fail(f"Error reading Grace file: {e}"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_enriched_output(data, enriched_funds):
    """Build the complete enriched JSON for the HTML template."""
    meta = data.get("meta", {})

    # --- Per-fund enriched data ---
    funds_out = []
    for f in enriched_funds:
        pct_of_global = (f["asia_aum"] / f["global_aum"]) if f["global_aum"] else 0.0
        funds_out.append({
            "ticker": f["ticker"],
            "family_name": f["family_name"],
            "asia_aum": f["asia_aum"],
            "asia_aum_prior": f["asia_aum_prior"],
            "global_aum": f["global_aum"],
            "global_aum_prior": f["global_aum_prior"],
            "pct_of_global": pct_of_global,
            "mom": f["mom"],
            "global_mom": f["global_mom"],
            "dollar_change": f["dollar_change"],
            "market_move": f["market_move"],
            "flows": f["flows"],
        })

    # --- Suite aggregation ---
    suite_map = defaultdict(lambda: {
        "aum": 0.0, "aum_prior": 0.0, "flows": 0.0,
        "market_move": 0.0, "dollar_change": 0.0, "fund_count": 0,
        "global_aum": 0.0, "global_aum_prior": 0.0,
    })
    for f in enriched_funds:
        s = suite_map[f["family_name"]]
        s["aum"] += f["asia_aum"]
        s["aum_prior"] += f["asia_aum_prior"]
        s["flows"] += f["flows"]
        s["market_move"] += f["market_move"]
        s["dollar_change"] += f["dollar_change"]
        s["global_aum"] += f["global_aum"]
        s["global_aum_prior"] += f["global_aum_prior"]
        s["fund_count"] += 1

    suites_out = {}
    for name, s in suite_map.items():
        mom = (s["aum"] / s["aum_prior"] - 1) if s["aum_prior"] else 0.0
        pct_in_asia = (s["aum"] / s["global_aum"]) if s["global_aum"] else 0.0
        pct_in_asia_prior = (s["aum_prior"] / s["global_aum_prior"]) if s["global_aum_prior"] else 0.0
        suites_out[name] = {
            "aum": s["aum"],
            "aum_prior": s["aum_prior"],
            "flows": s["flows"],
            "market_move": s["market_move"],
            "dollar_change": s["dollar_change"],
            "mom": mom,
            "pct_in_asia": pct_in_asia,
            "pct_in_asia_prior": pct_in_asia_prior,
            "global_aum": s["global_aum"],
            "global_aum_prior": s["global_aum_prior"],
            "fund_count": s["fund_count"],
        }

    # --- Headlines ---
    total_aum = sum(f["asia_aum"] for f in enriched_funds)
    total_aum_prior = sum(f["asia_aum_prior"] for f in enriched_funds)
    total_flows = sum(f["flows"] for f in enriched_funds)
    total_mm = sum(f["market_move"] for f in enriched_funds)
    total_dc = total_aum - total_aum_prior
    mom_pct = (total_aum / total_aum_prior - 1) if total_aum_prior else 0.0
    total_global = sum(f["global_aum"] for f in enriched_funds)

    # Total REX AUM includes ALL 93+ REX ETPs — the DB only tracks 84 with Asia AUM.
    # The complete total comes from Bloomberg/Seamus and must be set per month.
    # DB global AUM (84 funds):
    db_global = total_global
    # Bloomberg/Seamus total (all REX funds) — stored in meta if available, otherwise use DB
    bloomberg_global = data.get("meta", {}).get("bloomberg_total_rex_aum")
    effective_global = bloomberg_global if bloomberg_global else db_global

    headlines = {
        "total_asia_aum": total_aum,
        "total_asia_aum_prior": total_aum_prior,
        "dollar_change": total_dc,
        "mom_pct": mom_pct,
        "total_flows": total_flows,
        "total_market_move": total_mm,
        "total_global_aum": effective_global,
        "db_global_aum": db_global,
        "pct_in_asia": (total_aum / effective_global) if effective_global else 0.0,
        "fund_count": len(enriched_funds),
        "country_count": len(data.get("countries", [])),
        "exchange_count": len(data.get("exchanges", [])),
    }

    return {
        "meta": meta,
        "headlines": headlines,
        "funds": funds_out,
        "suites": suites_out,
        "timeline": data.get("timeline", []),
        "countries": data.get("countries", []),
        "exchanges": data.get("exchanges", []),
        "country_timeline": data.get("country_timeline", []),
        "global_aum_timeline": data.get("global_aum_timeline", {}),
        "suite_global_timeline": data.get("suite_global_timeline", []),
        "suite_country_6m": data.get("suite_country_6m", {}),
        "fund_countries": data.get("fund_countries", []),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Audit and enrich report data")
    parser.add_argument("json_path", nargs="?", default="report_data.json",
                        help="Input report_data.json path")
    parser.add_argument("--output-enriched", default="enriched_report_data.json",
                        help="Output enriched JSON path")
    args = parser.parse_args()

    json_path = args.json_path
    if not os.path.exists(json_path):
        print(fail(f"File not found: {json_path}"))
        print("Usage: python audit_report.py [report_data.json]")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    funds = data.get("funds", [])
    if not funds:
        print(fail("No fund data in JSON."))
        sys.exit(1)

    meta = data.get("meta", {})
    print(f"{BOLD}Audit Report for {meta.get('report_month', '?')}{RESET}")
    print(f"Generated: {meta.get('generated_at', '?')}")
    print(f"Funds: {meta.get('fund_count', len(funds))}")

    # Compute derived values
    enriched = [compute_derived(f) for f in funds]

    # Run all checks
    errors = 0
    errors += check_balance(enriched)
    errors += check_aum_vs_timeline(data, enriched)
    check_absurd_flows(enriched)
    smell_test(enriched)
    suite_totals(enriched)
    headline_numbers(enriched, data)

    # Cross-validate against Grace
    cross_validate_grace(enriched, data)

    # Build and write enriched output
    enriched_output = build_enriched_output(data, enriched)
    output_path = args.output_enriched
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched_output, f, indent=2)
    header("ENRICHED OUTPUT")
    print(ok(f"Written to {output_path}"))
    print(f"  Funds: {len(enriched_output['funds'])}")
    print(f"  Suites: {list(enriched_output['suites'].keys())}")
    print(f"  Headlines: {list(enriched_output['headlines'].keys())}")

    # Final verdict
    header("FINAL VERDICT")
    if errors == 0:
        print(ok("All critical checks passed."))
    else:
        print(fail(f"{errors} critical check(s) failed. Review above."))

    return errors


if __name__ == "__main__":
    sys.exit(main())

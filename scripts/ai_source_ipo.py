"""AI IPO-watchlist sourcer — keep config/ipo_watchlist.yaml current without manual edits.

The Pre-IPO section of the T-REX recommendation report is yaml-backed, and the yaml
went stale because there is no clean data feed for private-company valuations / S-1
status (unlike SEC filings). This step sources it with an LLM:

  1. Discovers the private pre-IPO names competitors are actually filing L&I on
     (category=pre_ipo from the ai_underlier_intel journals) and unions them with the
     names already in the yaml — so the watchlist is driven by the real filing race,
     not a hand-kept list.
  2. For every name that is NEW or whose valuation is stale (as_of older than --max-age
     days), asks the LLM for the latest known valuation_usd (in $B), valuation as-of
     month, S-1 status + date, expected IPO window, and sector — each carrying an
     as-of date so staleness stays visible rather than masquerading as fresh.
  3. Writes the merged result back to config/ipo_watchlist.yaml (high_profile_pre_ipo),
     preserving any human-curated fields. Journals to logs/ai_ipo_*.jsonl.

Runs nightly in apply_bloomberg_post_steps.py after ai_underlier_intel. Idempotent:
fresh entries are not re-sourced, so it only spends tokens on new / stale names.

    python scripts/ai_source_ipo.py                 # apply (rewrite yaml)
    python scripts/ai_source_ipo.py --dry-run       # source + journal only
    python scripts/ai_source_ipo.py --max-age 45    # re-source entries older than 45d
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODEL = "claude-sonnet-4-6"
YAML_PATH = PROJECT_ROOT / "config" / "ipo_watchlist.yaml"

PROMPT = """You are maintaining a watchlist of genuinely-PRIVATE, pre-IPO companies that leveraged-ETF issuers are filing products on. For each company name, return the LATEST publicly-known facts as of your knowledge:
- valuation_usd_b: most recent primary/secondary round or tender valuation, in BILLIONS USD (number; null if genuinely unknown)
- valuation_as_of: the YYYY-MM that valuation is from (or "unknown")
- s1_filed: true ONLY if the company has publicly filed an S-1/F-1 registration (boolean)
- s1_filed_date: YYYY-MM-DD if known, else ""
- expected_ipo_window: short phrase ("2026 H2", "rumored 2027", "no timeline")
- sector: short label
- still_private: false if the company has since IPO'd or been acquired (so it should leave the pre-IPO list)
Be conservative: if you are not reasonably confident a company is still private and pre-IPO, set still_private=false. Do not invent valuations — use null when unsure.
Return STRICT JSON array, one object per input: {"company","valuation_usd_b","valuation_as_of","s1_filed","s1_filed_date","expected_ipo_window","sector","still_private"}."""


def _pre_ipo_from_journals() -> set[str]:
    out = set()
    for j in (PROJECT_ROOT / "logs").glob("ai_underlier_*.jsonl"):
        for line in j.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if r.get("category") == "pre_ipo" and (r.get("company") or "").strip():
                    out.add(r["company"].strip())
            except Exception:
                pass
    return out


def _load_yaml() -> dict:
    if not YAML_PATH.exists():
        return {"high_profile_pre_ipo": [], "recently_priced": []}
    import yaml
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}


def _is_stale(entry: dict, max_age: int) -> bool:
    asof = entry.get("as_of_date") or entry.get("last_round_date") or entry.get("valuation_as_of")
    if not asof:
        return True
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            d = datetime.strptime(str(asof)[:len(fmt) - 2 + 2], fmt).date()
            return (date.today() - d).days > max_age
        except Exception:
            continue
    return True


def _source_batch(client, names):
    msg = client.messages.create(
        model=MODEL, max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT + "\n\nCOMPANIES:\n" + json.dumps(names)}],
    )
    txt = msg.content[0].text
    s, e = txt.find("["), txt.rfind("]")
    return json.loads(txt[s:e + 1]) if s >= 0 and e > s else []


def main() -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-age", type=int, default=60, help="re-source entries older than N days")
    args = ap.parse_args()

    data = _load_yaml()
    hp = data.get("high_profile_pre_ipo", []) or []
    by_company = {(e.get("company") or "").strip(): e for e in hp if e.get("company")}

    discovered = _pre_ipo_from_journals()
    universe = set(by_company) | discovered
    if not universe:
        print("No pre-IPO names to source.")
        return 0

    to_source = [c for c in sorted(universe)
                 if c not in by_company or _is_stale(by_company[c], args.max_age)]
    if not to_source:
        print(f"All {len(universe)} pre-IPO entries fresh (< {args.max_age}d). Nothing to source.")
        return 0
    print(f"Sourcing {len(to_source)} pre-IPO names (new or >{args.max_age}d stale): {to_source}")

    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"LLM unavailable ({e}); skipping.", file=sys.stderr)
        return 0

    results = []
    for i in range(0, len(to_source), 40):
        try:
            results.extend(_source_batch(client, to_source[i:i + 40]))
        except Exception as e:
            print(f"  batch {i} failed: {e}", file=sys.stderr)

    logs = PROJECT_ROOT / "logs"
    logs.mkdir(exist_ok=True)
    jn = logs / f"ai_ipo_{date.today().isoformat().replace('-', '')}.jsonl"
    with jn.open("a", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    today = date.today().isoformat()
    added, updated, retired = 0, 0, 0
    for r in results:
        c = (r.get("company") or "").strip()
        if not c:
            continue
        if r.get("still_private") is False:
            if c in by_company:
                by_company.pop(c); retired += 1
            continue
        val = r.get("valuation_usd_b")
        entry = by_company.get(c, {"company": c})
        if isinstance(val, (int, float)):
            entry["valuation_usd"] = float(val)
        entry["as_of_date"] = today
        entry["valuation_as_of"] = r.get("valuation_as_of") or ""
        entry["s1_filed"] = bool(r.get("s1_filed"))
        entry["s1_filed_date"] = r.get("s1_filed_date") or ""
        entry["expected_ipo_window"] = r.get("expected_ipo_window") or ""
        entry["sector"] = r.get("sector") or entry.get("sector", "")
        entry["source"] = "ai-source-ipo"
        if c in by_company:
            updated += 1
        else:
            added += 1
        by_company[c] = entry

    merged = sorted(by_company.values(), key=lambda e: -(e.get("valuation_usd") or 0))
    print(f"  sourced {len(results)} | added {added} updated {updated} retired {retired} "
          f"| watchlist now {len(merged)} -> journal {jn.name}")

    if args.dry_run:
        return 0
    data["high_profile_pre_ipo"] = merged
    import yaml
    YAML_PATH.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"  wrote {YAML_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

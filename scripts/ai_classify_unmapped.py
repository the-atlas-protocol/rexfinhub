"""AI classification middleman — context-aware classifier for unmapped ETPs.

The deterministic rule engine queues new funds it can't confidently place, which
left upcoming launches (e.g. "Social 50 Income", thematic funds) miscategorized.
This step uses an LLM that understands CONTEXT — crucially, it tells bond-"income"
(fixed income, outside our tracked categories) apart from option-"income"
(covered-call → CC), and recognizes thematic / L&I / crypto / defined-outcome
structures from the fund name + metadata.

Scope: ACTV+PEND funds with NULL etp_category that carry a tracked-category name
signal (so we never spend tokens on the bond universe). Each decision is journaled
to logs/ai_classify_YYYYMMDD.jsonl; HIGH/MEDIUM non-"none" results are applied to
config/rules/fund_mapping.csv and mkt_master_data. LOW-confidence go to review.

Wire-up: runs in scripts/apply_bloomberg_post_steps.py after classify_daily so new
funds self-classify nightly.

    python scripts/ai_classify_unmapped.py            # apply
    python scripts/ai_classify_unmapped.py --dry-run  # classify + journal only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODEL = "claude-sonnet-4-6"
BATCH = 60

TAXONOMY = """Classify each ETP into REX's proprietary taxonomy. Assign exactly ONE etp_category:
- LI = Leveraged & Inverse: daily-reset leveraged (2x/3x) or inverse/short exposure (single stock OR index).
- CC = Income via OPTIONS: covered-call / option-income / premium-income / autocallable / buffered-income written on EQUITIES. CRITICAL: plain FIXED-INCOME / bond / high-yield-credit / preferred / treasury / municipal / ultra-short funds are NOT CC even though their name says "income" — those are "none". Decide by mechanism: options overlay -> CC; bonds/credit/duration -> none.
- Crypto = spot or staking crypto (Bitcoin/Ether/Solana/etc.). Crypto-equity-leverage is LI; crypto-option-income is CC.
- Defined = defined-outcome / buffer / floor / cap / target-outcome structured payoffs.
- Thematic = thematic EQUITY basket (AI, drones, nuclear, space, uranium, defense, etc.), plain-beta (not leveraged).
- none = anything else: broad equity, fixed income/bonds, money market, commodity spot, multi-asset, currency.
Return STRICT JSON array, one object per fund: {"ticker","etp_category":"LI|CC|Crypto|Defined|Thematic|none","confidence":"HIGH|MEDIUM|LOW","reason":"<=15 words"}."""


def _already_seen() -> set[str]:
    """Tickers decided in any prior ai-classify journal — skip them so the nightly
    run only spends tokens on genuinely new funds (incl. ones decided 'none')."""
    seen = set()
    for j in (PROJECT_ROOT / "logs").glob("ai_classify_*.jsonl"):
        for line in j.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["ticker"])
            except Exception:
                pass
    return seen


def _candidates(db):
    from sqlalchemy import text
    _seen = _already_seen()
    rows = db.execute(text("""
        SELECT ticker, fund_name, COALESCE(issuer,''), COALESCE(asset_class,''), market_status
        FROM mkt_master_data
        WHERE COALESCE(etp_category,'')='' AND market_status IN ('ACTV','PEND')
          AND ( market_status='PEND'
             OR UPPER(fund_name) LIKE '%INCOME%' OR UPPER(fund_name) LIKE '%YIELD%'
             OR UPPER(fund_name) LIKE '%OPTION%' OR UPPER(fund_name) LIKE '% 2X %'
             OR UPPER(fund_name) LIKE '% 3X %' OR UPPER(fund_name) LIKE '%LEVERAGED%'
             OR UPPER(fund_name) LIKE '%INVERSE%' OR UPPER(fund_name) LIKE '%BUFFER%'
             OR UPPER(fund_name) LIKE '%COVERED CALL%' OR UPPER(fund_name) LIKE '%CRYPTO%' )
        ORDER BY market_status, fund_name
    """)).fetchall()
    return [{"ticker": r[0], "fund_name": r[1], "issuer": r[2], "asset_class": r[3], "status": r[4]}
            for r in rows if r[0] not in _seen]


def _classify_batch(client, funds):
    payload = [{"ticker": f["ticker"], "fund_name": f["fund_name"], "issuer": f["issuer"], "asset_class": f["asset_class"]} for f in funds]
    msg = client.messages.create(
        model=MODEL, max_tokens=8000,
        messages=[{"role": "user", "content": TAXONOMY + "\n\nFUNDS:\n" + json.dumps(payload)}],
    )
    txt = msg.content[0].text
    s, e = txt.find("["), txt.rfind("]")
    return json.loads(txt[s:e + 1]) if s >= 0 and e > s else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from webapp.database import init_db, SessionLocal
    init_db()
    db = SessionLocal()
    funds = _candidates(db)
    if not funds:
        print("No unmapped tracked-category funds to classify.")
        return 0
    print(f"AI classifying {len(funds)} unmapped funds...")

    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"LLM unavailable ({e}); skipping.", file=sys.stderr)
        return 0

    results = []
    for i in range(0, len(funds), BATCH):
        try:
            results.extend(_classify_batch(client, funds[i:i + BATCH]))
        except Exception as e:
            print(f"  batch {i} failed: {e}", file=sys.stderr)

    # Journal everything
    from datetime import date
    logs = PROJECT_ROOT / "logs"
    logs.mkdir(exist_ok=True)
    jn = logs / f"ai_classify_{date.today().isoformat().replace('-','')}.jsonl"
    with jn.open("a", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    apply = [r for r in results if r.get("etp_category") not in (None, "none") and r.get("confidence") in ("HIGH", "MEDIUM")]
    low = [r for r in results if r.get("confidence") == "LOW"]
    counts = {}
    for r in apply:
        counts[r["etp_category"]] = counts.get(r["etp_category"], 0) + 1
    print(f"  classified {len(results)} | applying {len(apply)} {counts} | LOW (review) {len(low)}")

    if args.dry_run:
        db.close()
        return 0

    # Apply to DB + fund_mapping.csv
    from sqlalchemy import text
    for r in apply:
        db.execute(text("UPDATE mkt_master_data SET etp_category=:e WHERE ticker=:t"),
                   {"e": r["etp_category"], "t": r["ticker"]})
    db.commit()
    fm = PROJECT_ROOT / "config" / "rules" / "fund_mapping.csv"
    existing = {l.split(",")[0] for l in fm.read_text(encoding="utf-8").splitlines()[1:]}
    with fm.open("a", encoding="utf-8", newline="") as f:
        for r in apply:
            if r["ticker"] not in existing:
                f.write(f"{r['ticker']},{r['etp_category']},1,ai-classify\n")
    print(f"  applied to DB + fund_mapping.csv; journal {jn.name}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

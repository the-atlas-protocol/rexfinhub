"""Agentic investigator for UNKNOWN tickers / companies / ETFs / themes.

When someone asks about a name the T-REX System doesn't track, this runs a live
web-research pass (``claude_service.investigate``) and returns a research card —
the way an analyst would look it up. It does NOT add anything to the dataset;
instead it (a) journals the request + result to ``logs/ai_investigate_*.jsonl``
for audit/idempotency and (b) appends a row to the review queue
``data/trex_investigation_queue.csv`` where Ryu decides keep / drop.

Usage:
    python scripts/ai_investigate.py "NVDA"
    python scripts/ai_investigate.py "Figure AI" --kind pre_ipo --by ryu
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
QUEUE_CSV = PROJECT_ROOT / "data" / "trex_investigation_queue.csv"
_QUEUE_COLS = ["requested_at", "query", "kind", "requested_by", "headline",
               "confidence", "identity_kind", "n_citations", "decision"]


def _today_journal() -> Path:
    return LOG_DIR / f"ai_investigate_{datetime.now(timezone.utc):%Y%m%d}.jsonl"


def _cache_lookup(query: str) -> dict | None:
    """Return today's journalled result for an identical query (per-day cache)."""
    jp = _today_journal()
    if not jp.exists():
        return None
    key = query.strip().lower()
    hit = None
    try:
        for line in jp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if str(rec.get("query", "")).strip().lower() == key and rec.get("result"):
                hit = rec["result"]  # last one wins
    except Exception:
        return None
    return hit


def _journal(query: str, kind: str, requested_by: str, result: dict | None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": query, "kind": kind, "requested_by": requested_by,
        "result": result,
    }
    with _today_journal().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _enqueue(query: str, kind: str, requested_by: str, result: dict) -> None:
    """Append a review-queue row (Ryu decides keep/drop; decision left blank)."""
    QUEUE_CSV.parent.mkdir(parents=True, exist_ok=True)
    card = (result or {}).get("card") or {}
    row = {
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "query": query,
        "kind": kind,
        "requested_by": requested_by,
        "headline": str(card.get("headline", ""))[:200],
        "confidence": card.get("confidence", ""),
        "identity_kind": (card.get("identity") or {}).get("kind", ""),
        "n_citations": len((result or {}).get("citations") or []),
        "decision": "",
    }
    new = not QUEUE_CSV.exists()
    with QUEUE_CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_QUEUE_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def investigate(query: str, kind: str = "ticker", requested_by: str = "web",
                context: str = "", use_cache: bool = True) -> dict:
    """Run (or reuse) an investigation. Returns {ok, cached, result|error}."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query"}

    if use_cache:
        cached = _cache_lookup(query)
        if cached is not None:
            return {"ok": True, "cached": True, "result": cached}

    try:
        from webapp.services import claude_service
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"claude_service unavailable: {e}"}
    if not claude_service.is_configured():
        return {"ok": False, "error": "AI is not configured (no API key)"}

    result = claude_service.investigate(query, kind=kind, context=context)
    _journal(query, kind, requested_by, result)
    if result is None:
        return {"ok": False, "error": "investigation returned no result"}
    _enqueue(query, kind, requested_by, result)
    return {"ok": True, "cached": False, "result": result}


def main() -> int:
    ap = argparse.ArgumentParser(description="Investigate an unknown ticker/company/theme")
    ap.add_argument("query")
    ap.add_argument("--kind", default="ticker")
    ap.add_argument("--by", default="cli", dest="requested_by")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    out = investigate(args.query, kind=args.kind, requested_by=args.requested_by,
                      use_cache=not args.no_cache)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

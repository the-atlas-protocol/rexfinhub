"""
Nightly Trust Universe Sync — ensure we track every SEC filer that matters.

Downloads SEC's submissions.zip (updated nightly, ~1GB), scans for ALL
entities filing 485*, N-1A, S-1, or S-3 forms, and upserts any new trusts
into the database so the next pipeline run scrapes them automatically.

This replaces the old workflow of manual curation + one-time bulk scan.
The SEC is the sole source of truth — if they file, we track.

Usage:
    python scripts/sync_trust_universe.py              # full sync
    python scripts/sync_trust_universe.py --skip-download  # re-scan cached ZIP
    python scripts/sync_trust_universe.py --dry-run    # report only, no DB writes
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Where to store the downloaded ZIP (reuse between runs, ~1GB)
ZIP_CACHE = Path("D:/sec-data/submissions.zip")
ZIP_FALLBACK = PROJECT_ROOT / "temp" / "submissions.zip"

# All form types we care about
TARGET_FORMS = ("485", "N-1A", "S-1", "S-3", "10-K", "10-Q")

# Forms that indicate specific regulatory acts
_485_FORMS = {"485APOS", "485BPOS", "485BXT", "497", "497J", "497K", "N-1A"}
_S_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A"}
_10K_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}

USER_AGENT = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:200]


def _classify(forms: set[str], entity_type: str, name: str) -> dict:
    """Classify a filer based on their forms and entity metadata."""
    has_485 = bool(forms & _485_FORMS)
    has_s = bool(forms & _S_FORMS)
    has_10k = bool(forms & _10K_FORMS)

    # Regulatory act
    if has_485:
        reg_act = "40_act"
    elif has_s:
        reg_act = "33_act"
    elif has_10k:
        reg_act = "33_act"
    else:
        reg_act = "unknown"

    # Entity type classification
    if entity_type == "investment" and has_485:
        etype = "etf_trust"
    elif entity_type == "operating" and (has_s or has_10k):
        etype = "grantor_trust"
    elif has_485:
        etype = "etf_trust"  # Has 485 forms regardless of entity_type
    else:
        etype = "unknown"

    return {"entity_type": etype, "regulatory_act": reg_act}


def sync_universe(skip_download: bool = False, dry_run: bool = False,
                  prime_cache_dir: Path | None = None) -> dict:
    """Full universe sync. Returns stats dict.

    Args:
        prime_cache_dir: If set, extract submission JSONs for NEW trusts
                         into this cache dir so the pipeline doesn't re-fetch.
    """
    from etp_tracker.bulk_loader import download_submissions_zip, scan_for_etf_trusts

    stats = {"downloaded": False, "scanned": 0, "new_trusts": 0, "updated": 0,
             "total_db": 0, "cache_primed": 0}

    # Determine ZIP location
    zip_path = ZIP_CACHE if ZIP_CACHE.parent.exists() else ZIP_FALLBACK
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Download (skip if cached and recent)
    if not skip_download:
        should_download = True
        if zip_path.exists():
            age_hours = (time.time() - zip_path.stat().st_mtime) / 3600
            if age_hours < 20:
                print(f"  ZIP is {age_hours:.0f}h old (< 20h), skipping download")
                should_download = False

        if should_download:
            print("  Downloading submissions.zip from SEC...")
            download_submissions_zip(zip_path, user_agent=USER_AGENT)
            stats["downloaded"] = True
    else:
        if not zip_path.exists():
            print(f"  ERROR: No cached ZIP at {zip_path}")
            return stats

    # Step 2: Scan for ALL relevant filers (485 + S-1 + S-3 + N-1A + 10-K)
    print("  Scanning for relevant filers...")
    discovered = scan_for_etf_trusts(zip_path, target_forms=TARGET_FORMS)
    stats["scanned"] = len(discovered)
    print(f"  Found {len(discovered):,} filers in submissions.zip")

    if dry_run:
        print("  DRY RUN: no database changes")
        return stats

    # Step 3: Upsert into database
    from webapp.database import init_db, SessionLocal
    from webapp.models import Trust
    from sqlalchemy import select

    init_db()
    db = SessionLocal()

    try:
        # Load existing CIKs for fast lookup
        existing = {str(int(row[0])): row[1] for row in
                    db.execute(select(Trust.cik, Trust.id)).all()}

        new_trusts = []
        updated = 0

        for entry in discovered:
            cik = str(int(entry["cik"]))
            name = entry["name"]
            forms = set(entry.get("forms", []))
            # Get entity_type from the JSON if available
            entity_type = entry.get("entity_type", "")

            classification = _classify(forms, entity_type, name)

            if cik in existing:
                # Update metadata for existing trusts
                trust = db.get(Trust, existing[cik])
                if trust:
                    changed = False
                    if not trust.entity_type and classification["entity_type"] != "unknown":
                        trust.entity_type = classification["entity_type"]
                        changed = True
                    if not trust.regulatory_act and classification["regulatory_act"] != "unknown":
                        trust.regulatory_act = classification["regulatory_act"]
                        changed = True
                    if changed:
                        trust.updated_at = datetime.utcnow()
                        updated += 1
            else:
                # New trust — add it
                trust = Trust(
                    cik=cik,
                    name=name,
                    slug=_slugify(name),
                    is_active=True,
                    source="sec_universe",
                    entity_type=classification["entity_type"],
                    regulatory_act=classification["regulatory_act"],
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(trust)
                new_trusts.append(f"{cik} {name}")

        db.commit()

        total = db.execute(select(Trust.id)).all()
        stats["new_trusts"] = len(new_trusts)
        stats["updated"] = updated
        stats["total_db"] = len(total)

        if new_trusts:
            print(f"  Added {len(new_trusts)} new trusts:")
            for t in new_trusts[:20]:
                print(f"    + {t}")
            if len(new_trusts) > 20:
                print(f"    ... and {len(new_trusts) - 20} more")
        else:
            print("  No new trusts found (universe is current)")

        if updated:
            print(f"  Updated metadata for {updated} existing trusts")

        print(f"  Total trusts in DB: {stats['total_db']:,}")

    finally:
        db.close()

    # Step 4: Prime HTTP cache for new trusts (extract submission JSONs from ZIP)
    if new_trusts and prime_cache_dir and zip_path.exists():
        try:
            from etp_tracker.bulk_loader import prime_cache
            # Build the subset of only NEW trust entries for cache priming
            new_ciks = set(t.split(" ")[0] for t in new_trusts)
            new_entries = [e for e in discovered if str(int(e["cik"])) in new_ciks]
            if new_entries:
                primed = prime_cache(zip_path, new_entries, prime_cache_dir)
                stats["cache_primed"] = primed
                print(f"  Primed cache for {primed} new trust files -> {prime_cache_dir}")
        except Exception as e:
            print(f"  Cache priming failed (non-fatal): {e}")

    # Step 5: Save discovered list for reference
    json_path = PROJECT_ROOT / "data" / "discovered_trusts.json"
    enriched = []
    for entry in discovered:
        forms = set(entry.get("forms", []))
        classification = _classify(forms, entry.get("entity_type", ""), entry["name"])
        enriched.append({
            "cik": f"{int(entry['cik']):010d}",
            "name": entry["name"],
            "sic": entry.get("sic", ""),
            "entity_type": entry.get("entity_type", ""),
            "latest_485": entry.get("latest_485", ""),
            "forms_485": entry.get("forms", []),
            "classification": classification,
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(enriched):,} entries to {json_path.name}")

    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync trust universe from SEC")
    parser.add_argument("--skip-download", action="store_true",
                        help="Reuse cached submissions.zip")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only, no DB writes")
    args = parser.parse_args()

    print("=== Trust Universe Sync ===")
    t0 = time.time()
    result = sync_universe(
        skip_download=args.skip_download,
        dry_run=args.dry_run,
    )
    elapsed = time.time() - t0
    print(f"\n=== Done in {elapsed:.0f}s ===")
    print(f"  Scanned: {result['scanned']:,} filers")
    print(f"  New:     {result['new_trusts']}")
    print(f"  Updated: {result['updated']}")
    print(f"  Total:   {result['total_db']:,}")

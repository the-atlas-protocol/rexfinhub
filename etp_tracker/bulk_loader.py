"""
Bulk Submissions Loader

Downloads SEC's nightly submissions.zip (~1GB), scans for CIKs that file
485-series / N-1A forms (ETF trusts), and primes the local HTTP cache so
the pipeline never needs to hit the SEC API for submission JSONs.

This is a one-time (or periodic) bootstrap step that unlocks scaling from
~236 hand-curated trusts to the full universe of 1,830+ ETF filers.

Usage:
    from etp_tracker.bulk_loader import bulk_load
    discovered = bulk_load()
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from .config import USER_AGENT_DEFAULT
except Exception:
    USER_AGENT_DEFAULT = "REX-ETP-FilingTracker/1.0 (contact: set USER_AGENT)"

# SEC bulk data endpoint
SUBMISSIONS_ZIP_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"

# Default form prefixes that identify ETF / investment company trusts
DEFAULT_TARGET_PREFIXES = ("485", "N-1A")

# Download chunk size (64KB)
CHUNK_SIZE = 65_536


def _build_session(user_agent: str) -> requests.Session:
    """Build a requests session matching sec_client.py conventions."""
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["HEAD", "GET", "OPTIONS"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def download_submissions_zip(
    dest_path: str | Path,
    user_agent: str = USER_AGENT_DEFAULT,
) -> Path:
    """Download submissions.zip with progress. Returns path to downloaded file."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    session = _build_session(user_agent)
    print(f"Downloading {SUBMISSIONS_ZIP_URL}")
    print(f"  -> {dest_path}")

    resp = session.get(SUBMISSIONS_ZIP_URL, stream=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0
    last_print = 0

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                # Print progress every ~10MB
                if downloaded - last_print >= 10 * 1024 * 1024:
                    if total:
                        pct = downloaded / total * 100
                        print(
                            f"  {downloaded:,} / {total:,} bytes ({pct:.1f}%)",
                            flush=True,
                        )
                    else:
                        print(f"  {downloaded:,} bytes", flush=True)
                    last_print = downloaded

    if total:
        print(f"  Download complete: {downloaded:,} / {total:,} bytes")
    else:
        print(f"  Download complete: {downloaded:,} bytes")

    return dest_path


def _extract_forms_from_submission(data: dict) -> list[str]:
    """Extract all form types from a submissions JSON (recent + overflow files)."""
    forms = []
    recent = data.get("filings", {}).get("recent", {})
    form_list = recent.get("form", [])
    if isinstance(form_list, list):
        forms.extend(form_list)
    return forms


def _matches_target(form: str, target_prefixes: tuple[str, ...]) -> bool:
    """Check if a form type matches any of the target prefixes."""
    form_upper = form.strip().upper()
    for prefix in target_prefixes:
        if form_upper.startswith(prefix.upper()):
            return True
    return False


def scan_for_etf_trusts(
    zip_path: str | Path,
    target_forms: tuple[str, ...] | None = None,
) -> list[dict]:
    """
    Scan ZIP for CIKs that file 485-series / N-1A forms.

    Returns list of dicts: [{"cik": str, "name": str, "forms": [str, ...]}]
    Each entry's "forms" contains the distinct matching form types found.
    """
    zip_path = Path(zip_path)
    target_prefixes = target_forms or DEFAULT_TARGET_PREFIXES

    results = []
    total_ciks = 0
    overflow_files = []

    print(f"Scanning {zip_path.name} for filers matching: {target_prefixes}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        print(f"  ZIP contains {len(names):,} files")

        for name in names:
            # Main CIK files: CIK0000000000.json
            if not name.startswith("CIK") or not name.endswith(".json"):
                continue

            # Overflow files handled separately (CIK{padded}-submissions-001.json)
            if "-submissions-" in name:
                overflow_files.append(name)
                continue

            total_ciks += 1

            try:
                with zf.open(name) as f:
                    data = json.loads(f.read())
            except (json.JSONDecodeError, KeyError, zipfile.BadZipFile):
                continue

            entity_name = data.get("name", "Unknown")
            forms = _extract_forms_from_submission(data)
            matching = sorted(set(
                f for f in forms if _matches_target(f, target_prefixes)
            ))

            if matching:
                # Extract CIK from the JSON data (authoritative) or filename
                cik_str = str(data.get("cik", ""))
                if not cik_str:
                    # Fallback: parse from filename CIK0001174610.json
                    cik_str = name.replace("CIK", "").replace(".json", "").lstrip("0")
                else:
                    cik_str = str(int(cik_str))  # Strip leading zeros

                results.append({
                    "cik": cik_str,
                    "name": entity_name,
                    "forms": matching,
                })

            # Progress every 10,000 files
            if total_ciks % 10_000 == 0:
                print(
                    f"  Scanned {total_ciks:,} CIKs, {len(results):,} matches so far",
                    flush=True,
                )

    print(f"  Scan complete: {total_ciks:,} total CIKs, {len(results):,} ETF trust matches")
    print(f"  Overflow files found: {len(overflow_files):,}")

    return results


def prime_cache(
    zip_path: str | Path,
    cik_list: list[dict],
    cache_dir: str | Path,
) -> int:
    """
    Extract matching CIK JSONs to cache directory.

    Copies main submission JSONs and any overflow files (-submissions-NNN.json)
    to the same location sec_client.py would cache them:
        http_cache/submissions/{cik_padded_10}.json

    Returns count of files cached.
    """
    zip_path = Path(zip_path)
    cache_dir = Path(cache_dir)
    submissions_dir = cache_dir / "submissions"
    submissions_dir.mkdir(parents=True, exist_ok=True)

    # Build set of 10-digit padded CIKs for fast lookup
    matching_ciks_padded = set()
    for entry in cik_list:
        padded = f"{int(entry['cik']):010d}"
        matching_ciks_padded.add(padded)

    cached = 0

    print(f"Priming cache for {len(cik_list):,} CIKs -> {submissions_dir}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not name.startswith("CIK") or not name.endswith(".json"):
                continue

            # Parse CIK from filename: CIK0001174610.json or CIK0001174610-submissions-001.json
            base = name.split(".json")[0]
            if "-submissions-" in base:
                padded = base.split("-submissions-")[0].replace("CIK", "")
            else:
                padded = base.replace("CIK", "")

            if padded not in matching_ciks_padded:
                continue

            # Determine destination filename
            # Main file: CIK0001174610.json -> 0001174610.json
            # Overflow:  CIK0001174610-submissions-001.json -> 0001174610-submissions-001.json
            dest_name = name.replace("CIK", "")
            dest_path = submissions_dir / dest_name

            try:
                with zf.open(name) as src:
                    data = src.read()
                dest_path.write_bytes(data)
                cached += 1
            except Exception as e:
                print(f"  Warning: failed to cache {name}: {e}")

    print(f"  Cached {cached:,} files to {submissions_dir}")
    return cached


def bulk_load(
    cache_dir: str | Path | None = None,
    user_agent: str = USER_AGENT_DEFAULT,
    target_forms: tuple[str, ...] | None = None,
    keep_zip: bool = False,
) -> list[dict]:
    """
    Main entry point. Downloads submissions.zip, scans for ETF trusts,
    primes the HTTP cache, and returns discovered trusts.

    Args:
        cache_dir: Path to HTTP cache root (default: http_cache/ in project root)
        user_agent: SEC User-Agent header
        target_forms: Form type prefixes to match (default: 485*, N-1A)
        keep_zip: If True, keep the downloaded ZIP after processing

    Returns:
        List of dicts: [{"cik": str, "name": str, "forms": [str, ...]}]
    """
    if cache_dir is None:
        cache_dir = Path("D:/sec-data/cache/rexfinhub")
    else:
        cache_dir = Path(cache_dir)

    # Use a temp directory for the ZIP download
    tmp_dir = tempfile.mkdtemp(prefix="sec_bulk_")
    zip_path = Path(tmp_dir) / "submissions.zip"

    try:
        t0 = time.time()

        # Step 1: Download
        print("=" * 60)
        print("STEP 1: Download submissions.zip")
        print("=" * 60)
        download_submissions_zip(zip_path, user_agent=user_agent)

        t1 = time.time()
        print(f"  Download took {t1 - t0:.0f}s\n")

        # Step 2: Scan for ETF trusts
        print("=" * 60)
        print("STEP 2: Scan for ETF trust filers")
        print("=" * 60)
        discovered = scan_for_etf_trusts(zip_path, target_forms=target_forms)

        t2 = time.time()
        print(f"  Scan took {t2 - t1:.0f}s\n")

        # Step 3: Prime cache
        print("=" * 60)
        print("STEP 3: Prime HTTP cache")
        print("=" * 60)
        cached_count = prime_cache(zip_path, discovered, cache_dir)

        t3 = time.time()
        print(f"  Cache priming took {t3 - t2:.0f}s\n")

        # Summary
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Total time:       {t3 - t0:.0f}s")
        print(f"  ETF trusts found: {len(discovered):,}")
        print(f"  Files cached:     {cached_count:,}")
        print(f"  Cache location:   {cache_dir / 'submissions'}")

        return discovered

    finally:
        if not keep_zip:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
        else:
            print(f"\n  ZIP retained at: {zip_path}")

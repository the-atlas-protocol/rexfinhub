"""
Processing manifest for incremental pipeline runs.

Tracks which accession numbers have been processed per trust,
enabling the pipeline to skip already-processed filings.
"""
from __future__ import annotations
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Bump this to force re-processing of all filings
PIPELINE_VERSION = 2

MANIFEST_FILENAME = "_manifest.json"


def load_manifest(trust_folder: Path) -> dict:
    """Load the processing manifest for a trust folder.

    Returns dict mapping accession_number -> metadata.
    """
    path = trust_folder / MANIFEST_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_manifest(trust_folder: Path, manifest: dict) -> None:
    """Atomically write manifest to disk (temp file + rename)."""
    trust_folder.mkdir(parents=True, exist_ok=True)
    path = trust_folder / MANIFEST_FILENAME
    # Write to temp file first, then rename for atomicity
    try:
        fd, tmp = tempfile.mkstemp(dir=str(trust_folder), suffix=".tmp")
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
        tmp_path = Path(tmp)
        tmp_path.replace(path)
    except OSError:
        # Fallback: direct write if atomic rename fails (Windows edge case)
        path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def get_processed_accessions(manifest: dict) -> set[str]:
    """Return set of accession numbers successfully processed at current version."""
    return {
        k for k, v in manifest.items()
        if v.get("status") == "success"
        and v.get("version", 0) >= PIPELINE_VERSION
    }


def get_retry_accessions(manifest: dict, max_retries: int = 3) -> set[str]:
    """Return set of accession numbers that should be retried (errored, under retry limit)."""
    return {
        k for k, v in manifest.items()
        if v.get("status") == "error"
        and v.get("retry_count", 0) < max_retries
    }


def record_success(manifest: dict, accession: str, form: str, extraction_count: int) -> None:
    """Record a successfully processed filing."""
    manifest[accession] = {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "form": form,
        "extraction_count": extraction_count,
        "status": "success",
        "error_message": None,
        "version": PIPELINE_VERSION,
    }


def record_error(manifest: dict, accession: str, form: str, error_msg: str) -> None:
    """Record a failed filing extraction."""
    prev = manifest.get(accession, {})
    manifest[accession] = {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "form": form,
        "extraction_count": 0,
        "status": "error",
        "error_message": str(error_msg)[:500],
        "retry_count": prev.get("retry_count", 0) + 1,
        "version": PIPELINE_VERSION,
    }


def clear_manifest(trust_folder: Path) -> None:
    """Delete manifest file to force full reprocessing."""
    path = trust_folder / MANIFEST_FILENAME
    if path.exists():
        path.unlink()

# Structured Notes Pipeline — CLAUDE.md

## CRITICAL: D: Drive Safety Rules

The D: drive is a **Transcend USB external drive**. It is slow and will hang any process that tries to enumerate large directories.

### NEVER DO:
- `du`, `ls -R`, `find`, or any recursive directory scan on `D:/sec-data/cache/`
- `os.listdir()`, `Path.iterdir()`, `glob()` on the cache directory
- Read or count the total number of cached files
- Any operation that enumerates all files in the cache (hundreds of thousands of small .txt/.json files)
- PowerShell `Get-ChildItem -Recurse` on D: drive directories

### SAFE operations on D: drive:
- Read/write a SINGLE known file path (e.g., the SQLite database)
- Check if a specific file exists
- `os.path.getsize()` on a specific file
- SQLite queries (the DB engine handles its own I/O)

### If asked to "verify data is safe":
- Check the SQLite database file exists: `D:/sec-data/databases/structured_notes.db`
- Run a simple SQL query: `SELECT COUNT(*) FROM products`
- Check `data/extraction_progress.json` for last known state
- Do NOT enumerate the cache directory

## Storage Architecture

| Location | What | Why |
|----------|------|-----|
| `D:/sec-data/databases/structured_notes.db` | SQLite database | Too large for C: |
| `D:/sec-data/cache/structured-notes/` | SEC filing cache | Hundreds of thousands of small files |
| `C:/Projects/rexfinhub/http_cache/` | Legacy shared cache | **PENDING CLEANUP** — 13GB+, should be moved to D: or deleted |
| `data/extraction_progress.json` | Progress tracker | Stays on C: (tiny) |

## C: Drive is critically low (~4 GB free)
- Never write large files to C:
- Cache writes to D: are disabled in sec_client.py (USB too slow for many small writes)
- The SQLite database and all cache live on D:

## Extraction Pipeline

- **Resume command**: `python run_extraction.py --since 2018`
- **Parallel mode**: `python run_extraction.py --since 2018 --group 1/2` (two workers)
- **Commits every 25 filings** — partial progress is never lost
- **Adaptive rate limiting** (AIMD): starts fast, backs off on 503s
- **Cache reads work** (existing files on D:), but **cache writes are disabled** (USB too slow)

## SEC Rate Limiting
- 0.10s floor, 2.0s ceiling, 1.5x backoff on 503
- Sustained rate: ~1.6/s (peaks at 2.7/s, SEC pushes back overnight)
- User-Agent: REX-StructuredNotes/1.0 (relasmar@rexfin.com)

# FIXES Agent Task

## Mission
1. Fix screener "No Bloomberg Data" bug (PRIORITY)
2. Add pagination to downloads page

## Status: NOT STARTED

## Task 1: Fix Screener (PRIORITY)

**Problem**: `/screener/` shows "No Bloomberg Data available"

**Facts**:
- File exists: `data/SCREENER/data.xlsx` (5MB)
- Sheets: `stock_data` (6,479 rows), `etp_data` (5,074 rows)
- Path resolves correctly in config

**Files to investigate**:
```
webapp/routers/screener.py         # _data_available() function
webapp/services/screener_3x_cache.py # compute_and_cache()
screener/data_loader.py            # load_all()
screener/config.py                 # DATA_FILE path
```

**Approach**:
1. Add logging to trace data loading
2. Find where it fails silently
3. Fix the issue
4. Test /screener/ shows data

## Task 2: Downloads Pagination

**Problem**: 7,000+ funds on one page, too slow

**Files**:
```
webapp/routers/downloads.py
webapp/templates/downloads.html
```

**Solution**:
- Add pagination (50 per page)
- Add search/filter box

## Checklist
- [ ] Add logging to screener data path
- [ ] Find failure point
- [ ] Fix screener bug
- [ ] Test /screener/ shows data
- [ ] Add pagination to downloads
- [ ] Add search filter
- [ ] Test /downloads/ loads fast

## Notes
(Update with progress and context for next session)

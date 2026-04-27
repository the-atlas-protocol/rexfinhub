"""Async CBOE scanner.

Hits https://account.cboe.com/account/listings/symbol_reservations/symbol_status/
for each ticker with the session cookie from config/.env. Upserts results
into cboe_symbols, logs flips into cboe_state_changes, tracks run
metadata in cboe_scan_runs so interrupted scans can resume from the last
ticker on the next pass.

Backoff: per-request exponential on 429/5xx up to 30s.
Circuit-breaker: 5 consecutive 429s triggers a 10-minute pause.
Auth failure: 401/403 raises AuthError (cookie must be rotated manually).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Iterable

import aiohttp

from webapp.database import SessionLocal
from webapp.models import CboeScanRun, CboeStateChange, CboeSymbol

log = logging.getLogger(__name__)

CBOE_ENDPOINT = "https://account.cboe.com/account/listings/symbol_reservations/symbol_status/"
USER_AGENT = "rexfinhub-ticker-radar/0.1 (symbol-availability monitor)"

UPSERT_BATCH = 500
MAX_429_STREAK = 5
BACKOFF_429_SECONDS = 600
BASE_BACKOFF = 1.0
MAX_BACKOFF = 30.0
MAX_RETRIES = 4


class AuthError(Exception):
    """CBOE rejected the session cookie. Manual rotation required."""


def _state_name(available: bool | None) -> str:
    if available is True:
        return "available"
    if available is False:
        return "taken"
    return "unknown"


class CboeScanner:
    def __init__(
        self,
        cookie: str,
        concurrency: int,
        *,
        db_factory=SessionLocal,
        endpoint: str = CBOE_ENDPOINT,
    ) -> None:
        if not cookie:
            raise ValueError("CBOE session cookie required")
        self.cookie = cookie
        self.concurrency = max(1, concurrency)
        self.endpoint = endpoint
        self.db_factory = db_factory
        self._consecutive_429 = 0
        self._429_lock = asyncio.Lock()

    async def _fetch_one(
        self, session: aiohttp.ClientSession, ticker: str
    ) -> bool | None:
        headers = {
            "User-Agent": USER_AGENT,
            "Cookie": self.cookie,
            "Accept": "application/json",
        }
        backoff = BASE_BACKOFF
        for _ in range(MAX_RETRIES):
            try:
                async with session.get(
                    self.endpoint,
                    params={"symbol": ticker},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (401, 403):
                        raise AuthError(
                            f"CBOE auth rejected (status {resp.status}); refresh CBOE_SESSION_COOKIE"
                        )
                    # CBOE 302s expired sessions to /login. Treat as auth failure
                    # so the run aborts cleanly instead of churning on HTML responses.
                    if 300 <= resp.status < 400:
                        loc = resp.headers.get("Location", "")
                        raise AuthError(
                            f"CBOE redirected to {loc!r} (status {resp.status}); refresh CBOE_SESSION_COOKIE"
                        )
                    if resp.status == 429:
                        async with self._429_lock:
                            self._consecutive_429 += 1
                            streak = self._consecutive_429
                        if streak >= MAX_429_STREAK:
                            log.warning(
                                "429 storm (streak=%d); pausing %ds",
                                streak, BACKOFF_429_SECONDS,
                            )
                            await asyncio.sleep(BACKOFF_429_SECONDS)
                            async with self._429_lock:
                                self._consecutive_429 = 0
                        else:
                            await asyncio.sleep(backoff)
                            backoff = min(MAX_BACKOFF, backoff * 2)
                        continue
                    if 500 <= resp.status < 600:
                        await asyncio.sleep(backoff)
                        backoff = min(MAX_BACKOFF, backoff * 2)
                        continue
                    if resp.status != 200:
                        log.warning("%s: unexpected status %d", ticker, resp.status)
                        return None
                    async with self._429_lock:
                        self._consecutive_429 = 0
                    try:
                        data = await resp.json(content_type=None)
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                        # CBOE occasionally returns HTML/empty bodies with a 200.
                        # Treat as transient — back off and retry.
                        log.warning(
                            "%s: bad JSON (%s); retry in %.1fs",
                            ticker, type(e).__name__, backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(MAX_BACKOFF, backoff * 2)
                        continue
                    avail = data.get("available") if isinstance(data, dict) else None
                    return avail if isinstance(avail, bool) else None
            except AuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning(
                    "%s: transient (%s); retry in %.1fs",
                    ticker, type(e).__name__, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(MAX_BACKOFF, backoff * 2)
        log.warning("%s: exhausted retries", ticker)
        return None

    async def scan(
        self, tickers: Iterable[str], *, tier: str | None = None
    ) -> dict:
        tickers = list(tickers)
        run_id = await asyncio.to_thread(
            self._start_run, tier=tier, concurrency=self.concurrency
        )
        started_at = time.monotonic()

        sem = asyncio.Semaphore(self.concurrency)
        buffer: dict[str, bool] = {}
        buffer_lock = asyncio.Lock()
        state = {"last_ticker": None}  # type: ignore[var-annotated]
        auth_failure: list[AuthError] = []

        connector = aiohttp.TCPConnector(limit=self.concurrency * 2)
        async with aiohttp.ClientSession(connector=connector) as session:

            async def _flush_if_ready() -> None:
                async with buffer_lock:
                    if len(buffer) < UPSERT_BATCH:
                        return
                    batch = dict(buffer)
                    buffer.clear()
                    last = state["last_ticker"]
                await asyncio.to_thread(self._flush, batch, last, run_id)

            async def _worker(t: str) -> None:
                async with sem:
                    if auth_failure:
                        return
                    try:
                        avail = await self._fetch_one(session, t)
                    except AuthError as e:
                        auth_failure.append(e)
                        return
                    except Exception as e:
                        # Defensive: never let a single ticker take down the sweep.
                        log.warning("%s: unexpected (%s); skipping", t, type(e).__name__)
                        avail = None
                async with buffer_lock:
                    if avail is not None:
                        buffer[t] = avail
                    state["last_ticker"] = t
                await _flush_if_ready()

            await asyncio.gather(*(_worker(t) for t in tickers))

        # final flush
        async with buffer_lock:
            final_batch = dict(buffer)
            buffer.clear()
            last = state["last_ticker"]
        if final_batch:
            await asyncio.to_thread(self._flush, final_batch, last, run_id)

        if auth_failure:
            summary = await asyncio.to_thread(
                self._end_run,
                run_id,
                status="failed",
                error=str(auth_failure[0]),
                last_ticker=last,
            )
            summary["elapsed_seconds"] = round(time.monotonic() - started_at, 1)
            raise AuthError(str(auth_failure[0]))

        summary = await asyncio.to_thread(
            self._end_run, run_id, status="completed", last_ticker=last
        )
        summary["elapsed_seconds"] = round(time.monotonic() - started_at, 1)
        return summary

    def _start_run(self, *, tier: str | None, concurrency: int) -> int:
        with self.db_factory() as db:
            run = CboeScanRun(
                started_at=datetime.utcnow(),
                status="running",
                tier=tier,
                concurrency=concurrency,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            return run.id

    def _flush(
        self, batch: dict[str, bool], last_ticker: str | None, run_id: int
    ) -> None:
        if not batch:
            return
        with self.db_factory() as db:
            existing = {
                s.ticker: s
                for s in db.query(CboeSymbol).filter(
                    CboeSymbol.ticker.in_(list(batch.keys()))
                )
            }
            now = datetime.utcnow()
            state_changes = 0
            for ticker, avail in batch.items():
                row = existing.get(ticker)
                if row is None:
                    row = CboeSymbol(
                        ticker=ticker,
                        length=len(ticker),
                        available=avail,
                        last_checked_at=now,
                        first_seen_available_at=now if avail else None,
                        first_seen_taken_at=None if avail else now,
                        state_change_count=0,
                    )
                    db.add(row)
                    continue
                old_state = _state_name(row.available)
                new_state = _state_name(avail)
                row.available = avail
                row.last_checked_at = now
                if avail and row.first_seen_available_at is None:
                    row.first_seen_available_at = now
                if (not avail) and row.first_seen_taken_at is None:
                    row.first_seen_taken_at = now
                if old_state != new_state and old_state != "unknown":
                    row.state_change_count = (row.state_change_count or 0) + 1
                    db.add(
                        CboeStateChange(
                            ticker=ticker,
                            old_state=old_state,
                            new_state=new_state,
                            detected_at=now,
                        )
                    )
                    state_changes += 1
            run = db.get(CboeScanRun, run_id)
            if run is not None:
                run.tickers_checked = (run.tickers_checked or 0) + len(batch)
                run.state_changes_detected = (run.state_changes_detected or 0) + state_changes
                if last_ticker:
                    run.last_ticker_scanned = last_ticker
            db.commit()

    def _end_run(
        self,
        run_id: int,
        *,
        status: str,
        error: str | None = None,
        last_ticker: str | None = None,
    ) -> dict:
        with self.db_factory() as db:
            run = db.get(CboeScanRun, run_id)
            if run is None:
                return {"run_id": run_id, "status": status}
            run.finished_at = datetime.utcnow()
            run.status = status
            if error:
                run.error_message = error
            if last_ticker:
                run.last_ticker_scanned = last_ticker
            db.commit()
            return {
                "run_id": run.id,
                "status": run.status,
                "tier": run.tier,
                "tickers_checked": run.tickers_checked,
                "state_changes_detected": run.state_changes_detected,
                "last_ticker_scanned": run.last_ticker_scanned,
            }

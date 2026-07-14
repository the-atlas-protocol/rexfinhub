"""SEC EDGAR HTTP client with adaptive rate limiting and disk caching.

Rate strategy: AIMD (Additive Increase, Multiplicative Decrease) — same
algorithm TCP uses for congestion control.  Start fast, back off on 503s,
creep back up when the coast is clear.
"""
import time, json
from collections import deque
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import USER_AGENT, SEC_SUBMISSIONS_URL

# ---------------------------------------------------------------------------
# Adaptive pacer
# ---------------------------------------------------------------------------

class AdaptivePacer:
    """AIMD pacer: additive increase on success, multiplicative decrease on error."""

    def __init__(self, initial: float = 0.15, floor: float = 0.10,
                 ceiling: float = 2.0, increase: float = 0.003,
                 backoff: float = 1.5, window: int = 50,
                 recovery_streak: int = 10):
        self.pause = initial
        self.floor = floor
        self.ceiling = ceiling
        self.increase = increase   # subtract this on success (additive)
        self.backoff = backoff     # multiply by this on error (multiplicative)
        self.window = window
        self.recovery_streak = recovery_streak
        self._history: deque[bool] = deque(maxlen=window)
        self._consecutive_ok = 0
        self._last_log = 0.0

    def success(self):
        self._history.append(True)
        self._consecutive_ok += 1
        # After a streak of successes, speed up more aggressively
        if self._consecutive_ok >= self.recovery_streak:
            self.pause = max(self.floor, self.pause * 0.90)  # 10% faster
            self._consecutive_ok = 0
        else:
            self.pause = max(self.floor, self.pause - self.increase)

    def failure(self):
        self._history.append(False)
        self._consecutive_ok = 0
        # Back off: multiply pause, clamp to ceiling
        self.pause = min(self.ceiling, self.pause * self.backoff)

    def wait(self):
        time.sleep(self.pause)

    @property
    def error_rate(self) -> float:
        if not self._history:
            return 0.0
        return sum(1 for x in self._history if not x) / len(self._history)

    def status_line(self) -> str:
        return (f"pause={self.pause:.3f}s  "
                f"err={self.error_rate:.1%} ({sum(1 for x in self._history if not x)}"
                f"/{len(self._history)})")

    def maybe_log(self, print_fn=print, every: float = 120):
        """Print rate status at most every `every` seconds."""
        now = time.time()
        if now - self._last_log >= every:
            print_fn(f"    [rate] {self.status_line()}")
            self._last_log = now


class SECClient:
    def __init__(self, pause: float = 0.12):
        self.pacer = AdaptivePacer(initial=pause, floor=0.10)

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        # Fewer retries — let the pacer handle backoff instead of burning
        # through 5 retries at escalating waits inside urllib3.
        retry = Retry(total=2, backoff_factor=0.3,
                      status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    # Keep legacy .pause readable for progress logging
    @property
    def pause(self) -> float:
        return self.pacer.pause

    def _get(self, url: str, timeout: int = 30) -> requests.Response:
        """GET with adaptive pacing.  Raises on final failure."""
        self.pacer.wait()
        try:
            r = self.session.get(url, timeout=timeout)
            r.raise_for_status()
            self.pacer.success()
            return r
        except (requests.exceptions.HTTPError,
                requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout) as e:
            self.pacer.failure()
            raise

    def fetch_text(self, url: str, use_cache: bool = False) -> str:
        """Fetch HTML text from SEC. Cache disabled — D: drive (exFAT/USB) can't
        handle 375K small file lookups. The database tracks extraction progress,
        not the cache. Cache files remain on disk as cold archive."""
        if not url:
            return ""
        r = self._get(url)
        return r.text

    def load_submissions(self, cik: str) -> dict:
        """Fetch issuer submission index from SEC. Always fresh — no D: cache."""
        cik_padded = f"{int(cik):010d}"
        url = SEC_SUBMISSIONS_URL.format(cik_padded=cik_padded)
        r = self._get(url)
        return r.json()

    def fetch_json(self, url: str) -> dict:
        """Fetch JSON from SEC. Always fresh — no D: cache."""
        if not url:
            return {}
        r = self._get(url)
        return r.json()

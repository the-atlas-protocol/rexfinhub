from __future__ import annotations
import time, json, hashlib
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
try:
    from .config import USER_AGENT_DEFAULT, SEC_SUBMISSIONS_URL
except Exception:
    USER_AGENT_DEFAULT = "REX-ETP-FilingTracker/1.0 (contact: set USER_AGENT)"
    SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{CIK_PADDED}.json"

class SECClient:
    def __init__(self, user_agent: str = USER_AGENT_DEFAULT, request_timeout: int = 30, pause: float = 0.25, cache_dir: Path | str = "http_cache"):
        self.user_agent = user_agent or USER_AGENT_DEFAULT
        self.timeout = request_timeout
        self.pause = float(pause)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        retry = Retry(total=5, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(["HEAD","GET","OPTIONS"]))
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        (self.cache_dir / "submissions").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "web").mkdir(parents=True, exist_ok=True)

    def _hash_url(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def fetch_header_text(self, url: str, use_cache: bool = True) -> str:
        """Read only the SEC-HEADER portion (~2KB) from a cached .txt file.
        Falls back to full fetch if file is not cached yet."""
        if not url:
            return ""
        cache_path = self.cache_dir / "web" / (self._hash_url(url) + ".txt")
        if use_cache and cache_path.exists():
            try:
                lines = []
                with open(cache_path, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        lines.append(line)
                        if "</SEC-HEADER>" in line:
                            break
                return "".join(lines)
            except Exception:
                pass
        # Not cached or read error - fetch full file
        return self.fetch_text(url, use_cache=use_cache)

    def fetch_text(self, url: str, use_cache: bool = True) -> str:
        if not url: return ""
        cache_path = self.cache_dir / "web" / (self._hash_url(url) + ".txt")
        if use_cache and cache_path.exists():
            try: return cache_path.read_text(encoding="utf-8", errors="ignore")
            except Exception: pass
        time.sleep(self.pause)
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        text = r.text
        try: cache_path.write_text(text, encoding="utf-8", errors="ignore")
        except Exception: pass
        return text

    def fetch_bytes(self, url: str, use_cache: bool = True) -> bytes:
        if not url: return b""
        cache_path = self.cache_dir / "web" / (self._hash_url(url) + ".bin")
        if use_cache and cache_path.exists():
            try: return cache_path.read_bytes()
            except Exception: pass
        time.sleep(self.pause)
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.content
        try: cache_path.write_bytes(data)
        except Exception: pass
        return data

    def load_submissions_json(self, cik: str, refresh_submissions: bool = True, refresh_max_age_hours: int = 6, refresh_force_now: bool = False) -> dict:
        cik_int = int(str(cik))
        cik_padded = f"{cik_int:010d}"
        url = SEC_SUBMISSIONS_URL.replace("{CIK_PADDED}", cik_padded)
        cache_path = self.cache_dir / "submissions" / f"{cik_padded}.json"
        should_refresh = refresh_force_now
        if refresh_submissions and not should_refresh:
            if not cache_path.exists(): should_refresh = True
            else:
                try:
                    import time as _t
                    age = (_t.time() - cache_path.stat().st_mtime) / 3600.0
                    if age >= float(refresh_max_age_hours): should_refresh = True
                except Exception:
                    should_refresh = True
        if should_refresh:
            time.sleep(self.pause)
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            try: cache_path.write_text(json.dumps(data), encoding="utf-8")
            except Exception: pass
            return data
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(self.pause)
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            try: cache_path.write_text(json.dumps(data), encoding="utf-8")
            except Exception: pass
            return data

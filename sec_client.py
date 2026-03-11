"""SEC EDGAR HTTP client with disk caching and rate limiting."""
import time, json, hashlib
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import USER_AGENT, SEC_SUBMISSIONS_URL, CACHE_DIR


class SECClient:
    def __init__(self, pause: float = 0.25):
        self.pause = pause
        self.cache_dir = Path(CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "submissions").mkdir(exist_ok=True)
        (self.cache_dir / "web").mkdir(exist_ok=True)

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        retry = Retry(total=5, backoff_factor=0.5,
                      status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _hash(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def fetch_text(self, url: str, use_cache: bool = True) -> str:
        if not url:
            return ""
        path = self.cache_dir / "web" / (self._hash(url) + ".txt")
        if use_cache and path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        time.sleep(self.pause)
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        try:
            path.write_text(r.text, encoding="utf-8", errors="ignore")
        except Exception:
            pass
        return r.text

    def load_submissions(self, cik: str, max_age_hours: float = 6) -> dict:
        cik_padded = f"{int(cik):010d}"
        url = SEC_SUBMISSIONS_URL.format(cik_padded=cik_padded)
        path = self.cache_dir / "submissions" / f"{cik_padded}.json"

        refresh = not path.exists()
        if not refresh:
            age = (time.time() - path.stat().st_mtime) / 3600
            if age >= max_age_hours:
                refresh = True

        if refresh:
            time.sleep(self.pause)
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            path.write_text(json.dumps(data), encoding="utf-8")
            return data

        return json.loads(path.read_text(encoding="utf-8"))

    def fetch_json(self, url: str, use_cache: bool = True) -> dict:
        if not url:
            return {}
        path = self.cache_dir / "submissions" / (self._hash(url) + ".json")
        if use_cache and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        time.sleep(self.pause)
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

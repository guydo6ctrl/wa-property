from __future__ import annotations
import hashlib
import random
import time
from pathlib import Path

import httpx
from django.conf import settings


USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

DEFAULT_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-AU,en;q=0.9',
}


def _cache_path(url: str) -> Path:
    h = hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]
    safe = url.replace('://', '_').replace('/', '_').replace('?', '_')[:120]
    return settings.CACHE_DIR / f'{safe}__{h}.html'


class PoliteClient:
    """Single-connection httpx client with on-disk cache and jittered delay."""

    def __init__(self, min_delay: float = 3.0, max_delay: float = 8.0):
        self.client = httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=30.0,
            http2=False,
        )
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_fetch = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.client.close()

    def get(self, url: str, *, use_cache: bool = True) -> str:
        path = _cache_path(url)
        if use_cache and path.exists():
            return path.read_text(encoding='utf-8')

        # throttle
        elapsed = time.monotonic() - self._last_fetch
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            time.sleep(delay - elapsed)

        resp = self.client.get(url)
        self._last_fetch = time.monotonic()
        resp.raise_for_status()
        path.write_text(resp.text, encoding='utf-8')
        return resp.text

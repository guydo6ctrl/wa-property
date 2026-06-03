"""Geocoding via OSM Nominatim. Polite: 1 request/second, descriptive UA."""
from __future__ import annotations
import time

import httpx


NOMINATIM = 'https://nominatim.openstreetmap.org/search'
UA = 'wa-property/0.1 (personal-use; https://github.com/local)'

_last_call = 0.0
_MIN_INTERVAL = 1.05  # Nominatim usage policy: max 1 req/sec


def geocode(address: str, *, country: str = 'au', client: httpx.Client | None = None,
            retries: int = 2) -> tuple[float, float] | None:
    """Return (lat, lon) for an address, or None if not found."""
    global _last_call
    own = client is None
    if own:
        client = httpx.Client(timeout=30.0)
    try:
        for attempt in range(retries + 1):
            elapsed = time.monotonic() - _last_call
            if elapsed < _MIN_INTERVAL:
                time.sleep(_MIN_INTERVAL - elapsed)
            try:
                r = client.get(
                    NOMINATIM,
                    params={'q': address, 'format': 'json', 'limit': 1, 'countrycodes': country},
                    headers={'User-Agent': UA, 'Accept-Language': 'en-AU,en'},
                )
                _last_call = time.monotonic()
                r.raise_for_status()
                results = r.json()
                if not results:
                    return None
                return float(results[0]['lat']), float(results[0]['lon'])
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError):
                _last_call = time.monotonic()
                if attempt == retries:
                    return None
                time.sleep(2 ** attempt)
        return None
    finally:
        if own:
            client.close()

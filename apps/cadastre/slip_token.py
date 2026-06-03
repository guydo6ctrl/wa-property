"""SLIP token client for the DPLH_Restricted_Services ArcGIS endpoints.

SLIP's token service mints short-lived ArcGIS tokens from a username/password.
Default token TTL is 60 minutes; we request 240 min and cache in-process.
Use `with_token(client, url)` to GET any restricted endpoint — it appends the
token query param and refreshes transparently on 498 (token expired).
"""
from __future__ import annotations
import os
import threading
import time
from urllib.parse import urlparse

import httpx


TOKEN_URL = 'https://token.slip.wa.gov.au/arcgis/tokens/generateToken'
TOKEN_TTL_MIN = 240  # request 4 hours; SLIP may cap lower

_lock = threading.Lock()
_cached: dict = {'token': None, 'expires_at': 0.0}


class SlipAuthError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    u = os.environ.get('SLIP_USERNAME', '').strip()
    p = os.environ.get('SLIP_PASSWORD', '').strip()
    if not u or not p:
        raise SlipAuthError(
            'SLIP_USERNAME and SLIP_PASSWORD must be set in .env '
            '(register at https://www.landgate.wa.gov.au/.../slip/).'
        )
    return u, p


def get_token(client: httpx.Client, *, force_refresh: bool = False) -> str:
    """Return a valid SLIP token, minting/refreshing as needed."""
    now = time.time()
    with _lock:
        if not force_refresh and _cached['token'] and _cached['expires_at'] - 60 > now:
            return _cached['token']
        u, p = _credentials()
        r = client.post(
            TOKEN_URL,
            data={
                'username': u, 'password': p,
                'client': 'requestip',
                'expiration': TOKEN_TTL_MIN,
                'f': 'json',
            },
            timeout=30.0,
        )
        r.raise_for_status()
        body = r.json()
        if 'token' not in body:
            raise SlipAuthError(f'SLIP token request rejected: {body}')
        # 'expires' is a unix ms timestamp.
        _cached['token'] = body['token']
        _cached['expires_at'] = body.get('expires', (now + TOKEN_TTL_MIN * 60) * 1000) / 1000
        return _cached['token']


def authed_get(client: httpx.Client, url: str, params: dict | None = None,
               *, retries: int = 1, **kwargs) -> httpx.Response:
    """GET a restricted SLIP URL with the current token; refresh once on 498/401."""
    params = dict(params or {})
    for attempt in range(retries + 1):
        params['token'] = get_token(client, force_refresh=(attempt > 0))
        r = client.get(url, params=params, **kwargs)
        # Esri signals expired/invalid token with HTTP 498 or a 200 body with error.code=498.
        if r.status_code in (401, 498):
            if attempt < retries:
                continue
        if r.headers.get('content-type', '').startswith('application/json'):
            try:
                body = r.json()
                err = (body or {}).get('error') or {}
                if err.get('code') in (498, 499) and attempt < retries:
                    continue
            except ValueError:
                pass
        return r
    return r  # type: ignore[return-value]

"""Landgate SLIP cadastre fetcher (LGATE-002 attributed cadastre).

Layer: Places_and_Addresses MapServer / layer 4 ("Cadastre Address - Large Scale").
Fields available: land_id, lot_number, road_number_1, road_number_2,
road_number_type, road_name, road_type, road_suffix, locality.

Paginated by (locality + objectid) so a single suburb fetch is deterministic
and resumable.
"""
from __future__ import annotations
import time
from typing import Iterator

import httpx


SLIP_BASE = (
    'https://services.slip.wa.gov.au/public/rest/services/'
    'SLIP_Public_Services/Places_and_Addresses/MapServer/4/query'
)
PAGE_SIZE = 1000  # service-side cap
UA = 'wa-property/0.1 (personal-use; https://github.com/local)'


def fetch_suburb(
    locality: str,
    *,
    client: httpx.Client,
    page_size: int = PAGE_SIZE,
    pause: float = 0.4,
) -> Iterator[dict]:
    """Yield raw GeoJSON Features for one locality. Locality is matched
    case-insensitively against SLIP (which stores uppercase)."""
    offset = 0
    locality_uc = locality.upper().strip()
    while True:
        r = client.get(
            SLIP_BASE,
            params={
                'where': f"locality='{locality_uc}'",
                'outFields': (
                    'land_id,lot_number,road_number_type,road_number_1,'
                    'road_number_2,road_name,road_type,road_suffix,locality'
                ),
                'returnGeometry': 'true',
                'outSR': 4326,
                'f': 'geojson',
                'orderByFields': 'objectid',
                'resultOffset': offset,
                'resultRecordCount': page_size,
            },
            headers={'User-Agent': UA},
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        feats = data.get('features') or []
        if not feats:
            return
        for f in feats:
            yield f
        if len(feats) < page_size:
            return
        offset += page_size
        time.sleep(pause)


def compose_address(props: dict) -> str:
    """Best-effort human-readable address from LGATE-002 attributes."""
    num = (props.get('road_number_1') or '').strip()
    num2 = (props.get('road_number_2') or '').strip()
    if num and num2:
        num = f'{num}-{num2}'
    elif not num and num2:
        num = num2
    name = (props.get('road_name') or '').strip().title()
    rtype = (props.get('road_type') or '').strip().title()
    suffix = (props.get('road_suffix') or '').strip()
    locality = (props.get('locality') or '').strip().title()

    street = ' '.join(p for p in (num, name, rtype, suffix) if p).strip()
    return f'{street}, {locality} WA'.strip(', ').strip()

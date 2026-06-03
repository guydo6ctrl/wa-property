"""Thin client for Landgate SLIP public ArcGIS REST services."""
from __future__ import annotations
from datetime import date, datetime, timezone

import httpx


SLIP_BASE = (
    'https://services.slip.wa.gov.au/public/rest/services/'
    'SLIP_Public_Services/Property_and_Planning/MapServer'
)
RCODES_LAYER = 111  # Local Planning Scheme - R Codes (DPLH-070)


def _esri_ms_to_date(ms) -> date | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def query_rcode_at(lon: float, lat: float, *, client: httpx.Client | None = None) -> dict | None:
    """Return {'r_code', 'scheme', 'gazetted'} for the polygon containing (lon, lat),
    or None if no R-code polygon at that point."""
    own = client is None
    if own:
        client = httpx.Client(timeout=20.0)
    try:
        r = client.get(
            f'{SLIP_BASE}/{RCODES_LAYER}/query',
            params={
                'geometry': f'{lon},{lat}',
                'geometryType': 'esriGeometryPoint',
                'inSR': 4326,
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'rcode_no,scheme_nam,scheme_no,gazettal_d',
                'returnGeometry': 'false',
                'f': 'json',
            },
            headers={'User-Agent': 'wa-property/0.1 (personal use)'},
        )
        r.raise_for_status()
        data = r.json()
        feats = data.get('features') or []
        if not feats:
            return None
        attr = feats[0].get('attributes') or {}
        return {
            'r_code': attr.get('rcode_no') or '',
            'scheme': f"{attr.get('scheme_nam', '')} {attr.get('scheme_no', '')}".strip(),
            'gazetted': _esri_ms_to_date(attr.get('gazettal_d')),
        }
    finally:
        if own:
            client.close()

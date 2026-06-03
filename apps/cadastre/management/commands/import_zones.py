"""Bulk-import SLIP layer 112 (Local Planning Scheme - Zones and Reserves, DPLH-071).
~127k polygons statewide. Used to gate the nearest-zone R-code inference: a lot
is only eligible to inherit a neighbouring R-code if its centroid sits inside a
residential zone here."""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone

import httpx
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.cadastre.models import ZoneAndReserve


SLIP_URL = (
    'https://services.slip.wa.gov.au/public/rest/services/'
    'SLIP_Public_Services/Property_and_Planning/MapServer/112/query'
)
PAGE_SIZE = 2000
UA = 'wa-property/0.1 (personal-use; https://github.com/local)'


def _ms_to_date(ms):
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


class Command(BaseCommand):
    help = 'Import SLIP layer 112 (Zones and Reserves) into cadastre.ZoneAndReserve.'

    def add_arguments(self, parser):
        parser.add_argument('--no-truncate', action='store_true')
        parser.add_argument('--pause', type=float, default=0.5)
        parser.add_argument('--limit', type=int, default=0)

    def handle(self, *args, **opts):
        t_start = time.monotonic()
        new_rows: list[ZoneAndReserve] = []
        page = 0
        offset = 0
        with httpx.Client(timeout=120.0, headers={'User-Agent': UA}) as client:
            while True:
                page += 1
                r = client.get(SLIP_URL, params={
                    'where': '1=1',
                    'outFields': 'objectid,zone,label,scheme_nam,scheme_no,lga,gazettal_d',
                    'returnGeometry': 'true',
                    'outSR': 4326,
                    'f': 'geojson',
                    'orderByFields': 'objectid',
                    'resultOffset': offset,
                    'resultRecordCount': PAGE_SIZE,
                })
                r.raise_for_status()
                feats = r.json().get('features') or []
                if not feats:
                    break
                self.stdout.write(f'  page {page}: {len(feats)} polygons')
                for f in feats:
                    obj = _feature_to_zone(f)
                    if obj is not None:
                        new_rows.append(obj)
                if opts['limit'] and len(new_rows) >= opts['limit']:
                    new_rows = new_rows[:opts['limit']]
                    break
                if len(feats) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
                time.sleep(opts['pause'])

        with transaction.atomic():
            if not opts['no_truncate']:
                deleted = ZoneAndReserve.objects.all().delete()[0]
                self.stdout.write(f'  truncated {deleted:,} existing rows')
            ZoneAndReserve.objects.bulk_create(new_rows, batch_size=500)

        elapsed = time.monotonic() - t_start
        self.stdout.write(self.style.SUCCESS(
            f'Done. {len(new_rows):,} zones imported in {elapsed:.1f}s. '
            f'Table now: {ZoneAndReserve.objects.count():,} rows.'
        ))


def _feature_to_zone(feat: dict) -> ZoneAndReserve | None:
    props = feat.get('properties') or {}
    geom_json = feat.get('geometry')
    if geom_json is None:
        return None
    try:
        g = GEOSGeometry(json.dumps(geom_json), srid=4326)
    except Exception:
        return None
    if isinstance(g, Polygon):
        mpoly = MultiPolygon(g, srid=4326)
    elif g.geom_type == 'MultiPolygon':
        mpoly = g
    else:
        return None
    scheme = ' '.join(p for p in (
        (props.get('scheme_nam') or '').strip(),
        (props.get('scheme_no') or '').strip(),
    ) if p)
    return ZoneAndReserve(
        zone=(props.get('zone') or '').strip()[:150],
        label=(props.get('label') or '').strip()[:255],
        lga=(props.get('lga') or '').strip()[:128],
        scheme=scheme,
        gazetted=_ms_to_date(props.get('gazettal_d')),
        geom=mpoly,
        raw=props,
    )

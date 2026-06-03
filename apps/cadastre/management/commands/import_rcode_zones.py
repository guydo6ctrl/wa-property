"""Bulk-import SLIP layer 111 (Local Planning Scheme - R Codes, DPLH-070)
into cadastre.RCodeZone. ~40k polygons statewide, fits in a few pages.

Idempotent: existing rows are replaced wholesale (this layer has no stable
external PK, so we use objectid mapped to RCodeZone.id via a 'raw.objectid'
field for trace).
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone

import httpx
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.cadastre.models import RCodeZone


SLIP_URL = (
    'https://services.slip.wa.gov.au/public/rest/services/'
    'SLIP_Public_Services/Property_and_Planning/MapServer/111/query'
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
    help = 'Import SLIP R-code zone polygons into cadastre.RCodeZone (replaces table contents).'

    def add_arguments(self, parser):
        parser.add_argument('--no-truncate', action='store_true',
                            help="Keep existing rows (default: truncate before insert).")
        parser.add_argument('--pause', type=float, default=0.5)
        parser.add_argument('--limit', type=int, default=0,
                            help='Stop after N polygons (testing).')

    def handle(self, *args, **opts):
        t_start = time.monotonic()
        new_rows: list[RCodeZone] = []
        page = 0
        offset = 0
        with httpx.Client(timeout=120.0) as client:
            while True:
                page += 1
                r = client.get(SLIP_URL, params={
                    'where': '1=1',
                    'outFields': 'objectid,rcode_no,gazettal_d,scheme_nam,scheme_no',
                    'returnGeometry': 'true',
                    'outSR': 4326,
                    'f': 'geojson',
                    'orderByFields': 'objectid',
                    'resultOffset': offset,
                    'resultRecordCount': PAGE_SIZE,
                }, headers={'User-Agent': UA})
                r.raise_for_status()
                feats = r.json().get('features') or []
                if not feats:
                    break
                self.stdout.write(f'  page {page}: {len(feats)} polygons')
                for f in feats:
                    obj = _feature_to_rczone(f)
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
                deleted = RCodeZone.objects.all().delete()[0]
                self.stdout.write(f'  truncated {deleted} existing rows')
            RCodeZone.objects.bulk_create(new_rows, batch_size=500)

        elapsed = time.monotonic() - t_start
        self.stdout.write(self.style.SUCCESS(
            f'Done. {len(new_rows):,} R-code zones imported in {elapsed:.1f}s. '
            f'Table now: {RCodeZone.objects.count():,} rows.'
        ))


def _feature_to_rczone(feat: dict) -> RCodeZone | None:
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
    code = (props.get('rcode_no') or '').strip()
    scheme = ' '.join(p for p in (
        (props.get('scheme_nam') or '').strip(),
        (props.get('scheme_no') or '').strip(),
    ) if p)
    return RCodeZone(
        code=code,
        scheme=scheme,
        gazetted=_ms_to_date(props.get('gazettal_d')),
        geom=mpoly,
        raw={
            'objectid': props.get('objectid'),
            'rcode_no': props.get('rcode_no'),
            'scheme_nam': props.get('scheme_nam'),
            'scheme_no': props.get('scheme_no'),
        },
    )

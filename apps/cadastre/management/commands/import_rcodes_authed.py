"""Authenticated R-code zone import from DPLH_Restricted_Services.

Replaces what `import_rcode_zones` does using the public layer 111 with the
fuller picture available via the restricted endpoint (DPLH-070 token-auth).
This is the data PlanWA uses — generally includes Structure Plan and recent
amendment R-codes that the public layer hasn't yet picked up.

Stores into the existing `cadastre.RCodeZone` table, replacing its contents
by default (use --no-truncate to append).
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
from apps.cadastre.slip_token import authed_get, SlipAuthError


SLIP_URL = (
    'https://services.slip.wa.gov.au/arcgis/rest/services/'
    'DPLH_Restricted_Services/DPLH_Restricted_Government_Services/MapServer/5/query'
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
    help = 'Authenticated R-code zone import (DPLH restricted) — supersedes public layer 111.'

    def add_arguments(self, parser):
        parser.add_argument('--no-truncate', action='store_true')
        parser.add_argument('--pause', type=float, default=0.5)
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--probe', action='store_true',
                            help='Probe the endpoint schema/count and exit.')

    def handle(self, *args, **opts):
        try:
            with httpx.Client(timeout=120.0, headers={'User-Agent': UA}) as client:
                if opts['probe']:
                    self._probe(client)
                    return
                self._import(client, opts)
        except SlipAuthError as e:
            self.stderr.write(self.style.ERROR(str(e)))

    def _probe(self, client):
        """Inspect the layer's schema + total feature count."""
        meta_url = SLIP_URL.replace('/query', '')
        r = authed_get(client, meta_url, params={'f': 'json'})
        r.raise_for_status()
        d = r.json()
        self.stdout.write(f"NAME: {d.get('name')}")
        self.stdout.write(f"GEOM: {d.get('geometryType')}  MAX/PAGE: {d.get('maxRecordCount')}")
        self.stdout.write('FIELDS:')
        for f in d.get('fields') or []:
            self.stdout.write(f"  {f['name']} ({f.get('type')})")

        r = authed_get(client, SLIP_URL, params={
            'where': '1=1', 'returnCountOnly': 'true', 'f': 'json',
        })
        r.raise_for_status()
        self.stdout.write(f"COUNT: {r.json().get('count'):,}")

    def _import(self, client, opts):
        t_start = time.monotonic()
        new_rows: list[RCodeZone] = []
        page = 0
        offset = 0
        while True:
            page += 1
            r = authed_get(client, SLIP_URL, params={
                'where': '1=1',
                'outFields': '*',
                'returnGeometry': 'true',
                'outSR': 4326,
                'f': 'geojson',
                'orderByFields': 'objectid',
                'resultOffset': offset,
                'resultRecordCount': PAGE_SIZE,
            })
            r.raise_for_status()
            data = r.json()
            feats = data.get('features') or []
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
                self.stdout.write(f'  truncated {deleted:,} existing rows')
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
    # Restricted layer's field names may differ; common SLIP ones are listed first
    # with fallbacks. The --probe option reveals the exact schema.
    code = (
        props.get('rcode_no')
        or props.get('r_code')
        or props.get('code')
        or ''
    )
    scheme = ' '.join(p for p in (
        str(props.get('scheme_nam') or props.get('scheme_name') or '').strip(),
        str(props.get('scheme_no') or props.get('scheme_number') or '').strip(),
    ) if p)
    gazettal = props.get('gazettal_d') or props.get('gazettal_date')
    return RCodeZone(
        code=str(code).strip(),
        scheme=scheme,
        gazetted=_ms_to_date(gazettal),
        geom=mpoly,
        raw=props,
    )

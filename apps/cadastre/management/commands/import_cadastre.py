"""Bulk-import Landgate LGATE-002 cadastre polygons for the given suburbs.

Idempotent: re-running for the same suburb upserts rows keyed on landgate_id.
Multi-part parcels (multiple polygon rows sharing one land_id) are merged into
a single MultiPolygon per Lot.
"""
from __future__ import annotations
import time
from collections import defaultdict

import httpx
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.management.base import BaseCommand
from django.utils.text import capfirst

from apps.cadastre.models import Lot
from apps.cadastre.slip_cadastre import fetch_suburb, compose_address


# Bundled list — Perth metropolitan dev belt. Edit freely.
PERTH_METRO = [
    # inner west / coastal
    'Claremont', 'Cottesloe', 'Mosman Park', 'Peppermint Grove', 'Swanbourne',
    'Dalkeith', 'Nedlands', 'Mount Claremont', 'Shenton Park', 'Subiaco',
    'West Leederville', 'Leederville', 'Wembley', 'Floreat', 'City Beach',
    'Daglish', 'Jolimont', 'Glendalough', 'Wembley Downs', 'Doubleview',
    'Innaloo', 'Karrinyup', 'Trigg', 'Scarborough', 'North Beach',
    'Woodlands', 'Churchlands', 'Osborne Park', 'Stirling',
    # inner / central
    'Perth', 'West Perth', 'East Perth', 'Northbridge', 'Highgate',
    'Mount Lawley', 'Inglewood', 'Bedford', 'Maylands', 'Bayswater',
    'Embleton', 'Morley', 'Noranda', 'Dianella', 'Yokine', 'Coolbinia',
    'Menora', 'Mount Hawthorn', 'North Perth', 'Joondanna', 'Tuart Hill',
    'Nollamara', 'Mirrabooka', 'Balcatta', 'Westminster', 'Beechboro',
    'Bassendean', 'Ashfield', 'Eden Hill', 'Kiara',
    # eastern / airport
    'Belmont', 'Ascot', 'Redcliffe', 'Cloverdale', 'Rivervale', 'Kewdale',
    'Burswood', 'Victoria Park', 'East Victoria Park', 'Carlisle',
    'Lathlain', 'St James', 'Bentley', 'Wilson', 'Karawara', 'Waterford',
    'Como', 'Manning', 'Salter Point', 'South Perth', 'Kensington',
    # south / river
    'Applecross', 'Ardross', 'Mount Pleasant', 'Booragoon', 'Alfred Cove',
    'Brentwood', 'Bicton', 'East Fremantle', 'Palmyra', 'Fremantle',
    'North Fremantle', 'South Fremantle', 'Beaconsfield', 'White Gum Valley',
    'Hilton', 'Hamilton Hill', 'Coolbellup', 'Spearwood', 'Coogee',
    # south east / cannington corridor
    'Cannington', 'East Cannington', 'Queens Park', 'Beckenham',
    'Langford', 'Lynwood', 'Ferndale', 'Parkwood', 'Riverton', 'Shelley',
    'Rossmoyne', 'Willetton', 'Bull Creek', 'Leeming', 'Winthrop',
    # north corridor (Wanneroo / Joondalup belt)
    'Joondalup', 'Currambine', 'Edgewater', 'Heathridge', 'Mullaloo',
    'Hillarys', 'Sorrento', 'Duncraig', 'Padbury', 'Greenwood',
    'Kingsley', 'Warwick', 'Hamersley', 'Carine',
    # outer south (Mandurah corridor – optional, comment out if too big)
    'Rockingham', 'Kwinana', 'Baldivis',
    # swan valley / north east
    'Guildford', 'South Guildford', 'Midland', 'Midvale', 'Helena Valley',
    'Stratton', 'Swan View',
]


class Command(BaseCommand):
    help = 'Bulk-import LGATE-002 cadastre polygons into cadastre.Lot for the given suburbs.'

    def add_arguments(self, parser):
        parser.add_argument('--suburbs', help='Comma-separated suburb names (e.g. "Claremont,Bayswater"). Ignored if --metro is set.')
        parser.add_argument('--metro', action='store_true', help='Import the bundled Perth metro suburb list (~150).')
        parser.add_argument('--limit-per-suburb', type=int, default=0, help='Cap polygons per suburb for testing (0 = no cap).')
        parser.add_argument('--pause', type=float, default=0.4, help='Seconds to sleep between SLIP page requests.')

    def handle(self, *args, **opts):
        if opts['metro']:
            suburbs = PERTH_METRO
        elif opts['suburbs']:
            suburbs = [s.strip() for s in opts['suburbs'].split(',') if s.strip()]
        else:
            self.stderr.write('Pass --suburbs "X,Y" or --metro.')
            return

        total_new = 0
        total_updated = 0
        t_start = time.monotonic()
        with httpx.Client(timeout=60.0) as client:
            for suburb in suburbs:
                t0 = time.monotonic()
                self.stdout.write(self.style.NOTICE(f'== {suburb} =='))
                try:
                    new, upd = self._import_suburb(suburb, client=client,
                                                   limit=opts['limit_per_suburb'],
                                                   pause=opts['pause'])
                except Exception as e:
                    self.stderr.write(self.style.ERROR(
                        f'  !! {suburb} aborted: {type(e).__name__}: {e}'
                    ))
                    continue
                total_new += new
                total_updated += upd
                dt = time.monotonic() - t0
                self.stdout.write(f'   {new} new, {upd} updated in {dt:.1f}s')

        elapsed = time.monotonic() - t_start
        self.stdout.write(self.style.SUCCESS(
            f'Done. {total_new} new lots, {total_updated} updated. '
            f'Elapsed {elapsed/60:.1f}min. Table now: {Lot.objects.count():,} rows.'
        ))

    def _import_suburb(self, suburb: str, *, client: httpx.Client,
                       limit: int, pause: float) -> tuple[int, int]:
        # Group rows by land_id; multi-part parcels become MultiPolygon.
        parcels: dict[int, dict] = defaultdict(lambda: {'polys': [], 'props': None})
        count = 0
        for feat in fetch_suburb(suburb, client=client, pause=pause):
            props = feat.get('properties') or {}
            geom_json = feat.get('geometry')
            if geom_json is None or not props.get('land_id'):
                continue
            try:
                g = GEOSGeometry(_to_geom_str(geom_json), srid=4326)
            except Exception:
                continue
            land_id = int(props['land_id'])
            if isinstance(g, Polygon):
                parcels[land_id]['polys'].append(g)
            elif g.geom_type == 'MultiPolygon':
                parcels[land_id]['polys'].extend(list(g))
            else:
                continue
            if parcels[land_id]['props'] is None:
                parcels[land_id]['props'] = props
            count += 1
            if limit and count >= limit:
                break

        # Upsert.
        new_count = 0
        upd_count = 0
        BATCH = 500
        batch_objs: list[Lot] = []
        existing_ids = set(
            Lot.objects.filter(
                landgate_id__in=[str(lid) for lid in parcels.keys()]
            ).values_list('landgate_id', flat=True)
        )

        for land_id, payload in parcels.items():
            polys = payload['polys']
            props = payload['props']
            if not polys:
                continue
            try:
                mpoly = MultiPolygon(*polys, srid=4326)
            except Exception:
                continue
            address = compose_address(props)
            area_sqm = _approx_area_sqm(mpoly)
            obj = Lot(
                landgate_id=str(land_id),
                geom=mpoly,
                address=address,
                lga='',  # SLIP layer 4 doesn't carry LGA; will be backfilled later.
                area_sqm=area_sqm,
                raw={
                    'lot_number': props.get('lot_number'),
                    'road_number_type': props.get('road_number_type'),
                    'road_number_1': props.get('road_number_1'),
                    'road_number_2': props.get('road_number_2'),
                    'road_name': props.get('road_name'),
                    'road_type': props.get('road_type'),
                    'road_suffix': props.get('road_suffix'),
                    'locality': props.get('locality'),
                },
            )
            if str(land_id) in existing_ids:
                upd_count += 1
            else:
                new_count += 1
            batch_objs.append(obj)
            if len(batch_objs) >= BATCH:
                Lot.objects.bulk_create(
                    batch_objs,
                    update_conflicts=True,
                    unique_fields=['landgate_id'],
                    update_fields=['geom', 'address', 'area_sqm', 'raw'],
                )
                batch_objs = []
        if batch_objs:
            Lot.objects.bulk_create(
                batch_objs,
                update_conflicts=True,
                unique_fields=['landgate_id'],
                update_fields=['geom', 'address', 'area_sqm', 'raw'],
            )
        return new_count, upd_count


def _to_geom_str(geom_json: dict) -> str:
    import json
    return json.dumps(geom_json)


def _approx_area_sqm(mpoly: MultiPolygon) -> int | None:
    """Area in m² via GDA94 / MGA Zone 50 (EPSG:28350) — the standard projected
    CRS for SW Western Australia. Accurate to ~0.1% at Perth latitudes."""
    try:
        clone = mpoly.clone()
        clone.transform(28350)
        return int(round(clone.area))
    except Exception:
        return None

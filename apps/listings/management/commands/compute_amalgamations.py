"""Rebuild the AmalgamationCluster table from current Listings + cadastre.

Same logic as `report_amalgamations` but persists results so the admin can
sort/filter/click. Drops all existing clusters before recomputing.
"""
from __future__ import annotations
import re
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.listings.models import AmalgamationCluster, Listing
from apps.zoning.rcodes import avg_site_area, parse_rcode


ADDR_RE = re.compile(r'^(?P<num>\d+)([A-Za-z]?)\s+(?P<street>.+?),\s*(?P<suburb>[^,]+?)\s+WA\s+\d{4}\s*$')
UNDER_OFFER_RE = re.compile(r'(under\s+offer|under\s+contract|sold|withdrawn)', re.I)


def _resolve_lot(listing: Listing) -> dict | None:
    """Return {'lot_id', 'area_sqm', 'r_code'} from cadastre.Lot via best available method."""
    if listing.lot_id:
        with connection.cursor() as c:
            c.execute("SELECT id, area_sqm, r_code FROM cadastre_lot WHERE id = %s", [listing.lot_id])
            row = c.fetchone()
            if row:
                return {'lot_id': row[0], 'area_sqm': row[1], 'r_code': row[2]}

    # Address parse beats Nominatim because Nominatim collapses to suburb/street
    # centroid for inexact matches and makes neighbours look like the same lot.
    m = ADDR_RE.match(listing.address_raw or '')
    if m:
        bits = m.group('street').split()
        name = (' '.join(bits[:-1]) if len(bits) >= 2 else m.group('street')).upper()
        locality = m.group('suburb').strip().upper()
        with connection.cursor() as c:
            c.execute("""
                SELECT id, area_sqm, r_code FROM cadastre_lot
                WHERE raw->>'road_number_1' = %s
                  AND UPPER(raw->>'road_name') = %s
                  AND raw->>'locality' = %s
                LIMIT 1
            """, [m.group('num'), name, locality])
            row = c.fetchone()
            if row:
                return {'lot_id': row[0], 'area_sqm': row[1], 'r_code': row[2]}

    if listing.latitude is not None and listing.longitude is not None:
        with connection.cursor() as c:
            c.execute("""
                SELECT id, area_sqm, r_code FROM cadastre_lot
                WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ORDER BY area_sqm ASC NULLS LAST
                LIMIT 1
            """, [listing.longitude, listing.latitude])
            row = c.fetchone()
            if row:
                return {'lot_id': row[0], 'area_sqm': row[1], 'r_code': row[2]}
    return None


def _connected_components(lot_ids: list[int]) -> list[set[int]]:
    """Group `lot_ids` into connected components by ST_Touches. Returns one
    set per component. Singletons are included so the caller can decide what
    to do with them."""
    if not lot_ids:
        return []
    with connection.cursor() as c:
        c.execute("""
            SELECT a.id, b.id
            FROM cadastre_lot a, cadastre_lot b
            WHERE a.id = ANY(%s) AND b.id = ANY(%s) AND a.id < b.id
              AND ST_Touches(a.geom, b.geom)
        """, [lot_ids, lot_ids])
        pairs = c.fetchall()
    parent = {i: i for i in lot_ids}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in pairs:
        parent[find(a)] = find(b)
    groups: dict[int, set[int]] = {}
    for i in lot_ids:
        groups.setdefault(find(i), set()).add(i)
    return list(groups.values())


def _looks_like_strata_cluster(members: list[dict]) -> bool:
    """Heuristic: same street number with only alpha suffixes (6A/6B/6C) AND
    uniform lot sizes → new strata subdivision, not an amalgamation target."""
    nums = []
    for e in members:
        m = ADDR_RE.match(e['listing'].address_raw or '')
        if m:
            nums.append(m.group('num'))
    if not nums or len(set(nums)) != 1:
        return False
    # Same street number across all members; check size uniformity within 10%.
    areas = [e['area_sqm'] for e in members if e['area_sqm']]
    if len(areas) < 2:
        return False
    avg = sum(areas) / len(areas)
    return all(abs(a - avg) / avg < 0.1 for a in areas)


class Command(BaseCommand):
    help = 'Recompute and persist AmalgamationCluster rows from current data.'

    def add_arguments(self, parser):
        parser.add_argument('--include-zero-gain', action='store_true',
                            help='Persist clusters with gain <= 0 too (default: skip).')

    def handle(self, *args, **opts):
        # Group active non-under-offer listings by adjacency_key.
        clusters: dict[str, list[Listing]] = {}
        for l in (Listing.objects
                  .filter(status=Listing.STATUS_ACTIVE)
                  .exclude(adjacency_key='')):
            if UNDER_OFFER_RE.search(l.price_text or ''):
                continue
            clusters.setdefault(l.adjacency_key, []).append(l)

        rows: list[AmalgamationCluster] = []
        skipped_missing = 0
        skipped_not_touching = 0
        for key, members in clusters.items():
            if len(members) < 2:
                continue

            enriched: list[dict] = []
            for m in members:
                lot = _resolve_lot(m)
                area = (lot or {}).get('area_sqm') or m.land_area_sqm
                rcode = (lot or {}).get('r_code') or m.r_code
                base, _ = parse_rcode(rcode) if rcode else (None, None)
                avg = avg_site_area(rcode) if rcode else None
                enriched.append({
                    'listing': m,
                    'lot_id': (lot or {}).get('lot_id'),
                    'area_sqm': area, 'r_code': rcode or '',
                    'r_code_base': base, 'avg': avg,
                })

            if any(e['area_sqm'] in (None, 0) or e['avg'] is None or e['lot_id'] is None
                   for e in enriched):
                skipped_missing += 1
                continue

            # Find connected components within this address-heuristic cluster.
            # Each component of ≥2 members is a genuine amalgamation candidate.
            lot_ids = [e['lot_id'] for e in enriched]
            components = _connected_components(lot_ids)
            real_groups = [c for c in components if len(c) >= 2]
            if not real_groups:
                skipped_not_touching += 1
                continue

            for idx, group_lot_ids in enumerate(real_groups):
                group_members = [e for e in enriched if e['lot_id'] in group_lot_ids]
                base_codes = Counter(e['r_code_base'] for e in group_members)
                majority_base, _ = base_codes.most_common(1)[0]
                mixed = len(base_codes) > 1
                combine_avg = max(e['avg'] for e in group_members) if mixed else group_members[0]['avg']

                individual = sum(max(1, int(e['area_sqm'] // e['avg'])) for e in group_members)
                total_area = sum(e['area_sqm'] for e in group_members)
                combined = max(1, int(total_area // combine_avg))
                gain = combined - individual

                if gain <= 0 and not opts['include_zero_gain']:
                    continue

                notes = ''
                if _looks_like_strata_cluster(group_members):
                    notes = 'likely new strata cluster — false positive'

                # If the original cluster split into multiple touching groups,
                # disambiguate the persisted key.
                persist_key = key if len(real_groups) == 1 else f'{key}#{idx + 1}'

                rows.append(AmalgamationCluster(
                    adjacency_key=persist_key,
                    total_area_sqm=int(total_area),
                    majority_rcode=majority_base or '',
                    mixed_codes=mixed,
                    individual_dwellings=individual,
                    combined_dwellings=combined,
                    gain=gain,
                    member_count=len(group_members),
                    members=[
                        {
                            'listing_id': e['listing'].pk,
                            'address': e['listing'].address_raw,
                            'area_sqm': e['area_sqm'],
                            'r_code': e['r_code'],
                            'url': e['listing'].url,
                            'text_tags': e['listing'].text_tags or '',
                            'price_text': e['listing'].price_text or '',
                        }
                        for e in group_members
                    ],
                    notes=notes,
                ))

        with transaction.atomic():
            AmalgamationCluster.objects.all().delete()
            AmalgamationCluster.objects.bulk_create(rows)

        self.stdout.write(self.style.SUCCESS(
            f'Recomputed {len(rows)} AmalgamationCluster rows '
            f'(skipped {skipped_missing} missing data, '
            f'{skipped_not_touching} not geometrically touching).'
        ))

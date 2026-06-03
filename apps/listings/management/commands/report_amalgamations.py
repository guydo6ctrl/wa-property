"""Edge 2 — amalgamation opportunities.

For each adjacency cluster of active listings:
  - resolve every member to its cadastre Lot (matched id → address parse fallback)
  - read area and R-code from the Lot (authoritative) where present, else from the
    Listing as a backup
  - compute:
      individual_dwellings = sum(floor(member_area / avg_site_area(member_rcode)))
      combined_dwellings   = floor(sum_area / avg_site_area(majority_rcode))
      net_gain             = combined - individual
  - show the cluster with one row per member, the math, and the net gain.

Sorts clusters by net_gain desc; only shows clusters with net_gain >= 1 unless
--all is passed. Skips clusters where R-code is unknown for any member.
"""
from __future__ import annotations
import re
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection

from apps.listings.models import Listing
from apps.zoning.rcodes import avg_site_area, parse_rcode


ADDR_RE = re.compile(r'^(?P<num>\d+)([A-Za-z]?)\s+(?P<street>.+?),\s*(?P<suburb>[^,]+?)\s+WA\s+\d{4}\s*$')
UNDER_OFFER_RE = re.compile(r'(under\s+offer|under\s+contract|sold|withdrawn)', re.I)


def _resolve_lot(listing: Listing) -> dict | None:
    """Return {'area_sqm', 'r_code'} from cadastre.Lot via best available method."""
    # 1. Explicit FK
    if listing.lot_id:
        with connection.cursor() as c:
            c.execute("SELECT area_sqm, r_code FROM cadastre_lot WHERE id = %s", [listing.lot_id])
            row = c.fetchone()
            if row:
                return {'area_sqm': row[0], 'r_code': row[1]}

    # 2. Geocode → PIP
    if listing.latitude is not None and listing.longitude is not None:
        with connection.cursor() as c:
            c.execute("""
                SELECT area_sqm, r_code FROM cadastre_lot
                WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ORDER BY area_sqm ASC NULLS LAST
                LIMIT 1
            """, [listing.longitude, listing.latitude])
            row = c.fetchone()
            if row:
                return {'area_sqm': row[0], 'r_code': row[1]}

    # 3. Address parse
    m = ADDR_RE.match(listing.address_raw or '')
    if m:
        bits = m.group('street').split()
        name = (' '.join(bits[:-1]) if len(bits) >= 2 else m.group('street')).upper()
        locality = m.group('suburb').strip().upper()
        with connection.cursor() as c:
            c.execute("""
                SELECT area_sqm, r_code FROM cadastre_lot
                WHERE raw->>'road_number_1' = %s
                  AND UPPER(raw->>'road_name') = %s
                  AND raw->>'locality' = %s
                LIMIT 1
            """, [m.group('num'), name, locality])
            row = c.fetchone()
            if row:
                return {'area_sqm': row[0], 'r_code': row[1]}

    return None


class Command(BaseCommand):
    help = 'Edge 2: rank adjacency clusters where combined yield exceeds sum of individual yields.'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true',
                            help='Show all clusters, not only net-gain >= 1.')
        parser.add_argument('--top', type=int, default=40)

    def handle(self, *args, **opts):
        clusters: dict[str, list[Listing]] = {}
        for l in (Listing.objects
                  .filter(status=Listing.STATUS_ACTIVE)
                  .exclude(adjacency_key='')):
            if UNDER_OFFER_RE.search(l.price_text or ''):
                continue
            clusters.setdefault(l.adjacency_key, []).append(l)

        rows = []
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
                    'area_sqm': area,
                    'r_code': rcode,
                    'r_code_base': base,
                    'avg': avg,
                })

            # Skip if any member is missing area or R-code (can't compute meaningfully).
            if any(e['area_sqm'] in (None, 0) or e['avg'] is None for e in enriched):
                continue

            # Use the majority R-code for the combined-yield calculation. Mixed-code
            # amalgamation is more nuanced (downzones to lowest, usually) — flag it.
            base_codes = Counter(e['r_code_base'] for e in enriched)
            majority_base, _ = base_codes.most_common(1)[0]
            mixed = len(base_codes) > 1
            # Use the LOWEST density (largest avg site area) when codes are mixed —
            # that's how WA planning treats amalgamations of mixed-density lots.
            if mixed:
                combine_avg = max(e['avg'] for e in enriched)
            else:
                combine_avg = enriched[0]['avg']

            # Each existing lot already has a dwelling, so its capacity is at least 1
            # even if area/avg < 1 (you can't *lose* a dwelling by holding the title).
            # Combined capacity is similarly floored at 1.
            individual = sum(max(1, int(e['area_sqm'] // e['avg'])) for e in enriched)
            total_area = sum(e['area_sqm'] for e in enriched)
            combined = max(1, int(total_area // combine_avg))
            gain = combined - individual

            if not opts['all'] and gain < 1:
                continue

            rows.append({
                'key': key,
                'members': enriched,
                'total_area': total_area,
                'individual': individual,
                'combined': combined,
                'gain': gain,
                'mixed': mixed,
                'base_codes': dict(base_codes),
            })

        rows.sort(key=lambda r: (-r['gain'], -r['total_area']))

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(rows)} amalgamation clusters with net gain '
            f'{"≥ 1" if not opts["all"] else "shown"}\n'
        ))
        for i, r in enumerate(rows[:opts['top']], 1):
            mix = ' (mixed codes)' if r['mixed'] else ''
            self.stdout.write(self.style.WARNING(
                f'{i}. {r["key"]}  '
                f'— individual yields: {r["individual"]} dwellings, '
                f'combined: {r["combined"]} (+{r["gain"]}){mix}'
            ))
            for e in r['members']:
                m = e['listing']
                addr = (m.address_raw or '').split(',', 1)[0]
                tags = (m.text_tags or '') and f'  [{m.text_tags}]' or ''
                cap = max(1, int(e["area_sqm"] // e["avg"]))
                self.stdout.write(
                    f'     · {addr:<42}  {e["area_sqm"]}m²  {e["r_code"]:<8}  '
                    f'individual={cap}  {m.price_text[:32]}{tags}'
                )
            self.stdout.write('')

from __future__ import annotations
import httpx
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.listings.models import Listing
from apps.zoning import geocode as gc
from apps.zoning import slip
from apps.zoning.rcodes import avg_site_area


class Command(BaseCommand):
    help = 'Geocode listings and look up R-codes from SLIP. Computes subdivision ratio.'

    def add_arguments(self, parser):
        parser.add_argument('--suburbs', help='Comma-separated suburb filter (default: all)')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--force', action='store_true', help='Re-geocode and re-lookup even if already done')
        parser.add_argument('--needs-land', action='store_true', help='Only listings with land_area_sqm set')

    def handle(self, *args, **opts):
        qs = Listing.objects.filter(status=Listing.STATUS_ACTIVE)
        if opts['suburbs']:
            qs = qs.filter(suburb__in=[s.strip() for s in opts['suburbs'].split(',') if s.strip()])
        if opts['needs_land']:
            qs = qs.exclude(land_area_sqm__isnull=True)
        if not opts['force']:
            qs = qs.filter(Q(latitude__isnull=True) | Q(r_code='') | Q(subdivide_ratio__isnull=True))
        if opts['limit']:
            qs = qs[:opts['limit']]

        total = qs.count()
        self.stdout.write(f'Enriching {total} listings...')

        slip_client = httpx.Client(timeout=20.0)
        try:
            for i, listing in enumerate(qs, 1):
                self._enrich_one(listing, slip_client)
                self.stdout.write(
                    f'  [{i:>3}/{total}] {listing.address_raw[:50]:50s}  '
                    f'r={listing.r_code or "—":>8}  ratio={listing.subdivide_ratio or 0:.2f}'
                )
        finally:
            slip_client.close()

    def _enrich_one(self, listing: Listing, slip_client: httpx.Client) -> None:
        if listing.latitude is None or listing.longitude is None:
            point = gc.geocode(f'{listing.address_raw}, Australia')
            if point is None:
                self.stderr.write(f'  geocode miss: {listing.address_raw}')
                return
            listing.latitude, listing.longitude = point
            listing.geocode_source = 'nominatim'
            listing.geocoded_at = timezone.now()

        if not listing.r_code:
            info = slip.query_rcode_at(listing.longitude, listing.latitude, client=slip_client)
            if info is None:
                self.stderr.write(f'  r-code miss at point: {listing.address_raw}')
            else:
                listing.r_code = info['r_code']
                listing.r_code_scheme = info['scheme']
                listing.r_code_gazetted = info['gazetted']
                listing.r_code_lookup_at = timezone.now()

        if listing.land_area_sqm and listing.r_code:
            avg = avg_site_area(listing.r_code)
            if avg:
                listing.subdivide_ratio = listing.land_area_sqm / avg

        listing.save(update_fields=[
            'latitude', 'longitude', 'geocode_source', 'geocoded_at',
            'r_code', 'r_code_scheme', 'r_code_gazetted', 'r_code_lookup_at',
            'subdivide_ratio',
        ])

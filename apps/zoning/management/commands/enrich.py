from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.listings.models import Listing
from apps.zoning import geocode as gc


class Command(BaseCommand):
    help = 'Geocode active listings via Nominatim (used as PIP fallback by match_lots).'

    def add_arguments(self, parser):
        parser.add_argument('--suburbs', help='Comma-separated suburb filter (default: all)')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--force', action='store_true', help='Re-geocode even if already done')

    def handle(self, *args, **opts):
        qs = Listing.objects.filter(status=Listing.STATUS_ACTIVE)
        if opts['suburbs']:
            qs = qs.filter(suburb__in=[s.strip() for s in opts['suburbs'].split(',') if s.strip()])
        if not opts['force']:
            qs = qs.filter(latitude__isnull=True)
        if opts['limit']:
            qs = qs[:opts['limit']]

        total = qs.count()
        self.stdout.write(f'Geocoding {total} listings...')

        for i, listing in enumerate(qs, 1):
            point = gc.geocode(f'{listing.address_raw}, Australia')
            if point is None:
                self.stderr.write(f'  geocode miss: {listing.address_raw}')
                continue
            listing.latitude, listing.longitude = point
            listing.geocode_source = 'nominatim'
            listing.geocoded_at = timezone.now()
            listing.save(update_fields=['latitude', 'longitude', 'geocode_source', 'geocoded_at'])
            self.stdout.write(f'  [{i:>3}/{total}] {listing.address_raw[:60]}')

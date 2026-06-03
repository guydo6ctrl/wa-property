from __future__ import annotations
import dataclasses

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.listings.models import Listing
from apps.listings.sites import reiwa
from apps.listings.sites.common import PoliteClient


class Command(BaseCommand):
    help = 'Scrape reiwa.com.au listings for one or more suburbs.'

    def add_arguments(self, parser):
        parser.add_argument('--suburbs', required=True, help='Comma-separated suburb slugs (e.g. claremont,bayswater)')
        parser.add_argument('--max-pages', type=int, default=50)
        parser.add_argument('--no-cache', action='store_true', help='Bypass on-disk HTML cache')
        parser.add_argument('--min-delay', type=float, default=3.0)
        parser.add_argument('--max-delay', type=float, default=8.0)

    def handle(self, *args, **opts):
        suburbs = [s.strip().lower() for s in opts['suburbs'].split(',') if s.strip()]
        if not suburbs:
            self.stderr.write('No suburbs given')
            return

        client = PoliteClient(min_delay=opts['min_delay'], max_delay=opts['max_delay'])
        # If --no-cache, monkey-patch use_cache default by wrapping get
        if opts['no_cache']:
            orig = client.get
            client.get = lambda url, use_cache=False: orig(url, use_cache=False)  # type: ignore[method-assign]

        def on_error(stage, url, exc):
            self.stderr.write(f'  !! {stage} error {type(exc).__name__}: {url}  {exc!s:.120}')

        n_total = 0
        n_created = 0
        n_errors = 0
        with client:
            for suburb in suburbs:
                self.stdout.write(self.style.NOTICE(f'== {suburb} =='))
                suburb_count = 0
                try:
                    for parsed in reiwa.scrape_suburb(
                        suburb, client=client, max_pages=opts['max_pages'], on_error=on_error,
                    ):
                        n_total += 1
                        suburb_count += 1
                        fields = dataclasses.asdict(parsed)
                        fields.pop('source_id')
                        fields['raw'] = {}
                        try:
                            obj, created = Listing.objects.update_or_create(
                                source=Listing.SOURCE_REIWA,
                                source_id=parsed.source_id,
                                defaults={**fields, 'status': Listing.STATUS_ACTIVE, 'last_seen': timezone.now()},
                            )
                            n_created += int(created)
                            flag = '+' if created else '.'
                            self.stdout.write(f'  {flag} {parsed.address_raw}  {parsed.price_text}  '
                                              f'{parsed.bed}b/{parsed.bath}b/{parsed.car}c  {parsed.land_area_sqm}m²')
                        except Exception as e:
                            n_errors += 1
                            on_error('save', parsed.url, e)
                except Exception as e:
                    n_errors += 1
                    self.stderr.write(self.style.ERROR(f'  !! suburb {suburb!r} aborted: {type(e).__name__}: {e}'))
                self.stdout.write(self.style.NOTICE(f'   ({suburb_count} from {suburb})'))

        self.stdout.write(self.style.SUCCESS(
            f'Done. {n_total} listings, {n_created} new, {n_errors} errors.'
        ))

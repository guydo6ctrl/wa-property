from __future__ import annotations
import dataclasses

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from apps.listings.models import Listing, ScrapeRun
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
        n_completed = 0
        consecutive_blocked = 0   # suburbs in a row that returned nothing but errors
        BLOCK_CIRCUIT_BREAK = 4   # ...this many ⇒ reiwa is blocking us; stop the run
        with client:
            for suburb in suburbs:
                slug = slugify(suburb)
                self.stdout.write(self.style.NOTICE(f'== {suburb} =='))
                run = ScrapeRun.objects.create(suburb_slug=slug, started_at=timezone.now())
                suburb_count = 0
                outcome: dict = {}
                suburb_failed = False
                try:
                    for parsed in reiwa.scrape_suburb(
                        suburb, client=client, max_pages=opts['max_pages'],
                        on_error=on_error, outcome=outcome,
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
                                defaults={
                                    **fields,
                                    # Key the listing on its own (true) suburb, and
                                    # reset the grace counter — it was just seen.
                                    'suburb_slug': slugify(parsed.suburb),
                                    'status': Listing.STATUS_ACTIVE,
                                    'missed_scrapes': 0,
                                    'last_seen': timezone.now(),
                                },
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
                    suburb_failed = True
                    self.stderr.write(self.style.ERROR(f'  !! suburb {suburb!r} aborted: {type(e).__name__}: {e}'))

                # Record the run. Deactivation is a separate stage (`delist`) that
                # reads these rows — the scrape itself never marks anything gone.
                # A run is trustworthy ("completed") only if it was fully traversed
                # with no missing search page (a single detail miss is tolerated).
                completed = (not suburb_failed
                             and outcome.get('reached_terminal', False)
                             and outcome.get('search_errors', 0) == 0)
                run.finished_at = timezone.now()
                run.reached_terminal = outcome.get('reached_terminal', False)
                run.search_errors = outcome.get('search_errors', 0)
                run.detail_errors = outcome.get('detail_errors', 0)
                run.seen_count = outcome.get('seen_count', 0)
                run.completed = completed
                run.save()
                n_completed += int(completed)

                label = self.style.SUCCESS('completed') if completed else self.style.WARNING('INCOMPLETE')
                self.stdout.write(self.style.NOTICE(
                    f'   ({suburb_count} from {suburb}; {label}, '
                    f'{run.search_errors} search-err / {run.detail_errors} detail-err)'))

                # Circuit breaker: a suburb that yielded nothing but search errors is
                # either invalid or blocked. Several in a row means reiwa is blocking
                # this IP — stop rather than grind through the rest provoking it more.
                if run.seen_count == 0 and run.search_errors > 0:
                    consecutive_blocked += 1
                    if consecutive_blocked >= BLOCK_CIRCUIT_BREAK:
                        self.stderr.write(self.style.ERROR(
                            f'!! Aborting: {consecutive_blocked} suburbs in a row returned '
                            f'only errors — reiwa is likely blocking. Back off and retry later.'))
                        break
                else:
                    consecutive_blocked = 0

        self.stdout.write(self.style.SUCCESS(
            f'Done. {n_total} listings, {n_created} new, '
            f'{n_completed}/{len(suburbs)} suburbs completed, {n_errors} errors.\n'
            f'Run `python manage.py delist` to deactivate listings gone from completed scrapes.'
        ))

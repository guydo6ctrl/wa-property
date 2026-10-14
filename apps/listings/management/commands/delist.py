"""Deactivate listings that are gone from reiwa — the standalone delist stage.

Reads `ScrapeRun` rows the scrape left behind and, for each *completed* one not
yet applied, deactivates active listings in that suburb that were absent from the
scrape. Deactivation is `status='gone'` (never a delete); a relisting reactivates
itself on the next scrape via `update_or_create`.

Robustness layers:
  - Only `completed` runs (fully traversed, no missing search page) are trusted.
  - A 2-miss grace counter (`Listing.missed_scrapes`): a listing must be absent
    from two completed scrapes before it's reaped, so one flaky scrape can't
    mass-deactivate.
  - A relative undercount guard (E3): a completed-but-suspiciously-short run
    (seen_count < max(5, 0.5 x current active)) is skipped and surfaced, not
    acted on. `--force` bypasses it (use for the one-time backlog clear).

Each completed run is consumed exactly once (`delisted_at` stamp), so the grace
counter advances per completed *scrape*, not per `delist` invocation.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from apps.listings.models import Listing, ScrapeRun

REAP_THRESHOLD = 2          # missed_scrapes at which a listing is deactivated
UNDERCOUNT_FLOOR = 5        # never trust a completed run that saw fewer than this
UNDERCOUNT_FRACTION = 0.5   # ...or fewer than this fraction of the suburb's actives


class Command(BaseCommand):
    help = 'Deactivate listings gone from completed scrapes (reads ScrapeRun rows).'

    def add_arguments(self, parser):
        parser.add_argument('--suburbs', help='Comma-separated suburb filter (default: all pending runs)')
        parser.add_argument('--force', action='store_true',
                            help='Bypass the undercount guard (E3). Use for the one-time backlog clear.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        force = opts['force']

        runs = (ScrapeRun.objects
                .filter(completed=True, delisted_at__isnull=True)
                .order_by('suburb_slug', 'started_at'))
        if opts['suburbs']:
            slugs = [slugify(s) for s in opts['suburbs'].split(',') if s.strip()]
            runs = runs.filter(suburb_slug__in=slugs)

        runs = list(runs)
        if not runs:
            self.stdout.write('No completed, un-applied scrape runs to process.')
            return

        if dry:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be written.\n'))

        now = timezone.now()
        tot_absent = tot_missed = tot_reaped = tot_skipped = 0

        for run in runs:
            base = Listing.objects.filter(
                source=Listing.SOURCE_REIWA,
                status=Listing.STATUS_ACTIVE,
                suburb_slug=run.suburb_slug,
            )
            current_active = base.count()

            # Absent = active in this suburb, not touched by this run.
            # last_seen is global ("seen by any search"), so a listing surfaced in a
            # neighbouring suburb's scrape keeps a fresh last_seen and is correctly
            # spared here.
            absent = base.filter(last_seen__lt=run.started_at)
            n_absent = absent.count()
            refreshed = current_active - n_absent  # listings of THIS suburb the run renewed

            # E3 — relative undercount guard. Compare how many of THIS suburb's
            # listings the run actually refreshed against its current active count.
            # We deliberately use this per-suburb figure, not run.seen_count: reiwa's
            # search bleeds in neighbours (a 'south-perth' search also returns Como /
            # Victoria Park), so run.seen_count wildly overstates target coverage and
            # would let a glitchy run that missed the real suburb sail past the guard.
            threshold = max(UNDERCOUNT_FLOOR, int(UNDERCOUNT_FRACTION * current_active))
            if not force and refreshed < threshold:
                tot_skipped += 1
                self.stdout.write(self.style.WARNING(
                    f'  ! {run.suburb_slug}: SKIPPED undercount — refreshed {refreshed} '
                    f'of {current_active} active (need >= {threshold}). '
                    f'Re-scrape, or --force if the drop is real.'))
                continue

            # Those already at >= THRESHOLD-1 misses will reap on this +1.
            n_reaped = absent.filter(missed_scrapes__gte=REAP_THRESHOLD - 1).count()

            if not dry:
                absent.update(missed_scrapes=F('missed_scrapes') + 1)
                base.filter(last_seen__lt=run.started_at,
                            missed_scrapes__gte=REAP_THRESHOLD).update(status=Listing.STATUS_GONE)
                run.delisted_at = now
                run.save(update_fields=['delisted_at'])

            tot_absent += n_absent
            tot_missed += n_absent
            tot_reaped += n_reaped
            verb = 'would reap' if dry else 'reaped'
            self.stdout.write(
                f'  {run.suburb_slug}: refreshed {refreshed}/{current_active} active, '
                f'{n_absent} absent (+1 miss), {verb} {n_reaped}')

        self.stdout.write(self.style.SUCCESS(
            f'\n{"Would deactivate" if dry else "Deactivated"} {tot_reaped} listings '
            f'across {len(runs) - tot_skipped} runs '
            f'({tot_missed} absences recorded, {tot_skipped} runs skipped on undercount).'))

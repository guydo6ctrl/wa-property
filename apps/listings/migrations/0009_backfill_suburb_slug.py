"""Backfill suburb_slug, and a one-time backlog bridge for missed_scrapes.

Kept separate from the schema migration (0008): Django defers index creation to
the end of a migration's transaction, so doing the data writes in 0008 would make
the deferred CREATE INDEX fail with "pending trigger events".

suburb_slug = slugify(suburb) for every row — the stable key the `delist` sweep
groups on. Every active listing is pre-set to missed_scrapes=1 so that a single
re-scrape (which resets still-live listings to 0) plus one `delist --force` reaps
the accumulated June-3 ghosts (1 -> 2) in one cycle. This deactivates nothing
itself; reaping only ever happens off a real completed scrape.
"""
from __future__ import annotations

from django.db import migrations


def backfill(apps, schema_editor):
    from django.utils.text import slugify

    Listing = apps.get_model('listings', 'Listing')
    batch = []
    for obj in Listing.objects.only('id', 'suburb').iterator(chunk_size=5000):
        obj.suburb_slug = slugify(obj.suburb)
        batch.append(obj)
        if len(batch) >= 5000:
            Listing.objects.bulk_update(batch, ['suburb_slug'])
            batch = []
    if batch:
        Listing.objects.bulk_update(batch, ['suburb_slug'])

    Listing.objects.filter(status='active').update(missed_scrapes=1)


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0008_listing_missed_scrapes_listing_suburb_slug_scraperun'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

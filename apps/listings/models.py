from django.db import models


class Listing(models.Model):
    SOURCE_REIWA = 'reiwa'
    SOURCE_CHOICES = [(SOURCE_REIWA, 'reiwa.com.au')]

    STATUS_ACTIVE = 'active'
    STATUS_GONE = 'gone'
    STATUS_CHOICES = [(STATUS_ACTIVE, 'Active'), (STATUS_GONE, 'Gone')]

    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    source_id = models.CharField(max_length=32)
    url = models.URLField(max_length=500)

    address_raw = models.CharField(max_length=255)
    suburb = models.CharField(max_length=64, db_index=True)
    postcode = models.CharField(max_length=8, blank=True)

    price_text = models.CharField(max_length=128, blank=True)
    price_num = models.PositiveBigIntegerField(null=True, blank=True)

    bed = models.PositiveSmallIntegerField(null=True, blank=True)
    bath = models.PositiveSmallIntegerField(null=True, blank=True)
    car = models.PositiveSmallIntegerField(null=True, blank=True)
    land_area_sqm = models.PositiveIntegerField(null=True, blank=True)
    house_area_sqm = models.PositiveIntegerField(null=True, blank=True)
    property_type = models.CharField(max_length=32, blank=True)

    listing_text = models.TextField(blank=True)
    agent = models.CharField(max_length=255, blank=True)
    agency = models.CharField(max_length=255, blank=True)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocoded_at = models.DateTimeField(null=True, blank=True)
    geocode_source = models.CharField(max_length=32, blank=True)

    lot = models.ForeignKey(
        'cadastre.Lot', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='listings',
    )
    lot_match_method = models.CharField(max_length=16, blank=True)
    lot_match_confidence = models.FloatField(null=True, blank=True)
    lot_matched_at = models.DateTimeField(null=True, blank=True)

    raw = models.JSONField(default=dict, blank=True)

    # Delisting: suburb_slug is slugify(suburb) — the listing's own (true) suburb,
    # the stable key the `delist` sweep groups on. missed_scrapes counts completed
    # scrapes of that suburb in which this listing was absent; reaped to `gone` at 2.
    suburb_slug = models.CharField(max_length=64, blank=True, db_index=True)
    missed_scrapes = models.PositiveSmallIntegerField(default=0)

    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    class Meta:
        unique_together = [('source', 'source_id')]
        indexes = [models.Index(fields=['suburb', 'status'])]

    def __str__(self):
        return f'{self.address_raw} ({self.source}:{self.source_id})'


class ScrapeRun(models.Model):
    """One row per suburb scrape — the persisted record the `delist` sweep reads.

    `completed` (reached_terminal AND search_errors == 0) means the suburb was
    fully traversed with no missing search page, so a listing absent from it can
    be trusted as gone. `last_seen >= started_at` encodes "seen this run" (no need
    to store source-id sets). `delisted_at` marks a run the sweep has consumed."""
    suburb_slug = models.CharField(max_length=64, db_index=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    reached_terminal = models.BooleanField(default=False)
    search_errors = models.PositiveSmallIntegerField(default=0)
    detail_errors = models.PositiveSmallIntegerField(default=0)
    seen_count = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)

    delisted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['completed', 'delisted_at'])]

    def __str__(self):
        flag = 'completed' if self.completed else 'incomplete'
        return f'{self.suburb_slug} @ {self.started_at:%Y-%m-%d %H:%M} ({flag}, {self.seen_count} seen)'


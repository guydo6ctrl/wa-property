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

    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    class Meta:
        unique_together = [('source', 'source_id')]
        indexes = [models.Index(fields=['suburb', 'status'])]

    def __str__(self):
        return f'{self.address_raw} ({self.source}:{self.source_id})'

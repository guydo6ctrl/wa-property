from django.db import models


class AmalgamationCluster(models.Model):
    """A precomputed adjacency cluster where amalgamating the member lots
    yields more dwellings than they support individually.

    Rebuilt by `manage.py compute_amalgamations`. One row per `adjacency_key`."""

    adjacency_key = models.CharField(max_length=128, unique=True)

    total_area_sqm = models.PositiveIntegerField(default=0)
    majority_rcode = models.CharField(max_length=16, blank=True)
    mixed_codes = models.BooleanField(default=False)

    individual_dwellings = models.PositiveSmallIntegerField(default=0)
    combined_dwellings = models.PositiveSmallIntegerField(default=0)
    gain = models.IntegerField(default=0, db_index=True)

    member_count = models.PositiveSmallIntegerField(default=0)
    members = models.JSONField(default=list, blank=True,
                               help_text='List of {listing_id, address, area_sqm, r_code} dicts.')
    notes = models.CharField(max_length=255, blank=True,
                             help_text='Caveats (e.g. "strata-cluster — false positive").')

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-gain', '-total_area_sqm')

    def __str__(self):
        return f'{self.adjacency_key} (+{self.gain})'


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

    r_code = models.CharField(max_length=16, blank=True)
    r_code_scheme = models.CharField(max_length=128, blank=True)
    r_code_gazetted = models.DateField(null=True, blank=True)
    r_code_lookup_at = models.DateTimeField(null=True, blank=True)

    subdivide_ratio = models.FloatField(null=True, blank=True, help_text='land_area / R-code average site area')

    adjacency_key = models.CharField(
        max_length=128, blank=True, db_index=True,
        help_text='Street+suburb slug shared by listings detected as adjacency candidates',
    )

    lot = models.ForeignKey(
        'cadastre.Lot', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='listings',
    )
    lot_match_method = models.CharField(max_length=16, blank=True,
                                        help_text="'pip' (point-in-polygon) or 'address' fallback")
    lot_match_confidence = models.FloatField(null=True, blank=True)
    lot_matched_at = models.DateTimeField(null=True, blank=True)

    opportunity_score = models.FloatField(null=True, blank=True, db_index=True,
                                          help_text='Combined signal score; recomputed by `manage.py rescore`.')
    text_tags = models.CharField(max_length=255, blank=True,
                                 help_text='Comma-separated listing-text signal tags (deceased_estate, knockdown, etc.).')
    score_breakdown = models.JSONField(default=dict, blank=True,
                                       help_text='{ratio_score, adj_score, text_score} so the admin can show why a row ranked.')
    score_updated_at = models.DateTimeField(null=True, blank=True)

    raw = models.JSONField(default=dict, blank=True)

    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    class Meta:
        unique_together = [('source', 'source_id')]
        indexes = [models.Index(fields=['suburb', 'status'])]

    def __str__(self):
        return f'{self.address_raw} ({self.source}:{self.source_id})'

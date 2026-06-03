from django.contrib.gis.db import models


class Lot(models.Model):
    """One row per titled parcel from Landgate cadastre (LGATE-001).
    Polygons stored in SRID 4326 (lat/lon). Area cached in m² for index-friendly queries."""

    landgate_id = models.CharField(max_length=32, unique=True, db_index=True)

    geom = models.MultiPolygonField(srid=4326)

    address = models.CharField(max_length=255, blank=True)
    lga = models.CharField(max_length=64, blank=True, db_index=True)

    area_sqm = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    depth_m = models.FloatField(null=True, blank=True)
    frontage_count = models.PositiveSmallIntegerField(null=True, blank=True)

    r_code = models.CharField(max_length=16, blank=True, db_index=True)
    r_code_scheme = models.CharField(max_length=128, blank=True)
    r_code_joined_at = models.DateTimeField(null=True, blank=True)
    r_code_source = models.CharField(
        max_length=16, blank=True, db_index=True,
        help_text="'slip' (authoritative layer 111) or 'nearest_zone' (heuristic fallback).",
    )
    r_code_confidence = models.FloatField(null=True, blank=True,
                                          help_text="1.0 for SLIP, ~0.6 for nearest-zone inference.")

    raw = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['lga', 'r_code']),
        ]

    def __str__(self):
        return f'Lot {self.landgate_id} ({self.address or "no address"})'


class RCodeZone(models.Model):
    """One row per R-code polygon from SLIP layer 111. Source-of-truth for the
    cadastre.Lot.r_code spatial join. Zones are often multi-part (e.g. an R40
    band that wraps both sides of a street), so stored as MultiPolygon."""

    code = models.CharField(max_length=16, db_index=True)
    scheme = models.CharField(max_length=128, blank=True)
    gazetted = models.DateField(null=True, blank=True)

    geom = models.MultiPolygonField(srid=4326)

    raw = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.code} ({self.scheme or "unknown scheme"})'


class ZoneAndReserve(models.Model):
    """SLIP layer 112 — Local Planning Scheme Zones and Reserves (DPLH-071).
    Used to gate the nearest-zone R-code inference: only lots inside a
    residential zone are eligible for the fallback."""

    zone = models.CharField(max_length=150, db_index=True)
    label = models.CharField(max_length=255, blank=True)
    lga = models.CharField(max_length=128, blank=True, db_index=True)
    scheme = models.CharField(max_length=128, blank=True)
    gazetted = models.DateField(null=True, blank=True)

    geom = models.MultiPolygonField(srid=4326)

    raw = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.zone} ({self.lga})'

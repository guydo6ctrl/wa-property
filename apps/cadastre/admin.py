from django.contrib.gis import admin as gis_admin

from .models import Lot, RCodeZone


@gis_admin.register(Lot)
class LotAdmin(gis_admin.GISModelAdmin):
    list_display = ('landgate_id', 'address', 'lga', 'area_sqm',
                    'frontage_count', 'r_code', 'imported_at')
    list_filter = ('lga', 'r_code')
    search_fields = ('landgate_id', 'address')
    list_per_page = 50


@gis_admin.register(RCodeZone)
class RCodeZoneAdmin(gis_admin.GISModelAdmin):
    list_display = ('code', 'scheme', 'gazetted', 'imported_at')
    list_filter = ('code', 'scheme')
    search_fields = ('code', 'scheme')
    list_per_page = 50

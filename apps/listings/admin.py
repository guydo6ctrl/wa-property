from django.contrib import admin
from django.utils.html import format_html

from .models import Listing


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = (
        'address_short', 'suburb', 'land_area_sqm', 'price_text',
        'status', 'lot_match_method', 'open_link', 'last_seen',
    )
    list_filter = ('source', 'status', 'suburb', 'property_type', 'lot_match_method')
    search_fields = ('address_raw', 'source_id', 'agent', 'agency')
    readonly_fields = ('first_seen', 'last_seen', 'raw', 'open_link')
    list_per_page = 50
    ordering = ('-last_seen',)

    @admin.display(description='address', ordering='address_raw')
    def address_short(self, obj):
        return (obj.address_raw or '').split(',', 1)[0]

    @admin.display(description='link')
    def open_link(self, obj):
        if not obj.url:
            return ''
        return format_html('<a href="{}" target="_blank" rel="noopener">open ↗</a>', obj.url)

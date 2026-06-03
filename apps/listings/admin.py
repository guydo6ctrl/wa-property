from django.contrib import admin
from django.utils.html import format_html

from .models import AmalgamationCluster, Listing


class GainFilter(admin.SimpleListFilter):
    title = 'net dwellings gained'
    parameter_name = 'gain'

    def lookups(self, request, model_admin):
        return (('ge3', '≥ 3'), ('ge2', '≥ 2'), ('ge1', '≥ 1'), ('le0', '≤ 0'))

    def queryset(self, request, qs):
        v = self.value()
        if v == 'ge3': return qs.filter(gain__gte=3)
        if v == 'ge2': return qs.filter(gain__gte=2)
        if v == 'ge1': return qs.filter(gain__gte=1)
        if v == 'le0': return qs.filter(gain__lte=0)
        return qs


class NotesFilter(admin.SimpleListFilter):
    title = 'cluster notes'
    parameter_name = 'notes'

    def lookups(self, request, model_admin):
        return (('clean', 'Clean (no caveats)'), ('strata', 'Strata cluster (false positive)'))

    def queryset(self, request, qs):
        v = self.value()
        if v == 'clean': return qs.filter(notes='')
        if v == 'strata': return qs.filter(notes__icontains='strata')
        return qs


@admin.register(AmalgamationCluster)
class AmalgamationClusterAdmin(admin.ModelAdmin):
    list_display = (
        'gain_badge', 'adjacency_key', 'individual_dwellings', 'combined_dwellings',
        'total_area_sqm', 'majority_rcode', 'mixed_codes', 'member_count',
        'members_inline', 'notes', 'map_link', 'computed_at',
    )
    list_filter = (GainFilter, NotesFilter, 'majority_rcode', 'mixed_codes')
    search_fields = ('adjacency_key', 'majority_rcode', 'notes')
    readonly_fields = ('members', 'computed_at')
    ordering = ('-gain', '-total_area_sqm')
    list_per_page = 50

    @admin.display(description='gain', ordering='-gain')
    def gain_badge(self, obj):
        colour = '#1e7f3e' if obj.gain >= 2 else ('#1e6fdf' if obj.gain >= 1 else '#999')
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;background:{};color:white;'
            'border-radius:10px;font-weight:600;font-size:12px;">+{}</span>',
            colour, obj.gain,
        )

    @admin.display(description='map')
    def map_link(self, obj):
        return format_html(
            '<a href="/cluster/{}/map/" target="_blank" rel="noopener" '
            'style="display:inline-block;padding:2px 6px;background:#1e6fdf;color:white;'
            'border-radius:3px;text-decoration:none;font-size:11px;">map →</a>',
            obj.pk,
        )

    @admin.display(description='members')
    def members_inline(self, obj):
        chunks = []
        for m in obj.members[:6]:
            head = (m.get('address') or '').split(',', 1)[0]
            area = m.get('area_sqm') or '—'
            tags = m.get('text_tags') or ''
            chunks.append(format_html(
                '<a href="{}" target="_blank" rel="noopener" '
                'title="{}">{} ({}m²)</a>{}',
                m.get('url') or '#',
                tags,
                head, area,
                format_html(' <span style="color:#c66;font-size:10px;">[{}]</span>', tags) if tags else '',
            ))
        return format_html(' · '.join(['{}'] * len(chunks)), *chunks)


class OpportunityScoreFilter(admin.SimpleListFilter):
    title = 'opportunity score'
    parameter_name = 'score'

    def lookups(self, request, model_admin):
        return (
            ('ge5', '≥ 5.0  (strongest leads)'),
            ('ge3', '≥ 3.0  (worth a look)'),
            ('ge1', '≥ 1.0  (any signal)'),
            ('eq0', '0  (no signal or under offer)'),
        )

    def queryset(self, request, qs):
        v = self.value()
        if v == 'ge5':
            return qs.filter(opportunity_score__gte=5.0)
        if v == 'ge3':
            return qs.filter(opportunity_score__gte=3.0)
        if v == 'ge1':
            return qs.filter(opportunity_score__gte=1.0)
        if v == 'eq0':
            return qs.filter(opportunity_score=0)
        return qs


class SubdivideRatioFilter(admin.SimpleListFilter):
    title = 'subdivide ratio'
    parameter_name = 'subdiv'

    def lookups(self, request, model_admin):
        return (
            ('ge2', '≥ 2.0  (subdividable today)'),
            ('ge3', '≥ 3.0'),
            ('ge5', '≥ 5.0'),
            ('lt2', '< 2.0'),
            ('null', '(no ratio)'),
        )

    def queryset(self, request, qs):
        v = self.value()
        if v == 'ge2':
            return qs.filter(subdivide_ratio__gte=2.0)
        if v == 'ge3':
            return qs.filter(subdivide_ratio__gte=3.0)
        if v == 'ge5':
            return qs.filter(subdivide_ratio__gte=5.0)
        if v == 'lt2':
            return qs.filter(subdivide_ratio__lt=2.0)
        if v == 'null':
            return qs.filter(subdivide_ratio__isnull=True)
        return qs


class InAdjacencyPairFilter(admin.SimpleListFilter):
    title = 'in adjacency pair'
    parameter_name = 'adj'

    def lookups(self, request, model_admin):
        return (('yes', 'Yes'), ('no', 'No'))

    def queryset(self, request, qs):
        if self.value() == 'yes':
            return qs.exclude(adjacency_key='')
        if self.value() == 'no':
            return qs.filter(adjacency_key='')
        return qs


class UnderOfferFilter(admin.SimpleListFilter):
    title = 'under offer / contract'
    parameter_name = 'uo'

    def lookups(self, request, model_admin):
        return (('hide', 'Hide under offer/contract'), ('only', 'Only under offer/contract'))

    def queryset(self, request, qs):
        pat = r'(under offer|under contract|sold)'
        if self.value() == 'hide':
            return qs.exclude(price_text__iregex=pat)
        if self.value() == 'only':
            return qs.filter(price_text__iregex=pat)
        return qs


class TextTagFilter(admin.SimpleListFilter):
    title = 'text tag'
    parameter_name = 'tag'

    def lookups(self, request, model_admin):
        return (
            ('deceased_estate', 'Deceased estate'),
            ('must_sell',       'Must sell / motivated vendor'),
            ('knockdown',       'Knockdown / rebuild language'),
            ('da_approved',     'DA approved'),
            ('renovator',       'Renovator / original condition'),
            ('first_time',      'First time offered'),
            ('subdivide_txt',   'Subdivision language in copy'),
            ('relocating',      'Owner relocating'),
        )

    def queryset(self, request, qs):
        v = self.value()
        if v:
            return qs.filter(text_tags__icontains=v)
        return qs


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = (
        'opportunity_score', 'score_chip', 'address_short', 'suburb',
        'land_area_sqm', 'r_code', 'subdivide_ratio',
        'price_text', 'text_tags', 'partners',
        'map_link', 'open_link', 'last_seen',
    )
    list_filter = (
        OpportunityScoreFilter, SubdivideRatioFilter, InAdjacencyPairFilter,
        TextTagFilter, UnderOfferFilter,
        'source', 'status', 'r_code', 'suburb', 'property_type',
    )
    search_fields = ('address_raw', 'source_id', 'agent', 'agency', 'adjacency_key', 'text_tags')
    readonly_fields = ('first_seen', 'last_seen', 'raw', 'open_link', 'score_breakdown',
                       'opportunity_score', 'text_tags', 'score_updated_at')
    list_per_page = 50
    ordering = ('-opportunity_score',)

    @admin.display(description='address', ordering='address_raw')
    def address_short(self, obj):
        return (obj.address_raw or '').split(',', 1)[0]

    @admin.display(description='breakdown')
    def score_chip(self, obj):
        b = obj.score_breakdown or {}
        if not b:
            return ''
        return format_html(
            '<span title="ratio:{} adj:{} text:{}" style="color:#555;font-size:11px;">'
            'r{}·a{}·t{}</span>',
            b.get('ratio_score', 0), b.get('adj_score', 0), b.get('text_score', 0),
            b.get('ratio_score', 0), b.get('adj_score', 0), b.get('text_score', 0),
        )

    @admin.display(description='map')
    def map_link(self, obj):
        return format_html(
            '<a href="/listing/{}/map/" target="_blank" rel="noopener" '
            'style="display:inline-block;padding:2px 6px;background:#1e6fdf;color:white;'
            'border-radius:3px;text-decoration:none;font-size:11px;">map →</a>',
            obj.pk,
        )

    @admin.display(description='link')
    def open_link(self, obj):
        if not obj.url:
            return ''
        return format_html('<a href="{}" target="_blank" rel="noopener">open ↗</a>', obj.url)

    @admin.display(description='partners')
    def partners(self, obj):
        if not obj.adjacency_key:
            return ''
        siblings = (Listing.objects
                    .filter(adjacency_key=obj.adjacency_key)
                    .exclude(pk=obj.pk)
                    .order_by('address_raw'))
        if not siblings:
            return ''
        chunks = []
        for s in siblings:
            label = (s.address_raw or '').split(',', 1)[0]
            land = f'{s.land_area_sqm}m²' if s.land_area_sqm else '—'
            chunks.append(format_html(
                '<a href="{}" target="_blank" rel="noopener">{} ({})</a>',
                s.url, label, land,
            ))
        return format_html(' · '.join(['{}'] * len(chunks)), *chunks)

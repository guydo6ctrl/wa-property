"""Listing → map jump.

Single endpoint `/listing/<id>/map/` that resolves a Listing to lat/lon by the
best available source (matched lot polygon centroid → geocoded lat/lon →
address parse against cadastre.Lot) and redirects to the existing /lots/ map
with focus query params.
"""
from __future__ import annotations
import re

from django.db import connection
from django.http import HttpResponseRedirect, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from .models import Listing


ADDR_RE = re.compile(
    r'^(?P<num>\d+)([A-Za-z]?)\s+(?P<street>.+?),\s*(?P<suburb>[^,]+?)\s+WA\s+\d{4}\s*$'
)


def _resolve_latlon(listing: Listing) -> tuple[float, float] | None:
    """Best-effort resolve: matched lot centroid → cadastre address parse → geocoded point.

    Address parse beats Nominatim because Nominatim often collapses to the street
    or suburb centroid for addresses it can't pinpoint exactly, which makes every
    listing on the same street look like the same place. Cadastre lookups are
    address-precise."""
    # 1. Matched lot — most accurate.
    if listing.lot_id:
        with connection.cursor() as c:
            c.execute("""
                SELECT ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom))
                FROM cadastre_lot WHERE id = %s
            """, [listing.lot_id])
            row = c.fetchone()
            if row:
                return float(row[0]), float(row[1])

    # 2. Address parse against cadastre.
    m = ADDR_RE.match(listing.address_raw or '')
    if m:
        num = m.group('num')
        bits = m.group('street').split()
        if len(bits) >= 2:
            name = ' '.join(bits[:-1]).upper()
        else:
            name = m.group('street').upper()
        locality = m.group('suburb').strip().upper()
        with connection.cursor() as c:
            c.execute("""
                SELECT ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom))
                FROM cadastre_lot
                WHERE raw->>'road_number_1' = %s
                  AND UPPER(raw->>'road_name') = %s
                  AND raw->>'locality' = %s
                LIMIT 1
            """, [num, name, locality])
            row = c.fetchone()
            if row:
                return float(row[0]), float(row[1])

    # 3. Geocoded point fallback (least precise).
    if listing.latitude is not None and listing.longitude is not None:
        return float(listing.latitude), float(listing.longitude)

    return None


def listing_map(request, listing_id: int):
    listing = get_object_or_404(Listing, pk=listing_id)
    coords = _resolve_latlon(listing)
    if coords is None:
        raise Http404(
            f"No location resolvable for listing {listing_id} "
            f"('{listing.address_raw}'). Try `manage.py enrich` or `manage.py match_lots`."
        )
    lat, lon = coords
    short = (listing.address_raw or '').split(',', 1)[0]
    url = reverse('cadastre-lots')
    return HttpResponseRedirect(
        f'{url}?focus_lat={lat:.6f}&focus_lng={lon:.6f}&focus_label={short}'
    )


def cluster_map(request, cluster_id: int):
    """Open the lot map zoomed to fit every member of an AmalgamationCluster,
    highlighting each member's underlying lot polygon."""
    from .models import AmalgamationCluster
    cluster = get_object_or_404(AmalgamationCluster, pk=cluster_id)

    points: list[tuple[float, float]] = []
    labels: list[str] = []
    for m in cluster.members:
        listing_id = m.get('listing_id')
        if not listing_id:
            continue
        try:
            l = Listing.objects.get(pk=listing_id)
        except Listing.DoesNotExist:
            continue
        coords = _resolve_latlon(l)
        if coords is None:
            continue
        points.append(coords)
        labels.append((l.address_raw or '').split(',', 1)[0])

    if not points:
        raise Http404(f'No member of cluster {cluster.adjacency_key} could be resolved to a location.')

    pts = ';'.join(f'{lat:.6f},{lon:.6f}' for lat, lon in points)
    lbls = '|'.join(labels)
    url = reverse('cadastre-lots')
    return HttpResponseRedirect(
        f'{url}?cluster_points={pts}&cluster_labels={lbls}'
        f'&cluster_name={cluster.adjacency_key}'
    )

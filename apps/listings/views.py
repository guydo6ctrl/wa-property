"""Listing → map jump. `/listing/<id>/map/` redirects to the existing /lots/
map focused on the listing's matched lot (or its geocoded point as fallback)."""
from __future__ import annotations

from django.db import connection
from django.http import HttpResponseRedirect, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from .models import Listing


def _resolve_latlon(listing: Listing) -> tuple[float, float] | None:
    if listing.lot_id:
        with connection.cursor() as c:
            c.execute("""
                SELECT ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom))
                FROM cadastre_lot WHERE id = %s
            """, [listing.lot_id])
            row = c.fetchone()
            if row:
                return float(row[0]), float(row[1])
    if listing.latitude is not None and listing.longitude is not None:
        return float(listing.latitude), float(listing.longitude)
    return None


def listing_map(request, listing_id: int):
    listing = get_object_or_404(Listing, pk=listing_id)
    coords = _resolve_latlon(listing)
    if coords is None:
        raise Http404(
            f"No location resolvable for listing {listing_id} ('{listing.address_raw}')."
        )
    lat, lon = coords
    short = (listing.address_raw or '').split(',', 1)[0]
    url = reverse('cadastre-lots')
    return HttpResponseRedirect(
        f'{url}?focus_lat={lat:.6f}&focus_lng={lon:.6f}&focus_label={short}'
    )

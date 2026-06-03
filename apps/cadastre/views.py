"""Coverage map: a Leaflet page showing per-suburb convex hulls of imported lots.
Cheap fleet-level view (~150 polygons total) that fills in as the cadastre import
progresses."""
from __future__ import annotations
import json

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render


def coverage_map(request):
    return render(request, 'cadastre/coverage.html', {})


def coverage_geojson(request):
    """One feature per suburb: convex hull of all its imported lots."""
    with connection.cursor() as c:
        c.execute("""
            SELECT
                raw->>'locality' AS suburb,
                COUNT(*) AS n,
                AVG(area_sqm)::int AS avg_area,
                ST_AsGeoJSON(ST_ConvexHull(ST_Collect(geom))) AS hull
            FROM cadastre_lot
            WHERE raw->>'locality' IS NOT NULL
            GROUP BY raw->>'locality'
        """)
        features = []
        for suburb, n, avg_area, hull_json in c.fetchall():
            features.append({
                'type': 'Feature',
                'geometry': json.loads(hull_json),
                'properties': {
                    'suburb': suburb.title() if suburb else '',
                    'lot_count': n,
                    'avg_area_sqm': avg_area,
                },
            })

    # Bounding box of all hulls so the map can fit-bounds on load.
    return JsonResponse({
        'type': 'FeatureCollection',
        'features': features,
    })


def rcode_map(request):
    return render(request, 'cadastre/rcode_map.html', {})


# Perth-metro bounding box (lon_min, lat_min, lon_max, lat_max).
PERTH_BBOX = (115.5, -32.5, 116.3, -31.4)


def lot_map(request):
    return render(request, 'cadastre/lot_map.html', {})


def lots_geojson(request):
    """Lots inside the given bbox. Capped at 5000 to keep payload reasonable.
    bbox query param: 'lon_min,lat_min,lon_max,lat_max'."""
    bbox = request.GET.get('bbox', '')
    try:
        lon_min, lat_min, lon_max, lat_max = [float(v) for v in bbox.split(',')]
    except (ValueError, AttributeError):
        return JsonResponse({'error': 'bbox required as "lon_min,lat_min,lon_max,lat_max"'}, status=400)

    # Cap at zoom-out range to avoid 100k-row payloads.
    if (lon_max - lon_min) > 0.2 or (lat_max - lat_min) > 0.2:
        return JsonResponse({
            'type': 'FeatureCollection',
            'features': [],
            'meta': {'message': 'zoom in to see individual lots'},
        })

    with connection.cursor() as c:
        c.execute("""
            SELECT
                id, landgate_id, address, r_code, area_sqm,
                ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.00001)) AS g
            FROM cadastre_lot
            WHERE geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
            LIMIT 5000
        """, [lon_min, lat_min, lon_max, lat_max])
        features = []
        for row in c.fetchall():
            id_, landgate_id, address, r_code, area_sqm, g = row
            features.append({
                'type': 'Feature',
                'geometry': json.loads(g),
                'properties': {
                    'id': id_,
                    'landgate_id': landgate_id,
                    'address': address,
                    'r_code': r_code or '',
                    'area_sqm': area_sqm,
                },
            })
    return JsonResponse({'type': 'FeatureCollection', 'features': features})


def rcode_geojson(request):
    """R-code zone polygons within Perth metro, simplified for fast rendering."""
    lon_min, lat_min, lon_max, lat_max = PERTH_BBOX
    with connection.cursor() as c:
        c.execute("""
            SELECT
                code,
                scheme,
                ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0001)) AS g
            FROM cadastre_rcodezone
            WHERE code <> ''
              AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
        """, [lon_min, lat_min, lon_max, lat_max])
        features = []
        for code, scheme, g in c.fetchall():
            features.append({
                'type': 'Feature',
                'geometry': json.loads(g),
                'properties': {'code': code, 'scheme': scheme},
            })
    return JsonResponse({'type': 'FeatureCollection', 'features': features})

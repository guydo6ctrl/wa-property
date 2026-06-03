"""/leads/ — active touching/near-touching listing clusters.

A "cluster" is two or more active listings whose matched cadastre lots are
within 5m of each other (touching or laneway-separated). Sorted by the
most-recent member's first_seen descending — newest pairs at the top."""
from __future__ import annotations
from collections import Counter
from datetime import datetime

from django.db import connection
from django.shortcuts import render
from django.utils import timezone

from apps.zoning.rcodes import avg_site_area, parse_rcode


def leads(request):
    with connection.cursor() as c:
        c.execute("""
            SELECT l.id, l.address_raw, l.suburb, l.price_text, l.url, l.first_seen,
                   c.id, c.area_sqm, c.r_code
            FROM listings_listing l
            JOIN cadastre_lot c ON c.id = l.lot_id
            WHERE l.status = 'active'
        """)
        rows = c.fetchall()

    if not rows:
        return render(request, 'dashboard/leads.html', {'clusters': [], 'total_listings': 0})

    lot_to_member: dict[int, dict] = {}
    for (lid, addr, suburb, price, url, first_seen,
         lot_id, area, r_code) in rows:
        lot_to_member[lot_id] = {
            'listing_id': lid,
            'address': addr,
            'address_short': (addr or '').split(',', 1)[0],
            'suburb': suburb,
            'price_text': price or '—',
            'url': url,
            'first_seen': first_seen,
            'lot_id': lot_id,
            'area_sqm': area,
            'r_code': r_code or '',
        }
    lot_ids = list(lot_to_member.keys())

    with connection.cursor() as c:
        c.execute("""
            SELECT a.id, b.id
            FROM cadastre_lot a
            JOIN cadastre_lot b
              ON a.id < b.id
             AND a.id = ANY(%s) AND b.id = ANY(%s)
             AND ST_DWithin(a.geom::geography, b.geom::geography, 5)
        """, [lot_ids, lot_ids])
        pairs = c.fetchall()

    parent = {i: i for i in lot_ids}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in pairs:
        parent[find(a)] = find(b)

    components: dict[int, list[dict]] = {}
    for lid in lot_ids:
        components.setdefault(find(lid), []).append(lot_to_member[lid])

    now = timezone.now()
    clusters = []
    for members in components.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m['first_seen'], reverse=True)
        total_area = sum(m['area_sqm'] or 0 for m in members)
        rcodes = [m['r_code'] for m in members if m['r_code']]
        suburbs = [m['suburb'] for m in members if m['suburb']]
        suburb_label = Counter(suburbs).most_common(1)[0][0] if suburbs else '—'
        rcode_set = sorted(set(rcodes))
        rcode_mixed = len(rcode_set) > 1
        rcode_label = ', '.join(rcode_set) if rcode_set else '—'

        gain = _yield_gain(members, total_area)
        newest = max(m['first_seen'] for m in members)
        days_new = (now - newest).days

        clusters.append({
            'suburb': suburb_label,
            'members': members,
            'total_area': total_area,
            'rcode_label': rcode_label,
            'rcode_mixed': rcode_mixed,
            'gain': gain,
            'days_new': days_new,
            'newest_first_seen': newest,
        })

    clusters.sort(key=lambda c: c['newest_first_seen'], reverse=True)

    return render(request, 'dashboard/leads.html', {
        'clusters': clusters,
        'total_listings': len(rows),
        'total_clusters': len(clusters),
    })


def _yield_gain(members: list[dict], total_area: int) -> str:
    """Return 'individual → combined' or '—' if any member lacks R-code data."""
    individual = 0
    for m in members:
        avg = avg_site_area(m['r_code']) if m['r_code'] else None
        if not avg or not m['area_sqm']:
            return '—'
        individual += max(1, m['area_sqm'] // avg)
    rcode_bases = Counter(parse_rcode(m['r_code'])[0] for m in members if m['r_code'])
    majority_base = rcode_bases.most_common(1)[0][0] if rcode_bases else None
    majority_rcode = next((m['r_code'] for m in members
                           if parse_rcode(m['r_code'])[0] == majority_base), None)
    avg = avg_site_area(majority_rcode) if majority_rcode else None
    if not avg:
        return '—'
    combined = max(1, total_area // avg)
    diff = combined - individual
    sign = '+' if diff >= 0 else ''
    return f'{individual} → {combined} ({sign}{diff})'

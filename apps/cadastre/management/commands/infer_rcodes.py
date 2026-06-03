"""Refined Path C: infer R-codes for residentially-zoned lots that have no
authoritative R-code in SLIP layer 111.

Gates the nearest-zone heuristic on SLIP layer 112 so non-residential lots
(churches, schools, parks, road reserves) correctly stay R-code-less:

    lot has no r_code
    AND lot.area_sqm BETWEEN 200 AND 3000  (residential band)
    AND lot.centroid is inside a ZoneAndReserve polygon whose zone is residential
    AND there is an RCodeZone polygon within 200m of the lot centroid
    → inherit that R-code with confidence 0.6, source 'nearest_zone'.

Also (one-off): backfills r_code_source='slip' and r_code_confidence=1.0 on lots
that already have an R-code from the layer-111 join, so all rows have provenance.
"""
from __future__ import annotations
import time

from django.core.management.base import BaseCommand
from django.db import connection


RESIDENTIAL_ZONE_PATTERNS = [
    # SQL ILIKE patterns matching ZoneAndReserve.zone values that mean
    # "this lot is allowed to be developed as residential". Mixed-use zones
    # are excluded — they can fragment into commercial space and aren't safe
    # to auto-tag with a residential R-code.
    'Residential',
    'Residential (R20 density unless otherwise shown)',
    'Residential bushland',
    'Residential development',
    'Residential redevelopment',
    'Medium and high density residential',
    'Special residential',
    'Special mix residential',
    'Town centre residential',
    'Urban 4 - residential',
    'Urban 5 - special residential',
]
NEAREST_RADIUS_M = 200


class Command(BaseCommand):
    help = 'Path C: infer R-codes for residential lots missing them, gated on layer 112.'

    def add_arguments(self, parser):
        parser.add_argument('--radius', type=int, default=NEAREST_RADIUS_M,
                            help=f'Max distance (m) to nearest R-code zone (default {NEAREST_RADIUS_M}).')
        parser.add_argument('--force', action='store_true',
                            help='Re-infer even on lots that already have an inferred r_code.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report how many lots would be updated, but do not write.')

    def handle(self, *args, **opts):
        radius = opts['radius']
        t0 = time.monotonic()

        # First: backfill provenance on SLIP-derived R-codes so all rows are tagged.
        with connection.cursor() as c:
            c.execute("""
                UPDATE cadastre_lot
                SET r_code_source = 'slip', r_code_confidence = 1.0
                WHERE r_code <> '' AND (r_code_source = '' OR r_code_source IS NULL)
            """)
            if c.rowcount:
                self.stdout.write(f'Backfilled provenance on {c.rowcount:,} SLIP-derived lots.')

        # Build the residential-zone gate clause.
        zone_list = ', '.join(['%s'] * len(RESIDENTIAL_ZONE_PATTERNS))
        force_clause = "" if opts['force'] else "AND (l.r_code = '' OR l.r_code_source = 'nearest_zone' AND %s)"
        # Simpler: only target lots with no r_code at all unless --force
        eligible_where = "l.r_code = ''" if not opts['force'] else "(l.r_code = '' OR l.r_code_source = 'nearest_zone')"

        # Count + sample
        count_sql = f"""
            WITH eligible AS (
                SELECT l.id, ST_Centroid(l.geom) AS pt
                FROM cadastre_lot l
                WHERE {eligible_where}
                  AND l.area_sqm BETWEEN 200 AND 3000
                  AND EXISTS (
                      SELECT 1 FROM cadastre_zoneandreserve z
                      WHERE z.zone IN ({zone_list})
                        AND ST_Contains(z.geom, ST_Centroid(l.geom))
                  )
            )
            SELECT COUNT(*) FROM eligible e
            WHERE EXISTS (
                SELECT 1 FROM cadastre_rcodezone r
                WHERE ST_DWithin(r.geom::geography, e.pt::geography, %s)
            )
        """
        with connection.cursor() as c:
            c.execute(count_sql, list(RESIDENTIAL_ZONE_PATTERNS) + [radius])
            would_update = c.fetchone()[0]
        self.stdout.write(f'Lots eligible for nearest-zone inference: {would_update:,}')

        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('(dry-run — no rows updated)'))
            return

        # Update: for each eligible lot, pick the nearest residential R-code zone
        # within the radius and inherit its code.
        update_sql = f"""
            WITH eligible AS (
                SELECT l.id, ST_Centroid(l.geom) AS pt
                FROM cadastre_lot l
                WHERE {eligible_where}
                  AND l.area_sqm BETWEEN 200 AND 3000
                  AND EXISTS (
                      SELECT 1 FROM cadastre_zoneandreserve z
                      WHERE z.zone IN ({zone_list})
                        AND ST_Contains(z.geom, ST_Centroid(l.geom))
                  )
            ),
            picks AS (
                SELECT DISTINCT ON (e.id)
                    e.id AS lot_id,
                    r.code AS code,
                    r.scheme AS scheme
                FROM eligible e
                JOIN cadastre_rcodezone r
                  ON ST_DWithin(r.geom::geography, e.pt::geography, %s)
                WHERE r.code <> ''
                ORDER BY e.id, ST_Distance(r.geom::geography, e.pt::geography) ASC
            )
            UPDATE cadastre_lot l
            SET r_code = p.code,
                r_code_scheme = p.scheme,
                r_code_source = 'nearest_zone',
                r_code_confidence = 0.6,
                r_code_joined_at = NOW()
            FROM picks p
            WHERE l.id = p.lot_id;
        """
        with connection.cursor() as c:
            c.execute(update_sql, [radius] + list(RESIDENTIAL_ZONE_PATTERNS))
            updated = c.rowcount

            # Re-measure coverage.
            c.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE r_code <> '') FROM cadastre_lot")
            total, with_r = c.fetchone()
            c.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE r_code_source = 'slip'),
                    COUNT(*) FILTER (WHERE r_code_source = 'nearest_zone')
                FROM cadastre_lot
            """)
            n_slip, n_near = c.fetchone()

        dt = time.monotonic() - t0
        self.stdout.write(self.style.SUCCESS(
            f'Inferred R-codes for {updated:,} lots in {dt:.1f}s.\n'
            f'  Coverage now: {with_r:,}/{total:,} ({100*with_r/total:.1f}%)\n'
            f'  By source: slip={n_slip:,}  nearest_zone={n_near:,}'
        ))

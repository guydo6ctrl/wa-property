"""Spatial-join cadastre.Lot against cadastre.RCodeZone to cache r_code on each Lot.

PostGIS does the work in one SQL UPDATE using ST_Contains against lot centroids.
With GiST indexes on both geom columns this is fast on hundreds of thousands of rows.

Lots whose centroid falls outside every R-code zone (commercial, road reserves,
parks, etc.) keep r_code = '' — that's the correct answer for non-residential land.
"""
from __future__ import annotations
import time

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Cache R-code on each Lot via spatial join against RCodeZone.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Re-join lots that already have an r_code set.')

    def handle(self, *args, **opts):
        t0 = time.monotonic()
        where = '' if opts['force'] else "WHERE l.r_code = ''"
        sql = f"""
            UPDATE cadastre_lot l
            SET r_code = z.code,
                r_code_scheme = z.scheme,
                r_code_joined_at = NOW()
            FROM cadastre_rcodezone z
            WHERE ST_Contains(z.geom, ST_Centroid(l.geom))
              {('AND' if where else 'AND') if where else 'AND'} l.id IN (
                  SELECT id FROM cadastre_lot
                  {where}
              )
        """
        # Simpler / more idiomatic version (Postgres optimises the same way).
        sql = f"""
            UPDATE cadastre_lot l
            SET r_code = z.code,
                r_code_scheme = z.scheme,
                r_code_joined_at = NOW()
            FROM cadastre_rcodezone z
            WHERE ST_Contains(z.geom, ST_Centroid(l.geom))
              {"" if opts['force'] else "AND l.r_code = ''"}
        """
        with connection.cursor() as c:
            c.execute(sql)
            updated = c.rowcount

            c.execute("SELECT COUNT(*) FROM cadastre_lot WHERE r_code <> ''")
            total_with = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM cadastre_lot")
            total = c.fetchone()[0]

        dt = time.monotonic() - t0
        self.stdout.write(self.style.SUCCESS(
            f'Joined {updated:,} lots in {dt:.1f}s. '
            f'Coverage: {total_with:,}/{total:,} '
            f'({(100 * total_with / total) if total else 0:.1f}%) lots have an R-code.'
        ))

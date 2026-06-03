"""Match each Listing to its underlying cadastre.Lot.

Address-first strategy (replaces the old PIP-only matcher, which had a 3%
hit rate because Nominatim's rooftop points often land just outside the
cadastre polygon):

1. Parse Listing.address_raw with apps.listings.address.parse.
2. Look it up in an in-memory index built from Lot.raw (locality, road_name,
   road_type, road_number_1). Single hit -> match.
3. If multiple lots share that address (rare — usually a re-subdivided
   parent + child), prefer the one whose polygon contains the geocoded
   point. Otherwise mark as ambiguous (no match).
4. If the address doesn't parse cleanly, fall back to PIP using the geocode.
5. Strata addresses (`3/12 Smith St`) are tagged and skipped — no 1:1 lot.

Methods written to Listing.lot_match_method:
    'address'        — exact (num, name, type, locality) match, 0.95
    'address_loose'  — match without street_type (parser couldn't normalise), 0.85
    'address+pip'    — multiple address candidates disambiguated by PIP, 1.0
    'pip'            — fallback when address didn't parse / didn't match, 0.7

Unmatched listings keep lot_id NULL.
"""
from __future__ import annotations
import time
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.cadastre.models import Lot
from apps.listings.models import Listing
from apps.listings.address import parse as parse_address


class Command(BaseCommand):
    help = 'Match Listing rows to cadastre.Lot, address-first with PIP fallback.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Re-match listings that already have a lot set.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Only process this many listings (debugging).')

    def handle(self, *args, **opts):
        t0 = time.monotonic()

        self.stdout.write('Building cadastre address index…')
        # Three indices.
        # strict: (locality, road_name, road_type, road_number_1) -> [lot_id]
        # loose:  (locality, road_name, road_number_1) -> [lot_id]                — no street_type
        # base:   (locality, road_name, road_type, base_number) -> {suffix: [lot_id]}
        #         where base_number is the digits and suffix is the trailing letter
        #         (or '' for plain numbers). Used to handle reiwa's "31B" when
        #         cadastre only has the parent "31".
        strict: dict[tuple, list[int]] = defaultdict(list)
        loose: dict[tuple, list[int]] = defaultdict(list)
        base: dict[tuple, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

        cols = Lot.objects.values_list('id', 'raw')
        n_lots = 0
        for lot_id, raw in cols.iterator(chunk_size=20_000):
            if not raw:
                continue
            num = raw.get('road_number_1')
            name = raw.get('road_name')
            rtype = raw.get('road_type')
            loc = raw.get('locality')
            if not (num and name and loc):
                continue
            num_u = str(num).upper()
            strict[(loc, name, rtype, num_u)].append(lot_id)
            loose[(loc, name, num_u)].append(lot_id)
            # Split "31A" -> base "31", suffix "A"; "31" -> base "31", suffix ""
            digits = ''.join(ch for ch in num_u if ch.isdigit())
            suffix = num_u[len(digits):] if digits else ''
            if digits:
                base[(loc, name, rtype, digits)][suffix].append(lot_id)
            n_lots += 1
        self.stdout.write(f'  indexed {n_lots:,} lots')

        qs = Listing.objects.all()
        if not opts['force']:
            qs = qs.filter(lot__isnull=True)
        if opts['limit']:
            qs = qs[:opts['limit']]

        total = qs.count()
        self.stdout.write(f'Matching {total:,} listings…')

        # Counters
        stats = defaultdict(int)
        # Buffered updates: (listing_id, lot_id, method, confidence)
        updates: list[tuple[int, int, str, float]] = []
        now = timezone.now()

        for li in qs.only('id', 'address_raw', 'latitude', 'longitude').iterator(chunk_size=2000):
            parsed = parse_address(li.address_raw)
            if parsed is None:
                stats['unparseable'] += 1
                lot_id, method, conf = self._pip_fallback(li)
                if lot_id:
                    updates.append((li.id, lot_id, 'pip', 0.7))
                    stats['pip_fallback'] += 1
                continue

            if parsed['is_strata']:
                stats['strata'] += 1
                continue

            key_strict = (parsed['locality'], parsed['street_name'],
                          parsed['street_type'], parsed['house_number'])
            candidates = strict.get(key_strict)
            method = 'address'
            conf = 0.95

            if not candidates and parsed['street_type'] is None:
                key_loose = (parsed['locality'], parsed['street_name'], parsed['house_number'])
                candidates = loose.get(key_loose)
                method = 'address_loose'
                conf = 0.85

            # Letter-strip fallback: listing says "31B" but cadastre may only
            # carry the parent lot "31" because the green-title subdivision
            # hasn't been registered yet. Only safe when cadastre doesn't have
            # OTHER letter-variants for that street/number — if it has "31A"
            # and "31C" but no "31B", then the letter is meaningful and the
            # parent is the wrong target.
            if not candidates:
                num_u = parsed['house_number']
                digits = ''.join(ch for ch in num_u if ch.isdigit())
                suffix = num_u[len(digits):] if digits else ''
                if digits and suffix:
                    siblings = base.get(
                        (parsed['locality'], parsed['street_name'],
                         parsed['street_type'], digits),
                        {},
                    )
                    bare = siblings.get('')
                    other_letters = [s for s in siblings if s and s != suffix]
                    if bare and len(bare) == 1 and not other_letters:
                        candidates = bare
                        method = 'address_stripped'
                        conf = 0.7

            if not candidates:
                lot_id, _, _ = self._pip_fallback(li)
                if lot_id:
                    updates.append((li.id, lot_id, 'pip', 0.7))
                    stats['pip_fallback'] += 1
                else:
                    stats['no_match'] += 1
                continue

            if len(candidates) == 1:
                updates.append((li.id, candidates[0], method, conf))
                stats[method] += 1
                continue

            # Multiple address candidates — disambiguate by PIP if possible.
            pip_lot, _, _ = self._pip_fallback(li)
            if pip_lot and pip_lot in candidates:
                updates.append((li.id, pip_lot, 'address+pip', 1.0))
                stats['address+pip'] += 1
            else:
                stats['ambiguous'] += 1

        self.stdout.write(f'Writing {len(updates):,} matches…')
        with transaction.atomic():
            with connection.cursor() as c:
                # Use unnest for a single bulk UPDATE.
                c.execute(
                    """
                    UPDATE listings_listing l
                    SET lot_id = u.lot_id,
                        lot_match_method = u.method,
                        lot_match_confidence = u.conf,
                        lot_matched_at = %s
                    FROM unnest(%s::int[], %s::int[], %s::text[], %s::float[])
                         AS u(listing_id, lot_id, method, conf)
                    WHERE l.id = u.listing_id
                    """,
                    [now,
                     [u[0] for u in updates],
                     [u[1] for u in updates],
                     [u[2] for u in updates],
                     [u[3] for u in updates]],
                )

        with connection.cursor() as c:
            c.execute("SELECT COUNT(*) FROM listings_listing WHERE lot_id IS NOT NULL")
            with_lot = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM listings_listing")
            grand_total = c.fetchone()[0]

        dt = time.monotonic() - t0
        self.stdout.write(self.style.SUCCESS(
            f'Done in {dt:.1f}s.\n'
            f'  considered:     {total:,}\n'
            f'  address:        {stats["address"]:,}\n'
            f'  address_loose:  {stats["address_loose"]:,}\n'
            f'  address_stripped: {stats["address_stripped"]:,}\n'
            f'  address+pip:    {stats["address+pip"]:,}\n'
            f'  pip fallback:   {stats["pip_fallback"]:,}\n'
            f'  strata skipped: {stats["strata"]:,}\n'
            f'  unparseable:    {stats["unparseable"]:,}\n'
            f'  ambiguous:      {stats["ambiguous"]:,}\n'
            f'  no match:       {stats["no_match"]:,}\n'
            f'  with lot total: {with_lot:,}/{grand_total:,} '
            f'({100*with_lot/grand_total:.1f}%)'
        ))

    def _pip_fallback(self, li) -> tuple[int | None, str, float]:
        """Run the old PIP query for a single listing. Returns (lot_id, method, conf)."""
        if li.latitude is None or li.longitude is None:
            return None, '', 0.0
        with connection.cursor() as c:
            c.execute(
                """
                SELECT id FROM cadastre_lot
                WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ORDER BY area_sqm ASC NULLS LAST
                LIMIT 1
                """,
                [li.longitude, li.latitude],
            )
            row = c.fetchone()
        return (row[0] if row else None, 'pip', 0.7)

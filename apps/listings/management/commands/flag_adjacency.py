"""Flag listings that look like adjacency candidates by setting adjacency_key.

Heuristic: parse '<num><alpha?> <street>, <suburb> WA <postcode>'; group by
(street, suburb); a listing is in a pair if any other listing shares its group
and is at the same number with different alpha, the next/previous number on the
same side (gap = 2), or directly opposite (gap = 1).

Strata listings ('30/75 Stanley Street') are skipped.

Pure same-number triples where every lot is small (<350 m²) are skipped — those
are typically new strata subdivisions being marketed as separate units, not
amalgamation candidates.
"""
from __future__ import annotations
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.listings.models import Listing


ADDR = re.compile(r'^(\d+)([A-Za-z]?)\s+(.+?),\s*([^,]+?)\s+WA\s+\d{4}\s*$')
STRATA = re.compile(r'^\d+/')


class Command(BaseCommand):
    help = 'Set Listing.adjacency_key on listings that share a street with adjacent neighbours.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Clear adjacency_key on all listings before recomputing.')

    def handle(self, *args, **opts):
        if opts['reset']:
            n = Listing.objects.exclude(adjacency_key='').update(adjacency_key='')
            self.stdout.write(f'Cleared adjacency_key on {n} listings.')

        groups: dict[tuple[str, str], list[tuple[int, str, Listing]]] = defaultdict(list)
        for l in Listing.objects.filter(status=Listing.STATUS_ACTIVE):
            a = (l.address_raw or '').strip()
            if not a or STRATA.match(a):
                continue
            m = ADDR.match(a)
            if not m:
                continue
            num = int(m.group(1))
            alpha = m.group(2).lower()
            street = m.group(3).strip().lower()
            suburb = m.group(4).strip().lower()
            groups[(street, suburb)].append((num, alpha, l))

        ids_to_flag: dict[int, str] = {}
        for (street, suburb), entries in groups.items():
            if len(entries) < 2:
                continue
            entries.sort(key=lambda x: (x[0], x[1]))
            paired_ids: set[int] = set()
            for i in range(len(entries)):
                n1, a1, l1 = entries[i]
                for j in range(i + 1, len(entries)):
                    n2, a2, l2 = entries[j]
                    if l1.id == l2.id:
                        continue
                    gap = n2 - n1
                    same_side = (gap == 2) and ((n1 % 2) == (n2 % 2))
                    same_number = (n1 == n2 and a1 != a2)
                    opposite = (gap == 1)
                    if same_number or same_side or opposite:
                        paired_ids.add(l1.id)
                        paired_ids.add(l2.id)
            if not paired_ids:
                continue
            # Drop noisy "new strata" clusters: same number across all members and every lot < 350 m².
            paired_entries = [e for e in entries if e[2].id in paired_ids]
            unique_numbers = {e[0] for e in paired_entries}
            if len(unique_numbers) == 1:
                lots = [e[2].land_area_sqm for e in paired_entries]
                if all(la is not None and la < 350 for la in lots):
                    continue
            key = f'{slugify(street)}-{slugify(suburb)}'
            for lid in paired_ids:
                ids_to_flag[lid] = key

        # Persist (single bulk update per key for tidiness).
        per_key: dict[str, list[int]] = defaultdict(list)
        for lid, key in ids_to_flag.items():
            per_key[key].append(lid)
        for key, ids in per_key.items():
            Listing.objects.filter(id__in=ids).update(adjacency_key=key)

        self.stdout.write(self.style.SUCCESS(
            f'Flagged {len(ids_to_flag)} listings across {len(per_key)} adjacency groups.'
        ))

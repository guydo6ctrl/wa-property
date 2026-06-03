"""Recompute opportunity_score + text_tags on every active Listing.

Same scoring logic as `report_opportunities` but writes the result back to the
Listing row so the admin can sort/filter/display natively. Run whenever the
listing universe changes (after scrape / enrich / flag_adjacency).
"""
from __future__ import annotations
import re
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.listings.models import Listing


TEXT_TAGS: list[tuple[str, re.Pattern]] = [
    ('deceased_estate', re.compile(r'\bdecease(d)?\s+estate\b', re.I)),
    ('must_sell',       re.compile(r'\b(must\s+sell|owner\s+says\s+sell|priced\s+to\s+sell|urgent\s+sale|motivated\s+vendor)\b', re.I)),
    ('knockdown',       re.compile(r'\b(knock[\s-]?down|knockdown[\s-]?rebuild|develop[\s-]?or[\s-]?renovate|build[\s-]?your[\s-]?dream)\b', re.I)),
    ('da_approved',     re.compile(r'\b(d\.?a\.?\s+approved|development\s+approval|wapc\s+approval|subdivision\s+approval|approval\s+granted|approved\s+plans)\b', re.I)),
    ('renovator',       re.compile(r"\b(renovator'?s?\s+delight|original\s+condition|tlc|needs?\s+work|fixer[\s-]?upper)\b", re.I)),
    ('first_time',      re.compile(r'\bfirst\s+time\s+(offered|to\s+market|on\s+market)\b', re.I)),
    ('subdivide_txt',   re.compile(r'\b(subdivide|subdivision\s+potential|develop(ment)?\s+potential|triplex|duplex\s+potential|develop\s+&?\s+keep)\b', re.I)),
    ('relocating',      re.compile(r'\b(owner|vendor)\s+relocat(ing|ed)\b', re.I)),
]
UNDER_OFFER_RE = re.compile(r'(under\s+offer|under\s+contract|sold|withdrawn)', re.I)


class Command(BaseCommand):
    help = 'Recompute opportunity_score, text_tags, score_breakdown on every Listing.'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int, default=500)

    def handle(self, *args, **opts):
        t0 = time.monotonic()
        now = timezone.now()
        batch_size = opts['batch']
        updates: list[Listing] = []
        scored = 0
        suppressed_uo = 0

        for l in Listing.objects.iterator(chunk_size=1000):
            price_txt = l.price_text or ''
            ratio = l.subdivide_ratio or 0
            ratio_score = max(0.0, min(5.0, ratio - 1.0)) if ratio else 0.0
            adj_score = 2.0 if l.adjacency_key else 0.0

            blob = f'{l.listing_text or ""}\n{l.price_text or ""}\n{l.address_raw or ""}'
            tags: list[str] = []
            for name, pat in TEXT_TAGS:
                if pat.search(blob):
                    tags.append(name)
            text_score = min(3.0, float(len(tags)))

            total = round(ratio_score + adj_score + text_score, 2)

            # Suppress under-offer/contract/sold listings to 0 so they sort to the bottom
            # but stay scored (so the user can still bring them up by toggling the filter).
            if UNDER_OFFER_RE.search(price_txt) or l.status != Listing.STATUS_ACTIVE:
                total = 0.0
                suppressed_uo += 1

            l.opportunity_score = total
            l.text_tags = ','.join(tags)
            l.score_breakdown = {
                'ratio_score': round(ratio_score, 2),
                'adj_score': adj_score,
                'text_score': text_score,
            }
            l.score_updated_at = now
            updates.append(l)
            scored += 1

            if len(updates) >= batch_size:
                Listing.objects.bulk_update(
                    updates,
                    ['opportunity_score', 'text_tags', 'score_breakdown', 'score_updated_at'],
                )
                updates.clear()

        if updates:
            Listing.objects.bulk_update(
                updates,
                ['opportunity_score', 'text_tags', 'score_breakdown', 'score_updated_at'],
            )

        dt = time.monotonic() - t0
        self.stdout.write(self.style.SUCCESS(
            f'Rescored {scored:,} listings in {dt:.1f}s '
            f'({suppressed_uo:,} suppressed as under-offer/contract/inactive).'
        ))

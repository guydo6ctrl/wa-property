"""Ranked opportunities report from current data.

Combines the signals we already have on Listing rows — subdivide ratio,
adjacency-pair membership, and a quick text-flag regex pass — into a single
score and produces:

  1. A console summary of the top N opportunities.
  2. A full CSV at exports/opportunities-YYYY-MM-DD.csv for offline review.

Under-offer / contract / sold listings are filtered out. No new data is fetched;
no models are written to. Reproducible from the current database state."""
from __future__ import annotations
import csv
import re
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.listings.models import Listing


# Listing-text regex tags. Each match contributes to score + tag column.
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
    help = 'Rank active Listings by combined opportunity signals.'

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=30,
                            help='Rows to print to console (default 30).')
        parser.add_argument('--min-score', type=float, default=1.0,
                            help='Hide rows below this score in CSV + console (default 1.0).')
        parser.add_argument('--out', type=str, default='',
                            help='CSV output path (default exports/opportunities-YYYY-MM-DD.csv).')

    def handle(self, *args, **opts):
        out_path = Path(opts['out']) if opts['out'] else (
            Path(settings.BASE_DIR) / 'exports' / f'opportunities-{date.today().isoformat()}.csv'
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Active listings only.
        qs = Listing.objects.filter(status=Listing.STATUS_ACTIVE)
        rows: list[dict] = []
        for l in qs.iterator(chunk_size=500):
            price_txt = (l.price_text or '')
            if UNDER_OFFER_RE.search(price_txt):
                continue

            ratio = l.subdivide_ratio or 0
            ratio_score = max(0.0, min(5.0, ratio - 1.0)) if ratio else 0.0
            adj_score = 2.0 if l.adjacency_key else 0.0

            text_tags: list[str] = []
            blob = f'{l.listing_text or ""}\n{l.price_text or ""}\n{l.address_raw or ""}'
            for name, pat in TEXT_TAGS:
                if pat.search(blob):
                    text_tags.append(name)
            text_score = min(3.0, float(len(text_tags)))

            total = round(ratio_score + adj_score + text_score, 2)
            if total < opts['min_score']:
                continue

            # Partners (if in an adjacency cluster).
            partners: list[str] = []
            if l.adjacency_key:
                for s in Listing.objects.filter(
                    adjacency_key=l.adjacency_key, status=Listing.STATUS_ACTIVE
                ).exclude(pk=l.pk).order_by('address_raw')[:5]:
                    if UNDER_OFFER_RE.search(s.price_text or ''):
                        continue
                    head = (s.address_raw or '').split(',', 1)[0]
                    partners.append(f"{head} ({s.land_area_sqm or '—'}m²)")

            rows.append({
                'score': total,
                'address': l.address_raw,
                'suburb': l.suburb,
                'price': price_txt,
                'land_sqm': l.land_area_sqm,
                'r_code': l.r_code,
                'subdivide_ratio': round(ratio, 2) if ratio else None,
                'adjacency_key': l.adjacency_key,
                'partners': ' | '.join(partners),
                'text_tags': ','.join(text_tags),
                'url': l.url,
                'ratio_score': round(ratio_score, 2),
                'adj_score': adj_score,
                'text_score': text_score,
            })

        rows.sort(key=lambda r: (-r['score'], r['suburb'], r['address']))

        # Console
        self.stdout.write(self.style.SUCCESS(
            f'\n{len(rows)} active opportunities scored >= {opts["min_score"]}'
            f'  (from {qs.count():,} active listings)\n'
        ))
        self.stdout.write(
            f'{"score":>5}  {"land":>5}  {"r-code":<10}  {"suburb":<22}  {"tags":<28}  address'
        )
        self.stdout.write('-' * 130)
        for r in rows[:opts['top']]:
            land = f'{r["land_sqm"]}' if r['land_sqm'] else '—'
            r_code = (r['r_code'] or '—')[:10]
            sub = (r['suburb'] or '')[:22]
            tags = (r['text_tags'] or '')[:28]
            addr = (r['address'] or '')[:55]
            self.stdout.write(
                f'{r["score"]:>5.2f}  {land:>5}  {r_code:<10}  {sub:<22}  {tags:<28}  {addr}'
            )
            if r['partners']:
                self.stdout.write(f'        ↳ partners: {r["partners"][:100]}')

        # CSV
        if rows:
            with out_path.open('w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            self.stdout.write(self.style.SUCCESS(f'\nWrote {len(rows)} rows to {out_path}'))

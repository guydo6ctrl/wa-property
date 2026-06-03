from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .common import PoliteClient


BASE = 'https://reiwa.com.au'
SEARCH_URL = BASE + '/for-sale/{suburb}/'
SLUG_RE = re.compile(r'^/[a-z0-9][a-z0-9-]+-(\d{6,})/$')
SOURCE_ID_FROM_URL = re.compile(r'-(\d{6,})/$')

ADDRESS_RE = re.compile(r'^(?P<street>.+?),\s*(?P<suburb>[^,]+?)\s+WA\s+(?P<postcode>\d{4})\s*$')
NUM_RE = re.compile(r'[\d,]+')


@dataclass
class ParsedListing:
    source_id: str
    url: str
    address_raw: str = ''
    suburb: str = ''
    postcode: str = ''
    price_text: str = ''
    bed: int | None = None
    bath: int | None = None
    car: int | None = None
    land_area_sqm: int | None = None
    house_area_sqm: int | None = None
    property_type: str = ''
    listing_text: str = ''
    agent: str = ''
    agency: str = ''
    raw: dict = field(default_factory=dict)


def _to_int(s: str) -> int | None:
    m = NUM_RE.search(s)
    if not m:
        return None
    try:
        return int(m.group(0).replace(',', ''))
    except ValueError:
        return None


def discover_listing_urls(html: str) -> list[str]:
    """Return absolute URLs of listing detail pages found on a search results page."""
    tree = HTMLParser(html)
    seen: dict[str, None] = {}
    for a in tree.css('a[href]'):
        href = a.attributes.get('href') or ''
        if not href.startswith('/'):
            continue
        if not SLUG_RE.match(href):
            continue
        seen.setdefault(urljoin(BASE, href), None)
    return list(seen)


def has_next_page(html: str) -> bool:
    tree = HTMLParser(html)
    link = tree.css_first('link[rel="next"]')
    return link is not None


def parse_detail(url: str, html: str) -> ParsedListing:
    tree = HTMLParser(html)
    m = SOURCE_ID_FROM_URL.search(url)
    source_id = m.group(1) if m else ''
    out = ParsedListing(source_id=source_id, url=url)

    # Address from H1 (e.g. "32 Melvista Avenue, Claremont WA 6010")
    h1 = tree.css_first('h1')
    if h1:
        out.address_raw = h1.text(strip=True)
        addr_m = ADDRESS_RE.match(out.address_raw)
        if addr_m:
            out.suburb = addr_m.group('suburb').strip()
            out.postcode = addr_m.group('postcode').strip()

    # Price (first .u-text-large inside .key-info)
    info = tree.css_first('.property-info .key-info')
    if info:
        price_el = info.css_first('.u-text-large')
        if price_el:
            out.price_text = price_el.text(strip=True)

    # Feature list: bedrooms / bathrooms / cars / landsize
    for li in tree.css('.p-feature-list li'):
        txt = li.text(strip=True, separator=' ').lower()
        if 'bedroom' in txt:
            out.bed = _to_int(txt)
        elif 'bathroom' in txt:
            out.bath = _to_int(txt)
        elif 'car' in txt or 'garage' in txt:
            out.car = _to_int(txt)
        elif 'landsize' in txt or 'land size' in txt:
            out.land_area_sqm = _to_int(txt)
        elif 'house size' in txt or 'floor area' in txt:
            out.house_area_sqm = _to_int(txt)

    # Property type — look for known labels in the key-info text
    if info:
        info_text = info.text(separator=' ').lower()
        for ptype in ('vacant land', 'land', 'house', 'unit', 'apartment', 'townhouse', 'villa', 'duplex'):
            if re.search(rf'\b{re.escape(ptype)}\b', info_text):
                out.property_type = ptype
                break

    # Description — try common containers, fall back to longest <p> in main
    desc_node = (
        tree.css_first('.o-wysiwyg')
        or tree.css_first('.js-content-reveal')
        or tree.css_first('.p-detail__desc')
        or tree.css_first('[itemprop="description"]')
    )
    if desc_node:
        out.listing_text = desc_node.text(separator='\n', strip=True)

    # Agent + agency: first .agent block has agent name, agency name, phone
    agent_node = tree.css_first('.agent')
    if agent_node:
        # Crude split: text is "Name<Agency>Phone..."; agency text is recognisable by spaces+capitalisation.
        # Better: look for nested elements.
        parts = [n.text(strip=True) for n in agent_node.css('a, span, div, p') if n.text(strip=True)]
        # Heuristic: first non-empty is agent name; the next distinct one not equal to it
        # and not a phone is agency.
        seen = []
        for p in parts:
            if p not in seen:
                seen.append(p)
        if seen:
            out.agent = seen[0]
        for p in seen[1:]:
            if not re.match(r'^[\d\s+()-]+$', p) and p.lower() not in ('call',):
                out.agency = p
                break

    return out


def scrape_suburb(
    suburb: str,
    *,
    client: PoliteClient | None = None,
    max_pages: int = 50,
    on_error=None,
) -> Iterator[ParsedListing]:
    """Yield parsed listings for a suburb. Resilient: per-page and per-listing failures
    are reported via on_error(stage, url, exc) and skipped instead of aborting."""
    own = False
    if client is None:
        client = PoliteClient().__enter__()
        own = True

    def _report(stage, url, exc):
        if on_error is not None:
            on_error(stage, url, exc)

    try:
        page = 1
        while page <= max_pages:
            url = SEARCH_URL.format(suburb=suburb.lower())
            if page > 1:
                url = f'{url}?page={page}'
            try:
                html = client.get(url)
            except Exception as e:
                _report('search', url, e)
                # transient error on a search page — try the next page rather than abort
                page += 1
                continue
            urls = discover_listing_urls(html)
            for detail_url in urls:
                try:
                    detail_html = client.get(detail_url)
                    yield parse_detail(detail_url, detail_html)
                except Exception as e:
                    _report('detail', detail_url, e)
                    continue
            if not has_next_page(html):
                break
            page += 1
    finally:
        if own:
            client.__exit__(None, None, None)

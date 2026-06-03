"""Parse a free-form WA listing address string into structured parts that
match Landgate's cadastre raw fields.

Example:
    >>> parse('31B Sheldrake Way, Willetton WA 6155')
    {'is_strata': False, 'house_number': '31B', 'street_name': 'SHELDRAKE',
     'street_type': 'WAY', 'locality': 'WILLETTON', 'postcode': '6155'}

The cadastre side (`apps.cadastre.Lot.raw`) stores `road_name` and `locality`
uppercased, `road_type` as the SLIP abbreviation (ST, RD, AV, …). We return
the same forms so the matcher can do dict lookups without further work.
"""
from __future__ import annotations
import re
from typing import Optional, TypedDict


class ParsedAddress(TypedDict):
    is_strata: bool
    house_number: Optional[str]   # e.g. "37A"; None for strata
    street_name: Optional[str]    # UPPER, no type suffix
    street_type: Optional[str]    # UPPER, SLIP abbreviation
    locality: Optional[str]       # UPPER
    postcode: Optional[str]


# Long form -> SLIP abbreviation. Covers the WA Perth-metro long tail.
# Keys are uppercased before lookup so case doesn't matter here.
STREET_TYPE_ABBREV = {
    'STREET': 'ST', 'ST': 'ST',
    'ROAD': 'RD', 'RD': 'RD',
    'AVENUE': 'AV', 'AVE': 'AV', 'AV': 'AV',
    'DRIVE': 'DR', 'DR': 'DR',
    'COURT': 'CT', 'CT': 'CT',
    'CRESCENT': 'CR', 'CRES': 'CR', 'CR': 'CR',
    'PLACE': 'PL', 'PL': 'PL',
    'BOULEVARD': 'BVD', 'BOULEVARDE': 'BVD', 'BLVD': 'BVD', 'BVD': 'BVD',
    'TERRACE': 'TCE', 'TCE': 'TCE',
    'CLOSE': 'CL', 'CL': 'CL',
    'LANE': 'LANE',
    'HIGHWAY': 'HWY', 'HWY': 'HWY',
    'PARADE': 'PDE', 'PDE': 'PDE',
    'WAY': 'WAY',
    'LOOP': 'LOOP',
    'RISE': 'RISE',
    'GROVE': 'GR', 'GR': 'GR',
    'GARDENS': 'GDNS', 'GDNS': 'GDNS',
    'MEWS': 'MEWS',
    'CIRCLE': 'CIR', 'CIR': 'CIR',
    'CIRCUIT': 'CCT', 'CCT': 'CCT',
    'VIEW': 'VW', 'VW': 'VW',
    'VISTA': 'VSTA', 'VSTA': 'VSTA',
    'PROMENADE': 'PROM', 'PROM': 'PROM',
    'GLEN': 'GLN', 'GLN': 'GLN',
    'GREEN': 'GRN', 'GRN': 'GRN',
    'WALK': 'WALK',
    'TRAIL': 'TRL', 'TRL': 'TRL',
    'PARKWAY': 'PWY', 'PWY': 'PWY',
    'ROW': 'ROW',
    'QUAY': 'QY', 'QY': 'QY',
    'KEY': 'KEY',
    'BEND': 'BEND',
    'PASS': 'PASS',
    'RAMBLE': 'RMBL', 'RMBL': 'RMBL',
    'RETREAT': 'RTT', 'RTT': 'RTT',
    'NOOK': 'NOOK',
    'OUTLOOK': 'OTLK', 'OTLK': 'OTLK',
    'CHASE': 'CHASE',
    'BROW': 'BROW',
    'CORNER': 'CNR', 'CNR': 'CNR',
    'HEIGHTS': 'HTS', 'HTS': 'HTS',
    'ESPLANADE': 'ESP', 'ESP': 'ESP',
}

# "31B Sheldrake Way" — number (with optional letter suffix), then 1+ name
# words, then street type token. We capture greedily and split off the last
# token as the street type.
_FIRST_PART = re.compile(
    r'^\s*(?P<num>\d+[A-Za-z]?)\s+(?P<rest>.+?)\s*$'
)


def parse(address_raw: str) -> Optional[ParsedAddress]:
    """Parse a reiwa-style address string. Returns None if unparseable.

    Strata addresses (`"3/12 Smith St, …"`) return is_strata=True with
    everything else None — the caller should skip these for lot matching.
    """
    if not address_raw:
        return None

    s = address_raw.strip()

    # Strata: anything like "12/34 ..." or "Unit 12, 34 ..." — these don't
    # have a 1:1 freehold lot, so we tag and bail.
    if re.match(r'^\s*\d+\s*/\s*\d', s) or re.match(r'^\s*(?:Unit|U)\s+\d', s, re.I):
        return ParsedAddress(is_strata=True, house_number=None, street_name=None,
                             street_type=None, locality=None, postcode=None)

    parts = [p.strip() for p in s.split(',')]
    if len(parts) < 2:
        return None

    # First comma chunk: "31B Sheldrake Way"
    m = _FIRST_PART.match(parts[0])
    if not m:
        return None
    num = m.group('num').upper()
    rest_tokens = m.group('rest').split()
    if len(rest_tokens) < 2:
        # No street type — give up rather than guess.
        return None
    type_token = rest_tokens[-1].upper().rstrip('.')
    street_type = STREET_TYPE_ABBREV.get(type_token)
    if not street_type:
        # Unknown street type; keep the raw upper form so loose matching can
        # still try without a type constraint.
        street_type = None
        street_name = ' '.join(rest_tokens).upper()
    else:
        street_name = ' '.join(rest_tokens[:-1]).upper()

    # Second chunk: "Willetton WA 6155" or sometimes "Willetton WA" or just "Willetton 6155".
    tail = parts[1].split()
    postcode = None
    locality_tokens = []
    for t in tail:
        if re.fullmatch(r'\d{4}', t):
            postcode = t
        elif t.upper() == 'WA':
            continue
        else:
            locality_tokens.append(t)
    locality = ' '.join(locality_tokens).upper() if locality_tokens else None

    if not locality:
        return None

    return ParsedAddress(
        is_strata=False,
        house_number=num,
        street_name=street_name,
        street_type=street_type,
        locality=locality,
        postcode=postcode,
    )

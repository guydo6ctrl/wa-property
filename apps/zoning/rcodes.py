"""
WA R-Codes minimum and average site area lookup (per SPP 7.3 Volume 1, Table 1).

Used to calculate subdivision potential for a residential lot:
    n_dwellings_avg = land_area_sqm / avg_site_area
A lot is "subdividable today" if it can yield >= 2 dwellings under its own R-code,
i.e. land_area / avg_site_area >= 2 (or, more permissively, land_area / min_site_area >= 2).

Source: WA Planning Commission, Residential Design Codes Volume 1 (SPP 7.3).
"""
from __future__ import annotations
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RCodeRule:
    code: str
    min_site_area: int  # m²
    avg_site_area: int  # m²


# Single R-codes only. Split codes (e.g. R20/R40) handled by parse_rcode below.
RULES: dict[str, RCodeRule] = {
    'R2':   RCodeRule('R2',   2000, 4000),
    'R2.5': RCodeRule('R2.5', 2000, 4000),
    'R5':   RCodeRule('R5',   1000, 2000),
    'R10':  RCodeRule('R10',   700,  1000),
    'R12.5':RCodeRule('R12.5', 600,   800),
    'R15':  RCodeRule('R15',   500,   666),
    'R17.5':RCodeRule('R17.5', 400,   571),
    'R20':  RCodeRule('R20',   350,   450),
    'R25':  RCodeRule('R25',   300,   350),
    'R30':  RCodeRule('R30',   260,   300),
    'R35':  RCodeRule('R35',   220,   260),
    'R40':  RCodeRule('R40',   180,   220),
    'R50':  RCodeRule('R50',   160,   180),
    'R60':  RCodeRule('R60',   120,   150),
    'R80':  RCodeRule('R80',   100,   120),
    'R100': RCodeRule('R100',   90,   100),
    'R160': RCodeRule('R160',   80,    90),
    'R200': RCodeRule('R200',   70,    80),
}


_SPLIT_RE = re.compile(r'^R([0-9.]+)/R?([0-9.]+)$')


def parse_rcode(raw: str) -> tuple[str | None, str | None]:
    """Return (base, bonus) codes. SLIP delivers split codes as 'R15/20' or 'R20/R40';
    we normalise both forms. For non-split codes returns (code, None).
    Unknown codes return (None, None)."""
    s = (raw or '').strip().upper().replace(' ', '')
    if not s:
        return None, None
    m = _SPLIT_RE.match(s)
    if m:
        a, b = f'R{m.group(1)}', f'R{m.group(2)}'
        return (a if a in RULES else None, b if b in RULES else None)
    return (s if s in RULES else None, None)


def avg_site_area(raw: str) -> int | None:
    """Return the *base* (lower density) average site area for a code or split-code."""
    base, _ = parse_rcode(raw)
    if base is None:
        return None
    return RULES[base].avg_site_area


def min_site_area(raw: str) -> int | None:
    base, _ = parse_rcode(raw)
    if base is None:
        return None
    return RULES[base].min_site_area


def bonus_avg_site_area(raw: str) -> int | None:
    """For a split code R20/R40, return R40's avg (the bonus density). None for non-split."""
    _, bonus = parse_rcode(raw)
    if bonus is None:
        return None
    return RULES[bonus].avg_site_area

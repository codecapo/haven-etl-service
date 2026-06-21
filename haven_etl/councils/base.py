"""Canonical property row + block-explosion helpers shared by all council mappers.

A "block" in council stock is a building/range like "1-160 Southfleet (Cons)"
with a unit count. We explode it into individual property rows ("1 Southfleet",
… "160 Southfleet") so each unit is a surveyable property.

NB: the Haven `property` table is keyed by UPRN (NOT NULL primary key). Rows
WITHOUT a UPRN cannot be loaded into `property` yet — they're emitted separately
as "unmatched" candidates for the address→UPRN matching phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

# Columns we populate on the Haven `property` table (geom is generated in PG).
PROPERTY_COLUMNS = [
    "uprn",
    "usrn",  # Unique Street Reference Number (OS Linked Identifiers)
    "toid",  # OS MasterMap Topographic identifier (OS Linked Identifiers)
    "property_reference",
    "address_line1",
    "address_line2",
    "postcode",
    "estate",
    "tenure",
    "latitude",
    "longitude",
    "data_source",
]

# A trailing parenthetical like "(Cons)" is council annotation, not the address.
_PAREN = re.compile(r"\([^)]*\)")
# Numeric ranges "1-160" / "95–117" (hyphen or en-dash); also bare counts handled.
_RANGE = re.compile(r"(\d+)\s*[-–]\s*(\d+)")
# A leading single number ("8 Romford Road") when there's no range.
_LEADING_NUM = re.compile(r"^\s*(\d+)\b")

# Sanity cap so a malformed "1-999999" can't explode into a runaway loop.
MAX_UNITS_PER_BLOCK = 2000


@dataclass
class PropertyRow:
    property_reference: str
    address_line1: str
    address_line2: str | None = None
    postcode: str | None = None
    uprn: str | None = None
    usrn: str | None = None
    toid: str | None = None
    estate: str | None = None
    tenure: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    data_source: str = "import"
    # Provenance (not loaded into `property`, kept for auditing/matching).
    block_code: str | None = None
    source_council: str = ""

    def to_record(self) -> dict:
        return asdict(self)


def _base_name(address: str) -> str:
    """The building/street name with ranges, '&' joiners and parens stripped."""
    s = _PAREN.sub("", address)
    s = _RANGE.sub("", s)
    s = s.replace("&", " ")
    return re.sub(r"\s{2,}", " ", s).strip(" ,-")


def explode_block(
    *,
    council: str,
    block_code: str | None,
    block_address: str,
    units: int | None = None,
    estate: str | None = None,
    postcode: str | None = None,
    tenure: str | None = None,
) -> list[PropertyRow]:
    """Expand one block/range row into individual property rows.

    Strategy:
      * If the address contains numeric ranges ("1-160", "1-61 & 95-117"),
        generate one property per number in those ranges.
      * Else if a unit count is given, generate that many (1..units).
      * Else emit the address as a single property.
    """
    address = (block_address or "").strip()
    if not address:
        return []

    ref_stem = (block_code or re.sub(r"\W+", "_", address)).strip()
    name = _base_name(address)
    rows: list[PropertyRow] = []

    def make(num: int | None) -> PropertyRow:
        if num is not None:
            line1 = f"{num} {name}".strip()
            ref = f"{ref_stem}/{num}"
        else:
            line1 = address
            ref = ref_stem
        return PropertyRow(
            property_reference=ref,
            address_line1=line1,
            postcode=postcode or None,
            estate=estate or None,
            tenure=tenure or None,
            block_code=block_code or None,
            source_council=council,
        )

    ranges = _RANGE.findall(address)
    if ranges:
        seen: set[int] = set()
        for lo_s, hi_s in ranges:
            lo, hi = int(lo_s), int(hi_s)
            if hi < lo:
                lo, hi = hi, lo
            if hi - lo + 1 > MAX_UNITS_PER_BLOCK:
                hi = lo + MAX_UNITS_PER_BLOCK - 1
            for n in range(lo, hi + 1):
                if n not in seen:
                    seen.add(n)
                    rows.append(make(n))
        return rows

    if units and units > 0:
        for n in range(1, min(int(units), MAX_UNITS_PER_BLOCK) + 1):
            rows.append(make(n))
        return rows

    # No range, no count — a single addressable property (use the address as-is).
    rows.append(make(None))
    return rows

"""Enrichment derivable from an ICAO 24-bit address alone -- no registry needed.

Two independent things live here, both pure and both cheap enough to call per frame:

  * registration_from_hex(): US civil N-numbers are not an arbitrary allocation, they are
    a deterministic positional encoding over a 34-symbol alphabet (10 digits plus 24
    letters -- I and O are excluded because they look like 1 and 0). Address 0xA00001 is
    N1 and 0xADF7C7 is N99999, and every address in between decodes to exactly one
    registration. So a US aircraft's registration can be recovered from its hex with no
    data file at all, which matters because the whole point of this milestone is that a
    user with no downloaded registry still sees something useful.

    The one caveat worth knowing: the algorithm yields the ORIGINAL allocation for an
    address. An operator that later reserved a vanity registration (an airline fleet
    number, say) keeps its old address, so the derived value can differ from what the
    aircraft actually wears. That is why derived data sits at the BOTTOM of the provider
    priority order in providers.py -- anything that actually knows beats it.

  * country_for_hex() / is_military_hex(): ICAO assigns 24-bit address blocks per state of
    registry (Annex 10, Volume III), so the address prefix identifies the country. That
    part IS data, but it is tiny and static, so a small CSV ships inside the package at
    adsbtui/data/icao_ranges.csv. That table is deliberately PARTIAL: it lists only the
    blocks we are confident about, since a confidently wrong country is worse than an
    honest None. Lookups are a bisect over integer bounds, and nested ranges (a military
    sub-block inside a national block) resolve to the most specific match.

Nothing here does I/O beyond reading that one bundled CSV once, and nothing here raises:
malformed input, a non-ICAO '~' address, or a missing table all just produce None.
"""

from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

# --------------------------------------------------------------------------------------
# US civil N-number encoding
# --------------------------------------------------------------------------------------

#: First and last address of the US civil (N-number) allocation, inclusive.
N_NUMBER_FIRST_HEX = 0xA00001
N_NUMBER_LAST_HEX = 0xADF7C7

#: The 24 letters usable in an N-number suffix: the Latin alphabet without I and O.
_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "0123456789"

#: Number of suffix slots at a digit position that still has room for two letters:
#: 1 (no suffix) + 24 (one letter) + 24*24 (two letters).
_SUFFIX_SLOTS = 601

#: Size of the sub-tree hanging off each digit position. An N-number is at most five
#: characters after the N, so the deepest digit can only be followed by nothing, one
#: letter, or one more digit: 1 + 24 + 10 = 35. Each shallower level is
#: _SUFFIX_SLOTS + 10 * (the level below it), which is where 951/10111/101711 come from.
_BLOCK_SIZES = (10111, 951, 35)
_BLOCK_FIRST_DIGIT = 101711


def _hex_to_int(hex_id: str) -> int | None:
    """Parse a 24-bit ICAO address into an int, or None if it is not one.

    Rejects the '~xxxxxx' form readsb uses for non-ICAO (TIS-B/MLAT) addresses: those are
    not ICAO allocations at all, so neither the N-number algorithm nor the country table
    means anything for them.
    """
    if not isinstance(hex_id, str):
        return None
    text = hex_id.strip()
    if not text or text.startswith("~"):
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    if not 0 <= value <= 0xFFFFFF:
        return None
    return value


def normalize_hex(hex_id: str) -> str | None:
    """Return an ICAO address as lowercase, zero-padded 6 hex digits, or None.

    The canonical key form used by every enrichment provider and by the SQLite registry,
    so that 'A004B3', 'a004b3' and ' a004b3 ' all hit the same row.
    """
    value = _hex_to_int(hex_id)
    return None if value is None else f"{value:06x}"


def _suffix_for(index: int) -> str:
    """Map a suffix slot (0..600) to '', a single letter, or a letter pair.

    The slots interleave rather than grouping all single letters before all pairs: each
    first letter owns a block of 25 -- itself alone, then its 24 pairs -- so the sequence
    runs '', A, AA, AB, ... AZ, B, BA, ... ZZ. Getting this wrong still yields exactly 601
    slots and a self-consistent round trip, which is why it has to be checked against real
    registrations rather than against its own inverse.
    """
    if index <= 0:
        return ""
    offset = index - 1
    first, second = divmod(offset, len(_LETTERS) + 1)
    letters = _LETTERS[first]
    if second:
        letters += _LETTERS[second - 1]
    return letters


def _suffix_index(suffix: str) -> int | None:
    """Inverse of _suffix_for(); None if the suffix is not a legal one."""
    if not suffix:
        return 0
    stride = len(_LETTERS) + 1
    try:
        if len(suffix) == 1:
            return _LETTERS.index(suffix) * stride + 1
        if len(suffix) == 2:
            first = _LETTERS.index(suffix[0])
            second = _LETTERS.index(suffix[1])
            return first * stride + second + 2
    except ValueError:
        return None
    return None


def registration_from_hex(hex_id: str) -> str | None:
    """Derive the US civil registration for an ICAO address, or None if it is not one.

    Returns the ORIGINAL N-number allocated to the address (see the module docstring);
    callers should prefer any registration a feed or registry actually reports.
    """
    value = _hex_to_int(hex_id)
    if value is None or not (N_NUMBER_FIRST_HEX <= value <= N_NUMBER_LAST_HEX):
        return None

    offset = value - N_NUMBER_FIRST_HEX
    first, offset = divmod(offset, _BLOCK_FIRST_DIGIT)
    registration = f"N{first + 1}"

    for block in _BLOCK_SIZES:
        if offset < _SUFFIX_SLOTS:
            return registration + _suffix_for(offset)
        offset -= _SUFFIX_SLOTS
        digit, offset = divmod(offset, block)
        registration += str(digit)

    # Four digits are placed; the remaining 35 slots are bare / one letter / a fifth digit.
    if offset == 0:
        return registration
    if offset <= len(_LETTERS):
        return registration + _LETTERS[offset - 1]
    return registration + str(offset - 1 - len(_LETTERS))


def hex_from_registration(registration: str) -> str | None:
    """Encode a US civil N-number back to its lowercase 6-digit ICAO address.

    The exact inverse of registration_from_hex() over the whole valid range. Returns None
    for anything that is not a well-formed N-number: a bad prefix, an I or O in the
    suffix, more than five characters after the N, or a suffix where no suffix can go
    (N12345A does not exist).
    """
    if not isinstance(registration, str):
        return None
    text = registration.strip().upper()
    if len(text) < 2 or text[0] != "N":
        return None

    body = text[1:]
    split = 0
    while split < len(body) and body[split] in _DIGITS:
        split += 1
    digits, suffix = body[:split], body[split:]

    if not digits or digits[0] == "0":
        return None
    if len(digits) + len(suffix) > 5:
        return None
    if len(digits) == 5 and suffix:
        return None
    if len(digits) == 4 and len(suffix) > 1:
        return None

    offset = (int(digits[0]) - 1) * _BLOCK_FIRST_DIGIT
    for level, digit in enumerate(digits[1:4]):
        offset += _SUFFIX_SLOTS + int(digit) * _BLOCK_SIZES[level]

    if len(digits) == 5:
        offset += 1 + len(_LETTERS) + int(digits[4])
    elif len(digits) == 4:
        # The deepest level has no room for a letter PAIR, so its 35 slots are laid out
        # flat -- bare, 24 single letters, then a fifth digit -- rather than in the
        # 25-wide blocks _suffix_index assumes. Using the general inverse here silently
        # lands on the wrong address.
        if not suffix:
            pass
        elif suffix in _LETTERS:
            offset += _LETTERS.index(suffix) + 1
        else:
            return None
    else:
        suffix_index = _suffix_index(suffix)
        if suffix_index is None:
            return None
        offset += suffix_index

    value = N_NUMBER_FIRST_HEX + offset
    if value > N_NUMBER_LAST_HEX:
        return None
    return f"{value:06x}"


# --------------------------------------------------------------------------------------
# ICAO address block -> country
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IcaoRange:
    """One row of the bundled country table: an inclusive address range."""

    start: int
    end: int
    iso2: str
    country: str
    military: bool


def _read_table() -> str:
    """Return the bundled CSV's text, or '' if it cannot be read.

    A missing data file must not break enrichment -- the caller loses country lookups and
    keeps everything else -- so every failure mode here collapses to an empty table.
    """
    try:
        return (
            resources.files("adsbtui").joinpath("data/icao_ranges.csv").read_text(encoding="utf-8")
        )
    except (OSError, ModuleNotFoundError, TypeError):
        return ""


@lru_cache(maxsize=1)
def _table() -> tuple[tuple[IcaoRange, ...], tuple[int, ...], tuple[int, ...]]:
    """Parse the bundled table once into (ranges, starts, running_max_ends).

    running_max_ends[i] is the largest end seen in ranges[:i+1]. Because ranges are sorted
    by start, that lets _range_for() walk backwards from the bisect point only as far as a
    containing range could possibly reach, which is what makes nested ranges (a military
    sub-block inside a national block) resolve correctly without scanning the whole table.
    """
    rows: list[IcaoRange] = []
    lines = [
        line
        for line in _read_table().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for row in csv.reader(lines):
        if len(row) < 4:
            continue
        try:
            start = int(row[0], 16)
            end = int(row[1], 16)
        except ValueError:
            continue  # the header row and any junk line land here
        if start > end:
            continue
        military = len(row) > 4 and row[4].strip().lower() in {"1", "true", "yes"}
        rows.append(
            IcaoRange(
                start=start,
                end=end,
                iso2=row[2].strip().upper(),
                country=row[3].strip(),
                military=military,
            )
        )

    rows.sort(key=lambda r: (r.start, r.end))
    starts = tuple(r.start for r in rows)

    max_ends: list[int] = []
    running = -1
    for row_range in rows:
        running = max(running, row_range.end)
        max_ends.append(running)

    return tuple(rows), starts, tuple(max_ends)


def range_for_hex(hex_id: str) -> IcaoRange | None:
    """Return the most specific bundled range containing an address, or None."""
    value = _hex_to_int(hex_id)
    if value is None:
        return None

    ranges, starts, max_ends = _table()
    if not ranges:
        return None

    index = bisect.bisect_right(starts, value) - 1
    while index >= 0 and max_ends[index] >= value:
        candidate = ranges[index]
        if candidate.start <= value <= candidate.end:
            return candidate
        index -= 1
    return None


def country_for_hex(hex_id: str) -> tuple[str, str] | None:
    """Return (iso2, country name) for an ICAO address, or None if it is not in the table."""
    found = range_for_hex(hex_id)
    if found is None:
        return None
    return found.iso2, found.country


def is_military_hex(hex_id: str) -> bool:
    """True if the address falls in a block the bundled table marks as military.

    False means 'not in a known military block', not 'definitely civil' -- military
    aircraft do fly on civil addresses, and the table is partial.
    """
    found = range_for_hex(hex_id)
    return found is not None and found.military

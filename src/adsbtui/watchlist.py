"""Watchlist: a plain-text list of aircraft-matching patterns.

The file format is one entry per line: "prefix:pattern" where prefix is one of hex,
reg, call, owner, type. Blank lines and lines starting with '#' are treated as
comments and ignored. The pattern uses shell-style globbing (fnmatch), matched
case-insensitively against the corresponding Aircraft field:

  hex   -> aircraft.hex (a leading '~' -- readsb's marker for a non-ICAO address --
           is stripped before comparing, since users write plain hex codes)
  reg   -> aircraft.registration
  call  -> aircraft.flight
  owner -> aircraft.owner_name
  type  -> aircraft.type_code

This module is pure (no curses, no network, no globals) so it is unit-testable on
its own; it does not know anything about the UI or alerting -- callers use
matches() to decide what to do with a hit.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from fnmatch import fnmatch

logger = logging.getLogger("adsbtui")

#: prefix -> Aircraft attribute name it is matched against.
_PREFIX_TO_FIELD: dict[str, str] = {
    "hex": "hex",
    "reg": "registration",
    "call": "flight",
    "owner": "owner_name",
    "type": "type_code",
}


@dataclass
class WatchEntry:
    """One parsed watchlist line."""

    prefix: str
    pattern: str
    raw_line: str
    enabled: bool = True


def parse_line(line: str) -> WatchEntry | None:
    """Parse a single watchlist line into a WatchEntry.

    Returns None for a blank line or a comment (one whose first non-whitespace
    character is '#'). Raises ValueError for anything else that is malformed: no
    colon separator, or a prefix that is not one of hex/reg/call/owner/type.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if ":" not in stripped:
        raise ValueError(f"malformed watchlist line (missing ':'): {line!r}")

    prefix, _, pattern = stripped.partition(":")
    prefix = prefix.strip().lower()
    pattern = pattern.strip()

    if prefix not in _PREFIX_TO_FIELD:
        raise ValueError(
            f"malformed watchlist line (unknown prefix {prefix!r}, "
            f"expected one of {sorted(_PREFIX_TO_FIELD)}): {line!r}"
        )
    if not pattern:
        raise ValueError(f"malformed watchlist line (empty pattern): {line!r}")

    return WatchEntry(prefix=prefix, pattern=pattern, raw_line=stripped)


def load(path: str) -> list[WatchEntry]:
    """Load watchlist entries from `path`.

    Malformed lines are logged as a warning and skipped rather than failing the
    whole load. A missing file is not an error -- a watchlist is optional -- and
    yields an empty list.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("could not read watchlist %r: %s", path, e)
        return []

    entries: list[WatchEntry] = []
    for lineno, line in enumerate(lines, start=1):
        try:
            entry = parse_line(line)
        except ValueError as e:
            logger.warning("skipping watchlist %s line %d: %s", path, lineno, e)
            continue
        if entry is not None:
            entries.append(entry)
    return entries


def save(path: str, entries: list[WatchEntry]) -> None:
    """Write `entries` back out to `path`, one per line.

    Creates the parent directory if needed. This is a simple round-trip of the
    active entries -- it does not attempt to preserve original comments or
    formatting from a previously loaded file.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(f"{entry.prefix}:{entry.pattern}\n")


def matches(aircraft, entries: list[WatchEntry]) -> WatchEntry | None:
    """Return the first enabled entry in `entries` that matches `aircraft`, or None.

    For each entry, the corresponding Aircraft field is looked up (hex/reg/call/
    owner/type); if that field is None the entry is skipped (no match). Otherwise
    the field value and the pattern are both uppercased and compared with fnmatch.
    hex comparisons strip a leading '~' from aircraft.hex first.
    """
    for entry in entries:
        if not entry.enabled:
            continue

        field_name = _PREFIX_TO_FIELD.get(entry.prefix)
        if field_name is None:
            continue

        value = getattr(aircraft, field_name, None)
        if value is None:
            continue

        if entry.prefix == "hex":
            value = value.lstrip("~")

        if fnmatch(value.upper(), entry.pattern.upper()):
            return entry

    return None


def add_entry(path: str, prefix: str, pattern: str) -> None:
    """Append a new watchlist entry to the file at `path`.

    Loads the existing entries, appends the new one, and saves. Raises ValueError
    if `prefix` is not one of hex/reg/call/owner/type.
    """
    prefix = prefix.strip().lower()
    if prefix not in _PREFIX_TO_FIELD:
        raise ValueError(
            f"unknown watchlist prefix {prefix!r}, expected one of {sorted(_PREFIX_TO_FIELD)}"
        )

    entries = load(path)
    raw_line = f"{prefix}:{pattern}"
    entries.append(WatchEntry(prefix=prefix, pattern=pattern, raw_line=raw_line))
    save(path, entries)

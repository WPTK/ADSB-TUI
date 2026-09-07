"""Aircraft enrichment: several providers, resolved one FIELD at a time.

The point of this layer is that no single source knows everything. The receiver's own
feed knows the callsign and, if the operator configured a db-file, sometimes a
registration; a downloaded registry knows type codes and owners worldwide; and the hex
address itself always knows at least the country, and for a US aircraft the original
N-number. So resolution happens per field, not per record:
each provider reports only what it positively knows, and the first provider in priority
order that has an answer for a given field wins that field. A record can therefore end up
with its registration from the feed, its owner from the database and its country derived,
and AircraftInfo.sources records which provider supplied each one.

Default priority is feed > db > derived, which is deliberate: the receiver is
looking at the actual transmission, a downloaded registry is a snapshot, and a derived
N-number is only the address's ORIGINAL allocation (see derived.py). The order is
configurable because a user with a very stale receiver db-file may reasonably want the
downloaded registry to win.

Nothing here raises on missing data. A user with no registry database and no network
still gets a derived registration and country for every US aircraft, which is the whole
reason the derived provider exists.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from typing import Protocol

from adsbtui.enrich.derived import (
    normalize_hex,
    range_for_hex,
    registration_from_hex,
)
from adsbtui.model import Aircraft

#: Provider names in descending priority, as accepted by Enricher.from_paths().
DEFAULT_ORDER: tuple[str, ...] = ("feed", "db", "derived")

#: How many hex addresses to remember resolved provider results for. A busy receiver sees
#: a few hundred distinct aircraft an hour, so this holds a session comfortably.
DEFAULT_CACHE_SIZE = 4096

_MILITARY_BIT = 0x1


@dataclass(frozen=True)
class AircraftInfo:
    """What the providers collectively know about one aircraft.

    Every field is optional and None means 'nobody knew', never 'no'. In particular
    is_military is None when no provider had an opinion; callers should read it as
    bool(info.is_military).

    sources maps a field name to the name of the provider that supplied it, so the UI can
    say where an owner name came from and a bug report can say which provider was wrong.
    """

    registration: str | None = None
    type_code: str | None = None
    type_desc: str | None = None
    owner: str | None = None
    year: str | None = None
    country: str | None = None
    country_iso2: str | None = None
    db_flags: int | None = None
    is_military: bool | None = None
    sources: dict[str, str] = field(default_factory=dict)


#: The AircraftInfo fields that participate in per-field merging (everything but sources).
_MERGE_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(AircraftInfo) if f.name != "sources")


class Provider(Protocol):
    """One source of enrichment.

    name identifies the provider in AircraftInfo.sources and in a configured order.
    per_aircraft is True for a provider whose answer depends on the live Aircraft rather
    than only on the address, which is what tells the Enricher its result must not be
    cached per hex.
    """

    name: str
    per_aircraft: bool

    def lookup(self, hex_id: str, aircraft: Aircraft | None = None) -> AircraftInfo | None:
        """Return what this provider knows about hex_id, or None if nothing."""
        ...


class FeedProvider:
    """The values readsb/dump1090 already put on the Aircraft, straight off the wire."""

    name = "feed"
    per_aircraft = True

    def lookup(self, hex_id: str, aircraft: Aircraft | None = None) -> AircraftInfo | None:
        if aircraft is None:
            return None

        # dbFlags of 0 is the default for a receiver with no db-file at all, so it carries
        # no information; only a non-zero value is treated as the feed knowing something.
        flags = aircraft.db_flags or None

        info = AircraftInfo(
            registration=aircraft.registration,
            type_code=aircraft.type_code,
            type_desc=aircraft.type_desc,
            owner=aircraft.owner_operator or aircraft.owner_name,
            year=aircraft.year,
            db_flags=flags,
            is_military=bool(flags & _MILITARY_BIT) if flags else None,
        )
        return info if _has_content(info) else None


class SqliteProvider:
    """The local registry database built by registry_update.py.

    A missing, unreadable or pre-schema database is not an error: every failure returns
    None so the providers below this one still get their turn.
    """

    name = "db"
    per_aircraft = False

    _QUERY = """
    SELECT registration, type_code, type_desc, owner, year, flags
    FROM aircraft WHERE hex = ?
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def lookup(self, hex_id: str, aircraft: Aircraft | None = None) -> AircraftInfo | None:
        key = normalize_hex(hex_id)
        if key is None or not os.path.exists(self.db_path):
            return None

        # A short-lived connection per miss, like history.py: the Enricher caches results
        # by hex, so this runs once per aircraft per session rather than once per frame,
        # and no connection has to be shared across threads.
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(self._QUERY, (key,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None

        if row is None:
            return None

        flags = int(row[5] or 0) or None
        info = AircraftInfo(
            registration=row[0] or None,
            type_code=row[1] or None,
            type_desc=row[2] or None,
            owner=row[3] or None,
            year=row[4] or None,
            db_flags=flags,
            is_military=bool(flags & _MILITARY_BIT) if flags else None,
        )
        return info if _has_content(info) else None


class DerivedProvider:
    """Everything computable from the address itself: N-number, country, military block."""

    name = "derived"
    per_aircraft = False

    def lookup(self, hex_id: str, aircraft: Aircraft | None = None) -> AircraftInfo | None:
        block = range_for_hex(hex_id)
        info = AircraftInfo(
            registration=registration_from_hex(hex_id),
            country=block.country if block else None,
            country_iso2=block.iso2 if block else None,
            # Only a positive claim: a civil block says nothing about the operator.
            is_military=True if block is not None and block.military else None,
        )
        return info if _has_content(info) else None


def _has_content(info: AircraftInfo) -> bool:
    return any(getattr(info, name) is not None for name in _MERGE_FIELDS)


def merge(parts: Sequence[tuple[str, AircraftInfo | None]]) -> AircraftInfo:
    """Merge (provider name, info) pairs in priority order, first non-None per field."""
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    for name, info in parts:
        if info is None:
            continue
        for field_name in _MERGE_FIELDS:
            if field_name in values:
                continue
            value = getattr(info, field_name)
            if value is None:
                continue
            values[field_name] = value
            sources[field_name] = name
    return AircraftInfo(sources=sources, **values)  # type: ignore[arg-type]


class Enricher:
    """Resolves an aircraft through an ordered list of providers.

    Results from address-only providers are cached per hex, because they cannot change
    between polls; the feed provider is consulted fresh every time since its answer is the
    live record. Call clear_cache() after rebuilding the registry database.
    """

    def __init__(self, providers: Sequence[Provider], cache_size: int = DEFAULT_CACHE_SIZE) -> None:
        self.providers: tuple[Provider, ...] = tuple(providers)
        self.cache_size = max(1, cache_size)
        self._cache: dict[str, tuple[AircraftInfo | None, ...]] = {}

    @classmethod
    def from_paths(
        cls,
        db_path: str | None = None,
        order: Sequence[str] = DEFAULT_ORDER,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> Enricher:
        """Build an Enricher from configured paths, honoring a priority order.

        A database that has not been downloaded yet is simply left out, so passing no
        path still yields a working (feed plus derived) Enricher. An unknown name in order
        is a configuration mistake and raises ValueError rather than silently disabling
        enrichment.
        """
        providers: list[Provider] = []
        for name in order:
            if name == "feed":
                providers.append(FeedProvider())
            elif name == "db":
                if db_path:
                    providers.append(SqliteProvider(db_path))
            elif name == "derived":
                providers.append(DerivedProvider())
            else:
                raise ValueError(f"unknown enrichment provider {name!r}")
        return cls(providers, cache_size=cache_size)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _cached_parts(self, key: str) -> tuple[AircraftInfo | None, ...]:
        """Per-provider results for the address-only providers, aligned with self.providers."""
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        parts = tuple(
            None if provider.per_aircraft else provider.lookup(key) for provider in self.providers
        )
        if len(self._cache) >= self.cache_size:
            # Plain FIFO eviction: cheap, and the working set is one session's aircraft.
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[key] = parts
        return parts

    def lookup(self, hex_id: str) -> AircraftInfo:
        """Resolve an address with no live record available."""
        key = normalize_hex(hex_id)
        if key is None:
            return AircraftInfo()
        parts = self._cached_parts(key)
        return merge(
            [(provider.name, part) for provider, part in zip(self.providers, parts, strict=True)]
        )

    def enrich(self, aircraft: Aircraft) -> AircraftInfo:
        """Resolve one live Aircraft, letting the feed provider see the record itself."""
        key = normalize_hex(aircraft.hex)
        if key is None:
            # A non-ICAO ('~xxxxxx') address: nothing address-derived applies, but the feed
            # may still carry a registration, so run only the per-aircraft providers.
            return merge(
                [
                    (provider.name, provider.lookup(aircraft.hex, aircraft))
                    for provider in self.providers
                    if provider.per_aircraft
                ]
            )

        parts = self._cached_parts(key)
        resolved: list[tuple[str, AircraftInfo | None]] = []
        for provider, part in zip(self.providers, parts, strict=True):
            if provider.per_aircraft:
                resolved.append((provider.name, provider.lookup(key, aircraft)))
            else:
                resolved.append((provider.name, part))
        return merge(resolved)

    def apply(self, aircraft: Aircraft) -> AircraftInfo:
        """Fill in the Aircraft's empty enrichment fields in place and return the merged info.

        Only unset fields are written: a value the feed provided is already the winner, and
        overwriting it here would defeat the priority order. Country has no home on the
        Aircraft model, so callers that want it read it from the returned AircraftInfo.
        """
        info = self.enrich(aircraft)

        if aircraft.registration is None and info.registration:
            aircraft.registration = info.registration
        if aircraft.type_code is None and info.type_code:
            aircraft.type_code = info.type_code
        if aircraft.type_desc is None and info.type_desc:
            aircraft.type_desc = info.type_desc
        if aircraft.year is None and info.year:
            aircraft.year = info.year
        if info.owner:
            if aircraft.owner_operator is None:
                aircraft.owner_operator = info.owner
            if aircraft.owner_name is None:
                aircraft.owner_name = info.owner
        if not aircraft.db_flags and info.db_flags:
            aircraft.db_flags = info.db_flags
        if info.is_military:
            aircraft.db_flags |= _MILITARY_BIT

        return info

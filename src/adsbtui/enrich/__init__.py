"""Offline aircraft enrichment: who is that, what is it, and where is it registered.

Three layers, deliberately separate:

  * derived.py    -- what the ICAO address alone implies (US N-number, country, military
                     block). No download, no configuration, always available.
  * registry_update.py -- fetching public bulk registries (tar1090-db, the FAA
                     Releasable Aircraft Database) into a local SQLite cache.
  * providers.py  -- the lookup layer that resolves one aircraft across the live feed,
                     that SQLite cache and the derived values, choosing a winner per FIELD
                     rather than per record.

The entry point almost every caller wants is Enricher.from_paths(db_path), then
enricher.apply(aircraft) once per aircraft per poll.
"""

from __future__ import annotations

from adsbtui.enrich.derived import (
    IcaoRange,
    country_for_hex,
    hex_from_registration,
    is_military_hex,
    normalize_hex,
    range_for_hex,
    registration_from_hex,
)
from adsbtui.enrich.providers import (
    DEFAULT_ORDER,
    AircraftInfo,
    DerivedProvider,
    Enricher,
    FeedProvider,
    Provider,
    SqliteProvider,
    merge,
)

__all__ = [
    "DEFAULT_ORDER",
    "AircraftInfo",
    "DerivedProvider",
    "Enricher",
    "FeedProvider",
    "IcaoRange",
    "Provider",
    "SqliteProvider",
    "country_for_hex",
    "hex_from_registration",
    "is_military_hex",
    "merge",
    "normalize_hex",
    "range_for_hex",
    "registration_from_hex",
]

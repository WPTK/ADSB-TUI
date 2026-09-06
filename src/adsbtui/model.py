"""Core data model.

All numeric fields are stored in the source's native units: knots for speed, feet for altitude,
feet/minute for vertical rate, statute miles for derived distance, degrees for angles. Conversion
to a display unit system happens only in `units.py` / the UI layer, never here or in `normalize.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AlertLevel(StrEnum):
    NONE = "none"
    PASSING = "passing"
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    OVERHEAD = "overhead"
    EMERGENCY = "emergency"


@dataclass
class Snapshot:
    """One poll of the data source, close to the wire format."""

    now: float  # epoch seconds, from the feed's top-level "now" (falls back to wall clock)
    messages: int | None  # top-level cumulative message counter, if the feed provides one
    raw_aircraft: list[dict]  # untouched entries from the aircraft.json "aircraft" array
    fetched_at: float  # local wall-clock epoch seconds when this snapshot was received


@dataclass
class Aircraft:
    """A single aircraft, normalized from one raw aircraft.json entry."""

    hex: str  # lowercase 6 hex digits, or "~xxxxxx" for a non-ICAO (TIS-B/MLAT) address
    is_icao: bool
    flight: str | None = None
    squawk: str | None = None
    emergency: str | None = None  # None when absent or reported as "none"
    category: str | None = None

    altitude_ft: float | None = None
    on_ground: bool = False
    geom_altitude_ft: float | None = None
    baro_rate_fpm: float | None = None
    geom_rate_fpm: float | None = None

    ground_speed_kt: float | None = None
    track_deg: float | None = None
    true_heading_deg: float | None = None
    mag_heading_deg: float | None = None

    lat: float | None = None
    lon: float | None = None
    seen_pos_s: float | None = None  # seconds since the position last updated
    seen_s: float | None = None  # seconds since any message last received

    source_type: str | None = None  # readsb "type": adsb_icao, mlat, tisb_icao, mode_s, ...
    rssi: float | None = None
    messages: int | None = None

    # readsb/dump1090 enrichment fields, present only when the receiver has a --db-file configured
    registration: str | None = None
    type_code: str | None = None
    type_desc: str | None = None
    owner_operator: str | None = None
    year: str | None = None
    db_flags: int = 0  # bit0 military, bit1 interesting, bit2 PIA, bit3 LADD

    nav_altitude_mcp: float | None = None
    nav_heading: float | None = None

    # derived by geo.py once home coordinates are known
    distance_mi: float | None = None
    bearing_deg: float | None = None
    elevation_deg: float | None = None
    cpa_distance_mi: float | None = None
    cpa_seconds: float | None = None

    # derived by tracker.py across snapshots
    first_seen: float | None = None  # local epoch seconds
    last_seen: float | None = None
    is_new: bool = False
    is_stale: bool = False
    is_lost: bool = False

    # local registry lookup (FAA CSV owner name; see normalize.py's registry loader)
    owner_name: str | None = None

    # set by the watchlist matcher when this aircraft matches a user pattern
    is_watched: bool = False

    alert_level: AlertLevel = AlertLevel.NONE

    extra: dict = field(default_factory=dict)  # unmodeled raw fields, for the detail pane

    @property
    def is_military(self) -> bool:
        return bool(self.db_flags & 0x1)

    @property
    def is_interesting(self) -> bool:
        return bool(self.db_flags & 0x2)

    @property
    def is_pia(self) -> bool:
        return bool(self.db_flags & 0x4)

    @property
    def is_ladd(self) -> bool:
        return bool(self.db_flags & 0x8)

    @property
    def display_flight(self) -> str:
        return self.flight or self.registration or self.hex.upper()

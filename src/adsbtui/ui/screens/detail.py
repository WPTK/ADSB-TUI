"""The aircraft detail screen: a read-only, scrollable "spec sheet" for one aircraft.

DetailScreen follows the Screen design contract documented in ui/screens/__init__.py: a
read-only title, a pure render_lines(width, height), and a handle_key() that only ever
closes itself (this screen has no internal navigation -- every field is plain text). Like
ui/columns.py, this module is pure: no curses calls, no network calls, no global state, so
it is fully unit-testable without a terminal. curses is imported only for its KEY_ENTER
integer constant, which is safe without an initialized screen.
"""

from __future__ import annotations

import curses

from adsbtui.alerts import decode_category, decode_db_flags
from adsbtui.geo import compass_point
from adsbtui.model import Aircraft
from adsbtui.units import (
    UNIT_SYSTEMS,
    format_altitude,
    format_distance,
    format_speed,
    format_vspeed,
)

#: Keys that close the screen: Escape, the letter q, and Enter (in any of the forms
#: TextField/MessageBox elsewhere in ui/widgets.py already treat as "Enter").
_CLOSE_KEYS = frozenset({27, ord("q"), ord("Q"), 10, 13, curses.KEY_ENTER})


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


def _format_duration(seconds: float | None) -> str:
    """Human-friendly duration: "N/A", "<60>s", "<N>m <N>s", or "<N>h <N>m"."""
    if seconds is None:
        return "N/A"
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _format_type(type_code: str | None, type_desc: str | None) -> str:
    if type_code and type_desc:
        return f"{type_code} - {type_desc}"
    if type_code:
        return type_code
    return "N/A"


def _format_owner(aircraft: Aircraft) -> str:
    """Best available owner label.

    Prefers owner_name (the FAA-registry lookup done by normalize.py) since it names the
    actual registered owner; falls back to owner_operator (the readsb/dump1090 db-file
    enrichment, which is often just the operating airline/agency) when the registry has
    nothing, and "N/A" when neither source has anything.
    """
    return aircraft.owner_name or aircraft.owner_operator or "N/A"


def _format_vertical_rate(aircraft: Aircraft, unit: str) -> str:
    rate = aircraft.baro_rate_fpm if aircraft.baro_rate_fpm is not None else aircraft.geom_rate_fpm
    return format_vspeed(rate, unit)


def _format_track(track_deg: float | None) -> str:
    return "N/A" if track_deg is None else f"{track_deg:03.0f}°"


def _format_bearing(bearing_deg: float | None) -> str:
    if bearing_deg is None:
        return "N/A"
    return f"{compass_point(bearing_deg)} {bearing_deg:03.0f}°"


def _format_closest_approach(aircraft: Aircraft, distance_unit: str) -> str:
    if aircraft.cpa_distance_mi is None or aircraft.cpa_seconds is None:
        return "n/a"
    dist = format_distance(aircraft.cpa_distance_mi, distance_unit)
    return f"{dist} in {_format_duration(aircraft.cpa_seconds)}"


def _format_flags(db_flags: int) -> str:
    tags = decode_db_flags(db_flags)
    return ", ".join(tags) if tags else "-"


def _format_signal(rssi: float | None) -> str:
    return "N/A" if rssi is None else f"{rssi:.1f} dBFS"


def _build_fields(aircraft: Aircraft, unit_system: str) -> list[tuple[str, str]]:
    """Build the ordered (label, value) pairs shown by render_lines().

    Never raises, even for a fully-empty Aircraft (every field default is None) -- each
    formatter above already renders "N/A" (or the field's own documented fallback, e.g.
    "None" for Emergency, "-" for Flags, "n/a" for Closest approach) for a missing value.
    """
    units = UNIT_SYSTEMS[unit_system]
    flight = (aircraft.flight or "").strip()
    return [
        ("Hex", aircraft.hex.upper()),
        ("Flight", flight or "N/A"),
        ("Registration", aircraft.registration or "N/A"),
        ("Type", _format_type(aircraft.type_code, aircraft.type_desc)),
        ("Owner", _format_owner(aircraft)),
        ("Altitude", format_altitude(aircraft.altitude_ft, units["altitude"], aircraft.on_ground)),
        ("Vertical rate", _format_vertical_rate(aircraft, units["vspeed"])),
        ("Ground speed", format_speed(aircraft.ground_speed_kt, units["speed"])),
        ("Track", _format_track(aircraft.track_deg)),
        ("Bearing", _format_bearing(aircraft.bearing_deg)),
        ("Distance", format_distance(aircraft.distance_mi, units["distance"])),
        ("Closest approach", _format_closest_approach(aircraft, units["distance"])),
        ("Squawk", aircraft.squawk or "N/A"),
        ("Emergency", aircraft.emergency or "None"),
        ("Category", decode_category(aircraft.category) or "N/A"),
        ("Flags", _format_flags(aircraft.db_flags)),
        ("Position age", _format_duration(aircraft.seen_pos_s)),
        ("Message source", aircraft.source_type or "N/A"),
        ("Signal", _format_signal(aircraft.rssi)),
    ]


class DetailScreen:
    """A read-only "spec sheet" for one aircraft.

    Every field renders gracefully -- "N/A" (or its documented equivalent) for any missing
    value -- so a fully-empty Aircraft() never raises. There is no internal navigation:
    handle_key() only ever recognizes the keys that close the screen.
    """

    def __init__(self, aircraft: Aircraft, unit_system: str) -> None:
        if unit_system not in UNIT_SYSTEMS:
            raise ValueError(f"unknown unit system {unit_system!r}")
        self._aircraft = aircraft
        self._unit_system = unit_system

    @property
    def title(self) -> str:
        flight = (self._aircraft.flight or "").strip()
        label = flight if flight else self._aircraft.hex.upper()
        return f"Aircraft {label}"

    @property
    def aircraft(self) -> Aircraft:
        return self._aircraft

    def handle_key(self, key: int) -> str | None:
        if key in _CLOSE_KEYS:
            return "close"
        return None

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []
        fields = _build_fields(self._aircraft, self._unit_system)
        label_width = max(len(label) for label, _ in fields)
        lines = [_clip(f"{label.ljust(label_width)} : {value}", width) for label, value in fields]
        return lines[:height]

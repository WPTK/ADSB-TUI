"""Normalize raw readsb/dump1090-fa aircraft.json data into the Aircraft/Snapshot model.

This module is pure: no curses calls, no network calls, no global state. It only turns
untrusted dicts (parsed JSON) into the dataclasses defined in model.py, and it
must never raise on malformed input -- a single bad record must not blank the whole table,
and an untrusted string must never crash curses.addstr with an embedded NUL or control
character.
"""

from __future__ import annotations

import math
import re
from typing import Any

from adsbtui.model import Aircraft, Snapshot


def sanitize(s: Any, max_len: int | None = None) -> str | None:
    """Strip NUL/control characters and surrounding whitespace from an untrusted string.

    Returns None if s is not a usable string or the cleaned result is empty. Never raises,
    even on surrogate pairs or other malformed text -- offending characters are dropped
    rather than allowed to crash the caller (e.g. curses.addstr on an embedded NUL).
    """
    if not isinstance(s, str):
        return None
    try:
        cleaned = "".join(ch for ch in s if ch.isprintable() or ch == " ")
    except Exception:
        return None
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if max_len is not None:
        cleaned = cleaned[:max_len]
    return cleaned or None


def _num(v: Any) -> float | None:
    """Coerce int/float (excluding bool) to a finite float; everything else becomes None.

    JSON `1e999` and Python's `Infinity`/`NaN` parse to non-finite floats that would sail
    through the geometry and formatting code and blow up there, so they stop here.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except OverflowError:
            return None
        return f if math.isfinite(f) else None
    return None


def _ranged(v: Any, lo: float, hi: float) -> float | None:
    """_num(v) if it lies in [lo, hi], else None."""
    n = _num(v)
    return n if n is not None and lo <= n <= hi else None


def _int(v: Any) -> int | None:
    """Coerce int/float (excluding bool) to a finite int; everything else becomes None."""
    n = _num(v)
    return None if n is None else int(n)


# Physical limits. A value outside them is a corrupt or hostile record, not an extreme
# aircraft, so the field becomes None (unknown) and the rest of the record survives.
_ALT_FT = (-2_000.0, 100_000.0)
_RATE_FPM = (-100_000.0, 100_000.0)
_GS_KT = (0.0, 3_000.0)
_RSSI_DB = (-200.0, 50.0)
_MAX_AGE_S = 1e9

_HEX_RE = re.compile(r"~?[0-9a-f]{6}")
_SQUAWK_RE = re.compile(r"[0-7]{4}")
_CATEGORY_RE = re.compile(r"[A-D][0-7]")
_SOURCE_TYPE_RE = re.compile(r"[a-z0-9_]{1,16}")
_EMERGENCY = frozenset(
    {"general", "lifeguard", "minfuel", "nordo", "unlawful", "downed", "reserved"}
)


def _match(pattern: re.Pattern[str], v: Any, *, lower: bool = False) -> str | None:
    """v if it is a string that matches pattern in full, else None. Never raises."""
    if not isinstance(v, str):
        return None
    if lower:
        v = v.lower()
    return v if pattern.fullmatch(v) else None


def _angle(v: Any) -> float | None:
    """A compass angle in degrees, folded into [0, 360), or None if out of range."""
    n = _ranged(v, 0.0, 360.0)
    return None if n is None else n % 360.0


def parse_snapshot(raw: dict, fetched_at: float) -> Snapshot:
    """Parse the top level of an aircraft.json payload into a Snapshot.

    Does not parse individual aircraft entries -- those are kept raw in raw_aircraft for
    the caller to pass through parse_aircraft() one at a time, so that one malformed entry
    can be skipped without discarding the whole snapshot.
    """
    now = _num(raw.get("now"))
    if now is None:
        now = fetched_at

    messages = _int(_ranged(raw.get("messages"), 0.0, 1e15))

    raw_aircraft = raw.get("aircraft", [])
    if not isinstance(raw_aircraft, list):
        raw_aircraft = []

    return Snapshot(
        now=now,
        messages=messages,
        raw_aircraft=raw_aircraft,
        fetched_at=fetched_at,
    )


def parse_aircraft(raw: dict) -> Aircraft | None:
    """Parse one raw aircraft.json entry into an Aircraft, or None on any error.

    This is the trust boundary: every field is validated here, so nothing downstream (the
    curses UI, the table and CSV output, the history database, webhooks) has to treat an
    Aircraft as hostile. Strings are matched against their wire format or stripped of
    control characters and capped; numbers must be finite and physically plausible. A bad
    field becomes None; only a missing or malformed hex drops the record.

    This never raises: a single malformed record is dropped by returning None instead of
    blanking the whole table, which was the original bug.
    """
    try:
        hex_val = _match(_HEX_RE, raw.get("hex"), lower=True)
        if hex_val is None:
            return None

        emergency_raw = raw.get("emergency")
        emergency = emergency_raw.lower() if isinstance(emergency_raw, str) else None
        if emergency not in _EMERGENCY:
            emergency = None

        alt_baro_raw = raw.get("alt_baro")
        on_ground = alt_baro_raw == "ground"
        altitude_ft = None if on_ground else _ranged(alt_baro_raw, *_ALT_FT)

        # A position is both coordinates or neither: half of one is a corrupt record.
        lat = _ranged(raw.get("lat"), -90.0, 90.0)
        lon = _ranged(raw.get("lon"), -180.0, 180.0)
        if lat is None or lon is None:
            lat = lon = None

        return Aircraft(
            hex=hex_val,
            is_icao=not hex_val.startswith("~"),
            flight=sanitize(raw.get("flight"), 10),
            squawk=_match(_SQUAWK_RE, raw.get("squawk")),
            emergency=emergency,
            category=_match(_CATEGORY_RE, raw.get("category")),
            altitude_ft=altitude_ft,
            on_ground=on_ground,
            geom_altitude_ft=_ranged(raw.get("alt_geom"), *_ALT_FT),
            baro_rate_fpm=_ranged(raw.get("baro_rate"), *_RATE_FPM),
            geom_rate_fpm=_ranged(raw.get("geom_rate"), *_RATE_FPM),
            ground_speed_kt=_ranged(raw.get("gs"), *_GS_KT),
            track_deg=_angle(raw.get("track")),
            true_heading_deg=_angle(raw.get("true_heading")),
            mag_heading_deg=_angle(raw.get("mag_heading")),
            lat=lat,
            lon=lon,
            seen_pos_s=_ranged(raw.get("seen_pos"), 0.0, _MAX_AGE_S),
            seen_s=_ranged(raw.get("seen"), 0.0, _MAX_AGE_S),
            source_type=_match(_SOURCE_TYPE_RE, raw.get("type")),
            rssi=_ranged(raw.get("rssi"), *_RSSI_DB),
            messages=_int(_ranged(raw.get("messages"), 0.0, 1e15)),
            registration=sanitize(raw.get("r"), 12),
            type_code=sanitize(raw.get("t"), 8),
            type_desc=sanitize(raw.get("desc"), 64),
            owner_operator=sanitize(raw.get("ownOp"), 64),
            year=sanitize(raw.get("year"), 4),
            db_flags=_int(_ranged(raw.get("dbFlags"), 0.0, 255.0)) or 0,
            nav_altitude_mcp=_ranged(raw.get("nav_altitude_mcp"), *_ALT_FT),
        )
    except Exception:
        return None

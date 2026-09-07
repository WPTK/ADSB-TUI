"""Normalize raw readsb/dump1090-fa aircraft.json data into the Aircraft/Snapshot model.

This module is pure: no curses calls, no network calls, no global state. It only turns
untrusted dicts (parsed JSON) into the dataclasses defined in model.py, and it
must never raise on malformed input -- a single bad record must not blank the whole table,
and an untrusted string must never crash curses.addstr with an embedded NUL or control
character.
"""

from __future__ import annotations

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
    """Coerce int/float (excluding bool) to float; everything else becomes None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _int(v: Any) -> int | None:
    """Coerce int/float (excluding bool) to int; everything else becomes None."""
    n = _num(v)
    return None if n is None else int(n)


def parse_snapshot(raw: dict, fetched_at: float) -> Snapshot:
    """Parse the top level of an aircraft.json payload into a Snapshot.

    Does not parse individual aircraft entries -- those are kept raw in raw_aircraft for
    the caller to pass through parse_aircraft() one at a time, so that one malformed entry
    can be skipped without discarding the whole snapshot.
    """
    now = _num(raw.get("now"))
    if now is None:
        now = fetched_at

    messages = _int(raw.get("messages"))

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

    This never raises: a single malformed record (missing keys, wrong types) is dropped
    by returning None instead of blanking the whole table, which was the original bug.
    """
    try:
        hex_val = raw.get("hex")
        if not isinstance(hex_val, str) or not hex_val:
            return None

        is_icao = not hex_val.startswith("~")

        flight = sanitize(raw.get("flight"), 10)

        squawk_raw = raw.get("squawk")
        squawk = squawk_raw if isinstance(squawk_raw, str) and squawk_raw else None

        emergency_raw = raw.get("emergency")
        emergency = (
            emergency_raw
            if isinstance(emergency_raw, str) and emergency_raw and emergency_raw != "none"
            else None
        )

        category = raw.get("category") or None

        alt_baro_raw = raw.get("alt_baro")
        if alt_baro_raw == "ground":
            on_ground = True
            altitude_ft = None
        else:
            num = _num(alt_baro_raw)
            on_ground = False
            altitude_ft = num

        registration = raw.get("r")
        registration = registration if isinstance(registration, str) and registration else None

        type_code = raw.get("t")
        type_code = type_code if isinstance(type_code, str) and type_code else None

        year = raw.get("year")
        year = year if isinstance(year, str) and year else None

        db_flags = _int(raw.get("dbFlags")) or 0

        return Aircraft(
            hex=hex_val,
            is_icao=is_icao,
            flight=flight,
            squawk=squawk,
            emergency=emergency,
            category=category,
            altitude_ft=altitude_ft,
            on_ground=on_ground,
            geom_altitude_ft=_num(raw.get("alt_geom")),
            baro_rate_fpm=_num(raw.get("baro_rate")),
            geom_rate_fpm=_num(raw.get("geom_rate")),
            ground_speed_kt=_num(raw.get("gs")),
            track_deg=_num(raw.get("track")),
            true_heading_deg=_num(raw.get("true_heading")),
            mag_heading_deg=_num(raw.get("mag_heading")),
            lat=_num(raw.get("lat")),
            lon=_num(raw.get("lon")),
            seen_pos_s=_num(raw.get("seen_pos")),
            seen_s=_num(raw.get("seen")),
            source_type=raw.get("type") if isinstance(raw.get("type"), str) else None,
            rssi=_num(raw.get("rssi")),
            messages=_int(raw.get("messages")),
            registration=registration,
            type_code=type_code,
            type_desc=sanitize(raw.get("desc")),
            owner_operator=sanitize(raw.get("ownOp")),
            year=year,
            db_flags=db_flags,
            nav_altitude_mcp=_num(raw.get("nav_altitude_mcp")),
            extra=dict(raw),
        )
    except Exception:
        return None

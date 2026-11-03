"""Sort keys for the table and the pure function that orders aircraft by one of them.

Moved out of ui/app.py unchanged (D5). No curses and no state: the fetch thread sorts today
and the main thread will, so this module must stay importable and testable on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adsbtui import alerts
from adsbtui.model import Aircraft, AlertLevel

#: Sort keys offered by the Sort screen, as (value, label) pairs. "distance", "altitude",
#: and "callsign" are kept first and spelled out (rather than the abbreviated column keys
#: below) for backward compatibility: they predate the rest of this list, and
#: DisplayConfig.sort_key's default ("distance") and any value a user already saved to
#: config.toml must keep meaning what they always have. Everything after them is every
#: other column a table row can show, so there is no visible field you cannot sort by.
SORT_KEYS: list[tuple[str, str]] = [
    ("distance", "Distance"),
    ("altitude", "Altitude"),
    ("callsign", "Callsign"),
    ("reg", "Registration"),
    ("type", "Type"),
    ("gs", "Ground speed"),
    ("vs", "Vertical speed"),
    ("brg", "Bearing"),
    ("cpa", "Closest approach"),
    ("owner", "Owner"),
    ("flags", "Flags"),
    ("age", "Position age"),
    ("alert", "Alert level"),
    ("hex", "Hex"),
]


#: AlertLevel -> severity rank, for sorting by "alert level". Built from the enum's own
#: declaration order (model.py), which is already least-to-most alarming.
_ALERT_RANK: dict[AlertLevel, int] = {level: rank for rank, level in enumerate(AlertLevel)}


def _vs_sort_value(ac: Aircraft) -> float | None:
    """Vertical rate for sorting: same barometric-preferred-over-geometric fallback the
    "vs" column's cell uses, so the order matches what the trend arrow implies."""
    return ac.baro_rate_fpm if ac.baro_rate_fpm is not None else ac.geom_rate_fpm


def _cpa_sort_value(ac: Aircraft) -> float | None:
    """Seconds to closest approach, for sorting -- soonest first by default.

    An aircraft on the ground mirrors the "cpa" column's own cell, which hides the
    projection entirely (a taxiing aircraft's extrapolated closest approach is not a real
    threat): treating it as no value here too keeps "sort by closest approach" from
    surfacing exactly the rows the table itself hides that value for.
    """
    if ac.on_ground or ac.cpa_seconds is None:
        return None
    return ac.cpa_seconds


def _owner_sort_value(ac: Aircraft) -> str | None:
    return ac.owner_name or ac.owner_operator


def _flags_sort_value(ac: Aircraft) -> str | None:
    """The same tag text the "flags" column renders, so the order matches what is shown.
    An aircraft with no tags at all sorts last rather than grouping at an arbitrary ""."""
    tags = alerts.decode_db_flags(ac.db_flags)
    category = alerts.decode_category(ac.category)
    if category:
        tags = [*tags, category]
    return " ".join(tags) or None


def _alert_sort_value(ac: Aircraft) -> int:
    return _ALERT_RANK.get(ac.alert_level, 0)


#: sort key -> a function from Aircraft to its sort value for that key. One entry per
#: SORT_KEYS value; every column a table row can show has one, so there is no field a user
#: can see but not sort by. A returned None always sorts last -- see sort_aircraft().
_SORT_KEY_FUNCS: dict[str, Callable[[Aircraft], Any]] = {
    "distance": lambda ac: ac.distance_mi,
    "altitude": lambda ac: ac.altitude_ft,
    "callsign": lambda ac: ac.display_flight,
    "reg": lambda ac: ac.registration,
    "type": lambda ac: ac.type_code,
    "gs": lambda ac: ac.ground_speed_kt,
    "vs": _vs_sort_value,
    "brg": lambda ac: ac.bearing_deg,
    "cpa": _cpa_sort_value,
    "owner": _owner_sort_value,
    "flags": _flags_sort_value,
    "age": lambda ac: ac.seen_pos_s,
    "alert": _alert_sort_value,
    "hex": lambda ac: ac.hex,
}


def _sort_key_func(sort_key: str) -> Callable[[Aircraft], Any]:
    # Any unrecognized key falls back to distance -- there must always be a sensible
    # default ordering.
    return _SORT_KEY_FUNCS.get(sort_key, _SORT_KEY_FUNCS["distance"])


def sort_aircraft(aircraft_list: list[Aircraft], sort_key: str, reverse: bool) -> list[Aircraft]:
    """Sort aircraft by cfg.display.sort_key -- any value in SORT_KEYS, i.e. any column a
    table row can show.

    Aircraft whose sort value is None always sort last, regardless of `reverse` -- an
    unknown value (no registration, no altitude, ...) is least useful information, not
    "biggest" or "smallest".
    """
    keyfunc = _sort_key_func(sort_key)
    with_value = [ac for ac in aircraft_list if keyfunc(ac) is not None]
    without_value = [ac for ac in aircraft_list if keyfunc(ac) is None]
    with_value.sort(key=keyfunc, reverse=reverse)
    return with_value + without_value

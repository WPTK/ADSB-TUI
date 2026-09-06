"""Non-curses render/run modes: table/json/csv formatters plus the --once/--watch/
--headless/--batch runners.

This module must have ZERO curses dependency, even indirectly -- it exists precisely so
that scripting/automation use of adsbtui (a cron job, a systemd unit, a shell pipeline)
never pulls in curses. It therefore does NOT import adsbtui.ui.app or adsbtui.ui.columns
(both curses-adjacent); the table formatter here duplicates a small amount of the cell
logic in ui/columns.py rather than sharing it, which is expected and fine -- see the task
docstring in that module for why it can't be reused directly (it is built around a
terminal-width-driven column layout this module has no need for).

Everything below the formatters (fetch_and_process and the four run_* functions) is the
one-shot/looping data pipeline: fetch -> normalize -> geo -> filter -> alerts.grade, with
no tracker.py or watchlist/dispatch/history wiring yet (see run_headless's docstring for
what is deliberately deferred to a later integration pass).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
import time
from collections.abc import Callable, Generator
from typing import Any, TextIO

from adsbtui import alerts, geo, normalize
from adsbtui.config import Config
from adsbtui.model import Aircraft
from adsbtui.sources import Source, SourceError
from adsbtui.units import (
    UNIT_SYSTEMS,
    format_altitude,
    format_distance,
    format_speed,
)

_LOGGER_NAME = "adsbtui"

# --------------------------------------------------------------------------------------
# Formatters
# --------------------------------------------------------------------------------------

#: Column order for aircraft_to_dict()'s output -- also used as render_csv()'s header
#: when the aircraft list is empty (DictWriter otherwise infers the header from the first
#: row, so an empty list needs this fixed fallback).
_DICT_FIELDS: list[str] = [
    "hex",
    "flight",
    "squawk",
    "emergency",
    "altitude",
    "altitude_ft",
    "ground_speed",
    "ground_speed_kt",
    "distance",
    "distance_mi",
    "bearing_deg",
    "cpa_distance_mi",
    "cpa_seconds",
    "owner_name",
    "registration",
    "type_code",
    "alert_level",
    "is_military",
    "is_pia",
    "is_ladd",
    "seen_pos_s",
]


def aircraft_to_dict(aircraft: Aircraft, unit_system: str) -> dict[str, Any]:
    """Flatten one Aircraft into a JSON-safe dict of the key fields.

    Every unit-bearing field (altitude, ground speed, distance) is emitted twice: once as
    a display string formatted for `unit_system` via units.format_*, and once as the raw
    native-unit number (feet / knots / statute miles) so a downstream tool can convert it
    itself rather than re-parsing the formatted string. Field order matches _DICT_FIELDS.
    """
    units = UNIT_SYSTEMS[unit_system]
    return {
        "hex": aircraft.hex,
        "flight": aircraft.flight,
        "squawk": aircraft.squawk,
        "emergency": aircraft.emergency,
        "altitude": format_altitude(aircraft.altitude_ft, units["altitude"], aircraft.on_ground),
        "altitude_ft": aircraft.altitude_ft,
        "ground_speed": format_speed(aircraft.ground_speed_kt, units["speed"]),
        "ground_speed_kt": aircraft.ground_speed_kt,
        "distance": format_distance(aircraft.distance_mi, units["distance"]),
        "distance_mi": aircraft.distance_mi,
        "bearing_deg": aircraft.bearing_deg,
        "cpa_distance_mi": aircraft.cpa_distance_mi,
        "cpa_seconds": aircraft.cpa_seconds,
        "owner_name": aircraft.owner_name,
        "registration": aircraft.registration,
        "type_code": aircraft.type_code,
        "alert_level": aircraft.alert_level.value,
        "is_military": aircraft.is_military,
        "is_pia": aircraft.is_pia,
        "is_ladd": aircraft.is_ladd,
        "seen_pos_s": aircraft.seen_pos_s,
    }


#: (key, header, width) for render_table()'s fixed-width plain-text columns. This is
#: intentionally simple -- a static width per column, no terminal-width-aware layout like
#: ui/columns.py's -- since non-interactive output (a pipe, a log file, a cron mailer) has
#: no "current terminal size" to lay out against.
_TABLE_COLUMNS: list[tuple[str, str, int]] = [
    ("flight", "FLIGHT", 8),
    ("hex", "HEX", 7),
    ("alt", "ALT", 9),
    ("gs", "GS", 8),
    ("dist", "DIST", 9),
    ("owner", "OWNER", 20),
    ("alert", "ALERT", 9),
]


def _table_cell(value: str, width: int) -> str:
    return value[:width].ljust(width)


def render_table(aircraft_list: list[Aircraft], unit_system: str) -> str:
    """Render a plain fixed-width text table: a header row plus one row per aircraft.

    Covers flight/hex/alt/gs/dist/owner/alert. Not responsive to terminal width -- see
    _TABLE_COLUMNS -- this is meant for piping/logging, not an interactive display.
    """
    units = UNIT_SYSTEMS[unit_system]
    header = "  ".join(_table_cell(label, width) for _, label, width in _TABLE_COLUMNS)
    lines = [header, "-" * len(header)]
    for ac in aircraft_list:
        values = {
            "flight": ac.display_flight,
            "hex": ac.hex.upper(),
            "alt": format_altitude(ac.altitude_ft, units["altitude"], ac.on_ground),
            "gs": format_speed(ac.ground_speed_kt, units["speed"]),
            "dist": format_distance(ac.distance_mi, units["distance"]),
            "owner": ac.owner_name or ac.owner_operator or "N/A",
            "alert": ac.alert_level.value,
        }
        lines.append("  ".join(_table_cell(values[key], width) for key, _, width in _TABLE_COLUMNS))
    return "\n".join(lines)


def render_json(aircraft_list: list[Aircraft], unit_system: str) -> str:
    """Render the aircraft list as an indented JSON array of aircraft_to_dict() results."""
    return json.dumps([aircraft_to_dict(ac, unit_system) for ac in aircraft_list], indent=2)


def render_csv(aircraft_list: list[Aircraft], unit_system: str) -> str:
    """Render the aircraft list as CSV text: a header row plus one row per aircraft.

    The header comes from aircraft_to_dict() of the first aircraft when the list is
    non-empty, so it always matches the row shape exactly; an empty list falls back to the
    fixed _DICT_FIELDS header so callers still get a well-formed (header-only) CSV.
    """
    buf = io.StringIO()
    if aircraft_list:
        fieldnames = list(aircraft_to_dict(aircraft_list[0], unit_system).keys())
    else:
        fieldnames = list(_DICT_FIELDS)
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for ac in aircraft_list:
        writer.writerow(aircraft_to_dict(ac, unit_system))
    return buf.getvalue()


def render(aircraft_list: list[Aircraft], unit_system: str, fmt: str) -> str:
    """Dispatch to render_table/render_json/render_csv based on fmt.

    Raises ValueError for any fmt other than "table", "json", or "csv".
    """
    if fmt == "table":
        return render_table(aircraft_list, unit_system)
    if fmt == "json":
        return render_json(aircraft_list, unit_system)
    if fmt == "csv":
        return render_csv(aircraft_list, unit_system)
    raise ValueError(f"unknown output format {fmt!r} (want 'table', 'json', or 'csv')")


# --------------------------------------------------------------------------------------
# Fetch + process pipeline
# --------------------------------------------------------------------------------------


def _is_watched_emergency(cfg: Config, aircraft: Aircraft) -> bool:
    """Lightweight pre-check mirroring alerts.grade()'s first branch, used only to decide
    whether an aircraft should bypass the radius filter -- the authoritative alert_level
    (including EMERGENCY) is still assigned afterwards by alerts.grade() itself."""
    if not cfg.alerts.emergency:
        return False
    watched_squawks = {str(s) for s in cfg.alerts.squawks}
    if aircraft.squawk is not None and aircraft.squawk in watched_squawks:
        return True
    return aircraft.emergency is not None


def fetch_and_process(source: Source, cfg: Config) -> tuple[list[Aircraft], SourceError | None]:
    """One fetch-and-process cycle: fetch, normalize, geo-derive, radius-filter, grade.

    Returns (aircraft_list, None) on success. Any adsbtui.sources.SourceError raised by
    the fetch (SourceUnreachable, SourceTimeout, SourceInvalidData) is caught and returned
    as (empty_list, that_exception) instead of propagating -- an expected data/network
    failure, not a programming error. Any other exception type is a programming error and
    is left to propagate uncaught.
    """
    try:
        raw = source.fetch(cfg.source.timeout_s)
    except SourceError as exc:
        return [], exc

    fetched_at = time.time()
    snapshot = normalize.parse_snapshot(raw, fetched_at)

    aircraft_list: list[Aircraft] = []
    for raw_ac in snapshot.raw_aircraft:
        ac = normalize.parse_aircraft(raw_ac)
        if ac is None:
            continue
        aircraft_list.append(ac)

    home_lat = cfg.home.lat
    home_lon = cfg.home.lon
    for ac in aircraft_list:
        if ac.lat is None or ac.lon is None:
            continue
        ac.distance_mi = geo.haversine_mi(home_lat, home_lon, ac.lat, ac.lon)
        ac.bearing_deg = geo.initial_bearing_deg(home_lat, home_lon, ac.lat, ac.lon)
        cpa_dist, cpa_secs = geo.closest_point_of_approach(
            home_lat, home_lon, ac.lat, ac.lon, ac.track_deg, ac.ground_speed_kt
        )
        ac.cpa_distance_mi = cpa_dist
        ac.cpa_seconds = cpa_secs

    radius = cfg.filter.radius
    ignore_radius_for_emergency = cfg.alerts.emergency_ignore_radius
    filtered: list[Aircraft] = []
    for ac in aircraft_list:
        within_radius = ac.distance_mi is not None and ac.distance_mi <= radius
        if within_radius or (ignore_radius_for_emergency and _is_watched_emergency(cfg, ac)):
            filtered.append(ac)

    for ac in filtered:
        ac.alert_level = alerts.grade(
            ac,
            squawks=cfg.alerts.squawks,
            emergency_enabled=cfg.alerts.emergency,
            proximity_mi=cfg.filter.proximity,
            cpa_enabled=cfg.alerts.cpa_enabled,
            cpa_distance_mi=cfg.alerts.cpa_distance,
            cpa_horizon_s=cfg.alerts.cpa_horizon_s,
            cpa_min_gs_kt=cfg.alerts.cpa_min_gs_kt,
        )

    return filtered, None


# --------------------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------------------


def run_once(cfg: Config, source: Source, fmt: str, out: TextIO = sys.stdout) -> int:
    """Fetch once, print the rendered result to `out`, and return an exit code.

    Returns 0 on success. On a fetch/data error, prints "ERROR: <message>" to sys.stderr
    and returns 4 instead of printing anything to `out`.
    """
    aircraft_list, err = fetch_and_process(source, cfg)
    if err is not None:
        print(f"ERROR: {err}", file=sys.stderr)
        return 4
    print(render(aircraft_list, cfg.display.units, fmt), file=out)
    return 0


def run_watch(
    cfg: Config,
    source: Source,
    fmt: str,
    interval_s: float,
    iterations: int | None = None,
    out: TextIO = sys.stdout,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Like run_once, but loops: fetch, print, sleep, repeat.

    A timestamped separator line is printed to `out` between iterations (not before the
    first one). Sleeping between iterations goes through the injectable `sleep_fn` so
    tests can avoid real wall-clock delays. Stops after `iterations` loops if given; None
    means loop forever (callers that want a bounded test run must pass a finite value).

    Returns 0 if at least one iteration succeeded, 4 if every iteration errored (each
    error is still printed as "ERROR: <message>" to sys.stderr as it happens).
    """
    any_success = False
    i = 0
    while iterations is None or i < iterations:
        if i > 0:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"--- {stamp} ---", file=out)

        aircraft_list, err = fetch_and_process(source, cfg)
        if err is not None:
            print(f"ERROR: {err}", file=sys.stderr)
        else:
            any_success = True
            print(render(aircraft_list, cfg.display.units, fmt), file=out)

        i += 1
        if iterations is None or i < iterations:
            sleep_fn(interval_s)

    return 0 if any_success else 4


def run_headless(
    cfg: Config,
    source: Source,
    stop_predicate: Callable[[], bool],
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Background-service loop: fetch and log, with NO printing at all.

    Loops until `stop_predicate()` returns True, checked once at the top of every
    iteration -- an injectable callable so tests can stop the loop after N calls without a
    real background process. Each cycle's result is logged via
    logging.getLogger("adsbtui").info(...); sleeping between cycles uses cfg.source.refresh_s
    through the injectable `sleep_fn`.

    This is intentionally minimal: it is only the fetch-log-sleep loop. Watchlist
    matching, alert dispatch (webhook/desktop/command), and history-database writes are
    NOT wired in here yet -- that is deferred to a later integration pass that sits on top
    of this loop's fetch_and_process() results.

    Always returns 0 -- this loop's job is to run until told to stop, not to report a
    pass/fail exit code the way run_once/run_watch do.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    while not stop_predicate():
        aircraft_list, err = fetch_and_process(source, cfg)
        if err is not None:
            logger.info("headless fetch failed: %s", err)
        else:
            logger.info("headless fetch ok: %d aircraft tracked", len(aircraft_list))
        sleep_fn(cfg.source.refresh_s)
    return 0


def run_batch(
    cfg: Config,
    source: Source,
    diff_only: bool,
    sleep_fn: Callable[[float], None] = time.sleep,
    iterations: int | None = None,
) -> Generator[str, None, None]:
    """Generator yielding one line per event, for scripting/piping (e.g. `adsbtui --batch`).

    On the first iteration: if `diff_only` is False, yields one "SEEN <hex> <flight>
    <dist>" line per currently-tracked aircraft; if `diff_only` is True, nothing is
    yielded for the first iteration (there is nothing to diff against yet).

    On every iteration (including transitions found on the second iteration onward),
    compares this iteration's hex set against the previous iteration's and yields one
    "NEW <hex> <flight> <dist>" line per arrival and one "LOST <hex>" line per departure.
    A fetch/data error yields a single "ERROR <message>" line for that iteration instead.

    Sleeps between iterations via the injectable `sleep_fn`, using cfg.source.refresh_s.
    Stops after `iterations` loops if given; None means loop forever.
    """
    unit = UNIT_SYSTEMS[cfg.display.units]["distance"]
    previous_hexes: set[str] | None = None
    i = 0
    while iterations is None or i < iterations:
        aircraft_list, err = fetch_and_process(source, cfg)
        if err is not None:
            yield f"ERROR {err}"
        else:
            current = {ac.hex: ac for ac in aircraft_list}
            current_hexes = set(current.keys())

            if previous_hexes is None:
                if not diff_only:
                    for ac in aircraft_list:
                        dist = format_distance(ac.distance_mi, unit)
                        yield f"SEEN {ac.hex} {ac.display_flight} {dist}"
            else:
                for h in sorted(current_hexes - previous_hexes):
                    ac = current[h]
                    dist = format_distance(ac.distance_mi, unit)
                    yield f"NEW {h} {ac.display_flight} {dist}"
                for h in sorted(previous_hexes - current_hexes):
                    yield f"LOST {h}"

            previous_hexes = current_hexes

        i += 1
        if iterations is None or i < iterations:
            sleep_fn(cfg.source.refresh_s)

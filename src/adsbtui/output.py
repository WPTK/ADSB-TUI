"""Non-curses render/run modes: table/json/csv formatters plus the --once/--watch/
--headless/--batch runners.

This module must have ZERO curses dependency, even indirectly -- it exists precisely so
that scripting/automation use of adsbtui (a cron job, a systemd unit, a shell pipeline)
never pulls in curses. It therefore does NOT import adsbtui.ui.app or adsbtui.ui.columns
(both curses-adjacent); the table formatter here duplicates a small amount of the cell
logic in ui/columns.py rather than sharing it, which is expected and fine -- see the task
docstring in that module for why it can't be reused directly (it is built around a
terminal-width-driven column layout this module has no need for).

Below the formatters are two pipelines, both curses-free:

  * fetch_and_process() and the pure helpers it composes (parse_aircraft_list, derive_geo,
    apply_filters, grade_all) -- stateless, one snapshot in, a graded aircraft list out.
    This is what --once/--watch/--batch use.
  * HeadlessRunner -- the same steps plus the cross-tick state a background service needs:
    tracker.update_tracks(), watchlist matching, alert dispatch, and sighting history.
    This is what --headless uses, and it is why running adsbtui as a systemd unit actually
    sends the notification it advertises.

HeadlessRunner deliberately duplicates ~40 lines of ui/app.py's fetch thread rather than
being imported by it: ui/app.py cannot be imported from here (it imports curses at module
level), so the alternative would be app.py importing this module -- a worthwhile cleanup,
but one that has to happen in app.py, not here.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, TextIO

from adsbtui import alerts, dispatch, geo, history, normalize, tracker
from adsbtui import watchlist as watchlist_mod
from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel, Snapshot
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
# Shared per-tick pipeline
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


def load_registry(cfg: Config, logger: logging.Logger | None = None) -> dict[str, str]:
    """Load the FAA owner registry named by cfg.registry.path, once.

    Returns {} when no path is configured, or when the file cannot be read (logged as a
    warning) -- an unreadable registry costs owner names, which is not a reason to refuse
    to run. Callers load this ONCE and pass the result into the pipeline for every tick;
    the CSV is far too big to re-read every few seconds.
    """
    if not cfg.registry.path:
        return {}
    path = os.path.expanduser(cfg.registry.path)
    log = logger if logger is not None else logging.getLogger(_LOGGER_NAME)
    try:
        return normalize.load_owner_registry(path)
    except OSError as exc:
        log.warning("could not load registry %s: %s", path, exc)
        return {}


def parse_aircraft_list(
    snapshot: Snapshot, registry: dict[str, str] | None = None
) -> list[Aircraft]:
    """Normalize a Snapshot's raw entries into Aircraft, dropping unparseable ones.

    When `registry` is non-empty, an aircraft whose (uppercased) hex is in it gets its
    owner_name filled in -- which is what makes 'owner:' watchlist patterns and the OWNER
    column mean anything outside the TUI.
    """
    aircraft_list: list[Aircraft] = []
    for raw_ac in snapshot.raw_aircraft:
        ac = normalize.parse_aircraft(raw_ac)
        if ac is None:
            continue
        if registry:
            name = registry.get(ac.hex.upper())
            if name:
                ac.owner_name = name
        aircraft_list.append(ac)
    return aircraft_list


def derive_geo(aircraft_list: list[Aircraft], cfg: Config) -> None:
    """Fill in distance/bearing/elevation/CPA on every aircraft that has a position.

    Mutates the Aircraft objects in place (they were just created by
    parse_aircraft_list(), so nobody else holds a reference yet). Aircraft with no lat/lon
    are left untouched: their distance_mi stays None, which the radius filter then drops.
    """
    home_lat = cfg.home.lat
    home_lon = cfg.home.lon
    for ac in aircraft_list:
        if ac.lat is None or ac.lon is None:
            continue
        ac.distance_mi = geo.haversine_mi(home_lat, home_lon, ac.lat, ac.lon)
        ac.bearing_deg = geo.initial_bearing_deg(home_lat, home_lon, ac.lat, ac.lon)
        # Barometric altitude preferred, geometric as a fallback, ground level if neither:
        # an elevation angle of 0 for an unknown altitude beats no elevation at all.
        if ac.altitude_ft is not None:
            alt_for_elev = ac.altitude_ft
        elif ac.geom_altitude_ft is not None:
            alt_for_elev = ac.geom_altitude_ft
        else:
            alt_for_elev = 0.0
        ac.elevation_deg = geo.elevation_angle_deg(ac.distance_mi, alt_for_elev)
        cpa_dist, cpa_secs = geo.closest_point_of_approach(
            home_lat, home_lon, ac.lat, ac.lon, ac.track_deg, ac.ground_speed_kt
        )
        ac.cpa_distance_mi = cpa_dist
        ac.cpa_seconds = cpa_secs


def apply_filters(aircraft_list: list[Aircraft], cfg: Config) -> list[Aircraft]:
    """Apply every [filter] setting: radius, hide_ground, include_nonicao, altitude band.

    This mirrors ui/app.py's _apply_filters() minus the live search box (there is no search
    box without a keyboard). An aircraft graded as an emergency bypasses the radius check
    (and only that check) when alerts.emergency_ignore_radius is set: a 7700 squawk 20
    miles out is exactly the thing you do not want filtered away.
    """
    f = cfg.filter
    ignore_radius_for_emergency = cfg.alerts.emergency_ignore_radius

    kept: list[Aircraft] = []
    for ac in aircraft_list:
        within_radius = ac.distance_mi is not None and ac.distance_mi <= f.radius
        if not within_radius and not (
            ignore_radius_for_emergency and _is_watched_emergency(cfg, ac)
        ):
            continue
        if f.hide_ground and ac.on_ground:
            continue
        if not f.include_nonicao and not ac.is_icao:
            continue
        if ac.altitude_ft is not None and not (f.min_alt_ft <= ac.altitude_ft <= f.max_alt_ft):
            continue
        kept.append(ac)
    return kept


def grade_all(aircraft_list: list[Aircraft], cfg: Config) -> None:
    """Set alert_level on every aircraft from the [alerts]/[filter] config, in place."""
    for ac in aircraft_list:
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


def fetch_and_process(
    source: Source,
    cfg: Config,
    registry: dict[str, str] | None = None,
) -> tuple[list[Aircraft], SourceError | None]:
    """One stateless fetch-and-process cycle: fetch, normalize, geo-derive, filter, grade.

    Stateless is the point: there is no tracker/dispatch/history state here, so this is
    what the one-shot and print-loop modes (run_once/run_watch/run_batch) use. The
    stateful, service-shaped pipeline lives in HeadlessRunner below.

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

    snapshot = normalize.parse_snapshot(raw, time.time())
    aircraft_list = parse_aircraft_list(snapshot, registry)
    derive_geo(aircraft_list, cfg)
    filtered = apply_filters(aircraft_list, cfg)
    grade_all(filtered, cfg)
    return filtered, None


# --------------------------------------------------------------------------------------
# The headless service pipeline
# --------------------------------------------------------------------------------------


@dataclass
class TickResult:
    """What one HeadlessRunner.tick() produced.

    `aircraft` is every currently tracked aircraft (including ones lingering after a
    dropout), `events` the alert events handed to the dispatcher this tick, and
    `closed_tracks` the number of sighting rows written because a track ended. On a fetch
    failure `error` is the SourceError and the other three fields are empty/zero.
    """

    aircraft: list[Aircraft]
    events: list[dispatch.AlertEvent]
    closed_tracks: int
    error: SourceError | None = None


class HeadlessRunner:
    """The full poll-and-notify pipeline, with no terminal and no curses anywhere.

    This is what makes 'adsbtui --headless' a real background service rather than a fetch
    loop that logs: it does the same work per tick that ui/app.py's fetch thread does --
    normalize, geo, watchlist match, filter, tracker.update_tracks, alerts.grade, alert
    dispatch, sighting history -- and none of the drawing.

    Deliberately NOT shared with ui/app.py: that module keeps its own copy of this
    pipeline. See this module's docstring for why.

    The dispatcher is injectable so tests can assert on alert events without anything
    actually beeping, POSTing, or exec'ing; production gets a real dispatch.Dispatcher
    with the real default channels. A headless service *should* ring the webhook and run
    the command -- those are the whole point of running it on a Pi -- and the terminal
    bell channel degrades to a harmless no-op when there is no terminal attached.
    """

    def __init__(
        self,
        cfg: Config,
        source: Source,
        *,
        dispatcher: dispatch.Dispatcher | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.cfg = cfg
        self.source = source
        self._logger = logger if logger is not None else logging.getLogger(_LOGGER_NAME)

        # Loaded once at startup, not per tick: both are files on disk that do not change
        # while the service runs, and the registry CSV is large.
        self._registry = load_registry(cfg, self._logger)
        self._watch_entries: list[watchlist_mod.WatchEntry] = []
        if cfg.watchlist.path:
            self._watch_entries = watchlist_mod.load(os.path.expanduser(cfg.watchlist.path))

        self._dispatcher = dispatcher if dispatcher is not None else dispatch.Dispatcher(cfg.alerts)

        #: hex -> the level it was last graded at, so transitions can be detected.
        self._prev_levels: dict[str, AlertLevel] = {}
        #: hex -> last tick's tracked Aircraft, threaded through tracker.update_tracks().
        self._tracked: dict[str, Aircraft] = {}
        #: hex -> running per-track aggregates, written out when the track closes.
        self._track_stats: dict[str, dict[str, Any]] = {}
        self._history_db = self._open_history()

    def _open_history(self) -> str:
        """Prepare the sightings database, returning its path or "" if unavailable.

        A database that cannot be opened disables history for the whole run (logged once)
        rather than raising on every closed track for the life of the service.
        """
        if not self.cfg.history.db:
            return ""
        db_path = os.path.expanduser(self.cfg.history.db)
        try:
            history.ensure_schema(db_path)
            removed = history.prune_old(db_path, self.cfg.history.retention_days, time.time())
        except (sqlite3.Error, OSError) as exc:
            self._logger.warning("history disabled, could not open %s: %s", db_path, exc)
            return ""
        if removed:
            self._logger.info("pruned %d sighting(s) past retention", removed)
        return db_path

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------

    def tick(self) -> TickResult:
        """Run one full cycle and return what happened.

        Fetch failures are returned as TickResult.error rather than raised: a background
        service must survive a receiver reboot, and the caller decides how loudly to
        complain about it.
        """
        try:
            raw = self.source.fetch(self.cfg.source.timeout_s)
        except SourceError as exc:
            return TickResult(aircraft=[], events=[], closed_tracks=0, error=exc)

        fetched_at = time.time()
        snapshot = normalize.parse_snapshot(raw, fetched_at)
        aircraft_list = parse_aircraft_list(snapshot, self._registry)
        derive_geo(aircraft_list, self.cfg)

        if self._watch_entries:
            for ac in aircraft_list:
                ac.is_watched = watchlist_mod.matches(ac, self._watch_entries) is not None

        filtered = apply_filters(aircraft_list, self.cfg)

        # Tracking must run on the filtered set (the same set the TUI shows), so a track
        # "closes" when the aircraft leaves the configured radius, not only when the feed
        # forgets it.
        self._tracked = tracker.update_tracks(
            self._tracked,
            {ac.hex: ac for ac in filtered},
            now=fetched_at,
            stale_after_s=self.cfg.display.stale_after_s,
            linger_s=self.cfg.display.linger_s,
        )

        graded = list(self._tracked.values())
        grade_all(graded, self.cfg)

        events = self._dispatch_alerts(graded)
        closed = self._record_tracks(self._tracked, fetched_at)
        return TickResult(aircraft=graded, events=events, closed_tracks=closed, error=None)

    def _dispatch_alerts(self, graded: list[Aircraft]) -> list[dispatch.AlertEvent]:
        """Fire notifications for alert-level transitions and watchlist hits.

        Returns every event handed to the dispatcher (the dispatcher itself may still
        swallow one under a cooldown or quiet hours). Each is logged at INFO so a
        journalctl reader can see exactly what the service reacted to.
        """
        events: list[dispatch.AlertEvent] = []
        seen: set[str] = set()
        for ac in graded:
            seen.add(ac.hex)
            previous = self._prev_levels.get(ac.hex, AlertLevel.NONE)
            event = dispatch.build_event(ac, ac.alert_level, previous, self.cfg.alerts.events)
            if event is None and ac.is_watched and previous == AlertLevel.NONE:
                # A watchlist hit is not an AlertLevel transition, so it goes through the
                # same gate under its own event name.
                event = dispatch.build_event(
                    ac,
                    ac.alert_level,
                    previous,
                    self.cfg.alerts.events,
                    event_name_override="watchlist",
                )
            self._prev_levels[ac.hex] = ac.alert_level
            if event is None:
                continue

            events.append(event)
            self._logger.info(
                "alert %s: %s %s at %s, %s",
                event.event_name,
                ac.hex,
                ac.display_flight,
                _distance_text(ac, self.cfg.display.units),
                _altitude_text(ac, self.cfg.display.units),
            )
            try:
                self._dispatcher.maybe_fire(event)
            except Exception as exc:  # never let a notification kill the service loop
                self._logger.warning("alert dispatch failed for %s: %s", ac.hex, exc)

        for gone in [h for h in self._prev_levels if h not in seen]:
            del self._prev_levels[gone]
        return events

    def _record_tracks(self, tracked: dict[str, Aircraft], now: float) -> int:
        """Accumulate per-track aggregates, writing a sighting row when a track drops.

        tracker.update_tracks() ages a vanished aircraft out after the linger window, so
        "no longer in tracked" is exactly the moment its track ended and the summary can
        be persisted. Returns the number of rows written this tick.
        """
        if not self._history_db:
            return 0

        for hexid, ac in tracked.items():
            stats = self._track_stats.get(hexid)
            if stats is None:
                stats = {
                    "first_seen": ac.first_seen or now,
                    "min_distance_mi": None,
                    "min_distance_at": None,
                    "max_altitude_ft": None,
                    "max_gs_kt": None,
                    "squawks": set(),
                    "had_emergency": False,
                    "aircraft": ac,
                }
                self._track_stats[hexid] = stats

            stats["aircraft"] = ac
            stats["last_seen"] = ac.last_seen or now
            if ac.distance_mi is not None and (
                stats["min_distance_mi"] is None or ac.distance_mi < stats["min_distance_mi"]
            ):
                stats["min_distance_mi"] = ac.distance_mi
                stats["min_distance_at"] = now
            if ac.altitude_ft is not None and (
                stats["max_altitude_ft"] is None or ac.altitude_ft > stats["max_altitude_ft"]
            ):
                stats["max_altitude_ft"] = ac.altitude_ft
            if ac.ground_speed_kt is not None and (
                stats["max_gs_kt"] is None or ac.ground_speed_kt > stats["max_gs_kt"]
            ):
                stats["max_gs_kt"] = ac.ground_speed_kt
            if ac.squawk:
                stats["squawks"].add(ac.squawk)
            if ac.alert_level == AlertLevel.EMERGENCY:
                stats["had_emergency"] = True

        written = 0
        for hexid in [h for h in self._track_stats if h not in tracked]:
            stats = self._track_stats.pop(hexid)
            try:
                history.record_close(
                    self._history_db,
                    stats["aircraft"],
                    first_seen=stats["first_seen"],
                    last_seen=stats.get("last_seen", now),
                    min_distance_mi=stats["min_distance_mi"],
                    min_distance_at=stats["min_distance_at"],
                    max_altitude_ft=stats["max_altitude_ft"],
                    max_gs_kt=stats["max_gs_kt"],
                    squawks_seen=sorted(stats["squawks"]),
                    had_emergency=stats["had_emergency"],
                )
                written += 1
            except sqlite3.Error as exc:
                self._logger.warning("could not record sighting for %s: %s", hexid, exc)
        return written

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    def run(
        self,
        stop_predicate: Callable[[], bool],
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> int:
        """Tick, log, sleep, repeat until stop_predicate() returns True. Always returns 0.

        stop_predicate is checked once at the top of every iteration, so a caller holding
        a signal-set threading.Event stops within one refresh interval. A fetch failure is
        logged and retried on the next cycle rather than ending the loop -- so is any
        unexpected exception, since a background service that dies on one malformed
        snapshot is worse than one that logs it and carries on.
        """
        while not stop_predicate():
            try:
                result = self.tick()
            except Exception as exc:
                # A service on a Pi must outlive one bad tick: the TUI's fetch thread
                # takes the same view (ui/app.py catches here too), and the loop sleeps
                # afterwards either way, so this cannot spin.
                self._logger.exception("unexpected error in headless cycle: %s", exc)
                sleep_fn(self.cfg.source.refresh_s)
                continue

            if result.error is not None:
                self._logger.warning("fetch failed: %s", result.error)
            else:
                self._logger.info(
                    "cycle: %d aircraft, %d alert(s), %d watched, %d sighting(s) recorded",
                    len(result.aircraft),
                    len(result.events),
                    sum(1 for ac in result.aircraft if ac.is_watched),
                    result.closed_tracks,
                )
            sleep_fn(self.cfg.source.refresh_s)
        return 0


def _distance_text(aircraft: Aircraft, unit_system: str) -> str:
    return format_distance(aircraft.distance_mi, UNIT_SYSTEMS[unit_system]["distance"])


def _altitude_text(aircraft: Aircraft, unit_system: str) -> str:
    return format_altitude(
        aircraft.altitude_ft, UNIT_SYSTEMS[unit_system]["altitude"], aircraft.on_ground
    )


# --------------------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------------------


def run_once(cfg: Config, source: Source, fmt: str, out: TextIO = sys.stdout) -> int:
    """Fetch once, print the rendered result to `out`, and return an exit code.

    Returns 0 on success. On a fetch/data error, prints "ERROR: <message>" to sys.stderr
    and returns 4 instead of printing anything to `out`.
    """
    aircraft_list, err = fetch_and_process(source, cfg, registry=load_registry(cfg))
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
    registry = load_registry(cfg)
    any_success = False
    i = 0
    while iterations is None or i < iterations:
        if i > 0:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"--- {stamp} ---", file=out)

        aircraft_list, err = fetch_and_process(source, cfg, registry=registry)
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
    """Background-service loop: the full pipeline, logged rather than printed.

    Every cycle fetches, normalizes, derives geometry, matches the watchlist loaded at
    startup, applies the [filter] settings, updates cross-tick tracks, grades alerts,
    dispatches notifications for level transitions and watchlist hits, and records closed
    tracks to cfg.history.db when one is configured. Nothing is printed: a one-line INFO
    summary per cycle plus one INFO line per alert event go to the "adsbtui" logger, which
    is where a systemd unit's journal reads them from.

    Loops until `stop_predicate()` returns True, checked once at the top of every
    iteration -- an injectable callable so tests can stop the loop after N calls without a
    real background process. Sleeping between cycles uses cfg.source.refresh_s through the
    injectable `sleep_fn`.

    This is a thin wrapper around HeadlessRunner; construct that directly if you need to
    inject a dispatcher or drive single ticks.

    Always returns 0 -- this loop's job is to run until told to stop, not to report a
    pass/fail exit code the way run_once/run_watch do.
    """
    return HeadlessRunner(cfg, source).run(stop_predicate, sleep_fn=sleep_fn)


def run_batch(
    cfg: Config,
    source: Source,
    diff_only: bool,
    sleep_fn: Callable[[float], None] = time.sleep,
    iterations: int | None = None,
) -> Generator[str, None, None]:
    """Generator yielding one line per event, for scripting/piping (e.g. adsbtui --batch).

    On the first iteration: if `diff_only` is False, yields one "SEEN <hex> <flight>
    <dist>" line per currently-tracked aircraft; if `diff_only` is True, nothing is
    yielded for the first iteration (there is nothing to diff against yet).

    On every iteration (including transitions found on the second iteration onward),
    compares this iteration's hex set against the previous iteration's and yields one
    "NEW <hex> <flight> <dist>" line per arrival and one "LOST <hex>" line per departure,
    followed by one "ALERT <hex> <level> <flight> <dist>" line per aircraft that just
    transitioned into a non-NONE alert level (an emergency squawk, an overhead pass, a
    projected close approach) -- the lines a consumer piping this into a script actually
    wants to act on. Unlike headless mode, no webhook/command/bell ever fires here: the
    pipe IS the notification channel.

    A fetch/data error yields a single "ERROR <message>" line for that iteration instead.

    Sleeps between iterations via the injectable `sleep_fn`, using cfg.source.refresh_s.
    Stops after `iterations` loops if given; None means loop forever.
    """
    unit = UNIT_SYSTEMS[cfg.display.units]["distance"]
    registry = load_registry(cfg)
    previous_hexes: set[str] | None = None
    previous_levels: dict[str, AlertLevel] = {}
    i = 0
    while iterations is None or i < iterations:
        aircraft_list, err = fetch_and_process(source, cfg, registry=registry)
        if err is not None:
            yield f"ERROR {err}"
        else:
            current = {ac.hex: ac for ac in aircraft_list}
            current_hexes = set(current.keys())
            first_iteration = previous_hexes is None

            if first_iteration:
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

            # The first iteration of a diff_only run stays silent, alerts included: the
            # whole point of diff_only is that nothing is reported until there is a
            # previous state to compare against.
            if not (first_iteration and diff_only):
                for h in sorted(current_hexes):
                    ac = current[h]
                    level = ac.alert_level
                    if level == AlertLevel.NONE:
                        continue
                    if previous_levels.get(h, AlertLevel.NONE) == level:
                        continue
                    dist = format_distance(ac.distance_mi, unit)
                    yield f"ALERT {h} {level.value} {ac.display_flight} {dist}"

            previous_hexes = current_hexes
            previous_levels = {h: ac.alert_level for h, ac in current.items()}

        i += 1
        if iterations is None or i < iterations:
            sleep_fn(cfg.source.refresh_s)

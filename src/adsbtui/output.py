"""Non-curses render/run modes: table/json/csv formatters plus the --once/--watch/
--headless/--batch runners.

This module must have ZERO curses dependency, even indirectly -- it exists precisely so
that scripting/automation use of adsbtui (a cron job, a systemd unit, a shell pipeline)
never pulls in curses. It therefore does NOT import adsbtui.ui.app or adsbtui.ui.columns
(both curses-adjacent); the table formatter here duplicates a small amount of the cell
logic in ui/columns.py rather than sharing it, which is expected and fine -- see the task
docstring in that module for why it can't be reused directly (it is built around a
terminal-width-driven column layout this module has no need for).

Below the formatters: fetch_and_process() is the stateless one-snapshot path that
--once/--watch/--batch use, and HeadlessRunner wraps the shared pipeline.Pipeline with a
fetch loop, registry refresh and logging for --headless. The per-tick rules themselves
(filters, tracking, grading, alerts, history) live in adsbtui.pipeline, which the TUI also
calls, so there is one copy.
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
from typing import Any, TextIO

from adsbtui import dispatch, enrich, normalize, pipeline, textwidth
from adsbtui import watchlist as watchlist_mod
from adsbtui.config import Config
from adsbtui.enrich import registry_update
from adsbtui.model import Aircraft, AlertLevel
from adsbtui.pipeline import (
    TickResult,
    apply_filters,
    derive_geo,
    enrich_all,
    grade_all,
    parse_aircraft_list,
)
from adsbtui.sources import Source, SourceError
from adsbtui.units import (
    UNIT_SYSTEMS,
    format_altitude,
    format_distance,
    format_speed,
)

_LOGGER_NAME = "adsbtui"

#: How often the headless service re-checks whether the registry has gone stale. Far more
#: frequent than the daily refresh it guards, and cheap enough to be invisible.
REGISTRY_CHECK_INTERVAL_S = 3600.0

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
    return textwidth.ljust(value, width)


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
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
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


def registry_is_due(cfg: Config) -> bool:
    """True when the registry database is missing or older than registry.max_age_days.

    Shared by the TUI and the headless service so both answer "is a refresh due" the same
    way. auto_update off means never due, whatever the age.
    """
    if not cfg.registry.auto_update:
        return False
    db_path = os.path.expanduser(cfg.registry.db or "")
    if not db_path:
        return False
    if not os.path.exists(db_path):
        return True
    try:
        meta = registry_update.read_meta(db_path)
    except sqlite3.Error:
        return True
    if not meta:
        return True
    max_age_s = max(0.0, float(cfg.registry.max_age_days) * 86400.0)
    return all(entry.age_s() >= max_age_s for entry in meta.values())


def refresh_registry(cfg: Config, logger: logging.Logger | None = None) -> bool:
    """Download and rebuild the registry. True if it now holds fresh data.

    Synchronous on purpose: the only caller is the headless service, which has no display
    to keep responsive, and starting up with owner data already resolved beats serving a
    few minutes of "N/A" while a thread catches up. Every failure is logged and swallowed
    -- a service must not refuse to run because a download failed.
    """
    log = logger if logger is not None else logging.getLogger(_LOGGER_NAME)
    db_path = os.path.expanduser(cfg.registry.db)
    try:
        result = registry_update.build_from_tar1090(registry_update.TAR1090_URL, db_path)
    except Exception as exc:
        log.warning("registry update failed: %s", exc)
        return False
    if result.not_modified:
        log.info("registry already current")
    else:
        log.info("registry updated: %d rows", result.row_count)
    return True


def build_enricher(cfg: Config, logger: logging.Logger | None = None) -> enrich.Enricher:
    """Build the registration/type/owner lookup chain for a non-curses run.

    Built once by the caller and reused for every tick: the SQLite handle and the
    per-address cache are what keep enrichment off the per-poll critical path. A missing
    database is not an error -- the feed's own fields and the derived N-number/country
    still resolve, which is why this always returns a usable Enricher.
    """
    log = logger if logger is not None else logging.getLogger(_LOGGER_NAME)
    db_path = os.path.expanduser(cfg.registry.db or "")
    if db_path and not os.path.exists(db_path):
        log.info("no registry database at %s; using feed and derived data only", db_path)
        db_path = ""
    try:
        return enrich.Enricher.from_paths(db_path or None)
    except sqlite3.Error as exc:
        log.warning("registry database unusable (%s): %s", db_path, exc)
        return enrich.Enricher.from_paths(None)


def fetch_and_process(
    source: Source,
    cfg: Config,
    enricher: enrich.Enricher | None = None,
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
    aircraft_list = parse_aircraft_list(snapshot)
    if enricher is not None:
        enrich_all(aircraft_list, enricher)
    derive_geo(aircraft_list, cfg)
    filtered = apply_filters(aircraft_list, cfg)
    grade_all(filtered, cfg)
    return filtered, None


# --------------------------------------------------------------------------------------
# The headless service pipeline
# --------------------------------------------------------------------------------------


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

        # A service that runs for weeks has to keep the registry current itself, and one
        # starting on a fresh machine has no registry at all -- so the check happens here
        # and then on the interval below, not only once at install time.
        self._registry_checked_at = 0.0
        self._refresh_registry_if_due()
        self._enricher = build_enricher(cfg, self._logger)
        self._watch_entries: list[watchlist_mod.WatchEntry] = []
        if cfg.watchlist.path:
            self._watch_entries = watchlist_mod.load(os.path.expanduser(cfg.watchlist.path))

        self._dispatcher = dispatcher if dispatcher is not None else dispatch.Dispatcher(cfg.alerts)
        self._pipeline = pipeline.Pipeline(
            cfg,
            enricher=self._enricher,
            watch_entries=self._watch_entries,
            notify=self._dispatcher.maybe_fire,
            logger=self._logger,
        )

    def _refresh_registry_if_due(self) -> bool:
        """Rebuild the registry when due, rate-limiting the staleness check itself.

        The check touches SQLite, so running it on every poll of a 5-second loop would be
        thousands of pointless queries an hour; once an hour is far more often than a
        daily refresh needs. Returns True when the database was actually rebuilt.
        """
        now = time.time()
        if now - self._registry_checked_at < REGISTRY_CHECK_INTERVAL_S:
            return False
        self._registry_checked_at = now
        if not registry_is_due(self.cfg):
            return False
        return refresh_registry(self.cfg, self._logger)

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

        if self._refresh_registry_if_due():
            self._enricher = build_enricher(self.cfg, self._logger)
            self._pipeline.enricher = self._enricher

        result = self._pipeline.tick(raw, time.time())
        by_hex = {ac.hex: ac for ac in result.aircraft}
        for event in result.events:
            ac = by_hex[event.hex]
            self._logger.info(
                "alert %s: %s %s at %s, %s",
                event.event_name,
                ac.hex,
                ac.display_flight,
                _distance_text(ac, self.cfg.display.units),
                _altitude_text(ac, self.cfg.display.units),
            )
        return result

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
    aircraft_list, err = fetch_and_process(source, cfg, enricher=build_enricher(cfg))
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
    enricher = build_enricher(cfg)
    any_success = False
    i = 0
    while iterations is None or i < iterations:
        if i > 0:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"--- {stamp} ---", file=out)

        aircraft_list, err = fetch_and_process(source, cfg, enricher=enricher)
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
    enricher = build_enricher(cfg)
    previous_hexes: set[str] | None = None
    previous_levels: dict[str, AlertLevel] = {}
    i = 0
    while iterations is None or i < iterations:
        aircraft_list, err = fetch_and_process(source, cfg, enricher=enricher)
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
                    if level in (AlertLevel.NONE, AlertLevel.NEARBY):
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

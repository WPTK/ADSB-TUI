"""The one per-tick pipeline: raw feed payload in, graded aircraft and side effects out.

Both front ends call `Pipeline.tick` -- the TUI's fetch thread and the headless service --
so a rule (what a filter drops, when an alert fires, when a sighting is written) exists
exactly once. A tick is: normalize, enrich, derive geometry, match the watchlist, filter,
track, grade, hand alert events to `notify`, and record closed tracks to the history
database. It never touches the network (the caller fetches) or the terminal.

The clock is an argument, not a call: `tick(raw, now)` is deterministic for a given input,
which is what lets the tests drive hours of flight in milliseconds.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from adsbtui import alerts, dispatch, enrich, geo, history, normalize, tracker
from adsbtui import watchlist as watchlist_mod
from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel, Snapshot
from adsbtui.sources import SourceError

_LOGGER_NAME = "adsbtui"

Notify = Callable[[dispatch.AlertEvent], None]


@dataclass
class TickResult:
    """What one tick produced.

    `aircraft` is every currently tracked aircraft (including ones lingering after a
    dropout), `events` the alert events handed to `notify` this tick, `closed_tracks` the
    number of sighting rows written because a track ended, and `snapshot` the parsed feed
    header (message counter, timestamp). On a fetch failure `error` is the SourceError and
    the other fields are empty/zero/None.
    """

    aircraft: list[Aircraft]
    events: list[dispatch.AlertEvent]
    closed_tracks: int
    error: SourceError | None = None
    snapshot: Snapshot | None = None
    #: True when the feed's own `now` has not advanced for source.stale_s (a dead decoder
    #: behind a web server that still answers).
    feed_stale: bool = False


# --------------------------------------------------------------------------------------
# Stateless stages
# --------------------------------------------------------------------------------------


def parse_aircraft_list(snapshot: Snapshot) -> list[Aircraft]:
    """Normalize a Snapshot's raw entries into Aircraft, dropping unparseable ones."""
    aircraft_list: list[Aircraft] = []
    for raw_ac in snapshot.raw_aircraft:
        ac = normalize.parse_aircraft(raw_ac)
        if ac is not None:
            aircraft_list.append(ac)
    return aircraft_list


def enrich_all(aircraft_list: list[Aircraft], enricher: enrich.Enricher) -> None:
    """Fill in registration/type/owner/flags for each aircraft, in place.

    A lookup failure is logged at debug and skipped rather than propagated: enrichment is
    decoration, and losing an owner name must never cost the whole poll.
    """
    log = logging.getLogger(_LOGGER_NAME)
    for ac in aircraft_list:
        try:
            enricher.apply(ac)
        except Exception as exc:
            log.debug("enrichment failed for %s: %s", ac.hex, exc)


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


def _matches_search(ac: Aircraft, needle: str) -> bool:
    haystack = (ac.flight, ac.hex, ac.registration, ac.owner_name, ac.type_code)
    return any(field and needle in field.lower() for field in haystack)


def apply_filters(
    aircraft_list: list[Aircraft], cfg: Config, search_text: str = ""
) -> list[Aircraft]:
    """Apply every [filter] setting -- radius, hide_ground, include_nonicao, altitude band --
    plus the TUI's live search box when `search_text` is given.

    An aircraft graded as an emergency bypasses the radius check (and only that check)
    when alerts.emergency_ignore_radius is set: a 7700 squawk 20 miles out is exactly the
    thing you do not want filtered away.
    """
    f = cfg.filter
    ignore_radius_for_emergency = cfg.alerts.emergency_ignore_radius
    needle = search_text.strip().lower()

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
        if needle and not _matches_search(ac, needle):
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


def open_history(cfg: Config, logger: logging.Logger) -> str:
    """Prepare the sightings database, returning its path or "" if unavailable.

    A database that cannot be opened disables history for the whole run (logged once)
    rather than raising on every closed track for the life of the process.
    """
    if not cfg.history.db:
        return ""
    db_path = os.path.expanduser(cfg.history.db)
    try:
        history.ensure_schema(db_path)
        removed = history.prune_old(db_path, cfg.history.retention_days, time.time())
    except (sqlite3.Error, OSError) as exc:
        logger.warning("history disabled, could not open %s: %s", db_path, exc)
        return ""
    if removed:
        logger.info("pruned %d sighting(s) past retention", removed)
    return db_path


# --------------------------------------------------------------------------------------
# The stateful pipeline
# --------------------------------------------------------------------------------------


class Pipeline:
    """Per-tick state (tracks, last alert levels, running sighting aggregates) plus the
    settings a tick reads. `cfg`, `enricher` and `watch_entries` are plain attributes the
    front end swaps when the user edits settings or the registry finishes downloading.

    `notify` receives each alert event and decides how to deliver it: the TUI queues it
    for a worker thread so a slow webhook cannot delay the next poll, the headless service
    fires it inline. An exception from `notify` is logged and never ends the tick.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        enricher: enrich.Enricher,
        watch_entries: Sequence[watchlist_mod.WatchEntry] = (),
        notify: Notify | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.cfg = cfg
        self.enricher = enricher
        self.watch_entries: list[watchlist_mod.WatchEntry] = list(watch_entries)
        self.notify = notify
        self._logger = logger if logger is not None else logging.getLogger(_LOGGER_NAME)
        #: hex -> the level it was last graded at, so transitions can be detected.
        self._prev_levels: dict[str, AlertLevel] = {}
        #: hex -> last tick's tracked Aircraft, threaded through tracker.update_tracks().
        self._tracked: dict[str, Aircraft] = {}
        #: hex -> running per-track aggregates, written out when the track closes.
        self._track_stats: dict[str, dict[str, Any]] = {}
        self._history_db = open_history(cfg, self._logger)
        #: The feed's last distinct `now` and the tick time it first appeared.
        self._feed_now: float | None = None
        self._feed_changed_at: float | None = None

    def tick(self, raw: Any, now: float, search_text: str = "") -> TickResult:
        """Run one cycle over an already-fetched payload and return what happened."""
        snapshot = normalize.parse_snapshot(raw, now)
        aircraft_list = parse_aircraft_list(snapshot)
        enrich_all(aircraft_list, self.enricher)
        derive_geo(aircraft_list, self.cfg)
        if self.watch_entries:
            for ac in aircraft_list:
                ac.is_watched = watchlist_mod.matches(ac, self.watch_entries) is not None

        # Tracking runs on the filtered set (the set the TUI shows), so a track "closes"
        # when the aircraft leaves the configured radius, not only when the feed forgets it.
        filtered = apply_filters(aircraft_list, self.cfg, search_text)
        self._tracked = tracker.update_tracks(
            self._tracked,
            {ac.hex: ac for ac in filtered},
            now=now,
            stale_after_s=self.cfg.display.stale_after_s,
            linger_s=self.cfg.display.linger_s,
        )
        feed_stale = self._feed_is_stale(snapshot, now)
        graded = list(self._tracked.values())
        for ac in graded:
            ac.is_stale = ac.is_stale or feed_stale
        grade_all(graded, self.cfg)

        events = self._dispatch_alerts(graded)
        closed = self._record_tracks(self._tracked, now)
        return TickResult(graded, events, closed, snapshot=snapshot, feed_stale=feed_stale)

    def _feed_is_stale(self, snapshot: Snapshot, now: float) -> bool:
        """Whether the feed's `now` has stood still for longer than source.stale_s.

        Compared against its own previous value rather than the wall clock, so a receiver
        with a skewed clock is not reported stale.
        """
        if snapshot.now != self._feed_now or self._feed_changed_at is None:
            self._feed_now = snapshot.now
            self._feed_changed_at = now
            return False
        return (now - self._feed_changed_at) > self.cfg.source.stale_s

    def _dispatch_alerts(self, graded: list[Aircraft]) -> list[dispatch.AlertEvent]:
        """Build an event for each alert-level transition and watchlist hit, and notify.

        Returns every event handed to `notify` (the dispatcher may still swallow one under
        a cooldown or quiet hours).
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
            if self.notify is not None:
                try:
                    self.notify(event)
                except Exception as exc:  # never let a notification end the tick
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

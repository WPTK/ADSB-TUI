"""The curses event loop: background network polling on a daemon thread, and a main thread
that only ever drains a queue and draws -- it never does I/O itself.

This is deliberately the most defensively-coded file in the project. Every one of the
numbered requirements in the module-level docstrings below fixes a specific, verified bug
in the original tool:

  1. curses.curs_set(0) is wrapped in try/except curses.error -- some terminals do not
     support cursor visibility control, and that must not crash startup.
  2. stdscr.timeout(<=250ms) makes getch() block briefly and return -1 on timeout, so the
     quit key is responsive immediately and the process idles instead of busy-polling.
  3. All networking runs on a background daemon thread; the fetch thread NEVER calls any
     curses function, so a network hiccup can never corrupt or block the display.
  4. The main thread only drains a bounded queue and draws -- it never blocks on the
     network -- and only repaints when a `dirty` flag is set, instead of clearing/redrawing
     every tick regardless of whether anything changed.
  5. curses.KEY_RESIZE re-runs the draw against the new stdscr.getmaxyx().
  6. Every stdscr.addstr() call is individually guarded against curses.error (writing to the
     bottom-right cell, or a too-narrow/short terminal, must never crash the program); below
     a sane minimum size the normal layout is skipped for a single centered message.
  7. Colors/attributes are resolved from ui.theme's semantic row styles ONCE at startup,
     with start_color()/use_default_colors() guarded and a bold/reverse-only fallback when
     colors are unavailable or disabled.
  8. q/F10 exits cleanly (the fetch thread is joined via a stop Event); p toggles pause;
     +/- adjust the filter radius live; a raw KeyboardInterrupt (Ctrl-C) is caught around the
     whole run() body instead of being allowed to dump a traceback through curses.
"""

from __future__ import annotations

import contextlib
import curses
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

from adsbtui import (
    __version__,
    alerts,
    config_write,
    dispatch,
    enrich,
    geo,
    history,
    normalize,
    tracker,
)
from adsbtui import watchlist as watchlist_mod
from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel, Snapshot
from adsbtui.sources import Source, SourceError
from adsbtui.ui import bars, theme
from adsbtui.ui import columns as columns_mod
from adsbtui.ui.screens.columns import ColumnsScreen
from adsbtui.ui.screens.data import DataScreen
from adsbtui.ui.screens.detail import DetailScreen
from adsbtui.ui.screens.filters import FiltersScreen
from adsbtui.ui.screens.help import HelpScreen
from adsbtui.ui.screens.settings import SettingsScreen
from adsbtui.ui.screens.sort import SortScreen
from adsbtui.ui.screens.watchlist import WatchlistScreen

#: Below this width or height, the normal table layout is abandoned for a single centered
#: "Terminal too small" message -- there simply is not room to draw anything useful, and
#: attempting to would just produce a wall of curses.error-guarded no-ops.
MIN_WIDTH = 40
MIN_HEIGHT = 8

#: getch() timeout in milliseconds. Must be <= 250 per the task's requirement so the UI
#: stays responsive to the quit key; kept comfortably under that ceiling.
GETCH_TIMEOUT_MS = 200

#: The first term (and the reset value) of the fetch thread's exponential backoff sequence
#: (1, 2, 4, 8, ... capped at cfg.source.backoff_max_s) used after a failed fetch.
BACKOFF_BASE_S = 1.0

#: Fixed rows above the aircraft table: title bar, key/legend bar, column header, divider.
_TOP_ROWS = 4

_LOGGER_NAME = "adsbtui"

#: Action name -> key label, rendered in the key bar and the help overlay. Single source
#: of truth: the event loop's key handling and this table must agree, so the bar can never
#: advertise a key that does nothing.
#: Order matters: the key bar is truncated to fit the terminal width, so the two keys a
#: user must never be unable to find -- how to get help and how to get out -- come first.
KEYMAP: dict[str, str] = {
    "help": "F1/?",
    "quit": "q",
    "settings": "F2",
    "data": "F8",
    "detail": "Enter",
    "sort": "s",
    "filters": "f",
    "columns": "c",
    "watchlist": "w",
    "pause": "p",
    "units": "u",
    "radius": "+/-",
}

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

_UNIT_SYSTEMS = ["imperial", "metric", "aviation"]

#: AlertLevel -> severity rank, for sorting by "alert level". Built from the enum's own
#: declaration order (model.py), which is already least-to-most alarming.
_ALERT_RANK: dict[AlertLevel, int] = {level: rank for rank, level in enumerate(AlertLevel)}


def _is_emergency(cfg: Config, aircraft: Aircraft) -> bool:
    """Lightweight pre-check mirroring alerts.grade()'s first branch, used only to decide
    whether an aircraft should bypass the radius filter -- the authoritative alert_level
    (including EMERGENCY) is still assigned afterwards by alerts.grade() itself."""
    if not cfg.alerts.emergency:
        return False
    watched_squawks = {str(s) for s in cfg.alerts.squawks}
    if aircraft.squawk is not None and aircraft.squawk in watched_squawks:
        return True
    return aircraft.emergency is not None


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


def run_check(cfg: Config, source: Source) -> int:
    """Non-curses helper for a CLI --check flag: fetch once, parse, and report.

    Prints "OK <n> aircraft" and returns 0 on success. On any Source-family error, prints
    "ERROR: <message>" and returns 4. Never touches curses.
    """
    try:
        raw = source.fetch(cfg.source.timeout_s)
    except SourceError as exc:
        print(f"ERROR: {exc}")
        return 4

    snapshot = normalize.parse_snapshot(raw, time.time())
    count = sum(
        1 for raw_ac in snapshot.raw_aircraft if normalize.parse_aircraft(raw_ac) is not None
    )
    print(f"OK {count} aircraft")
    return 0


class App:
    """Owns the fetch thread, the shared queue, and the curses draw loop."""

    def __init__(self, cfg: Config, source: Source) -> None:
        self.cfg = cfg
        self.source = source
        self._logger = logging.getLogger(_LOGGER_NAME)

        self._registry: dict[str, str] = {}
        if cfg.registry.path:
            try:
                self._registry = normalize.load_owner_registry(cfg.registry.path)
            except OSError as exc:
                self._logger.warning("could not load registry %s: %s", cfg.registry.path, exc)

        self._enricher = self._build_enricher()

        self._attrs: dict[str, int] = {}

        # Message-rate bookkeeping (main thread only): computed from the cumulative
        # "messages" counter across successive successful snapshots.
        self._prev_messages: int | None = None
        self._prev_messages_time: float | None = None
        self._msg_rate: float | None = None

        # --- watchlist -------------------------------------------------------------
        self._watch_entries: list[watchlist_mod.WatchEntry] = []
        if cfg.watchlist.path:
            try:
                self._watch_entries = watchlist_mod.load(cfg.watchlist.path)
            except OSError as exc:
                self._logger.warning("could not load watchlist %s: %s", cfg.watchlist.path, exc)

        # --- alert dispatch --------------------------------------------------------
        # The bell is the one channel that must happen on the main thread (curses.beep()
        # is a curses call, and the fetch thread must never make one). So the dispatcher,
        # which runs on the fetch thread to keep webhook/subprocess latency off the UI,
        # gets a bell_fn that only sets a flag; the event loop turns it into an actual beep.
        self._bell_pending = threading.Event()
        self._dispatcher = dispatch.Dispatcher(
            cfg.alerts, bell_fn=lambda _event: self._bell_pending.set()
        )
        #: hex -> the level it was last graded at, so transitions can be detected.
        self._prev_levels: dict[str, AlertLevel] = {}

        # --- sighting history ------------------------------------------------------
        self._history_db = cfg.history.db or ""
        if self._history_db:
            try:
                history.ensure_schema(self._history_db)
                removed = history.prune_old(
                    self._history_db, cfg.history.retention_days, time.time()
                )
                if removed:
                    self._logger.info("pruned %d sighting(s) past retention", removed)
            except sqlite3.Error as exc:
                self._logger.warning(
                    "history disabled, could not open %s: %s", self._history_db, exc
                )
                self._history_db = ""
        #: hex -> running per-track aggregates, accumulated while a track is live and
        #: written out as one row when the track finally drops.
        self._track_stats: dict[str, dict] = {}

        # --- live UI state that screens mutate -------------------------------------
        self._search_text = ""
        self._cursor = 0
        self._scroll = 0
        self._screen = None
        self._screen_name: str | None = None

    def _build_enricher(self) -> enrich.Enricher:
        """Assemble the registration/type/owner lookup chain.

        Ordered cheapest-and-most-authoritative first: whatever the receiver itself sent,
        then the downloaded registry database, then a legacy CSV if the user has one, then
        purely derived facts (an N-number and a country computed from the hex alone). Every
        provider is optional -- with no databases at all this still yields a registration
        and country for US aircraft, which is why it is built unconditionally.
        """
        providers: list[enrich.Provider] = [enrich.FeedProvider()]
        db_path = os.path.expanduser(self.cfg.registry.db or "")
        if db_path and os.path.exists(db_path):
            try:
                providers.append(enrich.SqliteProvider(db_path))
            except sqlite3.Error as exc:
                self._logger.warning("registry database unusable (%s): %s", db_path, exc)
        if self._registry:
            providers.append(enrich.CsvProvider(self._registry))
        providers.append(enrich.DerivedProvider())
        return enrich.Enricher(providers)

    def _enrich(self, aircraft: list[Aircraft]) -> None:
        """Fill in registration/type/owner/flags for each aircraft, in place.

        Enricher.apply() owns the merge rules -- it only writes fields the feed left empty,
        so the receiver's own database always wins over anything looked up or derived here.
        """
        for ac in aircraft:
            try:
                self._enricher.apply(ac)
            except Exception as exc:  # a lookup must never take down the fetch thread
                self._logger.debug("enrichment failed for %s: %s", ac.hex, exc)

    # ------------------------------------------------------------------
    # Background fetch thread -- NEVER call any curses function from here.
    # ------------------------------------------------------------------

    def _fetch_loop(self, stop_event: threading.Event, out_queue: queue.Queue) -> None:
        previous: dict[str, Aircraft] = {}
        backoff = BACKOFF_BASE_S

        while not stop_event.is_set():
            try:
                raw = self.source.fetch(self.cfg.source.timeout_s)
                fetched_at = time.time()
                snapshot = normalize.parse_snapshot(raw, fetched_at)

                aircraft_list: list[Aircraft] = []
                for raw_ac in snapshot.raw_aircraft:
                    ac = normalize.parse_aircraft(raw_ac)
                    if ac is None:
                        continue
                    if self._registry:
                        name = self._registry.get(ac.hex.upper())
                        if name:
                            ac.owner_name = name
                    aircraft_list.append(ac)

                self._enrich(aircraft_list)

                home_lat = self.cfg.home.lat
                home_lon = self.cfg.home.lon
                for ac in aircraft_list:
                    if ac.lat is None or ac.lon is None:
                        continue
                    ac.distance_mi = geo.haversine_mi(home_lat, home_lon, ac.lat, ac.lon)
                    ac.bearing_deg = geo.initial_bearing_deg(home_lat, home_lon, ac.lat, ac.lon)
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

                for ac in aircraft_list:
                    ac.is_watched = (
                        watchlist_mod.matches(ac, self._watch_entries) is not None
                        if self._watch_entries
                        else False
                    )

                filtered = self._apply_filters(aircraft_list)

                current_batch = {ac.hex: ac for ac in filtered}
                tracked = tracker.update_tracks(
                    previous,
                    current_batch,
                    now=fetched_at,
                    stale_after_s=self.cfg.display.stale_after_s,
                    linger_s=self.cfg.display.linger_s,
                )
                previous = tracked

                graded = list(tracked.values())
                for ac in graded:
                    ac.alert_level = alerts.grade(
                        ac,
                        squawks=self.cfg.alerts.squawks,
                        emergency_enabled=self.cfg.alerts.emergency,
                        proximity_mi=self.cfg.filter.proximity,
                        cpa_enabled=self.cfg.alerts.cpa_enabled,
                        cpa_distance_mi=self.cfg.alerts.cpa_distance,
                        cpa_horizon_s=self.cfg.alerts.cpa_horizon_s,
                        cpa_min_gs_kt=self.cfg.alerts.cpa_min_gs_kt,
                    )

                self._dispatch_alerts(graded)
                self._record_tracks(tracked, fetched_at)

                ordered = sort_aircraft(
                    graded, self.cfg.display.sort_key, self.cfg.display.sort_reverse
                )

                self._publish(out_queue, (snapshot, ordered, None))
                backoff = BACKOFF_BASE_S
                stop_event.wait(self.cfg.source.refresh_s)

            except SourceError as exc:
                self._logger.warning("fetch failed: %s", exc)
                self._publish(out_queue, (None, [], str(exc)))
                stop_event.wait(backoff)
                backoff = min(backoff * 2, self.cfg.source.backoff_max_s)
            except Exception as exc:  # pragma: no cover - defensive, never crash the thread
                self._logger.exception("unexpected error in fetch loop: %s", exc)
                self._publish(out_queue, (None, [], str(exc)))
                stop_event.wait(backoff)
                backoff = min(backoff * 2, self.cfg.source.backoff_max_s)

    def _apply_filters(self, aircraft_list: list[Aircraft]) -> list[Aircraft]:
        """Apply every [filter] setting plus the live search box.

        An aircraft graded as an emergency bypasses the radius check (and only that check)
        when alerts.emergency_ignore_radius is set: a 7700 squawk 20 miles out is exactly
        the thing you do not want filtered off the screen.
        """
        f = self.cfg.filter
        ignore_radius_for_emergency = self.cfg.alerts.emergency_ignore_radius
        needle = self._search_text.strip().lower()

        kept: list[Aircraft] = []
        for ac in aircraft_list:
            is_emergency = _is_emergency(self.cfg, ac)

            within_radius = ac.distance_mi is not None and ac.distance_mi <= f.radius
            if not within_radius and not (ignore_radius_for_emergency and is_emergency):
                continue
            if f.hide_ground and ac.on_ground:
                continue
            if not f.include_nonicao and not ac.is_icao:
                continue
            if ac.altitude_ft is not None and not (f.min_alt_ft <= ac.altitude_ft <= f.max_alt_ft):
                continue
            if needle and not self._matches_search(ac, needle):
                continue
            kept.append(ac)
        return kept

    @staticmethod
    def _matches_search(ac: Aircraft, needle: str) -> bool:
        haystack = (ac.flight, ac.hex, ac.registration, ac.owner_name, ac.type_code)
        return any(field and needle in field.lower() for field in haystack)

    def _dispatch_alerts(self, graded: list[Aircraft]) -> None:
        """Fire alert notifications for level transitions and watchlist matches.

        Runs on the fetch thread so a slow webhook or notify-send can never stall the
        display; the bell is the exception and is handed to the main thread via a flag.
        """
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
            if event is not None:
                try:
                    self._dispatcher.maybe_fire(event)
                except Exception as exc:  # never let a notification kill the fetch thread
                    self._logger.warning("alert dispatch failed for %s: %s", ac.hex, exc)
            self._prev_levels[ac.hex] = ac.alert_level

        for gone in [h for h in self._prev_levels if h not in seen]:
            del self._prev_levels[gone]

    def _record_tracks(self, tracked: dict[str, Aircraft], now: float) -> None:
        """Accumulate per-track aggregates, and write a sighting row when a track drops.

        tracker.update_tracks() ages a vanished aircraft out after the linger window, so
        "no longer in tracked" is exactly the moment its track ended and the summary can
        be persisted.
        """
        if not self._history_db:
            return

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
            except sqlite3.Error as exc:
                self._logger.warning("could not record sighting for %s: %s", hexid, exc)

    @staticmethod
    def _publish(
        out_queue: queue.Queue,
        item: tuple[Snapshot | None, list[Aircraft], str | None],
    ) -> None:
        """Drop-oldest publish: clear the queue first so the UI thread only ever sees the
        latest tick and never blocks a slow consumer, then put the new item."""
        while True:
            try:
                out_queue.get_nowait()
            except queue.Empty:
                break
        # pragma: no cover branch below - Full can't happen right after draining above.
        with contextlib.suppress(queue.Full):
            out_queue.put_nowait(item)

    # ------------------------------------------------------------------
    # Curses setup
    # ------------------------------------------------------------------

    def _build_attrs(self) -> dict[str, int]:
        """Resolve ui.theme's semantic RowStyles to real curses attributes, once.

        Falls back to bold/reverse/dim-only attributes (no color) when colors are
        unavailable or cfg.display.color is "never". Every curses color call is guarded --
        some terminals advertise color support but still fail start_color()/init_pair().
        """
        color_ok = False
        # Background used for every color pair: -1 (the terminal's own default background,
        # so colored rows still ride on whatever background the user's terminal theme sets)
        # when use_default_colors() is available, else an explicit black as a fallback.
        pair_bg = curses.COLOR_BLACK
        if self.cfg.display.color != "never":
            try:
                curses.start_color()
                try:
                    curses.use_default_colors()
                    pair_bg = -1
                except curses.error:
                    pair_bg = curses.COLOR_BLACK
                color_ok = curses.has_colors()
            except curses.error:
                color_ok = False

        color_map = {
            "cyan": curses.COLOR_CYAN,
            "yellow": curses.COLOR_YELLOW,
            "red": curses.COLOR_RED,
            "magenta": curses.COLOR_MAGENTA,
            "green": curses.COLOR_GREEN,
            "white": curses.COLOR_WHITE,
        }

        max_pairs = getattr(curses, "COLOR_PAIRS", 0)
        attrs: dict[str, int] = {}
        pair_id = 1
        for name, style in theme.ROW_STYLES.items():
            attr = curses.A_NORMAL
            if style.bold:
                attr |= curses.A_BOLD
            if style.reverse:
                attr |= curses.A_REVERSE
            if style.dim:
                attr |= curses.A_DIM

            # "default" rides on the terminal's own foreground -- no color pair needed.
            if color_ok and style.fg in color_map and pair_id < max_pairs:
                try:
                    curses.init_pair(pair_id, color_map[style.fg], pair_bg)
                    attr |= curses.color_pair(pair_id)
                    pair_id += 1
                except curses.error:
                    pass

            attrs[name] = attr

        return attrs

    # ------------------------------------------------------------------
    # Drawing -- main thread only.
    # ------------------------------------------------------------------

    def _safe_addstr(self, stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
        """Write one string to the screen, swallowing curses.error.

        Every draw call in this module routes through here so that writing to the
        bottom-right cell, or any other addstr() failure on a too-small/narrow terminal,
        never crashes the program -- it just skips that one piece of text.
        """
        with contextlib.suppress(curses.error):
            stdscr.addstr(y, x, text, attr)

    def _row_style_name(self, ac: Aircraft) -> str:
        if ac.alert_level == AlertLevel.EMERGENCY:
            return "emergency"
        if ac.is_military:
            return "military"
        if ac.alert_level == AlertLevel.OVERHEAD:
            return "overhead"
        if ac.alert_level in (AlertLevel.INBOUND, AlertLevel.OUTBOUND):
            return "inbound"
        if ac.is_stale:
            return "stale"
        if ac.is_new:
            return "new"
        return "normal"

    def _draw(
        self,
        stdscr,
        aircraft_list: list[Aircraft],
        last_error: str | None,
        last_success_at: float | None,
        paused: bool,
    ) -> None:
        height, width = stdscr.getmaxyx()
        stdscr.erase()

        if width < MIN_WIDTH or height < MIN_HEIGHT:
            msg = "Terminal too small"[: max(0, width)]
            y = max(0, height // 2)
            x = max(0, (width - len(msg)) // 2)
            self._safe_addstr(stdscr, y, x, msg, self._attrs.get("emergency", curses.A_BOLD))
            stdscr.noutrefresh()
            curses.doupdate()
            return

        display = self.cfg.display
        normal_attr = self._attrs.get("normal", curses.A_NORMAL)
        header_attr = self._attrs.get("header", curses.A_BOLD)

        config_path = getattr(self.cfg, "_config_path", None)
        title = bars.title_bar_text(width, __version__, paused, display.units, config_path)
        self._safe_addstr(stdscr, 0, 0, title, header_attr)

        key_line = bars.key_bar_text(width, KEYMAP)
        self._safe_addstr(stdscr, 1, 0, key_line, normal_attr)

        border_style = display.borders
        ascii_only = border_style == "ascii"
        columns = columns_mod.layout(display.columns, width, display.owner_width)
        widths = columns_mod.compute_widths(columns, width, display.owner_width)

        header_line = columns_mod.format_header(columns, widths, border_style)
        self._safe_addstr(stdscr, 2, 0, header_line, header_attr)

        divider_char = theme.glyphs(border_style)["h"] or "-"
        self._safe_addstr(stdscr, 3, 0, divider_char * width, normal_attr)

        bottom_rows = 1 if display.status_bar else 0
        table_height = max(0, height - _TOP_ROWS - bottom_rows)

        # Keep the cursor inside the list and the viewport around the cursor, so the table
        # scrolls instead of silently hiding everything past the first screenful.
        self._clamp_view(len(aircraft_list), table_height)
        visible = aircraft_list[self._scroll : self._scroll + table_height]

        for i, ac in enumerate(visible):
            row_text = columns_mod.format_row(ac, columns, widths, display.units, ascii_only)
            if self._scroll + i == self._cursor and aircraft_list:
                attr = self._attrs.get("selected", curses.A_REVERSE)
            else:
                attr = self._attrs.get(self._row_style_name(ac), normal_attr)
            self._safe_addstr(stdscr, _TOP_ROWS + i, 0, row_text, attr)

        if self._screen is not None:
            self._draw_screen_overlay(stdscr, width, height)

        if display.status_bar:
            age = None if last_success_at is None else max(0.0, time.time() - last_success_at)
            alert_count = sum(1 for ac in aircraft_list if ac.alert_level != AlertLevel.NONE)
            status = bars.status_line_text(
                width,
                source_ok=last_error is None,
                source_message=last_error,
                last_success_age_s=age,
                aircraft_count=len(aircraft_list),
                alert_count=alert_count,
                msg_rate=self._msg_rate,
            )
            self._safe_addstr(stdscr, height - 1, 0, status, normal_attr)

        stdscr.noutrefresh()
        curses.doupdate()

    def _page_size(self, stdscr) -> int:
        """Rows of table visible right now, for PageUp/PageDown."""
        height, _ = stdscr.getmaxyx()
        bottom_rows = 1 if self.cfg.display.status_bar else 0
        return max(1, height - _TOP_ROWS - bottom_rows)

    def _clamp_view(self, total: int, table_height: int) -> None:
        """Keep the cursor within the list and the scroll window around the cursor."""
        if total == 0:
            self._cursor = 0
            self._scroll = 0
            return
        self._cursor = max(0, min(self._cursor, total - 1))
        if table_height <= 0:
            self._scroll = 0
            return
        if self._cursor < self._scroll:
            self._scroll = self._cursor
        elif self._cursor >= self._scroll + table_height:
            self._scroll = self._cursor - table_height + 1
        self._scroll = max(0, min(self._scroll, max(0, total - table_height)))

    def _draw_screen_overlay(self, stdscr, width: int, height: int) -> None:
        """Draw the active screen centered over the table, inside a bordered box.

        The screen itself only produces plain strings (that is what makes every screen
        unit-testable without a terminal); all the curses geometry lives here.
        """
        screen = self._screen
        if screen is None:
            return

        box_w = max(20, min(width - 4, 76))
        inner_w = box_w - 4
        lines = screen.render_lines(inner_w, max(1, height - 8))
        box_h = min(height - 2, len(lines) + 4)
        top = max(0, (height - box_h) // 2)
        left = max(0, (width - box_w) // 2)

        glyph = theme.glyphs(self.cfg.display.borders)
        h, v = glyph.get("h") or "-", glyph.get("v") or "|"
        tl = glyph.get("tl") or "+"
        tr = glyph.get("tr") or "+"
        bl = glyph.get("bl") or "+"
        br = glyph.get("br") or "+"
        border_attr = self._attrs.get("header", curses.A_BOLD)
        body_attr = self._attrs.get("normal", curses.A_NORMAL)

        title = f" {screen.title} "[: max(0, box_w - 4)]
        top_line = tl + h + title + h * max(0, box_w - 3 - len(title) - 1) + tr
        self._safe_addstr(stdscr, top, left, top_line[:box_w], border_attr)

        for i in range(box_h - 2):
            text = lines[i] if i < len(lines) else ""
            row = v + " " + text.ljust(inner_w)[:inner_w] + " " + v
            self._safe_addstr(stdscr, top + 1 + i, left, row[:box_w], body_attr)

        bottom_line = (bl + h * (box_w - 2) + br)[:box_w]
        self._safe_addstr(stdscr, top + box_h - 1, left, bottom_line, border_attr)

    def _selected_aircraft(self, aircraft_list: list[Aircraft]) -> Aircraft | None:
        if not aircraft_list:
            return None
        index = max(0, min(self._cursor, len(aircraft_list) - 1))
        return aircraft_list[index]

    def _open_screen(self, name: str, aircraft_list: list[Aircraft]) -> None:
        """Construct and push the named screen. Unknown names are ignored."""
        display = self.cfg.display
        if name == "help":
            self._screen = HelpScreen(KEYMAP, __version__)
        elif name == "detail":
            ac = self._selected_aircraft(aircraft_list)
            if ac is None:
                return
            self._screen = DetailScreen(ac, display.units)
        elif name == "sort":
            self._screen = SortScreen(SORT_KEYS, display.sort_key, display.sort_reverse)
        elif name == "filters":
            f = self.cfg.filter
            self._screen = FiltersScreen(
                {
                    "hide_ground": f.hide_ground,
                    "include_nonicao": f.include_nonicao,
                    "min_alt_ft": f.min_alt_ft,
                    "max_alt_ft": f.max_alt_ft,
                },
                search_text=self._search_text,
            )
        elif name == "columns":
            self._screen = ColumnsScreen(display.columns, display.density, display.borders)
        elif name == "watchlist":
            self._screen = WatchlistScreen(
                self._watch_entries, self._selected_aircraft(aircraft_list)
            )
        elif name == "settings":
            self._screen = SettingsScreen(self.cfg)
        elif name == "data":
            self._screen = DataScreen(
                self.cfg.registry.db,
                csv_path=self.cfg.registry.path,
                stale_after_days=self.cfg.registry.max_age_days,
            )
        self._screen_name = name if self._screen is not None else None

    def _close_screen(self) -> None:
        """Pop the active screen, applying whatever it produced back onto live config.

        Every screen keeps its own committed result (Escape leaves it untouched), so
        reading result() unconditionally here is safe: a cancelled screen returns exactly
        what it was given.
        """
        screen, name = self._screen, self._screen_name
        self._screen = None
        self._screen_name = None
        if screen is None:
            return

        display = self.cfg.display
        if name == "sort":
            display.sort_key, display.sort_reverse = screen.result()
        elif name == "filters":
            result = screen.result()
            f = self.cfg.filter
            f.hide_ground = bool(result.get("hide_ground", f.hide_ground))
            f.include_nonicao = bool(result.get("include_nonicao", f.include_nonicao))
            f.min_alt_ft = int(result.get("min_alt_ft", f.min_alt_ft))
            f.max_alt_ft = int(result.get("max_alt_ft", f.max_alt_ft))
            self._search_text = str(result.get("search_text", self._search_text) or "")
        elif name == "columns":
            result = screen.result()
            display.columns = list(result.get("columns", display.columns))
            display.density = str(result.get("density", display.density))
            display.borders = str(result.get("borders", display.borders))
        elif name == "watchlist":
            self._watch_entries = list(screen.result())
            if self.cfg.watchlist.path:
                try:
                    watchlist_mod.save(self.cfg.watchlist.path, self._watch_entries)
                except OSError as exc:
                    self._logger.warning("could not save watchlist: %s", exc)
        elif name == "settings":
            self._apply_settings(screen)
        elif name == "data":
            # A finished download changes what the enricher can resolve, so rebuild it
            # rather than leaving the session querying the databases it started with.
            outcomes = screen.take_finished()
            if any(outcome.ok for outcome in outcomes):
                self._enricher = self._build_enricher()
                self._logger.info("registry updated; enrichment reloaded")

    def _apply_settings(self, screen: SettingsScreen) -> None:
        """Adopt an edited config into the running session, and persist it if asked.

        Applying and saving are separate user intents: you may want to try a setting for
        this session without writing it to disk. The screen reports both, and the session
        adopts the change either way.
        """
        if not getattr(screen, "applied", False):
            return
        new_cfg = screen.result()
        # Carry over the bookkeeping attributes the screen does not own.
        new_cfg._args = self.cfg._args
        new_cfg._config_path = self.cfg._config_path
        self.cfg = new_cfg

        # Settings that feed long-lived objects have to be re-read, not just stored.
        self._registry = {}
        if self.cfg.registry.path:
            try:
                self._registry = normalize.load_owner_registry(self.cfg.registry.path)
            except OSError as exc:
                self._logger.warning("could not load registry: %s", exc)
        self._enricher = self._build_enricher()
        self._dispatcher = dispatch.Dispatcher(
            self.cfg.alerts, bell_fn=lambda _event: self._bell_pending.set()
        )

        if getattr(screen, "save_requested", False):
            default_path = os.path.expanduser("~/.config/adsbtui/config.toml")
            path = self.cfg._config_path or default_path
            try:
                config_write.dump_config(self.cfg, path)
                self.cfg._config_path = path
                self._logger.info("configuration saved to %s", path)
            except OSError as exc:
                self._logger.warning("could not save configuration to %s: %s", path, exc)

    def _update_msg_rate(self, snapshot: Snapshot) -> None:
        """Update the rolling messages/sec estimate from two successive snapshots'
        cumulative "messages" counters. Resets to None (rather than going negative) if the
        counter ever decreases, which means the upstream source restarted."""
        if snapshot.messages is None:
            return
        if self._prev_messages is not None and self._prev_messages_time is not None:
            dt = snapshot.fetched_at - self._prev_messages_time
            delta = snapshot.messages - self._prev_messages
            if dt > 0 and delta >= 0:
                self._msg_rate = delta / dt
            else:
                self._msg_rate = None
        self._prev_messages = snapshot.messages
        self._prev_messages_time = snapshot.fetched_at

    # ------------------------------------------------------------------
    # Main event loop
    # ------------------------------------------------------------------

    def _event_loop(self, stdscr, stop_event: threading.Event, out_queue: queue.Queue) -> int:
        aircraft_list: list[Aircraft] = []
        last_error: str | None = None
        last_success_at: float | None = None
        paused = False
        dirty = True
        last_second = int(time.time())

        while True:
            got_update = False
            while True:
                try:
                    snapshot, acs, err = out_queue.get_nowait()
                except queue.Empty:
                    break
                got_update = True
                if err is not None:
                    last_error = err
                else:
                    last_error = None
                    last_success_at = time.time()
                    if snapshot is not None:
                        self._update_msg_rate(snapshot)
                    if not paused:
                        aircraft_list = acs
            if got_update:
                dirty = True

            now_second = int(time.time())
            if now_second != last_second:
                last_second = now_second
                dirty = True

            if dirty:
                self._draw(stdscr, aircraft_list, last_error, last_success_at, paused)
                dirty = False

            # The bell fires here rather than on the fetch thread: curses.beep() is a
            # curses call, and only this thread may make one.
            if self._bell_pending.is_set():
                self._bell_pending.clear()
                if self.cfg.alerts.bell:
                    with contextlib.suppress(curses.error):
                        curses.beep()

            key = stdscr.getch()
            if key == -1:
                continue
            dirty = True

            if key == curses.KEY_RESIZE:
                continue

            # A screen, when open, gets first refusal on every key: it owns the keyboard
            # until it says it is done.
            if self._screen is not None:
                if self._screen.handle_key(key) == "close":
                    self._close_screen()
                continue

            if key in (ord("q"), ord("Q"), curses.KEY_F10):
                return 0

            if key in (ord("?"), curses.KEY_F1):
                self._open_screen("help", aircraft_list)
            elif key in (curses.KEY_F2, ord(",")):
                self._open_screen("settings", aircraft_list)
            elif key in (curses.KEY_F8, ord("D")):
                self._open_screen("data", aircraft_list)
            elif key in (curses.KEY_ENTER, ord("\n"), ord("\r"), ord("d")):
                self._open_screen("detail", aircraft_list)
            elif key in (ord("/"), curses.KEY_F3, ord("f"), curses.KEY_F4):
                self._open_screen("filters", aircraft_list)
            elif key in (ord("s"), curses.KEY_F5):
                self._open_screen("sort", aircraft_list)
            elif key in (ord("S"),):
                self.cfg.display.sort_reverse = not self.cfg.display.sort_reverse
            elif key in (ord("c"), curses.KEY_F6):
                self._open_screen("columns", aircraft_list)
            elif key in (ord("w"), curses.KEY_F7):
                self._open_screen("watchlist", aircraft_list)
            elif key in (ord("p"), ord("P")):
                paused = not paused
            elif key == ord("u"):
                current = self.cfg.display.units
                index = _UNIT_SYSTEMS.index(current) if current in _UNIT_SYSTEMS else 0
                self.cfg.display.units = _UNIT_SYSTEMS[(index + 1) % len(_UNIT_SYSTEMS)]
            elif key in (curses.KEY_UP, ord("k")):
                self._cursor -= 1
            elif key in (curses.KEY_DOWN, ord("j")):
                self._cursor += 1
            elif key == curses.KEY_HOME:
                self._cursor = 0
            elif key == curses.KEY_END:
                self._cursor = max(0, len(aircraft_list) - 1)
            elif key == curses.KEY_PPAGE:
                self._cursor -= max(1, self._page_size(stdscr))
            elif key == curses.KEY_NPAGE:
                self._cursor += max(1, self._page_size(stdscr))
            elif key in (ord("+"), ord("=")):
                self.cfg.filter.radius += 1.0
            elif key in (ord("-"), ord("_")):
                self.cfg.filter.radius = max(1.0, self.cfg.filter.radius - 1.0)

    def run(self, stdscr) -> int:
        """Entry point for curses.wrapper(app.run). Returns the process exit code."""
        stop_event = threading.Event()
        out_queue: queue.Queue = queue.Queue(maxsize=2)
        fetch_thread = threading.Thread(
            target=self._fetch_loop, args=(stop_event, out_queue), daemon=True
        )
        try:
            # Some terminals do not support cursor visibility control at all -- this must
            # never crash startup.
            with contextlib.suppress(curses.error):
                curses.curs_set(0)
            stdscr.timeout(GETCH_TIMEOUT_MS)
            self._attrs = self._build_attrs()

            fetch_thread.start()
            return self._event_loop(stdscr, stop_event, out_queue)
        except KeyboardInterrupt:
            return 0
        finally:
            stop_event.set()
            fetch_thread.join(timeout=2.0)

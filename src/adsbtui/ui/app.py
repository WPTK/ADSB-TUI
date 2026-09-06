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
import queue
import threading
import time

from adsbtui import __version__, alerts, geo, normalize, tracker
from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel, Snapshot
from adsbtui.sources import Source, SourceError
from adsbtui.ui import bars, theme
from adsbtui.ui import columns as columns_mod

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


def _sort_key_func(sort_key: str):
    if sort_key == "altitude":
        return lambda ac: ac.altitude_ft
    if sort_key == "callsign":
        return lambda ac: ac.display_flight
    # "distance" and any unrecognized key both fall back to distance -- there must always
    # be a sensible default ordering.
    return lambda ac: ac.distance_mi


def sort_aircraft(aircraft_list: list[Aircraft], sort_key: str, reverse: bool) -> list[Aircraft]:
    """Sort aircraft by cfg.display.sort_key ("distance", "altitude", or "callsign").

    Aircraft whose sort value is None always sort last, regardless of `reverse` -- an
    unknown distance/altitude is least useful information, not "biggest" or "smallest".
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

        self._attrs: dict[str, int] = {}

        # Message-rate bookkeeping (main thread only): computed from the cumulative
        # "messages" counter across successive successful snapshots.
        self._prev_messages: int | None = None
        self._prev_messages_time: float | None = None
        self._msg_rate: float | None = None

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

                radius = self.cfg.filter.radius
                ignore_radius_for_emergency = self.cfg.alerts.emergency_ignore_radius
                filtered: list[Aircraft] = []
                for ac in aircraft_list:
                    within_radius = ac.distance_mi is not None and ac.distance_mi <= radius
                    if within_radius or ignore_radius_for_emergency and _is_emergency(self.cfg, ac):
                        filtered.append(ac)

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
            msg = "Terminal too small"[:max(0, width)]
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

        keymap = {
            "quit": "q",
            "pause": "p",
            "radius plus": "+",
            "radius minus": "-",
        }
        key_line = bars.key_bar_text(width, keymap)
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

        for i, ac in enumerate(aircraft_list[:table_height]):
            row_text = columns_mod.format_row(ac, columns, widths, display.units, ascii_only)
            attr = self._attrs.get(self._row_style_name(ac), normal_attr)
            self._safe_addstr(stdscr, _TOP_ROWS + i, 0, row_text, attr)

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

            key = stdscr.getch()
            if key == -1:
                continue
            dirty = True

            if key in (ord("q"), ord("Q"), curses.KEY_F10):
                return 0
            if key == curses.KEY_RESIZE:
                continue
            if key in (ord("p"), ord("P")):
                paused = not paused
            elif key in (ord("+"), ord("=")):
                self.cfg.filter.radius += 1.0
            elif key in (ord("-"), ord("_")):
                self.cfg.filter.radius = max(1.0, self.cfg.filter.radius - 1.0)
            # Future keys are handled here as this if/elif chain grows.

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

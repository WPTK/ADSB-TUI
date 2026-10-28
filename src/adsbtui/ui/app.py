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
    normalize,
    output,
    pipeline,
)
from adsbtui import watchlist as watchlist_mod
from adsbtui.config import Config, changed_keys, default_config_path, layer_for_save
from adsbtui.enrich import registry_update
from adsbtui.model import Aircraft, AlertLevel, Snapshot
from adsbtui.sources import Source, SourceError
from adsbtui.ui import bars, radar, theme
from adsbtui.ui import columns as columns_mod
from adsbtui.ui.keys import ACTION_FOR_KEY, BAR_KEYMAP, HELP_KEYMAP
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

#: Fixed rows around the aircraft table: the status line, the column header and its
#: divider above; the key bar below.
_TOP_ROWS = 3

#: Scope geometry. The scope only appears once the terminal is wide enough for it AND a
#: readable table, and it is capped so a very wide terminal spends the extra width on
#: aircraft data rather than an enormous circle.
RADAR_MIN_TOTAL_WIDTH = 110
RADAR_MIN_WIDTH = 24
RADAR_MAX_WIDTH = 44
RADAR_GAP = 2

_LOGGER_NAME = "adsbtui"

#: Key-table actions that open a screen of the same name.
_SCREEN_ACTIONS = frozenset(
    {"help", "settings", "data", "detail", "filters", "sort", "columns", "watchlist"}
)

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

    def __init__(self, cfg: Config, source: Source, clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.source = source
        self._clock = clock
        self._logger = logging.getLogger(_LOGGER_NAME)

        self._initial_enricher = self._build_enricher()

        # --- automatic registry refresh --------------------------------------------
        #: Set while a background download/rebuild is running, so the status line can say
        #: so and a second refresh cannot start on top of the first.
        self._registry_status: str | None = None
        self._registry_thread: threading.Thread | None = None
        self._registry_done = threading.Event()

        self._attrs: dict[str, int] = {}

        # Message-rate bookkeeping (main thread only): computed from the cumulative
        # "messages" counter across successive successful snapshots.
        self._prev_messages: int | None = None
        self._prev_messages_time: float | None = None
        self._msg_rate: float | None = None

        # --- watchlist, alert dispatch, sighting history ---------------------------
        watch_entries: list[watchlist_mod.WatchEntry] = []
        if cfg.watchlist.path:
            try:
                watch_entries = watchlist_mod.load(cfg.watchlist.path)
            except OSError as exc:
                self._logger.warning("could not load watchlist %s: %s", cfg.watchlist.path, exc)
        # The bell is the one channel that must happen on the main thread (curses.beep()
        # is a curses call), so its bell_fn only sets a flag the event loop turns into a beep.
        # Slow channels (webhook, command) run on the worker, never on the fetch thread.
        self._bell_pending = threading.Event()
        self._dispatcher = self._build_dispatcher()
        self._dispatch_worker = dispatch.DispatchWorker(lambda ev: self._dispatcher.maybe_fire(ev))
        self._pipeline = pipeline.Pipeline(
            cfg,
            enricher=self._initial_enricher,
            watch_entries=watch_entries,
            notify=self._dispatch_worker.submit,
            logger=self._logger,
        )

        # --- live UI state that screens mutate -------------------------------------
        #: The scope is on whenever the terminal can fit it; 'r' turns it off for anyone
        #: who wants the full width for the table.
        self._radar_on = True
        self._search_text = ""
        self._feed_stale = False
        #: (section, key) pairs edited in Settings this session; saved with the file layer.
        self._edited: set[tuple[str, str]] = set()
        self._flash_text: tuple[str, float] | None = None
        self._cursor = 0
        self._scroll = 0
        self._screen = None
        self._screen_name: str | None = None

    @property
    def _enricher(self) -> enrich.Enricher:
        return self._pipeline.enricher

    @_enricher.setter
    def _enricher(self, value: enrich.Enricher) -> None:
        self._pipeline.enricher = value

    @property
    def _watch_entries(self) -> list[watchlist_mod.WatchEntry]:
        return self._pipeline.watch_entries

    @_watch_entries.setter
    def _watch_entries(self, value: list[watchlist_mod.WatchEntry]) -> None:
        self._pipeline.watch_entries = value

    def _build_dispatcher(self) -> dispatch.Dispatcher:
        return dispatch.Dispatcher(
            self.cfg.alerts, bell_fn=lambda _event: self._bell_pending.set(), now_fn=self._clock
        )

    def _build_enricher(self) -> enrich.Enricher:
        """Assemble the registration/type/owner lookup chain.

        Ordered cheapest-and-most-authoritative first: whatever the receiver itself sent,
        then the downloaded registry database, then purely derived facts (an N-number and a
        country computed from the hex alone). Every provider is optional -- with no
        database at all this still yields a registration and country for US aircraft, which
        is why it is built unconditionally.
        """
        providers: list[enrich.Provider] = [enrich.FeedProvider()]
        db_path = os.path.expanduser(self.cfg.registry.db or "")
        if db_path and os.path.exists(db_path):
            try:
                providers.append(enrich.SqliteProvider(db_path))
            except sqlite3.Error as exc:
                self._logger.warning("registry database unusable (%s): %s", db_path, exc)
        providers.append(enrich.DerivedProvider())
        return enrich.Enricher(providers)

    # ------------------------------------------------------------------
    # Automatic registry refresh
    # ------------------------------------------------------------------

    def _registry_needs_refresh(self) -> bool:
        """True when the registry database is missing or older than registry.max_age_days.

        The rule itself lives in output.registry_is_due so the TUI and the headless service
        cannot drift apart on what "stale" means. "Missing" is the important half: a fresh
        install has no database at all, and requiring someone to find a screen and press a
        key before owner names work is the kind of manual step that leaves the feature
        switched off forever.
        """
        return output.registry_is_due(self.cfg)

    def _start_registry_refresh(self) -> None:
        """Download and rebuild the registry on a background thread, if one is due.

        Runs off the UI thread for the same reason the feed does: this is an 8 MB download
        plus a few hundred thousand SQLite inserts, and none of it may block drawing. The
        enricher is rebuilt on the main thread once the flag is set, since swapping it
        underneath a running fetch would be a data race.
        """
        if self._registry_thread is not None and self._registry_thread.is_alive():
            return
        if not self._registry_needs_refresh():
            return

        db_path = os.path.expanduser(self.cfg.registry.db)

        def _progress(fraction: float, message: str) -> None:
            # registry_update calls this as (fraction, message); the percentage is what
            # belongs in a one-line status, the message is already in the log.
            self._registry_status = f"registry {min(max(fraction, 0.0), 1.0) * 100:.0f}%"

        def _run() -> None:
            self._registry_status = "registry updating"
            try:
                result = registry_update.build_from_tar1090(
                    registry_update.TAR1090_URL, db_path, progress_fn=_progress
                )
            except Exception as exc:  # never let a download kill the session
                self._logger.warning("registry update failed: %s", exc)
                self._registry_status = "registry update failed"
            else:
                if result.not_modified:
                    self._logger.info("registry already current")
                else:
                    self._logger.info("registry updated: %d rows", result.row_count)
                self._registry_status = None
                self._registry_done.set()

        self._registry_thread = threading.Thread(target=_run, daemon=True)
        self._registry_thread.start()

    # ------------------------------------------------------------------
    # Background fetch thread -- NEVER call any curses function from here.
    # ------------------------------------------------------------------

    def _fetch_loop(self, stop_event: threading.Event, out_queue: queue.Queue) -> None:
        backoff = BACKOFF_BASE_S
        while not stop_event.is_set():
            due = time.monotonic() + self.cfg.source.refresh_s  # period, not fetch + refresh
            try:
                raw = self.source.fetch(self.cfg.source.timeout_s)
                result = self._pipeline.tick(raw, self._clock(), self._search_text)
                display = self.cfg.display
                ordered = sort_aircraft(result.aircraft, display.sort_key, display.sort_reverse)
                self._feed_stale = result.feed_stale
                self._publish(out_queue, (result.snapshot, ordered, None))
                backoff = BACKOFF_BASE_S
                stop_event.wait(max(0.0, due - time.monotonic()))
            except Exception as exc:  # SourceError is expected; anything else is a bug, banner it
                if isinstance(exc, SourceError):
                    self._logger.warning("fetch failed: %s", exc)
                else:
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
        # NO_COLOR (no-color.org): a non-empty value turns "auto" off; an explicit
        # "always" in the config still wins.
        no_color = self.cfg.display.color == "auto" and bool(os.environ.get("NO_COLOR"))
        if self.cfg.display.color != "never" and not no_color:
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

        # Every color name theme.ROW_STYLES can use must appear here: a name that is
        # missing silently falls back to the terminal's default foreground, which is how
        # the scope's rings ended up uncolored the first time.
        color_map = {
            "cyan": curses.COLOR_CYAN,
            "yellow": curses.COLOR_YELLOW,
            "red": curses.COLOR_RED,
            "magenta": curses.COLOR_MAGENTA,
            "green": curses.COLOR_GREEN,
            "blue": curses.COLOR_BLUE,
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
        """Write one string to the screen, swallowing curses.error and ValueError (an embedded NUL).

        Every draw call in this module routes through here so that writing to the
        bottom-right cell, or any other addstr() failure on a too-small/narrow terminal,
        never crashes the program -- it just skips that one piece of text.
        """
        with contextlib.suppress(curses.error, ValueError):
            stdscr.addstr(y, x, text, attr)

    def _row_style_name(self, ac: Aircraft) -> str:
        """Pick a row style: status first, altitude band as the resting state.

        Anything the user needs to react to (an emergency, something overhead, a watched
        aircraft) outranks decoration. Everything else is colored by altitude band, so an
        ordinary screenful still carries information instead of being a wall of one color.
        """
        if ac.alert_level == AlertLevel.EMERGENCY:
            return "emergency"
        if ac.is_military:
            return "military"
        if ac.is_watched:
            return "watchlist"
        if ac.alert_level == AlertLevel.OVERHEAD:
            return "overhead"
        if ac.alert_level in (AlertLevel.INBOUND, AlertLevel.OUTBOUND):
            return "inbound"
        if ac.is_stale:
            return "stale"
        return theme.altitude_style(ac.altitude_ft, ac.on_ground)

    def _radar_width(self, width: int) -> int:
        """Columns to give the scope, or 0 when it is off or the terminal is too narrow.

        The table needs most of the width to stay readable, so the scope only appears once
        there is genuinely room for both -- squeezing them together would make the table
        drop columns to pay for a scope too small to read.
        """
        if not self._radar_on or width < RADAR_MIN_TOTAL_WIDTH:
            return 0
        return min(RADAR_MAX_WIDTH, max(RADAR_MIN_WIDTH, width // 3))

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

        # Row 0 is live state, the bottom row is the key hints, and everything between is
        # the actual content. Nothing on screen spends a row on things that never change.
        age = None if last_success_at is None else max(0.0, time.time() - last_success_at)
        alert_count = sum(
            1 for ac in aircraft_list if ac.alert_level not in (AlertLevel.NONE, AlertLevel.NEARBY)
        )
        status = bars.status_line_text(
            width,
            source_ok=last_error is None,
            source_message=last_error,
            last_success_age_s=age,
            aircraft_count=len(aircraft_list),
            alert_count=alert_count,
            msg_rate=self._msg_rate,
            paused=paused,
            clock=time.strftime("%H:%M:%S"),
            note=self._current_note(),
            feed_stale=self._feed_stale,
        )
        bad = last_error is not None or self._feed_stale
        status_attr = self._attrs.get("bad" if bad else "ok", header_attr)
        self._safe_addstr(stdscr, 0, 0, status, status_attr)

        border_style = display.borders
        ascii_only = border_style == "ascii"

        radar_width = self._radar_width(width)
        table_width = width - radar_width - (RADAR_GAP if radar_width else 0)

        columns = columns_mod.layout(display.columns, table_width, display.owner_width)
        widths = columns_mod.compute_widths(columns, table_width, display.owner_width)

        header_line = columns_mod.format_header(columns, widths, border_style)
        self._safe_addstr(stdscr, 1, 0, header_line[:table_width], header_attr)

        divider_char = theme.glyphs(border_style)["h"] or "-"
        self._safe_addstr(stdscr, 2, 0, divider_char * table_width, self._attrs.get("chrome", 0))

        table_top = 3
        table_height = max(0, height - table_top - 1)

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
            self._safe_addstr(stdscr, table_top + i, 0, row_text[:table_width], attr)

        if radar_width:
            self._draw_radar(
                stdscr,
                aircraft_list,
                left=table_width + RADAR_GAP,
                top=1,
                width=radar_width,
                height=height - 2,
                ascii_only=ascii_only,
            )

        key_line = bars.key_bar_text(width, BAR_KEYMAP)
        self._safe_addstr(stdscr, height - 1, 0, key_line, self._attrs.get("chrome", normal_attr))

        if self._screen is not None:
            self._draw_screen_overlay(stdscr, width, height)

        stdscr.noutrefresh()
        curses.doupdate()

    def _draw_radar(
        self,
        stdscr,
        aircraft_list: list[Aircraft],
        left: int,
        top: int,
        width: int,
        height: int,
        ascii_only: bool,
    ) -> None:
        """Draw the scope pane: a bordered box with the PPI inside and a range caption.

        Cells are drawn one at a time because each carries its own style -- a ring dot, a
        compass tick and an emergency contact land in the same row and must not share an
        attribute.
        """
        if width < 8 or height < 5:
            return

        chrome = self._attrs.get("chrome", 0)

        selected = self._selected_aircraft(aircraft_list)
        # The scope can only be as tall as its width allows once the 2:1 cell aspect is
        # applied; asking for more just pads it with blank rows and strands the caption
        # far below the rings.
        scope_h = min(height - 2, int(width * radar.ASPECT) + 2)
        scope = radar.render(
            aircraft_list,
            width=width,
            height=scope_h,
            range_mi=self.cfg.filter.radius,
            selected_hex=selected.hex if selected else None,
            ascii_only=ascii_only,
        )

        for row_index, line in enumerate(scope.lines):
            styles = scope.styles[row_index]
            for col_index, char in enumerate(line):
                if char == " ":
                    continue
                attr = self._attrs.get(styles[col_index], 0)
                self._safe_addstr(stdscr, top + row_index, left + col_index, char, attr)

        caption = radar.range_label(self.cfg.filter.radius, self._distance_unit_label())
        self._safe_addstr(stdscr, top + scope.height, left, caption[:width].center(width), chrome)

    def _distance_unit_label(self) -> str:
        return {"imperial": "mi", "metric": "km", "aviation": "nm"}.get(
            self.cfg.display.units, "mi"
        )

    def _page_size(self, stdscr) -> int:
        """Rows of table visible right now, for PageUp/PageDown."""
        height, _ = stdscr.getmaxyx()
        return max(1, height - 4)

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

        # 96 rather than 76: the help screen's column glossary and the settings screen's
        # help text are both written as sentences, and clipping them mid-word on a wide
        # terminal wasted room that was sitting right there.
        box_w = max(20, min(width - 4, 96))
        inner_w = box_w - 4
        # The box itself can hold height-4 body rows (two borders, and box_h below adds two
        # rows of padding), so asking a screen for height-8 threw away four usable rows and
        # made long screens drop content that would have fitted.
        lines = screen.render_lines(inner_w, max(1, height - 6))
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
        top_line = tl + h + title + h * max(0, box_w - 3 - len(title)) + tr
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
            self._screen = HelpScreen(HELP_KEYMAP, __version__)
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
            self._screen = ColumnsScreen(display.columns, display.borders)
        elif name == "watchlist":
            self._screen = WatchlistScreen(
                self._watch_entries, self._selected_aircraft(aircraft_list)
            )
        elif name == "settings":
            self._screen = SettingsScreen(self.cfg)
        elif name == "data":
            self._screen = DataScreen(
                self.cfg.registry.db,
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
        new_cfg._file_layer = self.cfg._file_layer
        self._edited |= changed_keys(self.cfg, new_cfg)
        self.cfg = new_cfg
        self._pipeline.cfg = new_cfg

        # Settings that feed long-lived objects have to be re-read, not just stored.
        self._enricher = self._build_enricher()
        self._dispatcher = self._build_dispatcher()

        if getattr(screen, "save_requested", False):
            path = self.cfg._config_path or default_config_path()
            layer = layer_for_save(self.cfg._file_layer, self.cfg, self._edited)
            try:
                config_write.dump_layer(layer, path)
            except OSError as exc:
                self._logger.warning("could not save configuration to %s: %s", path, exc)
                self._flash(f"could not save settings: {exc.strerror or exc}")
            else:
                self.cfg._config_path = path
                self.cfg._file_layer = layer
                self._edited.clear()
                self._logger.info("configuration saved to %s", path)
                self._flash("settings saved")

    def _current_note(self) -> str | None:
        """The status-line note: a recent flash message, else the registry job's progress."""
        if self._flash_text is not None:
            text, until = self._flash_text
            if time.monotonic() < until:
                return text
            self._flash_text = None
        return self._registry_status

    def _flash(self, text: str, seconds: float = 6.0) -> None:
        """Show a short message in the status line for a few seconds."""
        self._flash_text = (text, time.monotonic() + seconds)

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

            # A finished registry download changes what the enricher can resolve, so it
            # is swapped in here, on the main thread, rather than under a running fetch.
            if self._registry_done.is_set():
                self._registry_done.clear()
                self._enricher = self._build_enricher()
                dirty = True

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
                if self._screen.handle_key(key) in ("close", "close-and-save"):
                    self._close_screen()
                continue

            action = ACTION_FOR_KEY.get(key)
            if action == "quit":
                return 0
            if action in _SCREEN_ACTIONS:
                self._open_screen(action, aircraft_list)
            elif action == "reverse":
                self.cfg.display.sort_reverse = not self.cfg.display.sort_reverse
            elif action == "pause":
                paused = not paused
            elif action == "radar":
                self._radar_on = not self._radar_on
            elif action == "units":
                current = self.cfg.display.units
                index = _UNIT_SYSTEMS.index(current) if current in _UNIT_SYSTEMS else 0
                self.cfg.display.units = _UNIT_SYSTEMS[(index + 1) % len(_UNIT_SYSTEMS)]
            elif action == "up":
                self._cursor -= 1
            elif action == "down":
                self._cursor += 1
            elif action == "top":
                self._cursor = 0
            elif action == "bottom":
                self._cursor = max(0, len(aircraft_list) - 1)
            elif action == "page_up":
                self._cursor -= max(1, self._page_size(stdscr))
            elif action == "page_down":
                self._cursor += max(1, self._page_size(stdscr))
            elif action == "radius":
                if key in (ord("+"), ord("=")):
                    self.cfg.filter.radius += 1.0
                else:
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
            self._start_registry_refresh()
            return self._event_loop(stdscr, stop_event, out_queue)
        except KeyboardInterrupt:
            return 0
        finally:
            stop_event.set()
            fetch_thread.join(timeout=2.0)
            self._dispatch_worker.close(timeout=0.5)

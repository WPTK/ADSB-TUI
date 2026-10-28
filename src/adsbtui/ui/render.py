"""Drawing: turns a ``ViewState`` and a ``Layout`` into curses calls, main thread only.

``layout_for`` is pure geometry. ``Renderer`` reads the view and the live display config, owns
the attribute table and the per-update row and scope caches, and never mutates the view: the
caller clamps the cursor before drawing. It reads no clock; every age and the clock text come
from ``view.now``.
"""

from __future__ import annotations

import contextlib
import curses
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from adsbtui.config import DisplayConfig
from adsbtui.model import Aircraft, AlertLevel
from adsbtui.ui import bars, radar, theme
from adsbtui.ui import columns as columns_mod
from adsbtui.ui.keys import BAR_KEYMAP
from adsbtui.ui.view import ViewState

#: Below this width or height, the normal table layout is abandoned for a single centered
#: "Terminal too small" message -- there simply is not room to draw anything useful, and
#: attempting to would just produce a wall of curses.error-guarded no-ops.
MIN_WIDTH = 40
MIN_HEIGHT = 8

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


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    too_small: bool
    radar_width: int  #: 0 when the scope is off or does not fit
    table_width: int
    table_top: int
    table_height: int


def layout_for(width: int, height: int, view: ViewState) -> Layout:
    """Where everything goes on a ``width`` x ``height`` terminal.

    The table needs most of the width to stay readable, so the scope only appears once there
    is genuinely room for both -- squeezing them together would make the table drop columns
    to pay for a scope too small to read.
    """
    radar_width = 0
    if view.radar_on and width >= RADAR_MIN_TOTAL_WIDTH:
        radar_width = min(RADAR_MAX_WIDTH, max(RADAR_MIN_WIDTH, width // 3))
    return Layout(
        width=width,
        height=height,
        too_small=width < MIN_WIDTH or height < MIN_HEIGHT,
        radar_width=radar_width,
        table_width=width - radar_width - (RADAR_GAP if radar_width else 0),
        table_top=_TOP_ROWS,
        table_height=max(0, height - _TOP_ROWS - 1),
    )


def build_attrs(no_color: bool, color: str = "auto") -> dict[str, int]:
    """Resolve ui.theme's semantic RowStyles to real curses attributes, once.

    Falls back to bold/reverse/dim-only attributes (no color) when colors are unavailable or
    ``color`` is "never". Every curses color call is guarded -- some terminals advertise color
    support but still fail start_color()/init_pair(). ``no_color`` is the NO_COLOR convention
    (no-color.org) already applied by the caller.
    """
    color_ok = False
    # Background used for every color pair: -1 (the terminal's own default background,
    # so colored rows still ride on whatever background the user's terminal theme sets)
    # when use_default_colors() is available, else an explicit black as a fallback.
    pair_bg = curses.COLOR_BLACK
    if color != "never" and not no_color:
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


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write one string to the screen, swallowing curses.error and ValueError (an embedded NUL).

    Every draw call in this module routes through here so that writing to the
    bottom-right cell, or any other addstr() failure on a too-small/narrow terminal,
    never crashes the program -- it just skips that one piece of text.
    """
    with contextlib.suppress(curses.error, ValueError):
        stdscr.addstr(y, x, text, attr)


def _row_style_name(ac: Aircraft) -> str:
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


class Renderer:
    def __init__(self, display: Callable[[], DisplayConfig]) -> None:
        self._display = display
        self.attrs: dict[str, int] = {}
        #: Per-update draw caches (A22). The aircraft list is replaced once per fetch, but
        #: the screen is redrawn every second and on every key; the table rows and the
        #: radar plot only change when the list, the layout or the scope settings do.
        self._row_cache: tuple[object, list[Aircraft], dict[int, str]] | None = None
        self._scope_cache: tuple[list[Aircraft], object, radar.Scope] | None = None

    def setup(self) -> None:
        """Resolve the colours; needs a live curses screen, so it is not done in __init__."""
        color = self._display().color
        no_color = color == "auto" and bool(os.environ.get("NO_COLOR"))
        self.attrs = build_attrs(no_color, color)

    def draw(self, stdscr: Any, view: ViewState, layout: Layout) -> None:
        stdscr.erase()
        attrs = self.attrs

        if layout.too_small:
            width, height = layout.width, layout.height
            msg = "Terminal too small"[: max(0, width)]
            y = max(0, height // 2)
            x = max(0, (width - len(msg)) // 2)
            _safe_addstr(stdscr, y, x, msg, attrs.get("emergency", curses.A_BOLD))
            stdscr.noutrefresh()
            curses.doupdate()
            return

        width, height = layout.width, layout.height
        display = self._display()
        aircraft_list = view.rows
        normal_attr = attrs.get("normal", curses.A_NORMAL)
        header_attr = attrs.get("header", curses.A_BOLD)

        # Row 0 is live state, the bottom row is the key hints, and everything between is
        # the actual content. Nothing on screen spends a row on things that never change.
        error = view.error
        alert_count = sum(
            1 for ac in aircraft_list if ac.alert_level not in (AlertLevel.NONE, AlertLevel.NEARBY)
        )
        status = bars.status_line_text(
            width,
            source_ok=error is None,
            source_message=error,
            last_success_age_s=view.last_success_age(),
            aircraft_count=len(aircraft_list),
            alert_count=alert_count,
            msg_rate=view.msg_rate,
            paused=view.paused,
            clock=view.clock_text(),
            note=view.note(),
            feed_stale=view.feed_stale,
            demo=view.demo,
        )
        bad = error is not None or view.feed_stale
        _safe_addstr(stdscr, 0, 0, status, attrs.get("bad" if bad else "ok", header_attr))

        border_style = display.borders
        ascii_only = border_style == "ascii"
        table_width = layout.table_width

        columns = columns_mod.layout(display.columns, table_width, display.owner_width)
        widths = columns_mod.compute_widths(columns, table_width, display.owner_width)

        header_line = columns_mod.format_header(columns, widths, border_style)
        _safe_addstr(stdscr, 1, 0, header_line[:table_width], header_attr)

        divider_char = theme.glyphs(border_style)["h"] or "-"
        _safe_addstr(stdscr, 2, 0, divider_char * table_width, attrs.get("chrome", 0))

        scroll = view.scroll
        visible = aircraft_list[scroll : scroll + layout.table_height]

        row_key = (tuple(widths), tuple(columns), display.units, ascii_only)
        if (
            self._row_cache is None
            or self._row_cache[0] != row_key
            or self._row_cache[1] is not aircraft_list
        ):
            self._row_cache = (row_key, aircraft_list, {})
        row_texts = self._row_cache[2]

        for i, ac in enumerate(visible):
            index = scroll + i
            row_text = row_texts.get(index)
            if row_text is None:
                row_text = columns_mod.format_row(ac, columns, widths, display.units, ascii_only)
                row_texts[index] = row_text
            if index == view.cursor and aircraft_list:
                attr = attrs.get("selected", curses.A_REVERSE)
            else:
                attr = attrs.get(_row_style_name(ac), normal_attr)
            _safe_addstr(stdscr, layout.table_top + i, 0, row_text[:table_width], attr)

        if layout.radar_width:
            self._draw_radar(
                stdscr,
                view,
                left=table_width + RADAR_GAP,
                top=1,
                width=layout.radar_width,
                height=height - 2,
                ascii_only=ascii_only,
            )

        key_line = bars.key_bar_text(width, BAR_KEYMAP)
        _safe_addstr(stdscr, height - 1, 0, key_line, attrs.get("chrome", normal_attr))

        if view.screen is not None:
            self._draw_screen_overlay(stdscr, view.screen, width, height)

        stdscr.noutrefresh()
        curses.doupdate()

    def _draw_radar(
        self,
        stdscr: Any,
        view: ViewState,
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

        attrs = self.attrs
        chrome = attrs.get("chrome", 0)
        aircraft_list = view.rows
        radius = view.cfg.filter.radius

        selected = view.selected()
        # The scope can only be as tall as its width allows once the 2:1 cell aspect is
        # applied; asking for more just pads it with blank rows and strands the caption
        # far below the rings.
        scope_h = min(height - 2, int(width * radar.ASPECT) + 2)
        scope_key = (width, scope_h, radius, selected.hex if selected else None, ascii_only)
        cached = self._scope_cache
        if cached is not None and cached[0] is aircraft_list and cached[1] == scope_key:
            scope = cached[2]
        else:
            scope = radar.render(
                aircraft_list,
                width=width,
                height=scope_h,
                range_mi=radius,
                selected_hex=selected.hex if selected else None,
                ascii_only=ascii_only,
            )
            self._scope_cache = (aircraft_list, scope_key, scope)

        for row_index, line in enumerate(scope.lines):
            styles = scope.styles[row_index]
            for col_index, char in enumerate(line):
                if char == " ":
                    continue
                attr = attrs.get(styles[col_index], 0)
                _safe_addstr(stdscr, top + row_index, left + col_index, char, attr)

        caption = radar.range_label(radius, self._distance_unit_label())
        _safe_addstr(stdscr, top + scope.height, left, caption[:width].center(width), chrome)

    def _distance_unit_label(self) -> str:
        return {"imperial": "mi", "metric": "km", "aviation": "nm"}.get(self._display().units, "mi")

    def _draw_screen_overlay(self, stdscr: Any, screen: Any, width: int, height: int) -> None:
        """Draw the active screen centered over the table, inside a bordered box.

        The screen itself only produces plain strings (that is what makes every screen
        unit-testable without a terminal); all the curses geometry lives here.
        """
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

        glyph = theme.glyphs(self._display().borders)
        h, v = glyph.get("h") or "-", glyph.get("v") or "|"
        tl = glyph.get("tl") or "+"
        tr = glyph.get("tr") or "+"
        bl = glyph.get("bl") or "+"
        br = glyph.get("br") or "+"
        border_attr = self.attrs.get("header", curses.A_BOLD)
        body_attr = self.attrs.get("normal", curses.A_NORMAL)

        title = f" {screen.title} "[: max(0, box_w - 4)]
        top_line = tl + h + title + h * max(0, box_w - 3 - len(title)) + tr
        _safe_addstr(stdscr, top, left, top_line[:box_w], border_attr)

        for i in range(box_h - 2):
            text = lines[i] if i < len(lines) else ""
            row = v + " " + text.ljust(inner_w)[:inner_w] + " " + v
            _safe_addstr(stdscr, top + 1 + i, left, row[:box_w], body_attr)

        bottom_line = (bl + h * (box_w - 2) + br)[:box_w]
        _safe_addstr(stdscr, top + box_h - 1, left, bottom_line, border_attr)

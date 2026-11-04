"""Key -> action -> change to the view, plus whatever the outside world must be told.

No curses and no clock: it sees the layout and ``view.now`` and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

from adsbtui.ui import radar
from adsbtui.ui.columns import column_at
from adsbtui.ui.effects import Effects
from adsbtui.ui.keys import ACTION_FOR_KEY
from adsbtui.ui.mouse import Geometry, MouseEvent
from adsbtui.ui.screen_stack import ScreenStack
from adsbtui.ui.sorting import sort_key_for_column
from adsbtui.ui.view import ViewState

if TYPE_CHECKING:
    from adsbtui.ui.render import Layout

_UNIT_SYSTEMS = ["imperial", "metric", "aviation"]

#: Rows the cursor moves per wheel notch.
_WHEEL_STEP = 3


class Outcome(Enum):
    CONTINUE = "continue"
    QUIT = "quit"


class Controller:
    def __init__(self, view: ViewState, effects: Effects) -> None:
        self.view = view
        self.effects = effects
        self.screens = ScreenStack(view, effects)

    def handle_key(self, key: int, layout: Layout) -> Outcome:
        """A key goes to the open screen first; otherwise it is looked up in the key table."""
        screen = self.view.screen
        if screen is not None:
            if screen.handle_key(key) in ("close", "close-and-save"):
                self.screens.close()
            return Outcome.CONTINUE
        handler = _HANDLERS.get(ACTION_FOR_KEY.get(key, ""))
        if handler is None:
            return Outcome.CONTINUE
        return handler(self, key, layout) or Outcome.CONTINUE

    def handle_mouse(self, event: MouseEvent, layout: Layout, geometry: Geometry | None) -> bool:
        """Act on a mouse event; True when anything on screen changed.

        Mouse is ignored while a screen is open (the screens are keyboard-driven and cover the
        table). ``geometry`` is where the last draw put the table and scope, so a click is
        mapped with the columns and widths that were actually shown.
        """
        view = self.view
        if view.screen is not None or geometry is None or layout.too_small:
            return self._set_hover(None)

        if event.kind in ("wheel_up", "wheel_down"):
            before = view.cursor
            view.move_cursor(view.cursor + (-_WHEEL_STEP if event.kind == "wheel_up" else _WHEEL_STEP))
            return view.cursor != before

        scope_hex = self._scope_hex(event, geometry)
        if event.kind == "move":
            return self._set_hover(scope_hex)

        if event.kind not in ("press", "click"):
            return False
        if scope_hex is not None:
            return self._select_hex(scope_hex)
        if event.y == geometry.header_row and event.x < geometry.table_width:
            return self._sort_by(column_at(list(geometry.columns), geometry.widths, event.x))
        index = view.scroll + event.y - geometry.first_row
        if (
            event.y >= geometry.first_row
            and event.x < geometry.table_width
            and 0 <= index < len(view.rows)
            and event.y < geometry.first_row + layout.table_height
        ):
            view.move_cursor(index)
            return True
        return False

    @staticmethod
    def _scope_hex(event: MouseEvent, geometry: Geometry) -> str | None:
        scope = geometry.scope
        if scope is None:
            return None
        return radar.hex_at(scope, event.x - geometry.scope_left, event.y - geometry.scope_top)

    def _set_hover(self, hex_: str | None) -> bool:
        if self.view.hover_hex == hex_:
            return False
        self.view.hover_hex = hex_
        return True

    def _select_hex(self, hex_: str) -> bool:
        for index, ac in enumerate(self.view.rows):
            if ac.hex == hex_:
                self.view.move_cursor(index)
                return True
        return False

    def _sort_by(self, column: str | None) -> bool:
        """Header click: sort by that column, or reverse the order if it is already the sort."""
        sort_key = sort_key_for_column(column) if column else None
        if sort_key is None:
            return False
        display = self.view.cfg.display
        if display.sort_key == sort_key:
            display.sort_reverse = not display.sort_reverse
        else:
            display.sort_key = sort_key
            display.sort_reverse = False
        self.view.resort()
        return True

    # One small method per action; _HANDLERS below is the dispatch table.

    def _quit(self, key: int, layout: Layout) -> Outcome:
        return Outcome.QUIT

    def _reverse(self, key: int, layout: Layout) -> None:
        display = self.view.cfg.display
        display.sort_reverse = not display.sort_reverse

    def _pause(self, key: int, layout: Layout) -> None:
        self.view.paused = not self.view.paused

    def _radar(self, key: int, layout: Layout) -> None:
        self.view.radar_on = not self.view.radar_on

    def _units(self, key: int, layout: Layout) -> None:
        display = self.view.cfg.display
        index = _UNIT_SYSTEMS.index(display.units) if display.units in _UNIT_SYSTEMS else 0
        display.units = _UNIT_SYSTEMS[(index + 1) % len(_UNIT_SYSTEMS)]

    def _radius(self, key: int, layout: Layout) -> None:
        f = self.view.cfg.filter
        if key in (ord("+"), ord("=")):
            f.radius += 1.0
        else:
            f.radius = max(1.0, f.radius - 1.0)
        self.effects.refresh_now()

    def _up(self, key: int, layout: Layout) -> None:
        self.view.move_cursor(self.view.cursor - 1)

    def _down(self, key: int, layout: Layout) -> None:
        self.view.move_cursor(self.view.cursor + 1)

    def _top(self, key: int, layout: Layout) -> None:
        self.view.move_cursor(0)

    def _bottom(self, key: int, layout: Layout) -> None:
        self.view.move_cursor(len(self.view.rows) - 1)

    def _page_up(self, key: int, layout: Layout) -> None:
        self.view.move_cursor(self.view.cursor - max(1, layout.table_height))

    def _page_down(self, key: int, layout: Layout) -> None:
        self.view.move_cursor(self.view.cursor + max(1, layout.table_height))


def _opener(name: str) -> Callable[[Controller, int, Layout], None]:
    def handler(controller: Controller, key: int, layout: Layout) -> None:
        controller.screens.open(name)

    return handler


_SCREEN_ACTIONS = ("help", "settings", "data", "detail", "filters", "sort", "columns", "watchlist")

_HANDLERS: dict[str, Callable[[Controller, int, Layout], Outcome | None]] = {
    **{name: _opener(name) for name in _SCREEN_ACTIONS},
    "quit": Controller._quit,
    "reverse": Controller._reverse,
    "pause": Controller._pause,
    "radar": Controller._radar,
    "units": Controller._units,
    "radius": Controller._radius,
    "up": Controller._up,
    "down": Controller._down,
    "top": Controller._top,
    "bottom": Controller._bottom,
    "page_up": Controller._page_up,
    "page_down": Controller._page_down,
}

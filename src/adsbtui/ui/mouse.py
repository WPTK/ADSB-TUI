"""Mouse input as plain data: a curses button state becomes a ``MouseEvent`` here, and the
controller acts on it without touching curses.

The curses constants differ by build (ncurses, PDCurses on Windows), so every one is looked up
with ``getattr`` and a missing one simply never matches. Nothing here calls a curses function.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass

from adsbtui.ui.radar import Scope

#: How soon after a press the same cell's "clicked" report is taken as that press again.
#: ncurses with ``mouseinterval(0)`` sends press and release only, but PDCurses and ncurses
#: without ``mouseinterval`` can send both a press and a click for one physical click, and a
#: header click acted on twice would sort and then immediately reverse.
_DUPLICATE_WINDOW_S = 0.3


@dataclass(frozen=True)
class MouseEvent:
    x: int
    y: int
    #: "press" and "click" both mean the primary button went down/was clicked; "move" is a
    #: position report with no button; "wheel_up" and "wheel_down" are the scroll wheel.
    kind: str


@dataclass(frozen=True)
class Geometry:
    """Where the last draw put the things a mouse can hit, so a click is mapped with the very
    same columns, widths and scope the screen shows."""

    columns: tuple[str, ...]
    widths: dict[str, int]
    table_width: int
    header_row: int
    first_row: int
    scope_left: int = 0
    scope_top: int = 0
    scope: Scope | None = None


def mask() -> int:
    """The mouse-event mask to hand to ``curses.mousemask``, from whichever constants exist."""
    names = (
        "BUTTON1_CLICKED",
        "BUTTON1_PRESSED",
        "BUTTON1_RELEASED",
        "REPORT_MOUSE_POSITION",
        "BUTTON4_PRESSED",
        "BUTTON5_PRESSED",
    )
    out = 0
    for name in names:
        out |= getattr(curses, name, 0)
    return out


def _has(bstate: int, name: str) -> bool:
    flag = getattr(curses, name, 0)
    return bool(flag) and bool(bstate & flag)


def translate(bstate: int, x: int, y: int) -> MouseEvent | None:
    """A ``MouseEvent`` for a ``curses.getmouse()`` result, or None for one nobody acts on
    (a button release, a button we do not use)."""
    if _has(bstate, "BUTTON4_PRESSED"):
        return MouseEvent(x, y, "wheel_up")
    if _has(bstate, "BUTTON5_PRESSED"):
        return MouseEvent(x, y, "wheel_down")
    if _has(bstate, "BUTTON1_PRESSED"):
        return MouseEvent(x, y, "press")
    if _has(bstate, "BUTTON1_CLICKED"):
        return MouseEvent(x, y, "click")
    if _has(bstate, "BUTTON1_RELEASED"):
        return None
    if _has(bstate, "REPORT_MOUSE_POSITION"):
        return MouseEvent(x, y, "move")
    return None


class ClickDeduper:
    """Drops a "click" that arrives right after the "press" of the same cell."""

    def __init__(self) -> None:
        self._last: tuple[int, int, float] | None = None

    def accept(self, event: MouseEvent, now: float) -> bool:
        if event.kind == "press":
            self._last = (event.x, event.y, now)
            return True
        if event.kind == "click":
            last = self._last
            if last is not None and (last[0], last[1]) == (event.x, event.y):
                if now - last[2] < _DUPLICATE_WINDOW_S:
                    return False
        return True

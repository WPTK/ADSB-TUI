"""The one key table. The event loop, the Help overlay and the key bar all read it, so a
key cannot work without being documented or be advertised without working."""

from __future__ import annotations

import curses
from dataclasses import dataclass


@dataclass(frozen=True)
class Binding:
    action: str  #: handler in controller._HANDLERS
    label: str  #: shown in Help and (when ``bar``) in the key bar
    keys: tuple[int, ...]
    bar: str = ""  #: label in the bottom key bar; empty = not advertised there


_ENTER = (curses.KEY_ENTER, ord("\n"), ord("\r"))

#: Order matters for the key bar, which truncates to the terminal width: the two keys a
#: user must never be unable to find -- how to get help and how to get out -- come first.
BINDINGS: tuple[Binding, ...] = (
    Binding("help", "F1/?", (curses.KEY_F1, ord("?")), bar="F1/?"),
    Binding("quit", "q/F10", (ord("q"), ord("Q"), curses.KEY_F10), bar="q"),
    Binding("settings", "F2/,", (curses.KEY_F2, ord(",")), bar="F2"),
    Binding("data", "F8/D", (curses.KEY_F8, ord("D")), bar="F8"),
    Binding("detail", "Enter/d", (*_ENTER, ord("d")), bar="Enter"),
    Binding("sort", "s/F5", (ord("s"), curses.KEY_F5), bar="s"),
    Binding("reverse", "S", (ord("S"),)),
    Binding("filters", "f//F3", (ord("f"), ord("/"), curses.KEY_F3, curses.KEY_F4), bar="f"),
    Binding("columns", "c/F6", (ord("c"), curses.KEY_F6), bar="c"),
    Binding("watchlist", "w/F7", (ord("w"), curses.KEY_F7), bar="w"),
    Binding("pause", "p", (ord("p"), ord("P")), bar="p"),
    Binding("radar", "r", (ord("r"), ord("R")), bar="r"),
    Binding("units", "u", (ord("u"),), bar="u"),
    Binding("radius", "+/-", (ord("+"), ord("="), ord("-"), ord("_")), bar="+/-"),
    Binding("up", "Up/k", (curses.KEY_UP, ord("k"))),
    Binding("down", "Down/j", (curses.KEY_DOWN, ord("j"))),
    Binding("page_up", "PgUp", (curses.KEY_PPAGE,)),
    Binding("page_down", "PgDn", (curses.KEY_NPAGE,)),
    Binding("top", "Home", (curses.KEY_HOME,)),
    Binding("bottom", "End", (curses.KEY_END,)),
)

#: Key code -> action name, for the event loop.
ACTION_FOR_KEY: dict[int, str] = {k: b.action for b in BINDINGS for k in b.keys}

#: action -> label for every binding (Help) and for the bar subset (key bar).
HELP_KEYMAP: dict[str, str] = {b.action: b.label for b in BINDINGS}
BAR_KEYMAP: dict[str, str] = {b.action: b.bar for b in BINDINGS if b.bar}

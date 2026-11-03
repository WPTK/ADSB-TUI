"""The Help screen: a dismiss-on-any-key overlay listing every keybinding.

HelpScreen follows the Screen design contract used across ui/screens/: a read-only
``title``, a pure ``render_lines(width, height)`` that returns plain, already-clipped
strings with no curses calls, and a ``handle_key(key)`` that reports navigation intent
back to the caller as a short string ("close" here, since any key dismisses this overlay).
"""

from __future__ import annotations

import curses
from collections.abc import Mapping


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width]


def _action_label(action: str) -> str:
    """Turn a keymap action name (e.g. "watchlist") into a display label ("Watchlist")."""
    return action.replace("_", " ").title()


#: Column header -> what it actually means, in words. Even with the headers spelled out,
#: a table of aviation quantities has terms that are only obvious to someone who already
#: knows them, and "go and read the README" is not an answer while you are looking at the
#: screen. Ordered as the columns are by default, so the list reads left to right.
COLUMN_GLOSSARY: tuple[tuple[str, str], ...] = (
    ("FLIGHT", "Callsign the aircraft is transmitting"),
    ("TAIL", "Registration, e.g. N12345"),
    ("TYPE", "ICAO type code, e.g. B738 for a 737-800"),
    ("ALTITUDE", "Height above sea level; GND means on the ground"),
    ("CLIMB", "Climb or descent rate, with an arrow for the direction"),
    ("SPEED", "Ground speed: speed over the ground, not through the air"),
    ("DISTANCE", "How far the aircraft is from you"),
    ("DIRECTION", "Compass direction FROM you TO the aircraft"),
    ("CLOSEST PASS", "How close it will get, and in how long, if it holds course"),
    ("OWNER", "Registered owner or operator"),
    ("FLAGS", "MIL military, PIA private address, LADD limited display, plus size class"),
    ("AGE", "Time since its position last updated"),
    (
        "ALERT",
        "OVHD overhead, NEAR ground, INBND closing, EMERG squawk",
    ),
    ("ICAO", "The 24-bit address that uniquely identifies the airframe"),
)


class HelpScreen:
    """Displays the full keybinding reference. Any keypress closes it.

    keymap maps action name -> key label (e.g. {"help": "F1", "quit": "F10 / q"}). The
    order of entries in render_lines follows the order the keymap was given in, so callers
    control the display order by the order they build the dict in.
    """

    def __init__(self, keymap: Mapping[str, str], version: str) -> None:
        self._keymap: dict[str, str] = dict(keymap)
        self._version = version
        self._offset = 0  #: first body line shown; clamped when rendering
        self._page = 10  #: lines per PgUp/PgDn, set from the last render

    @property
    def title(self) -> str:
        return "Help"

    @property
    def keymap(self) -> dict[str, str]:
        return dict(self._keymap)

    @property
    def version(self) -> str:
        return self._version

    def handle_key(self, key: int) -> str | None:
        """Scroll keys move the view; any other key dismisses the overlay."""
        step = {
            curses.KEY_UP: -1,
            ord("k"): -1,
            curses.KEY_DOWN: 1,
            ord("j"): 1,
            curses.KEY_PPAGE: -self._page,
            curses.KEY_NPAGE: self._page,
            curses.KEY_HOME: -(10**6),
            curses.KEY_END: 10**6,
        }.get(key)
        if step is None:
            return "close"
        self._offset = max(0, self._offset + step)
        return None

    def render_lines(self, width: int, height: int) -> list[str]:
        if height <= 0 or width <= 0:
            return []

        key_col = max((len(label) for label in self._keymap.values()), default=0)
        keys: list[str] = ["KEYS"]
        for action, key_label in self._keymap.items():
            keys.append(_clip(f"  {key_label:<{key_col}}  {_action_label(action)}", width))

        name_col = max(len(name) for name, _ in COLUMN_GLOSSARY)
        glossary: list[str] = ["", _clip("COLUMNS", width)]
        glossary += [
            _clip(f"  {name:<{name_col}}  {meaning}", width) for name, meaning in COLUMN_GLOSSARY
        ]

        footer = [
            "",
            _clip(f"Version: {self._version}", width),
            _clip("Up/Down/PgUp/PgDn scroll; any other key closes", width),
        ]

        body = keys + glossary
        if len(body) + len(footer) <= height:
            return body + footer
        # Too tall: scroll the body and keep the footer, which names the way out.
        room = max(0, height - len(footer))
        self._page = max(1, room - 1)
        self._offset = min(self._offset, len(body) - room)
        return (body[self._offset : self._offset + room] + footer)[:height]

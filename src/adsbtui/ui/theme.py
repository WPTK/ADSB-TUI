"""Color and border theme data for the curses UI.

This module holds only plain data -- box-drawing character sets and a semantic row-style
palette -- and it must never import the curses module or call any curses function: it is
loaded and unit-tested without a terminal. A later, curses-aware module (built in a
further build phase) is responsible for mapping the color/attribute names defined here to
real curses attributes once curses.start_color() has run.

Colors are meant to ride on the terminal's own theme rather than impose one. This file
defines a small, fixed accent set on top of whatever the terminal's default colors are:

  * cyan for chrome (borders, headers)
  * yellow for proximity/watchlist highlighting
  * red combined with reverse video for emergencies
  * magenta for military aircraft
  * dim for stale rows
  * green for climbing aircraft
  * yellow for descending aircraft

There is deliberately no green-on-black "hacker terminal" default -- that look is only
available as an opt-in "retro" theme layered on top of this data by a later module, never
the baseline.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Box-drawing character sets, keyed by the display.borders config value. Each set
#: provides:
#:   h, v        -- straight horizontal/vertical line segments
#:   tl, tr, bl, br -- corners (top-left, top-right, bottom-left, bottom-right)
#:   lt, rt, tt, bt -- T-junctions opening left/right/top/bottom
#:   x           -- four-way cross
#: "ascii" collapses every corner/junction to "+"; "none" is all empty strings so callers
#: can index it the same way as the other two styles without a special case (a borderless
#: layout still asks for glyphs("none")["h"], it just gets "").
BORDER_CHARS: dict[str, dict[str, str]] = {
    "unicode": {
        "h": "─",
        "v": "│",
        "tl": "┌",
        "tr": "┐",
        "bl": "└",
        "br": "┘",
        "lt": "├",
        "rt": "┤",
        "tt": "┬",
        "bt": "┴",
        "x": "┼",
    },
    "ascii": {
        "h": "-",
        "v": "|",
        "tl": "+",
        "tr": "+",
        "bl": "+",
        "br": "+",
        "lt": "+",
        "rt": "+",
        "tt": "+",
        "bt": "+",
        "x": "+",
    },
    "none": {
        "h": "",
        "v": "",
        "tl": "",
        "tr": "",
        "bl": "",
        "br": "",
        "lt": "",
        "rt": "",
        "tt": "",
        "bt": "",
        "x": "",
    },
}


def glyphs(style: str) -> dict[str, str]:
    """Return the border character set for style ("unicode", "ascii", or "none").

    Raises ValueError for any other style name.
    """
    try:
        return BORDER_CHARS[style]
    except KeyError:
        raise ValueError(f"unknown border style {style!r}") from None


@dataclass(frozen=True)
class RowStyle:
    """A semantic display style: a color name plus a small set of attribute flags.

    fg names a color that a later curses-aware layer maps to a real curses color (or to
    "default" to inherit the terminal's own foreground -- how "normal" text rides on the
    user's own theme instead of forcing e.g. black-on-white or green-on-black). This
    dataclass carries no curses state itself; it is pure data.
    """

    fg: str  # "default", "cyan", "yellow", "red", "magenta", "green", "white"
    bold: bool = False
    reverse: bool = False
    dim: bool = False


#: Semantic name -> RowStyle. The curses-aware drawing layer looks up styles by name, so
#: this table is the single place that decides what each situation looks like.
ROW_STYLES: dict[str, RowStyle] = {
    "normal": RowStyle(fg="default"),
    "header": RowStyle(fg="cyan", bold=True),
    "selected": RowStyle(fg="default", reverse=True),
    "overhead": RowStyle(fg="yellow", bold=True),
    "inbound": RowStyle(fg="yellow"),
    "emergency": RowStyle(fg="red", bold=True, reverse=True),
    "military": RowStyle(fg="magenta"),
    "watchlist": RowStyle(fg="yellow", bold=True),
    "stale": RowStyle(fg="default", dim=True),
    "new": RowStyle(fg="cyan", bold=True),
    "climb": RowStyle(fg="green"),
    "descend": RowStyle(fg="yellow"),
}

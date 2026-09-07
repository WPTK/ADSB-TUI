"""A PPI radar scope: where the traffic actually is, drawn as a picture.

A table of numbers tells you an aircraft is 6.2 miles out on a bearing of 012. It does not
tell you that three of them are stacked on the same approach line while the rest of the sky
is empty. That is what a scope is for, and it is the thing a terminal aircraft tracker is
supposed to have.

This module is pure: it turns a list of Aircraft into a grid of characters plus a parallel
grid of style names, and never imports curses or touches a terminal. The drawing layer maps
the style names to attributes and blits the cells. That split is what makes the scope
unit-testable without a terminal, the same contract every other ui/ module follows.

Two details matter for it to look right:

  * Terminal cells are about twice as tall as they are wide, so a circle drawn with equal
    row and column counts comes out as an egg. Every vertical distance is therefore halved
    (ASPECT), which is what makes the range rings read as circles.
  * North is up and bearings run clockwise, so screen dx = sin(bearing) and dy =
    -cos(bearing). Getting that backwards produces a scope that is subtly mirrored and
    impossible to trust.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from adsbtui.model import Aircraft, AlertLevel

#: Terminal character cells are roughly twice as tall as wide; halving vertical distance
#: makes a range ring look like a circle instead of an ellipse.
ASPECT = 0.5

#: Fractions of the scope radius to draw range rings at.
RING_FRACTIONS: tuple[float, ...] = (0.33, 0.66, 1.0)

#: Track direction -> glyph, in 45-degree steps starting at north. An arrow pointing where
#: the aircraft is going turns a scatter of dots into something you can read at a glance.
_ARROWS_UNICODE = "↑↗→↘↓↙←↖"
_ARROWS_ASCII = "^/>\\v/<\\"

#: Drawn for an aircraft with no usable track: it is somewhere, heading unknown.
_NO_TRACK_UNICODE = "•"
_NO_TRACK_ASCII = "o"


@dataclass(frozen=True)
class Scope:
    """A rendered scope: character rows plus the style name for each cell.

    styles[row][col] is a ui.theme.ROW_STYLES key, or "" for an unstyled blank. Callers
    zip the two together; nothing here knows what a curses attribute is.
    """

    lines: list[str]
    styles: list[list[str]]

    @property
    def height(self) -> int:
        return len(self.lines)


def _blank(width: int, height: int) -> tuple[list[list[str]], list[list[str]]]:
    grid = [[" "] * width for _ in range(height)]
    styles = [[""] * width for _ in range(height)]
    return grid, styles


def arrow_for_track(track_deg: float | None, ascii_only: bool = False) -> str:
    """The 45-degree arrow glyph nearest to track_deg, or a dot when the track is unknown."""
    arrows = _ARROWS_ASCII if ascii_only else _ARROWS_UNICODE
    if track_deg is None:
        return _NO_TRACK_ASCII if ascii_only else _NO_TRACK_UNICODE
    index = int((track_deg % 360.0) / 45.0 + 0.5) % 8
    return arrows[index]


def _style_for(aircraft: Aircraft, selected: bool) -> str:
    if selected:
        return "selected"
    if aircraft.alert_level == AlertLevel.EMERGENCY:
        return "emergency"
    if aircraft.is_military:
        return "military"
    if aircraft.is_watched:
        return "watchlist"
    if aircraft.alert_level == AlertLevel.OVERHEAD:
        return "overhead"
    if aircraft.alert_level in (AlertLevel.INBOUND, AlertLevel.OUTBOUND):
        return "inbound"
    if aircraft.is_stale:
        return "stale"
    return "normal"


def render(
    aircraft_list: list[Aircraft],
    width: int,
    height: int,
    range_mi: float,
    selected_hex: str | None = None,
    ascii_only: bool = False,
) -> Scope:
    """Draw the scope: range rings, compass ticks, home, and one glyph per aircraft.

    range_mi is the distance the outermost ring represents (the filter radius, normally).
    Aircraft beyond it, or with no computed distance/bearing yet, are simply not plotted --
    the scope shows the airspace it claims to show, and clamping strays onto the rim would
    invent positions that are not real.

    Later aircraft overwrite earlier ones in the same cell, with one exception: a cell
    already holding an emergency or the selection is never overwritten, so the aircraft
    that matters most is the one that stays visible in a crowded sector.
    """
    # Matches the drawing layer's own floor: anything smaller is a scope too small to read
    # rather than a small scope, and half a circle is worse than none.
    if width < 8 or height < 5:
        return Scope(lines=[], styles=[])

    grid, styles = _blank(width, height)
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    # Radius in columns; rows use the same radius scaled by ASPECT, which is what keeps
    # the rings circular rather than stretched.
    radius_cols = min(cx, cy / ASPECT)
    if radius_cols < 3:
        return Scope(lines=[], styles=[])

    ring_char = "." if ascii_only else "·"

    def put(col: int, row: int, char: str, style: str, force: bool = False) -> None:
        if not (0 <= row < height and 0 <= col < width):
            return
        if not force and styles[row][col] in ("emergency", "selected"):
            return
        grid[row][col] = char
        styles[row][col] = style

    # --- range rings -------------------------------------------------------------
    for fraction in RING_FRACTIONS:
        ring_r = radius_cols * fraction
        # Step by roughly one cell along the circumference so the ring is continuous
        # without drawing the same cell hundreds of times.
        steps = max(16, int(2 * math.pi * ring_r))
        for step in range(steps):
            angle = 2 * math.pi * step / steps
            col = int(round(cx + ring_r * math.sin(angle)))
            row = int(round(cy - ring_r * math.cos(angle) * ASPECT))
            put(col, row, ring_char, "ring")

    # --- compass ticks -----------------------------------------------------------
    for label, bearing in (("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0)):
        rad = math.radians(bearing)
        col = int(round(cx + radius_cols * math.sin(rad)))
        row = int(round(cy - radius_cols * math.cos(rad) * ASPECT))
        put(col, row, label, "header", force=True)

    # --- home --------------------------------------------------------------------
    put(int(round(cx)), int(round(cy)), "+" if ascii_only else "⌂", "header", force=True)

    # --- aircraft ----------------------------------------------------------------
    for ac in aircraft_list:
        if ac.distance_mi is None or ac.bearing_deg is None:
            continue
        if range_mi <= 0 or ac.distance_mi > range_mi:
            continue
        scaled = (ac.distance_mi / range_mi) * radius_cols
        rad = math.radians(ac.bearing_deg)
        col = int(round(cx + scaled * math.sin(rad)))
        row = int(round(cy - scaled * math.cos(rad) * ASPECT))
        selected = selected_hex is not None and ac.hex == selected_hex
        style = _style_for(ac, selected)
        # force=False on purpose. An aircraft still overwrites rings, ticks and the home
        # marker (none of those are protected), but it must NOT overwrite an emergency or
        # the selection already in that cell -- in a busy sector the contact you are
        # watching is exactly the one that would silently disappear.
        put(col, row, arrow_for_track(ac.track_deg, ascii_only), style)

    return Scope(lines=["".join(row) for row in grid], styles=styles)


def range_label(range_mi: float, unit_label: str = "mi") -> str:
    """Caption for under the scope, naming what the outer ring means."""
    if range_mi >= 10:
        return f"range {range_mi:.0f} {unit_label}"
    return f"range {range_mi:.1f} {unit_label}"

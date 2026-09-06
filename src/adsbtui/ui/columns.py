"""Table column layout and cell formatting for the aircraft list.

This module is pure: no curses calls, no network calls, no global state. It decides which
columns fit in a given terminal width, how wide each one gets, and builds the exact plain
strings for the header row and each aircraft row. A later, curses-aware drawing layer
addstr()s the strings this module returns inside its own try/except curses.error guard --
that separation is what makes this module unit-testable without a terminal, and it must
never import curses or call any curses function.

Column selection and sizing is a two-step pipeline:

  1. layout(columns, available_width, owner_width) decides which of the requested columns
     survive at this terminal width, dropping the least useful ones first.
  2. compute_widths(columns, available_width, owner_width) takes the survivors and decides
     how many characters each one gets.

format_header() and format_row() then render those survivors at those widths into a single
line each, using sanitize_cell() to guarantee every cell is exactly its assigned width --
never wider, which is what fixes the original terminal-corrupting overflow bug.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from adsbtui.alerts import classify_trend, decode_category, decode_db_flags, trend_glyph
from adsbtui.geo import compass_point
from adsbtui.model import Aircraft, AlertLevel
from adsbtui.normalize import sanitize
from adsbtui.ui.theme import glyphs
from adsbtui.units import (
    UNIT_SYSTEMS,
    format_altitude,
    format_distance,
    format_speed,
    format_vspeed,
)

#: Vertical-rate threshold used to classify the "vs" column's trend glyph. This mirrors
#: DisplayConfig.vs_threshold_fpm's default (see config.py); format_row()'s signature is
#: fixed by the caller's contract and has no room for a config object, so the module
#: default is used directly rather than threading one more parameter through every call.
DEFAULT_VS_THRESHOLD_FPM = 256.0


@dataclass(frozen=True)
class ColumnSpec:
    """Static description of one table column.

    priority controls drop order in layout(): a LOWER number means HIGHER priority, and
    a higher-priority column is dropped LAST (survives longest as the terminal narrows).
    align is "left" or "right" and controls which side sanitize_cell() pads.
    """

    key: str
    header: str
    min_width: int
    max_width: int
    priority: int
    align: str = "left"


#: key -> ColumnSpec, for every column this module knows how to render.
COLUMN_SPECS: dict[str, ColumnSpec] = {
    "flight": ColumnSpec("flight", "FLIGHT", min_width=7, max_width=10, priority=0, align="left"),
    "alt": ColumnSpec("alt", "ALT", min_width=9, max_width=10, priority=1, align="right"),
    "gs": ColumnSpec("gs", "GS", min_width=7, max_width=9, priority=2, align="right"),
    "dist": ColumnSpec("dist", "DIST", min_width=7, max_width=9, priority=3, align="right"),
    "alert": ColumnSpec("alert", "ALERT", min_width=5, max_width=8, priority=4, align="left"),
    "vs": ColumnSpec("vs", "VS", min_width=10, max_width=12, priority=5, align="left"),
    "brg": ColumnSpec("brg", "BRG", min_width=7, max_width=7, priority=6, align="left"),
    "reg": ColumnSpec("reg", "REG", min_width=6, max_width=8, priority=7, align="left"),
    "type": ColumnSpec("type", "TYPE", min_width=4, max_width=4, priority=8, align="left"),
    "flags": ColumnSpec("flags", "FLAGS", min_width=5, max_width=20, priority=9, align="left"),
    "owner": ColumnSpec("owner", "OWNER", min_width=5, max_width=40, priority=10, align="left"),
    "age": ColumnSpec("age", "AGE", min_width=3, max_width=3, priority=11, align="right"),
    "cpa": ColumnSpec("cpa", "CPA", min_width=10, max_width=12, priority=12, align="left"),
    "hex": ColumnSpec("hex", "HEX", min_width=6, max_width=7, priority=13, align="left"),
}

#: The columns shown out of the box, in display order -- mirrors DisplayConfig.columns'
#: default in config.py.
DEFAULT_COLUMNS: list[str] = [
    "flight",
    "reg",
    "type",
    "alt",
    "vs",
    "gs",
    "dist",
    "brg",
    "cpa",
    "owner",
    "flags",
    "age",
    "alert",
]

#: AlertLevel -> short display tag for the "alert" column. NONE/PASSING render blank --
#: there is nothing worth flagging in a terminal-width table for either state.
_ALERT_TAGS: dict[AlertLevel, str] = {
    AlertLevel.NONE: "",
    AlertLevel.PASSING: "",
    # Kept to 5 characters so they survive the alert column's min_width intact: a
    # truncated "OVERH"/"INBOU" reads as a rendering bug rather than a status.
    AlertLevel.OUTBOUND: "OUTBD",
    AlertLevel.INBOUND: "INBND",
    AlertLevel.OVERHEAD: "OVHD",
    AlertLevel.EMERGENCY: "EMERG",
}


def sanitize_cell(value: Any, width: int, align: str = "left") -> str:
    """Clean a cell value and force it to exactly width characters.

    Reuses normalize.sanitize() to strip NUL/control characters (and non-string input)
    before sizing, then truncates or pads with spaces so the result is always exactly
    width characters long -- never wider, which is what fixes the original overflow
    bug where a long or malformed field could corrupt the rest of the line. Treats every
    character as width 1; full East-Asian wide-character support is out of scope, the
    goal here is only "never crash and never overflow".

    Never raises: a non-string value, None, or an empty/whitespace-only string all become
    an empty cell rather than an exception.
    """
    if width <= 0:
        return ""
    cleaned = sanitize(value) if isinstance(value, str) else None
    if cleaned is None:
        cleaned = ""
    if len(cleaned) > width:
        cleaned = cleaned[:width]
    return cleaned.rjust(width) if align == "right" else cleaned.ljust(width)


def _format_short_duration(seconds: float | None) -> str:
    """Compact age/ETA formatting: "N/A", "<60>s", "<60>m", or "<N>h"."""
    if seconds is None:
        return "N/A"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


# --------------------------------------------------------------------------------------
# Per-column cell builders. Each takes (aircraft, unit_system, ascii_only) -> str. The
# returned string is unpadded/untruncated -- format_row() runs it through sanitize_cell().
# --------------------------------------------------------------------------------------


def _cell_flight(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    return ac.display_flight


def _cell_hex(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    return ac.hex.upper()


def _cell_reg(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    return ac.registration or "N/A"


def _cell_type(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    return ac.type_code or "N/A"


def _cell_alt(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    unit = UNIT_SYSTEMS[unit_system]["altitude"]
    return format_altitude(ac.altitude_ft, unit, on_ground=ac.on_ground)


def _cell_vs(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    trend = classify_trend(ac.baro_rate_fpm, ac.geom_rate_fpm, DEFAULT_VS_THRESHOLD_FPM)
    glyph = trend_glyph(trend, ascii_only=ascii_only)
    if not glyph:
        return ""
    rate = ac.baro_rate_fpm if ac.baro_rate_fpm is not None else ac.geom_rate_fpm
    unit = UNIT_SYSTEMS[unit_system]["vspeed"]
    return f"{glyph}{format_vspeed(rate, unit)}"


def _cell_gs(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    unit = UNIT_SYSTEMS[unit_system]["speed"]
    return format_speed(ac.ground_speed_kt, unit)


def _cell_dist(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    unit = UNIT_SYSTEMS[unit_system]["distance"]
    return format_distance(ac.distance_mi, unit)


def _cell_brg(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    if ac.bearing_deg is None:
        return "N/A"
    return f"{compass_point(ac.bearing_deg)} {ac.bearing_deg:03.0f}"


def _cell_cpa(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    # An aircraft on the ground is not going to fly over anyone; projecting its taxi
    # speed into a closest approach produces a technically-correct but useless "56m".
    if ac.on_ground:
        return ""
    if ac.cpa_distance_mi is None or ac.cpa_seconds is None:
        return ""
    unit = UNIT_SYSTEMS[unit_system]["distance"]
    dist = format_distance(ac.cpa_distance_mi, unit)
    return f"{dist} {_format_short_duration(ac.cpa_seconds)}"


def _cell_owner(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    """Owner, preferring a local registry lookup but falling back to whatever the receiver
    itself supplied.

    Many receivers run readsb with a --db-file and already send ownOp for every aircraft,
    including non-US ones a local FAA CSV could never cover. Showing "N/A" while that value
    sits unused in the record is the exact gap this column exists to close.
    """
    return ac.owner_name or ac.owner_operator or "N/A"


def _cell_flags(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    tags = decode_db_flags(ac.db_flags)
    category = decode_category(ac.category)
    if category:
        tags = [*tags, category]
    return " ".join(tags)


def _cell_age(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    return _format_short_duration(ac.seen_pos_s)


def _cell_alert(ac: Aircraft, unit_system: str, ascii_only: bool) -> str:
    return _ALERT_TAGS.get(ac.alert_level, "")


_CELL_BUILDERS: dict[str, Callable[[Aircraft, str, bool], str]] = {
    "flight": _cell_flight,
    "hex": _cell_hex,
    "reg": _cell_reg,
    "type": _cell_type,
    "alt": _cell_alt,
    "vs": _cell_vs,
    "gs": _cell_gs,
    "dist": _cell_dist,
    "brg": _cell_brg,
    "cpa": _cell_cpa,
    "owner": _cell_owner,
    "flags": _cell_flags,
    "age": _cell_age,
    "alert": _cell_alert,
}


# --------------------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------------------


def _owner_fit_width(owner_width: int) -> int:
    """The width the "owner" column effectively asks for when deciding what fits.

    owner_width (the user's configured DisplayConfig.owner_width) is what "owner" really
    wants to display a full name, so fit decisions use that -- clamped into the column's
    own [min_width, max_width] -- rather than its bare min_width, which would make a wide
    owner column look artificially cheap and get kept when it will not actually have room
    to show anything useful.
    """
    spec = COLUMN_SPECS["owner"]
    return max(spec.min_width, min(owner_width, spec.max_width))


def _fit_width(key: str, owner_width: int) -> int:
    return _owner_fit_width(owner_width) if key == "owner" else COLUMN_SPECS[key].min_width


def layout(columns: list[str], available_width: int, owner_width: int) -> list[str]:
    """Decide which of columns fit in available_width, dropping the rest.

    Unknown column keys are ignored. Columns are dropped one at a time, always the
    currently-remaining column with the LARGEST priority number (i.e. the least useful
    one -- see ColumnSpec.priority), until the sum of each remaining column's effective
    fit width (see _fit_width) plus one separator character between every pair of
    adjacent columns is <= available_width. "flight" is never dropped, even if the result
    is still wider than available_width -- there must always be at least one identifying
    column.
    """
    selected = [c for c in columns if c in COLUMN_SPECS]

    def total_width(cols: list[str]) -> int:
        if not cols:
            return 0
        return sum(_fit_width(c, owner_width) for c in cols) + (len(cols) - 1)

    while total_width(selected) > available_width and len(selected) > 1:
        droppable = [c for c in selected if c != "flight"]
        if not droppable:
            break
        drop = max(droppable, key=lambda c: COLUMN_SPECS[c].priority)
        selected.remove(drop)

    return selected


def compute_widths(columns: list[str], available_width: int, owner_width: int) -> dict[str, int]:
    """Distribute available_width across columns (the survivors from layout()).

    Every column starts at its min_width. Any leftover space (available_width minus the
    sum of min_widths and separators) is given first to "owner", up to
    min(owner_width, its max_width); anything still left over after that goes to the
    widest remaining column that still has room to grow (max_width > its current width),
    one column at a time, widest first, until either the leftover or the room runs out.

    Never returns a set of widths whose total (including one separator per gap) exceeds
    available_width, provided columns already fits per layout()'s rules; if it does not
    (e.g. compute_widths is called without first calling layout()), each column still gets
    at least its min_width and the total may legitimately exceed available_width.
    """
    columns = [c for c in columns if c in COLUMN_SPECS]
    widths = {c: COLUMN_SPECS[c].min_width for c in columns}
    if not columns:
        return widths

    used = sum(widths.values()) + (len(columns) - 1)
    leftover = available_width - used
    if leftover <= 0:
        return widths

    if "owner" in widths:
        cap = _owner_fit_width(owner_width)
        grow = min(leftover, max(0, cap - widths["owner"]))
        widths["owner"] += grow
        leftover -= grow

    while leftover > 0:
        growable = [c for c in columns if widths[c] < COLUMN_SPECS[c].max_width]
        if not growable:
            break
        target = max(growable, key=lambda c: widths[c])
        room = COLUMN_SPECS[target].max_width - widths[target]
        grow = min(leftover, room)
        widths[target] += grow
        leftover -= grow

    return widths


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def format_header(columns: list[str], widths: dict[str, int], border_style: str) -> str:
    """Render the header row: each column's label, sized to widths[key], separated by the
    border style's vertical glyph (or a single space when that glyph is empty, e.g. for
    border_style="none" -- kept at exactly one character wide either way so the total
    length matches what layout()/compute_widths() budgeted for).

    Raises ValueError if border_style is not a style theme.glyphs() recognizes.
    """
    sep = glyphs(border_style)["v"] or " "
    cells = [
        sanitize_cell(COLUMN_SPECS[key].header, widths[key], align=COLUMN_SPECS[key].align)
        for key in columns
        if key in COLUMN_SPECS and key in widths
    ]
    return sep.join(cells)


def format_row(
    aircraft: Aircraft,
    columns: list[str],
    widths: dict[str, int],
    unit_system: str,
    ascii_only: bool,
    selected: bool = False,
) -> str:
    """Render one aircraft's row at the given column widths.

    Uses the ascii-vs-unicode vertical-bar glyph (theme.glyphs) as the one-character column
    separator, matching whichever glyph set ascii_only selects for the trend arrows, so
    the row and a same-style header line up. Never raises: each cell's value is built in a
    try/except, and any failure (or a missing/None field) degrades to a blank cell rather
    than propagating -- sanitize_cell() then still pads it to the right width.

    When selected is set, the leading character of the first rendered column is replaced
    with a ">" marker (real color/reverse-video highlighting for a selected row is applied
    by the curses-aware drawing layer using theme.ROW_STYLES["selected"]; this plain-string
    layer can only add a text marker without changing any column's width).
    """
    sep = glyphs("ascii")["v"] if ascii_only else glyphs("unicode")["v"]
    cells = []
    for key in columns:
        if key not in COLUMN_SPECS or key not in widths:
            continue
        spec = COLUMN_SPECS[key]
        builder = _CELL_BUILDERS.get(key)
        try:
            raw_value = builder(aircraft, unit_system, ascii_only) if builder else ""
        except Exception:
            raw_value = ""
        cells.append(sanitize_cell(raw_value, widths[key], align=spec.align))

    if selected and cells and cells[0]:
        cells[0] = ">" + cells[0][1:]

    return sep.join(cells)

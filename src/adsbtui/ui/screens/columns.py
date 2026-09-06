"""The Columns screen: choose which table columns are shown, their order, and the
display density / border style.

ColumnsScreen follows the Screen design contract documented in ui/screens/__init__.py: a
read-only title, a pure render_lines(width, height) that returns plain, already-clipped
strings with no curses calls, and a handle_key(key) that reports navigation intent back to
the caller as a short string ("close" here) or None to stay open. curses is imported only
for its KEY_* integer constants, which is safe without an initialized screen.

Internally this composes three of ui.widgets' pure widgets:

  * a ui.widgets.ListPicker in multi-select mode, seeded with every column key
    ui.columns.COLUMN_SPECS knows about -- the caller's current_columns first (checked, in
    their existing display order), then any remaining known columns after (unchecked).
    Space toggles a column on/off; move_up()/move_down() (bound here to "+"/"-") reorder
    the item under the cursor, which is how the user changes display order.
  * two ui.widgets.Select widgets, one for density ("compact"/"normal"/"wide") and one for
    border style ("unicode"/"ascii"/"none").

Tab (and Shift-Tab) moves focus between the three widgets. Escape closes the screen
without applying whatever the widgets currently show -- the caller's original
current_columns/current_density/current_borders are preserved in result(). Enter or F10
commits the widgets' current state into result() and closes the screen.
"""

from __future__ import annotations

import curses
from collections.abc import Sequence
from typing import Any

from adsbtui.ui.columns import COLUMN_SPECS
from adsbtui.ui.widgets import ListPicker, Select

_ESCAPE_KEY = 27
_ENTER_KEYS = frozenset({10, 13, curses.KEY_ENTER})
_APPLY_KEYS = _ENTER_KEYS | {curses.KEY_F10}
_TAB_FORWARD_KEYS = frozenset({ord("\t")})
_TAB_BACKWARD_KEYS = frozenset({curses.KEY_BTAB})
_MOVE_UP_KEYS = frozenset({ord("+")})
_MOVE_DOWN_KEYS = frozenset({ord("-")})

_FOCUS_COLUMNS = 0
_FOCUS_DENSITY = 1
_FOCUS_BORDERS = 2
_FOCUS_COUNT = 3

#: (value, label) choices for the two Select widgets.
_DENSITY_CHOICES: list[tuple[str, str]] = [
    ("compact", "Compact"),
    ("normal", "Normal"),
    ("wide", "Wide"),
]
_BORDER_CHOICES: list[tuple[str, str]] = [
    ("unicode", "Unicode"),
    ("ascii", "ASCII"),
    ("none", "None"),
]


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


def _index_of(choices: Sequence[tuple[Any, str]], value: Any) -> int:
    """Index of the choice whose value matches, or 0 (the first entry) if none does."""
    for i, (choice_value, _label) in enumerate(choices):
        if choice_value == value:
            return i
    return 0


def _ordered_known_keys(current_columns: Sequence[str]) -> tuple[list[str], int]:
    """Every COLUMN_SPECS key, current_columns' known entries first (de-duplicated, in
    their given order), then any remaining known keys after. Returns (keys, enabled_count)
    where enabled_count is how many of the leading keys should start out checked.
    """
    seen: set[str] = set()
    enabled: list[str] = []
    for key in current_columns:
        if key in COLUMN_SPECS and key not in seen:
            seen.add(key)
            enabled.append(key)
    remaining = [key for key in COLUMN_SPECS if key not in seen]
    return enabled + remaining, len(enabled)


class ColumnsScreen:
    """Lets the user choose, reorder, and enable/disable table columns, plus set the
    display density and border style, then apply or cancel.

    current_columns is the caller's currently-enabled column keys (a subset/reordering of
    ui.columns.COLUMN_SPECS' keys) in display order; current_density and current_borders
    are the caller's current DisplayConfig-style density/borders values. Unknown values are
    tolerated everywhere (an unknown column key is simply not offered as one of the known
    columns; an unknown density/borders value falls back to highlighting the first choice)
    and never raise.
    """

    def __init__(
        self,
        current_columns: Sequence[str],
        current_density: str,
        current_borders: str,
    ) -> None:
        items, enabled_count = _ordered_known_keys(current_columns)
        self._picker = ListPicker(
            items,
            multi_select=True,
            visible_rows=max(1, len(items)),
            labeler=lambda key: COLUMN_SPECS[key].header,
        )
        # Seed the initial checked state through the picker's own public handle_key() API
        # (Home, then Space+Down for each of the leading "enabled" items) rather than
        # reaching into its private selection set.
        self._picker.handle_key(curses.KEY_HOME)
        for _ in range(enabled_count):
            self._picker.handle_key(ord(" "))
            self._picker.handle_key(curses.KEY_DOWN)
        self._picker.handle_key(curses.KEY_HOME)

        self._density_select = Select(
            _DENSITY_CHOICES, initial_index=_index_of(_DENSITY_CHOICES, current_density)
        )
        self._borders_select = Select(
            _BORDER_CHOICES, initial_index=_index_of(_BORDER_CHOICES, current_borders)
        )
        self._focus = _FOCUS_COLUMNS

        # The committed result, only updated when Enter/F10 applies the widgets' current
        # state -- Escape leaves these exactly as the caller passed them in.
        self._result_columns: list[str] = list(current_columns)
        self._result_density: str = current_density
        self._result_borders: str = current_borders

    @property
    def title(self) -> str:
        return "Columns"

    @property
    def focus(self) -> int:
        """0 for the column picker, 1 for density, 2 for borders."""
        return self._focus

    def _focused_widget(self) -> ListPicker | Select:
        if self._focus == _FOCUS_COLUMNS:
            return self._picker
        if self._focus == _FOCUS_DENSITY:
            return self._density_select
        return self._borders_select

    def _apply(self) -> None:
        self._result_columns = self._picker.selected_items
        self._result_density = self._density_select.value
        self._result_borders = self._borders_select.value

    def handle_key(self, key: int) -> str | None:
        if key == _ESCAPE_KEY:
            return "close"
        if key in _TAB_FORWARD_KEYS:
            self._focus = (self._focus + 1) % _FOCUS_COUNT
            return None
        if key in _TAB_BACKWARD_KEYS:
            self._focus = (self._focus - 1) % _FOCUS_COUNT
            return None
        if self._focus == _FOCUS_COLUMNS:
            if key in _MOVE_UP_KEYS:
                self._picker.move_up()
                return None
            if key in _MOVE_DOWN_KEYS:
                self._picker.move_down()
                return None
        if self._focused_widget().handle_key(key):
            return None
        if key in _APPLY_KEYS:
            self._apply()
            return "close"
        return None

    def result(self) -> dict[str, Any]:
        """The committed {"columns", "density", "borders"} dict: the caller's originals
        unless Enter/F10 applied the widgets' current state before the screen closed.
        """
        return {
            "columns": list(self._result_columns),
            "density": self._result_density,
            "borders": self._result_borders,
        }

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []

        lines: list[str] = []

        columns_mark = ">" if self._focus == _FOCUS_COLUMNS else " "
        lines.append(_clip(f"{columns_mark} Columns (Space: toggle  +/-: reorder):", width))
        indent = "  "
        picker_width = max(0, width - len(indent))
        for line in self._picker.render_lines(picker_width):
            lines.append(_clip(indent + line, width))

        lines.append("")

        density_mark = ">" if self._focus == _FOCUS_DENSITY else " "
        density_prefix = f"{density_mark} Density: "
        density_lines = self._density_select.render_lines(max(0, width - len(density_prefix)))
        density_text = density_lines[0] if density_lines else ""
        lines.append(_clip(density_prefix + density_text, width))

        borders_mark = ">" if self._focus == _FOCUS_BORDERS else " "
        borders_prefix = f"{borders_mark} Borders: "
        borders_lines = self._borders_select.render_lines(max(0, width - len(borders_prefix)))
        borders_text = borders_lines[0] if borders_lines else ""
        lines.append(_clip(borders_prefix + borders_text, width))

        lines.append("")
        lines.append(_clip("Tab: switch focus  Enter/F10: apply  Esc: cancel", width))

        return lines[:height]

"""The Columns screen: choose which table columns are shown, their order, and the
border style.

ColumnsScreen follows the Screen design contract documented in ui/screens/__init__.py: a
read-only title, a pure render_lines(width, height) that returns plain, already-clipped
strings with no curses calls, and a handle_key(key) that reports navigation intent back to
the caller as a short string ("close" here) or None to stay open. curses is imported only
for its KEY_* integer constants, which is safe without an initialized screen.

Internally this composes two of ui.widgets' pure widgets:

  * a ui.widgets.ListPicker in multi-select mode, seeded with every column key
    ui.columns.COLUMN_SPECS knows about -- the caller's current_columns first (checked, in
    their existing display order), then any remaining known columns after (unchecked).
    Space toggles a column on/off; move_up()/move_down() (bound here to "+"/"-") reorder
    the item under the cursor, which is how the user changes display order.
  * a ui.widgets.Select for border style ("unicode"/"ascii"/"none").

The screen is one list: the columns, then the Borders row. Up/Down move through it (Down
from the last column lands on Borders), Space or Enter shows/hides the column under the
cursor, "+"/"-" move it earlier/later in the table, and Tab / Shift-Tab jump between the
column list and Borders. Left/Right/Space/Enter change the border style. Enter never closes
the screen: s or F10 (also F2/S) applies and closes, Esc or q cancels -- with a "Discard
changes? y/n" question first when something was changed. result() is the caller's original
current_columns/current_borders unless an apply came first.
"""

from __future__ import annotations

import curses
from collections.abc import Sequence
from typing import Any

from adsbtui.ui.columns import COLUMN_SPECS
from adsbtui.ui.widgets import (
    ENTER_KEYS,
    DiscardPrompt,
    ListPicker,
    Select,
    classify_key,
    compose_dialog,
)

_FOOTER_COLUMNS = "Up/Down move  Space show  +/- reorder  Tab borders  s apply  Esc cancel"
_FOOTER_BORDERS = "Left/Right style  Up back to columns  Tab columns  s apply  Esc cancel"
_MOVE_UP_KEYS = frozenset({ord("+"), ord("=")})
_MOVE_DOWN_KEYS = frozenset({ord("-"), ord("_")})

_FOCUS_COLUMNS = 0
_FOCUS_BORDERS = 1

#: (value, label) choices for the border Select widget.
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
    border style, then apply or cancel.

    current_columns is the caller's currently-enabled column keys (a subset/reordering of
    ui.columns.COLUMN_SPECS' keys) in display order; current_borders is the caller's current
    DisplayConfig-style borders value. Unknown values are tolerated everywhere (an unknown
    column key is simply not offered as one of the known columns; an unknown borders value
    falls back to highlighting the first choice)
    and never raise.
    """

    def __init__(
        self,
        current_columns: Sequence[str],
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

        self._borders_select = Select(
            _BORDER_CHOICES, initial_index=_index_of(_BORDER_CHOICES, current_borders)
        )
        self._focus = _FOCUS_COLUMNS
        self._prompt = DiscardPrompt()
        self.applied = False
        self.highlight_row: int | None = None
        self._initial = self._state()

        # The committed result, only updated by an apply -- cancelling leaves these
        # exactly as the caller passed them in.
        self._result_columns: list[str] = list(current_columns)
        self._result_borders: str = current_borders

    def _state(self) -> tuple[Any, ...]:
        return (
            tuple(self._picker.selected_items),
            tuple(self._picker.items),
            self._borders_select.value,
        )

    @property
    def dirty(self) -> bool:
        return self._state() != self._initial

    @property
    def title(self) -> str:
        return "Columns *" if self.dirty else "Columns"

    @property
    def focus(self) -> int:
        """0 for the column list, 1 for borders."""
        return self._focus

    def _apply(self) -> None:
        self._result_columns = self._picker.selected_items
        self._result_borders = self._borders_select.value
        self.applied = True

    def handle_key(self, key: int) -> str | None:
        if self._prompt.active:
            return "close" if self._prompt.handle_key(key) == "discard" else None
        action = classify_key(key)
        if action == "cancel":
            if self.dirty:
                self._prompt.active = True
                return None
            return "close"
        if action in ("apply", "save"):
            self._apply()
            return "close"
        if action in ("next", "prev"):
            self._focus = _FOCUS_BORDERS if self._focus == _FOCUS_COLUMNS else _FOCUS_COLUMNS
            return None
        if self._focus == _FOCUS_BORDERS:
            if key in (curses.KEY_UP, ord("k")):
                self._focus = _FOCUS_COLUMNS
            elif key in ENTER_KEYS:
                self._borders_select.handle_key(curses.KEY_RIGHT)
            else:
                self._borders_select.handle_key(key)
            return None
        if key in _MOVE_UP_KEYS:
            self._picker.move_up()
        elif key in _MOVE_DOWN_KEYS:
            self._picker.move_down()
        elif key in (curses.KEY_DOWN, ord("j")) and self._picker.cursor == len(
            self._picker.items
        ) - 1:
            self._focus = _FOCUS_BORDERS
        elif key in ENTER_KEYS:
            self._picker.handle_key(ord(" "))
        else:
            self._picker.handle_key(key)
        return None

    def result(self) -> dict[str, Any]:
        """The committed {"columns", "borders"} dict: the caller's originals
        unless Enter/F10 applied the widgets' current state before the screen closed.
        """
        return {
            "columns": list(self._result_columns),
            "borders": self._result_borders,
        }

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []
        picker = self._picker
        n = len(picker.items)
        on_columns = self._focus == _FOCUS_COLUMNS
        rows = []
        for i, key in enumerate(picker.items):
            mark = ">" if on_columns and i == picker.cursor else " "
            box = "[x]" if picker.is_selected(i) else "[ ]"
            rows.append(f"{mark} {box} {COLUMN_SPECS[key].header}")
        border = self._borders_select.render_lines(width)[0]
        pinned = ["-" * width, f"{' ' if on_columns else '>'} {'Borders':<10} {border}"]
        focus_line = picker.cursor if on_columns else n + 1

        def body(capacity: int) -> tuple[list[str], int, str]:
            if n + 2 <= capacity:
                return rows + pinned, focus_line, ""
            room = max(1, capacity - 3)
            top = max(0, min(picker.cursor - room // 2, n - room))
            if not on_columns:
                top = n - room
            fl = picker.cursor - top if on_columns else room + 1
            return rows[top : top + room] + pinned, fl, f"{picker.cursor + 1}/{n}"

        help_text = (
            "Show or hide columns and set their order: + moves one earlier, - later."
            if on_columns
            else "Box-drawing style for the header divider and column separators."
        )
        lines, self.highlight_row = compose_dialog(
            width,
            height,
            head=[],
            body=body,
            help_text=help_text,
            status=DiscardPrompt.TEXT if self._prompt.active else "",
            footer=_FOOTER_COLUMNS if on_columns else _FOOTER_BORDERS,
            min_height=n + 7,
        )
        return lines

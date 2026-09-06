"""A small, curses-optional widget layer for building interactive dialogs and forms.

Every widget below keeps its state and logic in PURE, testable methods that never touch
curses:

  * handle_key(self, key) -> bool consumes a key (an int -- either a plain character code
    or a curses.KEY_* constant) and returns True if it changed the widget's state (or was
    otherwise "consumed" by it), False if the caller should try something else with it.
  * render_lines(self, width) -> list[str] renders the widget's current state as plain
    strings, each clipped/padded to at most width characters. No curses calls.

Only draw(self, win, y, x) touches curses: it calls render_lines() and then win.addstr()
for each line, with every call individually guarded in try/except curses.error (the same
discipline used by ui.bars and ui.columns -- writing to the bottom-right cell, or a
too-narrow/short terminal, must never crash the program). Because draw() is a thin,
mechanical wrapper, it is exercised only in real interactive use; the unit tests in
tests/test_widgets.py exercise handle_key()/render_lines()/value/items exclusively, with
no real curses window required.

This module must never import the curses module at study time in a way that would fail
without a terminal -- curses itself has no such requirement (importing it is always safe;
only *initializing* a screen requires a terminal), so it is imported normally here and used
only inside draw() and for KEY_* constant lookups.
"""

from __future__ import annotations

import contextlib
import curses
from collections.abc import Callable, Sequence
from typing import Any

from adsbtui.ui.theme import glyphs

#: Backspace can arrive as curses.KEY_BACKSPACE, or as the raw byte value terminals send
#: for it depending on stty settings/emulator -- 127 (DEL) or 8 (^H). Delete (forward) is
#: usually curses.KEY_DC, but some terminals/curses builds report it as byte 127 too, so
#: it is only added to the DELETE set when it differs from BACKSPACE_KEYS' 127 entry (it
#: never does in practice, but the check keeps the two sets unambiguous either way).
BACKSPACE_KEYS = frozenset({curses.KEY_BACKSPACE, 127, 8})
DELETE_KEYS = frozenset({curses.KEY_DC})

_ENTER_KEYS = frozenset({10, 13, curses.KEY_ENTER})


def _clip(text: str, width: int) -> str:
    """Truncate/pad text to exactly width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    if len(text) > width:
        return text[:width]
    return text


def _safe_addstr(win: Any, y: int, x: int, text: str, *args: Any) -> None:
    """win.addstr(y, x, text, *args), swallowing curses.error.

    Every widget's draw() routes through this so a too-narrow terminal, or a write to the
    bottom-right cell, never crashes the program.
    """
    with contextlib.suppress(curses.error):
        win.addstr(y, x, text, *args)


class TextField:
    """Single-line text entry with a cursor position and an optional validator.

    The validator, if given, is called as validator(candidate_text) -> bool on every edit
    that would change the text; the edit is rejected (handle_key returns False) if the
    validator returns False. This lets subclasses like NumberField restrict what
    characters end up in the field without duplicating the cursor/editing logic.
    """

    def __init__(
        self,
        initial: str = "",
        validator: Callable[[str], bool] | None = None,
        max_length: int | None = None,
    ) -> None:
        self._text = initial
        self._cursor = len(initial)
        self._validator = validator
        self._max_length = max_length

    @property
    def value(self) -> str:
        return self._text

    @value.setter
    def value(self, text: str) -> None:
        self._text = text
        self._cursor = min(self._cursor, len(self._text))

    @property
    def cursor(self) -> int:
        return self._cursor

    def _is_acceptable(self, candidate: str) -> bool:
        if self._max_length is not None and len(candidate) > self._max_length:
            return False
        return self._validator is None or self._validator(candidate)

    def _set_text(self, text: str) -> bool:
        """Replace the full text if acceptable; returns whether it was applied."""
        if not self._is_acceptable(text):
            return False
        self._text = text
        return True

    def handle_key(self, key: int) -> bool:
        if key in BACKSPACE_KEYS:
            if self._cursor <= 0:
                return False
            candidate = self._text[: self._cursor - 1] + self._text[self._cursor :]
            if self._set_text(candidate):
                self._cursor -= 1
                return True
            return False
        if key in DELETE_KEYS:
            if self._cursor >= len(self._text):
                return False
            candidate = self._text[: self._cursor] + self._text[self._cursor + 1 :]
            return self._set_text(candidate)
        if key == curses.KEY_LEFT:
            if self._cursor <= 0:
                return False
            self._cursor -= 1
            return True
        if key == curses.KEY_RIGHT:
            if self._cursor >= len(self._text):
                return False
            self._cursor += 1
            return True
        if key == curses.KEY_HOME:
            if self._cursor == 0:
                return False
            self._cursor = 0
            return True
        if key == curses.KEY_END:
            if self._cursor == len(self._text):
                return False
            self._cursor = len(self._text)
            return True
        if self._is_printable_key(key):
            ch = chr(key)
            candidate = self._text[: self._cursor] + ch + self._text[self._cursor :]
            if self._set_text(candidate):
                self._cursor += 1
                return True
            return False
        return False

    @staticmethod
    def _is_printable_key(key: int) -> bool:
        return isinstance(key, int) and 32 <= key <= 126

    def render_lines(self, width: int) -> list[str]:
        return [_clip(self._text, width)]

    def draw(self, win: Any, y: int, x: int) -> None:
        for i, line in enumerate(self.render_lines(10**9)):
            _safe_addstr(win, y + i, x, line)


class NumberField(TextField):
    """A TextField restricted to an optional leading '-', digits, and one '.'.

    value exposes the parsed float, or None if the current text does not parse (including
    the empty string, or a bare "-"/"." while mid-edit).
    """

    def __init__(self, initial: str = "") -> None:
        super().__init__(initial=initial, validator=self._is_valid_number_text)

    @staticmethod
    def _is_valid_number_text(text: str) -> bool:
        if text == "":
            return True
        allowed = set("0123456789.-")
        if any(ch not in allowed for ch in text):
            return False
        if text.count("-") > 1:
            return False
        if "-" in text and not text.startswith("-"):
            return False
        return not text.count(".") > 1

    @property
    def value(self) -> float | None:  # type: ignore[override]
        try:
            return float(self._text)
        except ValueError:
            return None

    @value.setter
    def value(self, text: str) -> None:  # type: ignore[override]
        self._text = text
        self._cursor = min(self._cursor, len(self._text))

    @property
    def text(self) -> str:
        """Raw text currently in the field (value parses this, or is None if it doesn't)."""
        return self._text


class Toggle:
    """A boolean switch. Space or Enter flips it."""

    def __init__(self, initial: bool = False, label: str = "") -> None:
        self._value = initial
        self.label = label

    @property
    def value(self) -> bool:
        return self._value

    @value.setter
    def value(self, v: bool) -> None:
        self._value = bool(v)

    def handle_key(self, key: int) -> bool:
        if key == ord(" ") or key in _ENTER_KEYS:
            self._value = not self._value
            return True
        return False

    def render_lines(self, width: int) -> list[str]:
        mark = "[x]" if self._value else "[ ]"
        text = f"{mark} {self.label}" if self.label else mark
        return [_clip(text, width)]

    def draw(self, win: Any, y: int, x: int) -> None:
        _safe_addstr(win, y, x, self.render_lines(10**9)[0])


class Select:
    """A single choice among a fixed list of (value, label) pairs.

    Left/Right or Space cycles through the choices, wrapping around at either end.
    """

    def __init__(self, choices: Sequence[tuple[Any, str]], initial_index: int = 0) -> None:
        if not choices:
            raise ValueError("Select requires at least one choice")
        self._choices = list(choices)
        self._index = initial_index % len(self._choices)

    @property
    def value(self) -> Any:
        return self._choices[self._index][0]

    @property
    def label(self) -> str:
        return self._choices[self._index][1]

    @property
    def index(self) -> int:
        return self._index

    def set_index(self, index: int) -> None:
        self._index = index % len(self._choices)

    def handle_key(self, key: int) -> bool:
        if key == curses.KEY_LEFT:
            self._index = (self._index - 1) % len(self._choices)
            return True
        if key in (curses.KEY_RIGHT, ord(" ")):
            self._index = (self._index + 1) % len(self._choices)
            return True
        return False

    def render_lines(self, width: int) -> list[str]:
        return [_clip(f"< {self.label} >", width)]

    def draw(self, win: Any, y: int, x: int) -> None:
        _safe_addstr(win, y, x, self.render_lines(10**9)[0])


class ListPicker:
    """A scrollable list of items with single- or multi-select.

    Up/Down or j/k move the cursor by one; PageUp/PageDown move by a page (a caller-visible
    window height, tracked internally as visible_rows); Home/End jump to the first/last
    item. Space toggles selection in multi-select mode; Enter confirms the current item in
    single-select mode (recorded in confirmed_index).

    In multi-select mode, move_up()/move_down() reorder the item currently under the
    cursor within the underlying list -- e.g. for a future column-order picker -- and the
    cursor follows the moved item. items returns the (possibly reordered) list of items in
    their current order; selected_items returns just the ones currently selected, in that
    same order.
    """

    def __init__(
        self,
        items: Sequence[Any],
        multi_select: bool = False,
        visible_rows: int = 10,
        labeler: Callable[[Any], str] | None = None,
    ) -> None:
        self._items: list[Any] = list(items)
        self.multi_select = multi_select
        self.visible_rows = max(1, visible_rows)
        self._labeler = labeler if labeler is not None else str
        self._cursor = 0
        self._selected: set[int] = set()
        self._top = 0
        self.confirmed_index: int | None = None

    @property
    def items(self) -> list[Any]:
        return list(self._items)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def selected_indices(self) -> list[int]:
        return sorted(self._selected)

    @property
    def selected_items(self) -> list[Any]:
        return [self._items[i] for i in sorted(self._selected)]

    def is_selected(self, index: int) -> bool:
        return index in self._selected

    def _clamp_cursor(self) -> None:
        if not self._items:
            self._cursor = 0
            return
        self._cursor = max(0, min(self._cursor, len(self._items) - 1))

    def _ensure_visible(self) -> None:
        if self._cursor < self._top:
            self._top = self._cursor
        elif self._cursor >= self._top + self.visible_rows:
            self._top = self._cursor - self.visible_rows + 1

    def _move_cursor(self, delta: int) -> bool:
        if not self._items:
            return False
        new_cursor = max(0, min(self._cursor + delta, len(self._items) - 1))
        if new_cursor == self._cursor:
            return False
        self._cursor = new_cursor
        self._ensure_visible()
        return True

    def move_up(self) -> bool:
        """Multi-select only: swap the item under the cursor with the one above it."""
        if not self.multi_select or self._cursor <= 0:
            return False
        i = self._cursor
        self._items[i - 1], self._items[i] = self._items[i], self._items[i - 1]
        was_selected = i in self._selected
        other_selected = (i - 1) in self._selected
        self._selected.discard(i)
        self._selected.discard(i - 1)
        if was_selected:
            self._selected.add(i - 1)
        if other_selected:
            self._selected.add(i)
        self._cursor -= 1
        self._ensure_visible()
        return True

    def move_down(self) -> bool:
        """Multi-select only: swap the item under the cursor with the one below it."""
        if not self.multi_select or self._cursor >= len(self._items) - 1:
            return False
        i = self._cursor
        self._items[i], self._items[i + 1] = self._items[i + 1], self._items[i]
        was_selected = i in self._selected
        other_selected = (i + 1) in self._selected
        self._selected.discard(i)
        self._selected.discard(i + 1)
        if was_selected:
            self._selected.add(i + 1)
        if other_selected:
            self._selected.add(i)
        self._cursor += 1
        self._ensure_visible()
        return True

    def handle_key(self, key: int) -> bool:
        if key in (curses.KEY_UP, ord("k")):
            return self._move_cursor(-1)
        if key in (curses.KEY_DOWN, ord("j")):
            return self._move_cursor(1)
        if key == curses.KEY_PPAGE:
            return self._move_cursor(-self.visible_rows)
        if key == curses.KEY_NPAGE:
            return self._move_cursor(self.visible_rows)
        if key == curses.KEY_HOME:
            return self._move_cursor(-len(self._items))
        if key == curses.KEY_END:
            return self._move_cursor(len(self._items))
        if key == ord(" "):
            if not self.multi_select or not self._items:
                return False
            if self._cursor in self._selected:
                self._selected.discard(self._cursor)
            else:
                self._selected.add(self._cursor)
            return True
        if key in _ENTER_KEYS:
            if self.multi_select or not self._items:
                return False
            self.confirmed_index = self._cursor
            return True
        return False

    def render_lines(self, width: int) -> list[str]:
        if not self._items:
            return [_clip("(empty)", width)]
        lines = []
        end = min(self._top + self.visible_rows, len(self._items))
        for i in range(self._top, end):
            item = self._items[i]
            cursor_mark = ">" if i == self._cursor else " "
            if self.multi_select:
                sel_mark = "[x]" if i in self._selected else "[ ]"
                text = f"{cursor_mark}{sel_mark} {self._labeler(item)}"
            else:
                text = f"{cursor_mark} {self._labeler(item)}"
            lines.append(_clip(text, width))
        return lines

    def draw(self, win: Any, y: int, x: int) -> None:
        for i, line in enumerate(self.render_lines(10**9)):
            _safe_addstr(win, y + i, x, line)


class ProgressBar:
    """A single-line progress bar: a filled/unfilled bar plus a percentage readout."""

    def __init__(self, value: float = 0.0) -> None:
        self._value = self._clamp(value)

    @staticmethod
    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, v: float) -> None:
        self._value = self._clamp(v)

    def render_lines(self, width: int) -> list[str]:
        if width <= 0:
            return [""]
        pct_text = f" {round(self._value * 100):3d}%"
        bar_width = max(0, width - len(pct_text))
        if bar_width < 2:
            bar = "-" * bar_width
        else:
            inner_width = bar_width - 2
            filled = round(inner_width * self._value)
            bar = "[" + ("#" * filled).ljust(inner_width, "-") + "]"
        line = _clip(bar + pct_text, width)
        return [line]

    def draw(self, win: Any, y: int, x: int) -> None:
        _safe_addstr(win, y, x, self.render_lines(10**9)[0])


class MessageBox:
    """A multi-line message plus a row of buttons (e.g. ["OK", "Cancel"]).

    Left/Right or Tab moves focus between buttons (wrapping); Shift-Tab moves focus back.
    Enter "presses" the focused button, recording its index in pressed_index.
    """

    def __init__(self, message: str, buttons: Sequence[str] = ("OK",)) -> None:
        if not buttons:
            raise ValueError("MessageBox requires at least one button")
        self.message = message
        self.buttons = list(buttons)
        self._focus = 0
        self.pressed_index: int | None = None

    @property
    def focus(self) -> int:
        return self._focus

    @property
    def pressed_label(self) -> str | None:
        return self.buttons[self.pressed_index] if self.pressed_index is not None else None

    def handle_key(self, key: int) -> bool:
        if key in (curses.KEY_RIGHT, ord("\t")):
            self._focus = (self._focus + 1) % len(self.buttons)
            return True
        if key == curses.KEY_LEFT or key == curses.KEY_BTAB:
            self._focus = (self._focus - 1) % len(self.buttons)
            return True
        if key in _ENTER_KEYS:
            self.pressed_index = self._focus
            return True
        return False

    def render_lines(self, width: int) -> list[str]:
        lines = [_clip(line, width) for line in self.message.splitlines()] or [""]
        button_parts = []
        for i, label in enumerate(self.buttons):
            text = f"[{label}]" if i == self._focus else f" {label} "
            button_parts.append(text)
        lines.append(_clip("  ".join(button_parts), width))
        return lines

    def draw(self, win: Any, y: int, x: int) -> None:
        for i, line in enumerate(self.render_lines(10**9)):
            _safe_addstr(win, y + i, x, line)


class Tabs:
    """A row of named tabs. Left/Right or Tab cycles the active tab, wrapping around."""

    def __init__(self, labels: Sequence[str], initial_index: int = 0) -> None:
        if not labels:
            raise ValueError("Tabs requires at least one label")
        self.labels = list(labels)
        self._active = initial_index % len(self.labels)

    @property
    def active_index(self) -> int:
        return self._active

    @property
    def active_label(self) -> str:
        return self.labels[self._active]

    def handle_key(self, key: int) -> bool:
        if key in (curses.KEY_RIGHT, ord("\t")):
            self._active = (self._active + 1) % len(self.labels)
            return True
        if key == curses.KEY_LEFT or key == curses.KEY_BTAB:
            self._active = (self._active - 1) % len(self.labels)
            return True
        return False

    def render_lines(self, width: int) -> list[str]:
        parts = []
        for i, label in enumerate(self.labels):
            parts.append(f"[{label}]" if i == self._active else f" {label} ")
        return [_clip(" ".join(parts), width)]

    def draw(self, win: Any, y: int, x: int) -> None:
        _safe_addstr(win, y, x, self.render_lines(10**9)[0])


class Form:
    """Composes an ordered list of (label, widget) pairs into a simple vertical form.

    Tab/Down moves focus to the next field, Shift-Tab/Up moves to the previous field
    (both wrap around); any other key is delegated to the currently-focused widget's
    handle_key(). render_lines(width) renders "<label>: " followed by the widget's own
    render_lines() output on the same/following line(s), one field after another.
    """

    _FOCUS_NEXT_KEYS = frozenset({ord("\t"), curses.KEY_DOWN})
    _FOCUS_PREV_KEYS = frozenset({curses.KEY_BTAB, curses.KEY_UP})

    def __init__(self, fields: Sequence[tuple[str, Any]]) -> None:
        if not fields:
            raise ValueError("Form requires at least one field")
        self.fields = list(fields)
        self._focus = 0

    @property
    def focus_index(self) -> int:
        return self._focus

    @property
    def focused_widget(self) -> Any:
        return self.fields[self._focus][1]

    def focus_next(self) -> None:
        self._focus = (self._focus + 1) % len(self.fields)

    def focus_prev(self) -> None:
        self._focus = (self._focus - 1) % len(self.fields)

    def handle_key(self, key: int) -> bool:
        if key in self._FOCUS_NEXT_KEYS:
            self.focus_next()
            return True
        if key in self._FOCUS_PREV_KEYS:
            self.focus_prev()
            return True
        return self.focused_widget.handle_key(key)

    def render_lines(self, width: int) -> list[str]:
        lines: list[str] = []
        for i, (label, widget) in enumerate(self.fields):
            prefix = f"{'>' if i == self._focus else ' '} {label}: "
            widget_lines = widget.render_lines(max(0, width - len(prefix)))
            if not widget_lines:
                widget_lines = [""]
            lines.append(_clip(prefix + widget_lines[0], width))
            for extra in widget_lines[1:]:
                lines.append(_clip(" " * len(prefix) + extra, width))
        return lines

    def draw(self, win: Any, y: int, x: int) -> None:
        for i, line in enumerate(self.render_lines(10**9)):
            _safe_addstr(win, y + i, x, line)


def center_rect(screen_w: int, screen_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """Return (y, x) top-left so a box_w x box_h box is centered on a screen_w x screen_h
    screen. Clamped to (0, 0) if the box is larger than the screen in either dimension.
    """
    y = max(0, (screen_h - box_h) // 2)
    x = max(0, (screen_w - box_w) // 2)
    return y, x


def draw_box(
    win: Any,
    y: int,
    x: int,
    h: int,
    w: int,
    title: str = "",
    border_style: dict[str, str] | None = None,
) -> None:
    """Draw an h x w box with its top-left corner at (y, x), using border_style's glyphs.

    border_style follows the ui.theme.BORDER_CHARS shape (see glyphs()) -- pass e.g.
    glyphs("unicode") or glyphs("ascii"); glyphs("none") draws nothing (its runs are all
    empty strings). Falls back to glyphs("unicode") if border_style is omitted. Every
    addstr is individually guarded against curses.error, matching the discipline used
    elsewhere in the curses-aware drawing layer -- a too-narrow/short terminal must never
    crash the program.

    If title is given and there is room, it is rendered inline in the top border as
    " title " (bracketed by the horizontal glyph on either side).
    """
    if h <= 0 or w <= 0:
        return
    chars = border_style if border_style is not None else glyphs("unicode")
    h_ch = chars["h"]
    v_ch = chars["v"]

    top = chars["tl"] + h_ch * max(0, w - 2) + chars["tr"]
    if title and w >= 4:
        label = f" {title} "
        if len(label) <= w - 2:
            start = 1
            top = top[:start] + label + top[start + len(label) :]
            top = top[:w]
    _safe_addstr(win, y, x, top[:w])

    for row in range(1, h - 1):
        line = v_ch + " " * max(0, w - 2) + v_ch
        _safe_addstr(win, y + row, x, line[:w])

    if h > 1:
        bottom = chars["bl"] + h_ch * max(0, w - 2) + chars["br"]
        _safe_addstr(win, y + h - 1, x, bottom[:w])

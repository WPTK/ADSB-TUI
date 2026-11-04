"""The Sort screen: choose a sort key and ascending/descending order.

SortScreen follows the Screen design contract used across ui/screens/: a read-only
title, a pure render_lines(width, height) that returns plain, already-clipped strings
with no curses calls, and a handle_key(key) that reports navigation intent back to the
caller as a short string ("close" here) or None to stay open.

The screen is one list: every sort key (a radio choice, the chosen one marked "*"), then
the Reverse toggle. Up/Down move through it, Enter or Space chooses the key under the
cursor or flips Reverse, and Tab / Shift-Tab jump between the key list and the Reverse
row. Enter never closes the screen: s or F10 (also F2/S) applies the choice and closes,
Esc or q cancels -- with a "Discard changes? y/n" question first if the choice was
changed. result() is the caller's original (key, reverse) unless an apply came first.
"""

from __future__ import annotations

import curses
from collections.abc import Sequence
from typing import Any

from adsbtui.ui.widgets import (
    ENTER_KEYS,
    DiscardPrompt,
    Toggle,
    classify_key,
    compose_dialog,
)

_FOOTER_TEXT = "Up/Down move  Enter/Space choose  Tab list/reverse  s apply  Esc cancel"


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


class SortScreen:
    """Lets the user pick a sort key and toggle reverse order, then apply or cancel.

    available_keys is a sequence of (value, label) pairs, e.g.
    [("distance", "Distance"), ("altitude", "Altitude"), ("callsign", "Callsign")].
    current_key selects which of those is initially chosen (falling back to the first
    entry if current_key does not match any value in available_keys).
    """

    def __init__(
        self,
        available_keys: Sequence[tuple[Any, str]],
        current_key: Any,
        current_reverse: bool,
    ) -> None:
        if not available_keys:
            raise ValueError("SortScreen requires at least one available sort key")
        self._available_keys = list(available_keys)
        self._chosen = 0
        for i, (value, _label) in enumerate(self._available_keys):
            if value == current_key:
                self._chosen = i
                break
        self._initial_chosen = self._chosen
        self._reverse_toggle = Toggle(initial=current_reverse)
        self._initial_reverse = current_reverse
        #: Row under the cursor: 0..n-1 are the keys, n is the Reverse row.
        self._cursor = self._chosen
        self._prompt = DiscardPrompt()
        self.applied = False
        self.highlight_row: int | None = None
        # The committed result, only updated by an apply -- cancelling leaves these
        # exactly as the caller passed them in.
        self._result_key: Any = current_key
        self._result_reverse: bool = current_reverse

    @property
    def dirty(self) -> bool:
        return (
            self._chosen != self._initial_chosen
            or self._reverse_toggle.value != self._initial_reverse
        )

    @property
    def title(self) -> str:
        return "Sort *" if self.dirty else "Sort"

    @property
    def focus(self) -> int:
        """0 while the cursor is in the key list, 1 while it is on the Reverse row."""
        return 1 if self._cursor == len(self._available_keys) else 0

    def _apply(self) -> None:
        self._result_key = self._available_keys[self._chosen][0]
        self._result_reverse = self._reverse_toggle.value
        self.applied = True

    def handle_key(self, key: int) -> str | None:
        if self._prompt.active:
            return "close" if self._prompt.handle_key(key) == "discard" else None
        last = len(self._available_keys)
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
            self._cursor = last if self._cursor < last else self._chosen
        elif key in (curses.KEY_UP, ord("k")):
            self._cursor = max(0, self._cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self._cursor = min(last, self._cursor + 1)
        elif key == curses.KEY_HOME:
            self._cursor = 0
        elif key == curses.KEY_END:
            self._cursor = last
        elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
            step = 5 if key == curses.KEY_NPAGE else -5
            self._cursor = max(0, min(last, self._cursor + step))
        elif self._cursor == last:
            if key in ENTER_KEYS or key in (ord(" "), curses.KEY_LEFT, curses.KEY_RIGHT):
                self._reverse_toggle.handle_key(ord(" "))
        elif key in ENTER_KEYS or key == ord(" "):
            self._chosen = self._cursor
        return None

    def result(self) -> tuple[Any, bool]:
        """The committed (key, reverse) tuple: the caller's originals unless an apply
        took the screen's current state before it closed.
        """
        return (self._result_key, self._result_reverse)

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []
        last = len(self._available_keys)
        rows = [
            f"{'>' if i == self._cursor else ' '} ({'*' if i == self._chosen else ' '}) {label}"
            for i, (_value, label) in enumerate(self._available_keys)
        ]
        rows.append("-" * width)
        toggle = self._reverse_toggle.render_lines(width)[0]
        rows.append(f"{'>' if self._cursor == last else ' '} {toggle} Reverse order")
        focus_line = self._cursor if self._cursor < last else last + 1

        def body(capacity: int) -> tuple[list[str], int, str]:
            if len(rows) <= capacity:
                return rows, focus_line, ""
            # The Reverse row stays pinned at the bottom; the key list scrolls above it.
            pinned = rows[-2:]
            room = max(1, capacity - len(pinned) - 1)
            keys = rows[:-2]
            top = max(0, min(self._cursor - room // 2, len(keys) - room))
            if self._cursor >= last:
                top = len(keys) - room
            note = f"{min(self._cursor, last - 1) + 1}/{last}"
            window = keys[top : top + room]
            fl = self._cursor - top if self._cursor < last else room + 1
            return window + pinned, fl, note

        lines, self.highlight_row = compose_dialog(
            width,
            height,
            head=[],
            body=body,
            help_text="Which column the table is sorted by, and whether the order is reversed.",
            status=DiscardPrompt.TEXT if self._prompt.active else "",
            footer=_FOOTER_TEXT,
            min_height=last + 5,
        )
        return lines

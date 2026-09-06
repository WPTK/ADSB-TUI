"""The Sort screen: choose a sort key and ascending/descending order.

SortScreen follows the Screen design contract used across ui/screens/: a read-only
title, a pure render_lines(width, height) that returns plain, already-clipped strings
with no curses calls, and a handle_key(key) that reports navigation intent back to the
caller as a short string ("close" here) or None to stay open.

Internally this composes two of ui.widgets' pure widgets:

  * a ui.widgets.Select cycling through the available sort keys (Left/Right/Space) --
    Select fits better than ListPicker here because it never consumes Enter for its own
    purposes, which is exactly the hook this screen needs: pressing Enter while the key
    picker has focus falls through to this screen's own "apply and close" handling
    instead of being swallowed by the widget.
  * a ui.widgets.Toggle for the reverse-order flag (Space or Enter flips it) -- Toggle
    *does* consume Enter for its own selection (flipping), so pressing Enter while the
    toggle has focus just flips it and never closes the screen, matching the "when not
    already consumed by the focused widget for its own selection" rule below.

Tab (and Shift-Tab) moves focus between the two widgets. Escape closes the screen
without applying whatever the widgets currently show -- the caller's original
current_key/current_reverse are preserved in result(). Enter, when the focused widget
does not consume it itself, commits the widgets' current state into result() and closes
the screen.
"""

from __future__ import annotations

import curses
from collections.abc import Sequence
from typing import Any

from adsbtui.ui.widgets import Select, Toggle

_ESCAPE_KEY = 27
_ENTER_KEYS = frozenset({10, 13, curses.KEY_ENTER})
_TAB_FORWARD_KEYS = frozenset({ord("\t")})
_TAB_BACKWARD_KEYS = frozenset({curses.KEY_BTAB})

_FOCUS_KEY_PICKER = 0
_FOCUS_REVERSE_TOGGLE = 1
_FOCUS_COUNT = 2


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


class SortScreen:
    """Lets the user pick a sort key and toggle reverse order, then apply or cancel.

    available_keys is a sequence of (value, label) pairs, e.g.
    [("distance", "Distance"), ("altitude", "Altitude"), ("callsign", "Callsign")].
    current_key selects which of those is initially highlighted (falling back to the
    first entry if current_key does not match any value in available_keys).
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
        initial_index = 0
        for i, (value, _label) in enumerate(self._available_keys):
            if value == current_key:
                initial_index = i
                break
        self._key_select = Select(self._available_keys, initial_index=initial_index)
        self._reverse_toggle = Toggle(initial=current_reverse, label="Reverse order")
        self._focus = _FOCUS_KEY_PICKER
        # The committed result, only updated when Enter applies the widgets' current
        # state -- Escape leaves these exactly as the caller passed them in.
        self._result_key: Any = current_key
        self._result_reverse: bool = current_reverse

    @property
    def title(self) -> str:
        return "Sort"

    @property
    def focus(self) -> int:
        """0 while the key picker has focus, 1 while the reverse toggle does."""
        return self._focus

    def _focused_widget(self) -> Select | Toggle:
        return self._key_select if self._focus == _FOCUS_KEY_PICKER else self._reverse_toggle

    def _apply(self) -> None:
        self._result_key = self._key_select.value
        self._result_reverse = self._reverse_toggle.value

    def handle_key(self, key: int) -> str | None:
        if key == _ESCAPE_KEY:
            return "close"
        if key in _TAB_FORWARD_KEYS:
            self._focus = (self._focus + 1) % _FOCUS_COUNT
            return None
        if key in _TAB_BACKWARD_KEYS:
            self._focus = (self._focus - 1) % _FOCUS_COUNT
            return None
        if self._focused_widget().handle_key(key):
            return None
        if key in _ENTER_KEYS:
            self._apply()
            return "close"
        return None

    def result(self) -> tuple[Any, bool]:
        """The committed (key, reverse) tuple: the caller's originals unless Enter
        applied the widgets' current state before the screen closed.
        """
        return (self._result_key, self._result_reverse)

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []

        lines: list[str] = []
        key_header_mark = ">" if self._focus == _FOCUS_KEY_PICKER else " "
        lines.append(_clip(f"{key_header_mark} Sort by:", width))
        for i, (_value, label) in enumerate(self._available_keys):
            current_mark = "*" if i == self._key_select.index else " "
            lines.append(_clip(f"    {current_mark} {label}", width))

        lines.append("")

        toggle_header_mark = ">" if self._focus == _FOCUS_REVERSE_TOGGLE else " "
        toggle_lines = self._reverse_toggle.render_lines(max(0, width - 2))
        toggle_text = toggle_lines[0] if toggle_lines else ""
        lines.append(_clip(f"{toggle_header_mark} {toggle_text}", width))

        lines.append("")
        lines.append(_clip("Tab: switch focus  Enter: apply  Esc: cancel", width))

        return lines[:height]

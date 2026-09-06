"""The Filters screen: edit the live aircraft filter settings, plus the search text.

FiltersScreen follows the Screen design contract documented in ui/screens/__init__.py: a
read-only 'title', a pure 'render_lines(width, height)' that returns plain,
already-clipped strings with no curses calls, and a 'handle_key(key)' that reports
navigation intent back to the caller as a short string ("close" here, for both an applied
and a cancelled dismissal -- callers distinguish the two by reading the 'applied'
property before deciding whether to pull 'result()').

This screen doubles as the search-text entry box, so a near-duplicate one-field screen
isn't needed just for that: the constructor takes both the [filter] config values (as a
dict shaped like adsbtui.config.FilterConfig's hide_ground/include_nonicao/min_alt_ft/
max_alt_ft fields) and the current search_text string, and composes all five as one
ui.widgets.Form built from ui.widgets.Toggle, ui.widgets.NumberField and ui.widgets.
TextField instances. curses is imported only for its KEY_* integer constants, which is
safe even without an initialized screen -- no curses drawing/input function is called from
handle_key() or render_lines().
"""

from __future__ import annotations

import curses
from collections.abc import Mapping
from typing import Any

from adsbtui.ui.widgets import Form, NumberField, TextField, Toggle

#: Escape cancels the screen: discard any in-progress edits and close without applying.
_CANCEL_KEYS = frozenset({27})

#: Enter (in any of its curses-reported forms) or F10 applies the edited values and closes.
_APPLY_KEYS = frozenset({10, 13, curses.KEY_ENTER, curses.KEY_F10})

_FOOTER_TEXT = "Enter/F10 apply   Esc cancel   Tab/Shift-Tab/Up/Down move   Space toggle"


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


def _coerce_int(value: float | None, fallback: int) -> int:
    """Round a NumberField's parsed value to an int, or fall back if it doesn't parse.

    NumberField.value is None while its text is empty or mid-edit (e.g. a bare "-"), so a
    fallback -- the value the field started with -- keeps result() always returning a
    usable int even if the user leaves the field in an unparseable state.
    """
    return int(round(value)) if value is not None else fallback


class FiltersScreen:
    """Edit hide_ground, include_nonicao, min_alt_ft, max_alt_ft, and the search text.

    filters is a dict shaped like the [filter] config section: {"hide_ground": bool,
    "include_nonicao": bool, "min_alt_ft": int, "max_alt_ft": int}. Missing keys fall back
    to adsbtui.config.FilterConfig's own defaults so a partial dict never raises.
    search_text is the free-text substring filter across flight/hex/registration/owner,
    edited here as an ordinary field of the same form rather than a separate screen.

    Tab/Shift-Tab or Up/Down move focus between the five fields (delegated straight to the
    underlying Form, which already implements that); every other key is delegated to the
    focused widget (Space flips a Toggle, printable characters/Backspace/Delete/Left/Right/
    Home/End edit a TextField or NumberField). Escape closes without applying: the
    'applied' flag stays False, telling a caller to discard whatever result() would
    return. Enter or F10 sets 'applied' to True and closes: the caller should then read
    result() and write it back into its own filter state.
    """

    def __init__(self, filters: Mapping[str, Any], search_text: str = "") -> None:
        self._initial_min_alt = int(filters.get("min_alt_ft", 0))
        self._initial_max_alt = int(filters.get("max_alt_ft", 60000))

        self._search = TextField(initial=search_text)
        self._hide_ground = Toggle(
            initial=bool(filters.get("hide_ground", False)), label="Hide ground traffic"
        )
        self._include_nonicao = Toggle(
            initial=bool(filters.get("include_nonicao", True)),
            label="Include non-ICAO addresses",
        )
        self._min_alt = NumberField(initial=str(self._initial_min_alt))
        self._max_alt = NumberField(initial=str(self._initial_max_alt))

        self._form = Form(
            [
                ("Search", self._search),
                ("Hide ground", self._hide_ground),
                ("Include non-ICAO", self._include_nonicao),
                ("Min altitude (ft)", self._min_alt),
                ("Max altitude (ft)", self._max_alt),
            ]
        )
        self.applied = False

    @property
    def title(self) -> str:
        return "Filters"

    @property
    def focus_index(self) -> int:
        """Index of the currently focused field within the form (0-based)."""
        return self._form.focus_index

    def handle_key(self, key: int) -> str | None:
        if key in _CANCEL_KEYS:
            return "close"
        if key in _APPLY_KEYS:
            self.applied = True
            return "close"
        self._form.handle_key(key)
        return None

    def result(self) -> dict[str, Any]:
        """Current values of every field, in the same shape the constructor takes.

        Always returns all five values regardless of 'applied' -- it is the caller's
        responsibility to check 'applied' first and only persist this when it's True.
        """
        return {
            "hide_ground": self._hide_ground.value,
            "include_nonicao": self._include_nonicao.value,
            "min_alt_ft": _coerce_int(self._min_alt.value, self._initial_min_alt),
            "max_alt_ft": _coerce_int(self._max_alt.value, self._initial_max_alt),
            "search_text": self._search.value,
        }

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []
        lines: list[str] = [_clip(self.title, width), ""]
        lines.extend(self._form.render_lines(width))
        lines.append("")
        lines.append(_clip(_FOOTER_TEXT, width))
        return lines[:height]

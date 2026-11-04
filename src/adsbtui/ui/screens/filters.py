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

from adsbtui.ui.widgets import (
    DiscardPrompt,
    FieldList,
    FieldRow,
    NumberField,
    TextField,
    Toggle,
    classify_key,
    compose_dialog,
)

_FOOTER_TEXT = "Up/Down field  Space/Enter change  s apply  Esc cancel"
_EDIT_FOOTER_TEXT = "Editing: Enter or Tab keep  Esc undo  F10 apply"


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

    Up/Down move between the five fields; Space or Enter flips a Toggle, and Enter starts
    editing a text or number field (Enter or Tab keeps the edit, Esc undoes it). s or F10
    (also F2/S, there being nothing to save to disk here) sets 'applied' and closes: the
    caller should then read result() and write it back into its own filter state. Esc or q
    closes without applying; with unsaved edits it first asks "Discard changes? y/n".
    """

    def __init__(self, filters: Mapping[str, Any], search_text: str = "") -> None:
        self._initial_min_alt = int(filters.get("min_alt_ft", 0))
        self._initial_max_alt = int(filters.get("max_alt_ft", 60000))

        self._search = TextField(initial=search_text)
        self._hide_ground = Toggle(initial=bool(filters.get("hide_ground", False)))
        self._include_nonicao = Toggle(initial=bool(filters.get("include_nonicao", True)))
        self._min_alt = NumberField(initial=str(self._initial_min_alt))
        self._max_alt = NumberField(initial=str(self._initial_max_alt))

        self._form = FieldList(
            [
                FieldRow(
                    "Search",
                    self._search,
                    "Show only aircraft whose flight, hex, registration or owner "
                    "contains this text.",
                ),
                FieldRow(
                    "Hide ground", self._hide_ground, "Hide aircraft that report being on the ground."
                ),
                FieldRow(
                    "Include non-ICAO",
                    self._include_nonicao,
                    "Include ~-prefixed TIS-B/MLAT addresses that have no real ICAO hex.",
                ),
                FieldRow("Min altitude (ft)", self._min_alt, "Hide aircraft below this altitude."),
                FieldRow("Max altitude (ft)", self._max_alt, "Hide aircraft above this altitude."),
            ]
        )
        self._prompt = DiscardPrompt()
        self.applied = False
        self.highlight_row: int | None = None

    @property
    def title(self) -> str:
        return "Filters *" if self._form.dirty else "Filters"

    @property
    def focus_index(self) -> int:
        """Index of the currently focused field within the form (0-based)."""
        return self._form.focus

    def handle_key(self, key: int) -> str | None:
        if self._prompt.active:
            return "close" if self._prompt.handle_key(key) == "discard" else None
        editing = self._form.editing
        action = classify_key(key)
        if editing and key not in (curses.KEY_F10, curses.KEY_F2):
            action = None
        if action == "cancel":
            if self._form.dirty:
                self._prompt.active = True
                return None
            return "close"
        if action in ("apply", "save"):
            if editing:
                self._form.stop_edit()
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
        form = self._form
        lines, self.highlight_row = compose_dialog(
            width,
            height,
            head=[],
            body=lambda capacity: form.render(width, capacity),
            help_text=form.focused.help,
            status=DiscardPrompt.TEXT if self._prompt.active else "",
            footer=_EDIT_FOOTER_TEXT if form.editing else _FOOTER_TEXT,
            min_height=len(form.rows) + 5,
        )
        return lines

"""The Watchlist screen: browse, add, remove, and enable/disable watchlist entries.

WatchlistScreen follows the Screen design contract documented in ui/screens/__init__.py: a
read-only 'title', a pure 'render_lines(width, height)' that returns plain,
already-clipped strings with no curses calls, and a 'handle_key(key)' that reports
navigation intent back to the caller as a short string ("close" here) or None to stay
open. Like the other screens in this package, curses is imported only for its KEY_*
integer constants, which is safe even without an initialized screen -- no curses
drawing/input function is called from handle_key() or render_lines().

The screen has two panes, switched with Tab / Shift-Tab: the entry list and an "add entry"
form (prefix choice, pattern text, Add button).

  * List pane: Up/Down/j/k/PgUp/PgDn/Home/End move; Space or Enter turns the entry under the
    cursor on or off; d or Del deletes it; a jumps to the add form with the pattern field
    already being edited; w (when an aircraft is selected) adds its hex.
  * Add pane: Up/Down move between the three controls; Left/Right/Space change the prefix;
    Enter on Pattern edits it (Enter or Tab keeps the text, Esc undoes it); Enter on Add adds
    the entry and returns to the list.

Edits happen on a private working copy. s or F10 (also F2/S) applies them and closes; Esc or
q cancels, with a "Discard changes? y/n" question first when the list was changed. result()
is the working copy once applied and the original entries otherwise. This screen never
touches the filesystem itself; the caller saves what result() returns.
"""

from __future__ import annotations

import copy
import curses
from typing import TYPE_CHECKING

from adsbtui.ui.widgets import (
    ENTER_KEYS,
    Button,
    DiscardPrompt,
    FieldList,
    FieldRow,
    Select,
    TextField,
    classify_key,
    compose_dialog,
)
from adsbtui.watchlist import WatchEntry, parse_line

if TYPE_CHECKING:
    from adsbtui.model import Aircraft

_MODE_LIST = "list"
_MODE_ADD_FORM = "add_form"

_ADD_KEYS = frozenset({ord("a"), ord("A")})
_DELETE_KEYS = frozenset({curses.KEY_DC, ord("d"), ord("D")})
_TOGGLE_KEYS = frozenset({ord(" "), ord("e"), ord("E")})
_QUICK_ADD_KEYS = frozenset({ord("w"), ord("W")})

_FOOTER_LIST = "Space on/off  a add  d del  Tab form  s apply  Esc cancel"
_FOOTER_ADD = "Up/Down move  Enter edit/add  Tab list  s apply  Esc cancel"
_FOOTER_EDIT = "Editing: Enter or Tab keep  Esc undo  F10 apply"

#: (value, label) choices for the prefix Select, in the same order as
#: watchlist._PREFIX_TO_FIELD so the on-screen order matches the file format's own list.
_PREFIX_CHOICES: list[tuple[str, str]] = [
    ("hex", "Hex"),
    ("reg", "Reg"),
    ("call", "Call"),
    ("owner", "Owner"),
    ("type", "Type"),
]

_HELP_LIST = "Entries the alert engine watches for. Space turns one on or off."
_HELP_ADD = "Add a pattern: hex code, registration, callsign, owner or type."


def _entry_state(entries: list[WatchEntry]) -> list[tuple[str, str, bool]]:
    return [(e.prefix, e.pattern, e.enabled) for e in entries]


class WatchlistScreen:
    """Browse, add, remove, and enable/disable watchlist entries.

    entries is a list of adsbtui.watchlist.WatchEntry; a deep copy is kept internally, so
    edits made here never affect the caller's originals unless/until it reads result()
    after an apply. selected_aircraft, if given, enables the 'w' quick-add action, which
    adds a "hex:<value>" entry for that aircraft without going through the add form.
    """

    def __init__(
        self,
        entries: list[WatchEntry],
        selected_aircraft: Aircraft | None = None,
    ) -> None:
        self._original: list[WatchEntry] = copy.deepcopy(list(entries))
        self._entries: list[WatchEntry] = copy.deepcopy(list(entries))
        self._selected_aircraft = selected_aircraft
        self._mode = _MODE_LIST
        self._cursor = 0
        self._page = 8
        self.applied = False
        self.highlight_row: int | None = None
        self._prompt = DiscardPrompt()

        self._prefix_select = Select(_PREFIX_CHOICES)
        self._pattern_field = TextField()
        self._add_button = Button("Add entry")
        self._form = FieldList(
            [
                FieldRow("Prefix", self._prefix_select, _HELP_ADD),
                FieldRow("Pattern", self._pattern_field, _HELP_ADD),
                FieldRow("", self._add_button, _HELP_ADD),
            ]
        )

    @property
    def dirty(self) -> bool:
        return _entry_state(self._entries) != _entry_state(self._original)

    @property
    def title(self) -> str:
        return "Watchlist *" if self.dirty else "Watchlist"

    @property
    def mode(self) -> str:
        """ "list" while browsing the entry list, "add_form" while in the add form."""
        return self._mode

    @property
    def cursor(self) -> int:
        return self._cursor

    def _commit_new_entry(self) -> bool:
        """Parse the add form's prefix/pattern and, if valid, append it.

        An empty pattern (parse_line raises ValueError) leaves the form as it is.
        """
        raw_line = f"{self._prefix_select.value}:{self._pattern_field.value}"
        try:
            entry = parse_line(raw_line)
        except ValueError:
            return False
        if entry is None:
            return False
        self._entries.append(entry)
        self._pattern_field.value = ""
        self._cursor = len(self._entries) - 1
        self._mode = _MODE_LIST
        return True

    def _delete_current(self) -> None:
        if not self._entries:
            return
        del self._entries[self._cursor]
        self._cursor = max(0, min(self._cursor, len(self._entries) - 1))

    def _toggle_current_enabled(self) -> None:
        if self._entries:
            entry = self._entries[self._cursor]
            entry.enabled = not entry.enabled

    def _quick_add(self) -> None:
        if self._selected_aircraft is None:
            return
        try:
            entry = parse_line(f"hex:{self._selected_aircraft.hex}")
        except ValueError:
            return
        if entry is None:
            return
        self._entries.append(entry)
        self._cursor = len(self._entries) - 1

    def _enter_add_form(self, edit_pattern: bool) -> None:
        self._mode = _MODE_ADD_FORM
        if edit_pattern:
            self._form.focus = 1
            self._form.start_edit()

    def _move(self, delta: int) -> None:
        if self._entries:
            self._cursor = max(0, min(self._cursor + delta, len(self._entries) - 1))

    def handle_key(self, key: int) -> str | None:
        if self._prompt.active:
            return "close" if self._prompt.handle_key(key) == "discard" else None

        editing = self._mode == _MODE_ADD_FORM and self._form.editing
        action = classify_key(key)
        if editing and key not in (curses.KEY_F10, curses.KEY_F2):
            action = None
        if action == "cancel":
            if self.dirty:
                self._prompt.active = True
                return None
            return "close"
        if action in ("apply", "save"):
            if editing:
                self._form.stop_edit()
            self.applied = True
            return "close"
        if action in ("next", "prev"):
            if self._mode == _MODE_LIST:
                self._enter_add_form(edit_pattern=False)
            else:
                self._mode = _MODE_LIST
            return None

        if self._mode == _MODE_ADD_FORM:
            self._form.handle_key(key)
            if self._add_button.pressed:
                self._add_button.pressed = False
                self._commit_new_entry()
            return None

        if key in _ADD_KEYS:
            self._enter_add_form(edit_pattern=True)
        elif key in _DELETE_KEYS:
            self._delete_current()
        elif key in ENTER_KEYS or key in _TOGGLE_KEYS:
            self._toggle_current_enabled()
        elif self._selected_aircraft is not None and key in _QUICK_ADD_KEYS:
            self._quick_add()
        elif key in (curses.KEY_UP, ord("k")):
            self._move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self._move(1)
        elif key == curses.KEY_PPAGE:
            self._move(-self._page)
        elif key == curses.KEY_NPAGE:
            self._move(self._page)
        elif key == curses.KEY_HOME:
            self._cursor = 0
        elif key == curses.KEY_END:
            self._move(len(self._entries))
        return None

    def result(self) -> list[WatchEntry]:
        """The working list once applied; otherwise the entries the screen was opened with."""
        return list(self._entries if self.applied else self._original)

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []
        in_list = self._mode == _MODE_LIST
        n = len(self._entries)
        form_height = len(self._form.rows)

        def body(capacity: int) -> tuple[list[str], int, str]:
            # Entries get what is left after the add form's own rows and its two headers.
            room = max(1, min(capacity - form_height - 2, max(6, n + 2)))
            self._page = max(1, room - 1)
            top = max(0, min(self._cursor - room // 2, max(0, n - room)))
            title = f"Entries ({n})"
            if n > room:
                title += f"  {self._cursor + 1}/{n}"
            lines = [f"-- {title} ".ljust(width, "-")]
            shown = self._entries[top : top + room - 1] if room > 1 else []
            if not shown:
                lines.append("  (none yet: press a to add one)")
            for i, entry in enumerate(shown, start=top):
                mark = ">" if in_list and i == self._cursor else " "
                box = "[x]" if entry.enabled else "[ ]"
                lines.append(f"{mark} {box} {entry.prefix}:{entry.pattern}")
            focus_line = (self._cursor - top + 1) if shown and in_list else 0
            while len(lines) < room:
                lines.append("")
            lines.append("-- Add entry ".ljust(width, "-"))
            form_lines, form_focus, _ = self._form.render(width, form_height)
            if not in_list:
                focus_line = len(lines) + form_focus
            lines.extend(form_lines)
            return lines, focus_line, ""

        if in_list:
            footer, help_text = _FOOTER_LIST, _HELP_LIST
            if self._selected_aircraft is not None:
                footer = footer.replace("d del", "d del  w add this")
        else:
            footer = _FOOTER_EDIT if self._form.editing else _FOOTER_ADD
            help_text = _HELP_ADD
        lines, self.highlight_row = compose_dialog(
            width,
            height,
            head=[],
            body=body,
            help_text=help_text,
            status=DiscardPrompt.TEXT if self._prompt.active else "",
            footer=footer,
            min_height=form_height + 10,
        )
        return lines

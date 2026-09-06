"""The Watchlist screen: browse, add, remove, and enable/disable watchlist entries.

WatchlistScreen follows the Screen design contract documented in ui/screens/__init__.py: a
read-only 'title', a pure 'render_lines(width, height)' that returns plain,
already-clipped strings with no curses calls, and a 'handle_key(key)' that reports
navigation intent back to the caller as a short string ("close" here) or None to stay
open. Like the other screens in this package, curses is imported only for its KEY_*
integer constants, which is safe even without an initialized screen -- no curses
drawing/input function is called from handle_key() or render_lines().

This screen composes three of ui.widgets' pure widgets:

  * a ui.widgets.ListPicker (single-select) to browse the current entries -- single-select
    fits an edit list better than multi-select here, since Space/Enter aren't needed for
    bulk selection and the cursor position alone is enough to say which entry the d/e/w
    actions below apply to.
  * a ui.widgets.Select cycling through the five watchlist prefixes (hex/reg/call/owner/
    type), plus a ui.widgets.TextField for the pattern text, composed together with
    ui.widgets.Form into a small "add new entry" mini-form -- Form already implements the
    Tab/Shift-Tab focus movement this screen's add-form needs between those two fields.

The screen has two modes, tracked by 'mode': "list" (the default -- arrow keys/j/k/PageUp/
PageDown/Home/End move the ListPicker's cursor, and a/d/e/w are single-key actions) and
"add_form" (entered via 'a' -- Tab/Shift-Tab move focus between the prefix Select and the
pattern TextField, and every other key is delegated to whichever of those two is
currently focused, so typing a pattern works exactly like any other text field). Escape
and F10 close the screen unconditionally, in either mode.

Adding, deleting, and toggling all take effect immediately against this screen's internal
working copy of the entries list (a shallow copy taken at construction time, so the
caller's original list is never mutated as a side effect of merely opening this screen);
result() returns that working copy so a caller can pass it to adsbtui.watchlist.save() if
it wants the edits persisted -- this screen never touches the filesystem itself.
"""

from __future__ import annotations

import curses
from typing import TYPE_CHECKING

from adsbtui.ui.widgets import Form, ListPicker, Select, TextField
from adsbtui.watchlist import WatchEntry, parse_line

if TYPE_CHECKING:
    from adsbtui.model import Aircraft

_MODE_LIST = "list"
_MODE_ADD_FORM = "add_form"

_CLOSE_KEYS = frozenset({27, curses.KEY_F10})
_ENTER_KEYS = frozenset({10, 13, curses.KEY_ENTER})
_ADD_KEYS = frozenset({ord("a"), ord("A")})
_DELETE_KEYS = frozenset({curses.KEY_DC, ord("d"), ord("D")})
_TOGGLE_ENABLED_KEYS = frozenset({ord("e"), ord("E")})
_QUICK_ADD_KEYS = frozenset({ord("w"), ord("W")})

_VISIBLE_ROWS = 8

#: (value, label) choices for the prefix Select, in the same order as
#: watchlist._PREFIX_TO_FIELD so the on-screen order matches the file format's own list.
_PREFIX_CHOICES: list[tuple[str, str]] = [
    ("hex", "Hex"),
    ("reg", "Reg"),
    ("call", "Call"),
    ("owner", "Owner"),
    ("type", "Type"),
]


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


def _label_entry(entry: WatchEntry) -> str:
    mark = "[x]" if entry.enabled else "[ ]"
    return f"{mark} {entry.prefix}:{entry.pattern}"


class WatchlistScreen:
    """Browse, add, remove, and enable/disable watchlist entries.

    entries is a list of adsbtui.watchlist.WatchEntry; a shallow copy is kept internally,
    so edits made here never affect the caller's original list unless/until it reads
    result(). selected_aircraft, if given, enables the 'w' quick-add action, which adds a
    "hex:<value>" entry for that aircraft's hex code without going through the add-form.
    """

    def __init__(
        self,
        entries: list[WatchEntry],
        selected_aircraft: Aircraft | None = None,
    ) -> None:
        self._entries: list[WatchEntry] = list(entries)
        self._selected_aircraft = selected_aircraft
        self._mode = _MODE_LIST

        self._list = ListPicker(
            self._entries,
            multi_select=False,
            visible_rows=_VISIBLE_ROWS,
            labeler=_label_entry,
        )
        self._prefix_select = Select(_PREFIX_CHOICES)
        self._pattern_field = TextField()
        self._form = Form(
            [
                ("Prefix", self._prefix_select),
                ("Pattern", self._pattern_field),
            ]
        )

    @property
    def title(self) -> str:
        return "Watchlist"

    @property
    def mode(self) -> str:
        """ "list" while browsing the entry list, "add_form" while adding a new one."""
        return self._mode

    def _rebuild_list(self, cursor: int) -> None:
        """Recreate the ListPicker over the current entries, restoring the cursor.

        ListPicker takes its own copy of the items it is given, so after a structural
        change (add/delete) to self._entries a fresh ListPicker is needed to see it;
        cursor is restored by moving down from 0 through the public handle_key() API,
        which already clamps to the valid range, rather than reaching into ListPicker's
        private state.
        """
        self._list = ListPicker(
            self._entries,
            multi_select=False,
            visible_rows=_VISIBLE_ROWS,
            labeler=_label_entry,
        )
        for _ in range(max(0, cursor)):
            self._list.handle_key(curses.KEY_DOWN)

    def _commit_new_entry(self) -> None:
        """Parse the add-form's current prefix/pattern and, if valid, append it.

        Invalid input (empty pattern -- parse_line raises ValueError for that) simply
        leaves the add-form open with whatever was typed so far, rather than closing the
        screen or raising out of handle_key().
        """
        raw_line = f"{self._prefix_select.value}:{self._pattern_field.value}"
        try:
            entry = parse_line(raw_line)
        except ValueError:
            return
        if entry is None:
            return
        self._entries.append(entry)
        self._pattern_field.value = ""
        self._rebuild_list(cursor=len(self._entries) - 1)
        self._mode = _MODE_LIST

    def _delete_current(self) -> None:
        if not self._entries:
            return
        cursor = self._list.cursor
        del self._entries[cursor]
        self._rebuild_list(cursor=cursor)

    def _toggle_current_enabled(self) -> None:
        if not self._entries:
            return
        entry = self._entries[self._list.cursor]
        entry.enabled = not entry.enabled

    def _quick_add(self) -> None:
        if self._selected_aircraft is None:
            return
        raw_line = f"hex:{self._selected_aircraft.hex}"
        try:
            entry = parse_line(raw_line)
        except ValueError:
            return
        if entry is None:
            return
        self._entries.append(entry)
        self._rebuild_list(cursor=len(self._entries) - 1)

    def handle_key(self, key: int) -> str | None:
        if key in _CLOSE_KEYS:
            return "close"

        if self._mode == _MODE_ADD_FORM:
            if key in _ENTER_KEYS:
                self._commit_new_entry()
                return None
            self._form.handle_key(key)
            return None

        # mode == "list"
        if key in _ADD_KEYS:
            self._mode = _MODE_ADD_FORM
            return None
        if key in _DELETE_KEYS:
            self._delete_current()
            return None
        if key in _TOGGLE_ENABLED_KEYS:
            self._toggle_current_enabled()
            return None
        if self._selected_aircraft is not None and key in _QUICK_ADD_KEYS:
            self._quick_add()
            return None
        self._list.handle_key(key)
        return None

    def result(self) -> list[WatchEntry]:
        """The screen's current working list of entries.

        Reflects every add/delete/toggle made so far, regardless of how the screen was
        (or will be) closed -- the caller decides whether to persist this via
        adsbtui.watchlist.save().
        """
        return list(self._entries)

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []

        lines: list[str] = [_clip(self.title, width), ""]

        list_mark = ">" if self._mode == _MODE_LIST else " "
        lines.append(_clip(f"{list_mark} Entries ({len(self._entries)}):", width))
        lines.extend(_clip(line, width) for line in self._list.render_lines(max(0, width - 2)))
        lines.append("")

        form_mark = ">" if self._mode == _MODE_ADD_FORM else " "
        lines.append(_clip(f"{form_mark} Add new entry:", width))
        lines.extend(_clip(line, width) for line in self._form.render_lines(max(0, width - 2)))
        lines.append("")

        footer = "a add   d/Del delete   e toggle enabled"
        if self._selected_aircraft is not None:
            footer += "   w quick-add hex"
        footer += "   Tab move   Enter commit   Esc/F10 close"
        lines.append(_clip(footer, width))

        return lines[:height]

"""_terminal_can_draw: the TERM=dumb refusal must not fire where there is no terminfo."""

from __future__ import annotations

import sys

from adsbtui import __main__ as entry


def test_windows_is_never_refused(monkeypatch):
    # PDCurses has no terminfo: tigetstr("cup") is None there even in a working console.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(entry.curses, "setupterm", lambda: None, raising=False)
    monkeypatch.setattr(entry.curses, "tigetstr", lambda _name: None, raising=False)
    assert entry._terminal_can_draw() is True  # noqa: SLF001


def test_a_terminal_without_cursor_addressing_is_refused_elsewhere(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(entry.curses, "setupterm", lambda: None, raising=False)
    monkeypatch.setattr(entry.curses, "tigetstr", lambda _name: None, raising=False)
    assert entry._terminal_can_draw() is False  # noqa: SLF001

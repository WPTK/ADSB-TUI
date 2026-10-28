"""The key table is the one source for the loop, Help and the README."""

from __future__ import annotations

import curses
from pathlib import Path

from adsbtui.ui import keys
from adsbtui.ui.screens.help import HelpScreen


def test_no_key_is_bound_twice():
    seen: dict[int, str] = {}
    for b in keys.BINDINGS:
        for k in b.keys:
            assert k not in seen, f"{k} bound to {seen[k]} and {b.action}"
            seen[k] = b.action


def test_every_binding_appears_in_help():
    text = chr(10).join(HelpScreen(keys.HELP_KEYMAP, "x").render_lines(100, 200))
    for b in keys.BINDINGS:
        assert b.label in text, b.action


def test_the_key_bar_only_advertises_bound_keys():
    assert set(keys.BAR_KEYMAP) <= set(keys.HELP_KEYMAP)


def test_help_scrolls_and_any_other_key_closes():
    screen = HelpScreen(keys.HELP_KEYMAP, "x")
    first = screen.render_lines(80, 12)
    assert screen.handle_key(curses.KEY_NPAGE) is None
    assert screen.render_lines(80, 12) != first
    assert screen.handle_key(curses.KEY_HOME) is None
    assert screen.render_lines(80, 12) == first
    assert screen.handle_key(ord("x")) == "close"


def test_readme_documents_every_action_key():
    readme = Path(__file__).resolve().parent.parent.joinpath("README.md").read_text("utf-8")
    for needle in ("F1", "F2", "F8", "Enter", "PgUp", "Home", "`S`", "`/`", "F10", "`d`"):
        assert needle in readme, needle

"""Tests for adsbtui.ui.screens.help.HelpScreen -- pure, no curses window required."""

from __future__ import annotations

import curses

import pytest

from adsbtui.ui.screens.help import HelpScreen

SAMPLE_KEYMAP = {
    "help": "F1",
    "settings": "F2",
    "search": "F3",
    "filters": "F4",
    "sort": "F5",
    "columns": "F6",
    "watchlist": "F7",
    "detail": "Enter",
    "pause": "p",
    "units": "u",
    "theme": "t",
    "quit": "F10 / q",
}


def make_screen(keymap: dict[str, str] | None = None, version: str = "1.2.3") -> HelpScreen:
    return HelpScreen(keymap if keymap is not None else SAMPLE_KEYMAP, version)


class TestTitle:
    def test_title_is_help(self) -> None:
        screen = make_screen()
        assert screen.title == "Help"


class TestHandleKey:
    @pytest.mark.parametrize(
        "key",
        [
            ord("q"),
            ord("a"),
            ord(" "),
            27,  # Escape
            curses.KEY_F1,
            curses.KEY_UP,
            curses.KEY_ENTER,
            10,
            0,
            -1,
        ],
    )
    def test_any_key_closes(self, key: int) -> None:
        screen = make_screen()
        assert screen.handle_key(key) == "close"

    def test_repeated_keys_keep_closing(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("x")) == "close"
        assert screen.handle_key(ord("y")) == "close"
        assert screen.handle_key(curses.KEY_F1) == "close"


class TestRenderLines:
    def test_lists_every_action_and_key(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        for action, key_label in SAMPLE_KEYMAP.items():
            assert _action_display(action) in text
            assert key_label in text

    def test_each_action_key_pair_shares_a_line(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        for action, key_label in SAMPLE_KEYMAP.items():
            matching = [
                line for line in lines if key_label in line and _action_display(action) in line
            ]
            assert matching, f"expected a line pairing {action!r} with {key_label!r}"

    def test_includes_version_string(self) -> None:
        screen = make_screen(version="9.9.9-test")
        lines = screen.render_lines(80, 40)
        assert any("9.9.9-test" in line for line in lines)

    def test_includes_dismiss_footer(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        assert any("press any key" in line.lower() for line in lines)

    def test_lines_clipped_to_width(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(6, 40)
        assert all(len(line) <= 6 for line in lines)

    def test_zero_or_negative_width_yields_empty_lines_or_none(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(0, 40)
        assert lines == []

    def test_zero_height_yields_no_lines(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 0)
        assert lines == []

    def test_height_truncates_line_count(self) -> None:
        screen = make_screen()
        full = screen.render_lines(80, 1000)
        truncated = screen.render_lines(80, 3)
        assert len(truncated) == 3
        assert truncated == full[:3]

    def test_all_lines_are_plain_strings(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)

    def test_empty_keymap_still_renders_version_and_footer(self) -> None:
        screen = make_screen(keymap={})
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert "1.2.3" in text
        assert "press any key" in text.lower()

    def test_no_curses_window_or_input_needed(self) -> None:
        # Sanity check on the design contract: render_lines/handle_key never touch a
        # curses window object -- calling them with plain ints/None is enough.
        screen = make_screen()
        assert screen.render_lines(40, 10)
        assert screen.handle_key(ord("q")) == "close"


def _action_display(action: str) -> str:
    return action.replace("_", " ").title()

"""Tests for adsbtui.ui.screens.filters.FiltersScreen -- pure, no curses window required.

Exercises handle_key() state transitions (focus movement, cancel vs apply) and
render_lines() content only, matching the pure-testability contract documented in
ui/screens/__init__.py and ui/screens/filters.py.
"""

from __future__ import annotations

import curses

import pytest

from adsbtui.ui.screens.filters import FiltersScreen

DEFAULT_FILTERS = {
    "hide_ground": False,
    "include_nonicao": True,
    "min_alt_ft": 0,
    "max_alt_ft": 60000,
}


def make_screen(filters: dict | None = None, search_text: str = "") -> FiltersScreen:
    return FiltersScreen(dict(filters if filters is not None else DEFAULT_FILTERS), search_text)


class TestTitle:
    def test_title_is_filters(self) -> None:
        assert make_screen().title == "Filters"


class TestConstructorDefaults:
    def test_missing_keys_fall_back_to_filterconfig_defaults(self) -> None:
        screen = FiltersScreen({}, "")
        result = screen.result()
        assert result["hide_ground"] is False
        assert result["include_nonicao"] is True
        assert result["min_alt_ft"] == 0
        assert result["max_alt_ft"] == 60000
        assert result["search_text"] == ""

    def test_initial_values_carried_through_to_result_unchanged(self) -> None:
        filters = {
            "hide_ground": True,
            "include_nonicao": False,
            "min_alt_ft": 500,
            "max_alt_ft": 25000,
        }
        screen = make_screen(filters, search_text="UAL")
        assert screen.result() == {
            "hide_ground": True,
            "include_nonicao": False,
            "min_alt_ft": 500,
            "max_alt_ft": 25000,
            "search_text": "UAL",
        }


class TestAppliedFlag:
    def test_starts_unapplied(self) -> None:
        assert make_screen().applied is False

    @pytest.mark.parametrize("key", [10, 13, curses.KEY_ENTER, curses.KEY_F10])
    def test_apply_keys_set_applied_and_close(self, key) -> None:
        screen = make_screen()
        assert screen.handle_key(key) == "close"
        assert screen.applied is True

    def test_escape_closes_without_applying(self) -> None:
        screen = make_screen()
        assert screen.handle_key(27) == "close"
        assert screen.applied is False

    def test_escape_after_edits_still_reports_unapplied(self) -> None:
        """Escape doesn't undo in-progress edits, but it must never flip applied True --
        a caller checks applied before deciding whether to read result() at all."""
        screen = make_screen()
        screen.handle_key(ord(" "))  # flips the initially-focused Search field... no-op
        screen.handle_key(curses.KEY_DOWN)  # focus -> Hide ground toggle
        screen.handle_key(ord(" "))  # flip it
        assert screen.handle_key(27) == "close"
        assert screen.applied is False


class TestFocusMovement:
    def test_starts_focused_on_first_field(self) -> None:
        assert make_screen().focus_index == 0

    def test_tab_advances_focus_and_wraps(self) -> None:
        screen = make_screen()
        for expected in (1, 2, 3, 4, 0):
            screen.handle_key(ord("\t"))
            assert screen.focus_index == expected

    def test_down_advances_focus_same_as_tab(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_DOWN)
        assert screen.focus_index == 1

    def test_shift_tab_moves_focus_back_and_wraps(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_BTAB)
        assert screen.focus_index == 4

    def test_up_moves_focus_back_same_as_shift_tab(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_UP)
        assert screen.focus_index == 4

    def test_focus_movement_keys_return_none_and_do_not_close(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("\t")) is None
        assert screen.handle_key(curses.KEY_BTAB) is None
        assert screen.handle_key(curses.KEY_UP) is None
        assert screen.handle_key(curses.KEY_DOWN) is None


class TestFieldEditing:
    def test_space_toggles_hide_ground_when_focused(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_DOWN)  # Search -> Hide ground
        assert screen.handle_key(ord(" ")) is None
        assert screen.result()["hide_ground"] is True

    def test_space_toggles_include_nonicao_when_focused(self) -> None:
        screen = make_screen()
        for _ in range(2):
            screen.handle_key(curses.KEY_DOWN)  # Search -> Hide ground -> Include non-ICAO
        screen.handle_key(ord(" "))
        assert screen.result()["include_nonicao"] is False

    def test_typing_appends_to_search_field(self) -> None:
        screen = make_screen(search_text="")
        for ch in "N123AB":
            screen.handle_key(ord(ch))
        assert screen.result()["search_text"] == "N123AB"

    def test_backspace_edits_search_field(self) -> None:
        screen = make_screen(search_text="UAL1")
        screen.handle_key(curses.KEY_END)
        screen.handle_key(curses.KEY_BACKSPACE)
        assert screen.result()["search_text"] == "UAL"

    def test_editing_min_alt_field_updates_result_as_int(self) -> None:
        screen = make_screen()
        for _ in range(3):
            screen.handle_key(curses.KEY_DOWN)  # -> Search, Hide ground, Include, Min alt
        screen.handle_key(curses.KEY_BACKSPACE)  # clear the initial "0"
        for ch in "250":
            screen.handle_key(ord(ch))
        assert screen.result()["min_alt_ft"] == 250

    def test_editing_max_alt_field_updates_result_as_int(self) -> None:
        screen = make_screen()
        for _ in range(4):
            screen.handle_key(curses.KEY_DOWN)  # -> ... -> Max alt
        for _ in range(5):
            screen.handle_key(curses.KEY_BACKSPACE)  # clear the initial "60000"
        for ch in "18000":
            screen.handle_key(ord(ch))
        assert screen.result()["max_alt_ft"] == 18000

    def test_clearing_a_number_field_falls_back_to_initial_value_in_result(self) -> None:
        screen = make_screen({**DEFAULT_FILTERS, "min_alt_ft": 1000})
        for _ in range(3):
            screen.handle_key(curses.KEY_DOWN)  # -> Min alt
        for _ in range(4):
            screen.handle_key(curses.KEY_BACKSPACE)  # clear "1000" entirely
        assert screen.result()["min_alt_ft"] == 1000

    def test_non_close_key_leaves_screen_open(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("x")) is None


class TestRenderLines:
    def test_title_appears_first(self) -> None:
        lines = make_screen().render_lines(80, 40)
        assert lines[0] == "Filters"

    def test_field_labels_present(self) -> None:
        text = "\n".join(make_screen().render_lines(80, 40))
        labels = (
            "Search",
            "Hide ground",
            "Include non-ICAO",
            "Min altitude (ft)",
            "Max altitude (ft)",
        )
        for label in labels:
            assert label in text

    def test_toggle_marks_reflect_current_values(self) -> None:
        filters = {**DEFAULT_FILTERS, "hide_ground": True, "include_nonicao": False}
        text = "\n".join(make_screen(filters).render_lines(80, 40))
        hide_ground_line = next(line for line in text.splitlines() if "Hide ground" in line)
        include_line = next(line for line in text.splitlines() if "Include non-ICAO" in line)
        assert "[x]" in hide_ground_line
        assert "[ ]" in include_line

    def test_altitude_values_shown(self) -> None:
        filters = {**DEFAULT_FILTERS, "min_alt_ft": 500, "max_alt_ft": 25000}
        text = "\n".join(make_screen(filters).render_lines(80, 40))
        assert "500" in text
        assert "25000" in text

    def test_search_text_shown(self) -> None:
        text = "\n".join(make_screen(search_text="N12345").render_lines(80, 40))
        assert "N12345" in text

    def test_footer_hints_present(self) -> None:
        text = "\n".join(make_screen().render_lines(80, 40))
        assert "Enter" in text
        assert "Esc" in text

    def test_focused_field_marked_with_caret(self) -> None:
        lines = make_screen().render_lines(80, 40)
        search_line = next(line for line in lines if "Search" in line)
        assert search_line.lstrip().startswith(">")

    def test_focus_marker_moves_with_tab(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("\t"))
        lines = screen.render_lines(80, 40)
        hide_ground_line = next(line for line in lines if "Hide ground" in line)
        search_line = next(line for line in lines if "Search:" in line)
        assert hide_ground_line.startswith(">")
        assert search_line.startswith(" ")

    def test_respects_width_clipping(self) -> None:
        lines = make_screen(search_text="X" * 200).render_lines(20, 40)
        assert all(len(line) <= 20 for line in lines)

    def test_respects_height_limit(self) -> None:
        lines = make_screen().render_lines(80, 3)
        assert len(lines) == 3

    def test_zero_or_negative_dimensions_return_empty(self) -> None:
        screen = make_screen()
        assert screen.render_lines(0, 40) == []
        assert screen.render_lines(80, 0) == []
        assert screen.render_lines(-5, 40) == []
        assert screen.render_lines(80, -5) == []

    def test_all_lines_are_plain_strings(self) -> None:
        lines = make_screen().render_lines(80, 40)
        assert all(isinstance(line, str) for line in lines)

"""Tests for adsbtui.ui.screens.sort.SortScreen -- pure, no curses window required."""

from __future__ import annotations

import curses

import pytest

from adsbtui.ui.screens.sort import SortScreen

AVAILABLE_KEYS = [
    ("distance", "Distance"),
    ("altitude", "Altitude"),
    ("callsign", "Callsign"),
]


def make_screen(
    available_keys: list[tuple[str, str]] | None = None,
    current_key: str = "distance",
    current_reverse: bool = False,
) -> SortScreen:
    return SortScreen(
        available_keys if available_keys is not None else AVAILABLE_KEYS,
        current_key,
        current_reverse,
    )


class TestConstruction:
    def test_title_is_sort(self) -> None:
        screen = make_screen()
        assert screen.title == "Sort"

    def test_empty_available_keys_raises(self) -> None:
        with pytest.raises(ValueError):
            SortScreen([], "distance", False)

    def test_starts_with_key_picker_focused(self) -> None:
        screen = make_screen()
        assert screen.focus == 0

    def test_result_matches_constructor_args_before_any_key(self) -> None:
        screen = make_screen(current_key="altitude", current_reverse=True)
        assert screen.result() == ("altitude", True)

    def test_unknown_current_key_falls_back_to_first_entry_but_result_preserved(self) -> None:
        screen = make_screen(current_key="bogus", current_reverse=False)
        # Nothing applied yet: result() must still echo exactly what was passed in,
        # even though "bogus" isn't one of the available keys.
        assert screen.result() == ("bogus", False)
        lines = screen.render_lines(80, 40)
        # The picker itself falls back to highlighting the first entry.
        assert any("*" in line and "Distance" in line for line in lines)


class TestFocusSwitching:
    def test_tab_moves_focus_to_toggle(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("\t")) is None
        assert screen.focus == 1

    def test_tab_wraps_around(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("\t"))
        screen.handle_key(ord("\t"))
        assert screen.focus == 0

    def test_shift_tab_moves_focus_backward_and_wraps(self) -> None:
        screen = make_screen()
        assert screen.handle_key(curses.KEY_BTAB) is None
        assert screen.focus == 1

    def test_tab_never_closes(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("\t")) is None
        assert screen.handle_key(curses.KEY_BTAB) is None


class TestKeyPickerInteraction:
    def test_right_arrow_cycles_key_selection(self) -> None:
        screen = make_screen(current_key="distance")
        assert screen.handle_key(curses.KEY_RIGHT) is None
        lines = screen.render_lines(80, 40)
        assert any("*" in line and "Altitude" in line for line in lines)

    def test_left_arrow_wraps_to_last_key(self) -> None:
        screen = make_screen(current_key="distance")
        screen.handle_key(curses.KEY_LEFT)
        lines = screen.render_lines(80, 40)
        assert any("*" in line and "Callsign" in line for line in lines)

    def test_space_also_cycles_key_selection(self) -> None:
        screen = make_screen(current_key="distance")
        screen.handle_key(ord(" "))
        lines = screen.render_lines(80, 40)
        assert any("*" in line and "Altitude" in line for line in lines)

    def test_arrow_keys_do_not_close_screen(self) -> None:
        screen = make_screen()
        assert screen.handle_key(curses.KEY_RIGHT) is None
        assert screen.handle_key(curses.KEY_LEFT) is None

    def test_cycling_does_not_apply_until_enter(self) -> None:
        screen = make_screen(current_key="distance")
        screen.handle_key(curses.KEY_RIGHT)
        assert screen.result() == ("distance", False)

    def test_enter_on_key_picker_applies_and_closes(self) -> None:
        screen = make_screen(current_key="distance")
        screen.handle_key(curses.KEY_RIGHT)
        assert screen.result() == ("distance", False)
        outcome = screen.handle_key(curses.KEY_ENTER)
        assert outcome == "close"
        assert screen.result() == ("altitude", False)

    @pytest.mark.parametrize("enter_key", [10, 13, curses.KEY_ENTER])
    def test_all_enter_variants_apply_and_close(self, enter_key: int) -> None:
        screen = make_screen(current_key="distance")
        screen.handle_key(curses.KEY_RIGHT)
        assert screen.handle_key(enter_key) == "close"
        assert screen.result() == ("altitude", False)


class TestReverseToggleInteraction:
    def test_space_flips_toggle_when_focused(self) -> None:
        screen = make_screen(current_reverse=False)
        screen.handle_key(ord("\t"))
        assert screen.handle_key(ord(" ")) is None
        lines = screen.render_lines(80, 40)
        assert any("[x]" in line for line in lines)

    def test_enter_on_toggle_flips_but_does_not_close(self) -> None:
        screen = make_screen(current_reverse=False)
        screen.handle_key(ord("\t"))
        outcome = screen.handle_key(curses.KEY_ENTER)
        assert outcome is None
        lines = screen.render_lines(80, 40)
        assert any("[x]" in line for line in lines)

    def test_toggle_change_not_applied_until_a_later_unconsumed_enter(self) -> None:
        screen = make_screen(current_key="distance", current_reverse=False)
        screen.handle_key(ord("\t"))  # focus -> toggle
        screen.handle_key(ord(" "))  # flip toggle to True, not yet applied
        assert screen.result() == ("distance", False)
        screen.handle_key(ord("\t"))  # focus -> key picker
        outcome = screen.handle_key(curses.KEY_ENTER)  # unconsumed by Select -> applies
        assert outcome == "close"
        assert screen.result() == ("distance", True)


class TestEscapeCancels:
    def test_escape_closes_without_applying_key_change(self) -> None:
        screen = make_screen(current_key="distance", current_reverse=False)
        screen.handle_key(curses.KEY_RIGHT)  # would-be change to "altitude"
        outcome = screen.handle_key(_ESCAPE := 27)
        assert outcome == "close"
        assert screen.result() == ("distance", False)

    def test_escape_closes_without_applying_toggle_change(self) -> None:
        screen = make_screen(current_key="distance", current_reverse=False)
        screen.handle_key(ord("\t"))
        screen.handle_key(ord(" "))  # flip toggle, not applied
        outcome = screen.handle_key(27)
        assert outcome == "close"
        assert screen.result() == ("distance", False)

    def test_escape_from_either_focus_cancels(self) -> None:
        screen = make_screen(current_key="distance", current_reverse=False)
        screen.handle_key(ord("\t"))
        outcome = screen.handle_key(27)
        assert outcome == "close"
        assert screen.result() == ("distance", False)


class TestRenderLines:
    def test_lists_every_available_key_label(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        for _value, label in AVAILABLE_KEYS:
            assert label in text

    def test_current_key_is_marked(self) -> None:
        screen = make_screen(current_key="altitude")
        lines = screen.render_lines(80, 40)
        marked = [line for line in lines if "*" in line]
        assert any("Altitude" in line for line in marked)
        assert not any("Distance" in line for line in marked)
        assert not any("Callsign" in line for line in marked)

    def test_shows_reverse_toggle_state_off(self) -> None:
        screen = make_screen(current_reverse=False)
        lines = screen.render_lines(80, 40)
        assert any("[ ]" in line for line in lines)
        assert any("Reverse" in line for line in lines)

    def test_shows_reverse_toggle_state_on(self) -> None:
        screen = make_screen(current_reverse=True)
        lines = screen.render_lines(80, 40)
        assert any("[x]" in line for line in lines)

    def test_focus_indicator_moves_with_tab(self) -> None:
        screen = make_screen()
        before = screen.render_lines(80, 40)
        assert before[0].startswith(">")
        screen.handle_key(ord("\t"))
        after = screen.render_lines(80, 40)
        assert after[0].startswith(" ")
        toggle_line_index = next(i for i, line in enumerate(after) if "Reverse" in line)
        assert after[toggle_line_index].startswith(">")

    def test_lines_clipped_to_width(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(6, 40)
        assert all(len(line) <= 6 for line in lines)

    def test_zero_or_negative_width_yields_no_lines(self) -> None:
        screen = make_screen()
        assert screen.render_lines(0, 40) == []
        assert screen.render_lines(-5, 40) == []

    def test_zero_height_yields_no_lines(self) -> None:
        screen = make_screen()
        assert screen.render_lines(80, 0) == []

    def test_height_truncates_line_count(self) -> None:
        screen = make_screen()
        full = screen.render_lines(80, 1000)
        truncated = screen.render_lines(80, 2)
        assert len(truncated) == 2
        assert truncated == full[:2]

    def test_all_lines_are_plain_strings(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)

    def test_single_available_key_still_renders(self) -> None:
        screen = SortScreen([("distance", "Distance")], "distance", False)
        lines = screen.render_lines(80, 40)
        assert any("Distance" in line for line in lines)

    def test_no_curses_window_or_input_needed(self) -> None:
        screen = make_screen()
        assert screen.render_lines(40, 10)
        assert screen.handle_key(ord("\t")) is None

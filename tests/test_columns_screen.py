"""Tests for adsbtui.ui.screens.columns.ColumnsScreen -- pure, no curses window required."""

from __future__ import annotations

import curses

import pytest

from adsbtui.ui.columns import COLUMN_SPECS, DEFAULT_COLUMNS
from adsbtui.ui.screens.columns import ColumnsScreen

ALL_KEYS = list(COLUMN_SPECS.keys())


def make_screen(
    current_columns: list[str] | None = None,
    current_density: str = "normal",
    current_borders: str = "unicode",
) -> ColumnsScreen:
    return ColumnsScreen(
        current_columns if current_columns is not None else DEFAULT_COLUMNS,
        current_density,
        current_borders,
    )


class TestConstruction:
    def test_title_is_columns(self) -> None:
        screen = make_screen()
        assert screen.title == "Columns"

    def test_starts_with_columns_picker_focused(self) -> None:
        screen = make_screen()
        assert screen.focus == 0

    def test_result_matches_constructor_args_before_any_key(self) -> None:
        screen = make_screen(
            current_columns=["flight", "alt"], current_density="wide", current_borders="ascii"
        )
        assert screen.result() == {
            "columns": ["flight", "alt"],
            "density": "wide",
            "borders": "ascii",
        }

    def test_unknown_density_and_borders_do_not_raise(self) -> None:
        screen = make_screen(current_density="bogus", current_borders="bogus")
        # Nothing applied yet: result() still echoes exactly what was passed in.
        assert screen.result()["density"] == "bogus"
        assert screen.result()["borders"] == "bogus"

    def test_unknown_column_keys_are_ignored_in_the_checklist(self) -> None:
        screen = make_screen(current_columns=["flight", "not_a_real_column"])
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert "not_a_real_column" not in text

    def test_every_known_column_appears_in_the_checklist(self) -> None:
        screen = make_screen(current_columns=["flight"])
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        for key in ALL_KEYS:
            assert COLUMN_SPECS[key].header in text

    def test_enabled_columns_start_checked(self) -> None:
        screen = make_screen(current_columns=["flight", "alt"])
        lines = screen.render_lines(80, 40)
        flight_line = next(line for line in lines if COLUMN_SPECS["flight"].header in line)
        alt_line = next(line for line in lines if COLUMN_SPECS["alt"].header in line)
        assert "[x]" in flight_line
        assert "[x]" in alt_line

    def test_not_yet_enabled_columns_start_unchecked(self) -> None:
        screen = make_screen(current_columns=["flight"])
        lines = screen.render_lines(80, 40)
        hex_line = next(line for line in lines if COLUMN_SPECS["hex"].header in line)
        assert "[ ]" in hex_line

    def test_enabled_columns_come_first_in_display_order(self) -> None:
        screen = make_screen(current_columns=["hex", "flight"])
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert text.index(COLUMN_SPECS["hex"].header) < text.index(COLUMN_SPECS["flight"].header)

    def test_duplicate_current_columns_not_duplicated_in_checklist(self) -> None:
        screen = make_screen(current_columns=["flight", "flight", "alt"])
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert text.count(COLUMN_SPECS["flight"].header) == 1


class TestFocusSwitching:
    def test_tab_moves_focus_to_density(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("\t")) is None
        assert screen.focus == 1

    def test_tab_moves_focus_through_all_three_and_wraps(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("\t"))
        assert screen.focus == 1
        screen.handle_key(ord("\t"))
        assert screen.focus == 2
        screen.handle_key(ord("\t"))
        assert screen.focus == 0

    def test_shift_tab_moves_focus_backward_and_wraps(self) -> None:
        screen = make_screen()
        assert screen.handle_key(curses.KEY_BTAB) is None
        assert screen.focus == 2

    def test_tab_never_closes(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("\t")) is None
        assert screen.handle_key(ord("\t")) is None
        assert screen.handle_key(ord("\t")) is None


class TestColumnPickerInteraction:
    def test_space_toggles_column_off(self) -> None:
        screen = make_screen(current_columns=["flight", "alt"])
        assert screen.handle_key(ord(" ")) is None
        lines = screen.render_lines(80, 40)
        flight_line = next(line for line in lines if COLUMN_SPECS["flight"].header in line)
        assert "[ ]" in flight_line

    def test_space_toggles_column_on(self) -> None:
        screen = make_screen(current_columns=["flight"])
        # Cursor starts on "flight" (index 0); move down once to reach the first
        # not-yet-enabled column and toggle it on.
        screen.handle_key(curses.KEY_DOWN)
        screen.handle_key(ord(" "))
        lines = screen.render_lines(80, 40)
        second_key = [k for k in ALL_KEYS if k != "flight"][0]
        toggled_line = next(line for line in lines if COLUMN_SPECS[second_key].header in line)
        assert "[x]" in toggled_line

    def test_plus_moves_item_up(self) -> None:
        screen = make_screen(current_columns=["flight", "alt", "gs"])
        screen.handle_key(curses.KEY_DOWN)  # cursor -> "alt"
        screen.handle_key(ord("+"))  # swap "alt" above "flight"
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert text.index(COLUMN_SPECS["alt"].header) < text.index(COLUMN_SPECS["flight"].header)

    def test_minus_moves_item_down(self) -> None:
        screen = make_screen(current_columns=["flight", "alt", "gs"])
        screen.handle_key(ord("-"))  # swap "flight" below "alt"
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert text.index(COLUMN_SPECS["alt"].header) < text.index(COLUMN_SPECS["flight"].header)

    def test_reorder_and_toggle_not_applied_until_enter(self) -> None:
        screen = make_screen(current_columns=["flight", "alt", "gs"])
        screen.handle_key(ord("-"))
        screen.handle_key(ord(" "))
        assert screen.result()["columns"] == ["flight", "alt", "gs"]

    def test_plus_minus_ignored_when_columns_not_focused(self) -> None:
        screen = make_screen(current_columns=["flight", "alt", "gs"])
        screen.handle_key(ord("\t"))  # focus -> density
        outcome = screen.handle_key(ord("+"))
        assert outcome is None
        # Order is unaffected since the picker never saw the key while unfocused.
        screen.handle_key(ord("\t"))
        screen.handle_key(ord("\t"))
        outcome2 = screen.handle_key(curses.KEY_ENTER)
        assert outcome2 == "close"
        assert screen.result()["columns"] == ["flight", "alt", "gs"]


class TestDensityAndBordersInteraction:
    def test_right_arrow_cycles_density_when_focused(self) -> None:
        screen = make_screen(current_density="compact")
        screen.handle_key(ord("\t"))  # focus -> density
        screen.handle_key(curses.KEY_RIGHT)
        lines = screen.render_lines(80, 40)
        density_line = next(line for line in lines if "Density:" in line)
        assert "Normal" in density_line

    def test_borders_select_cycles_when_focused(self) -> None:
        screen = make_screen(current_borders="unicode")
        screen.handle_key(ord("\t"))
        screen.handle_key(ord("\t"))  # focus -> borders
        screen.handle_key(curses.KEY_RIGHT)
        lines = screen.render_lines(80, 40)
        borders_line = next(line for line in lines if "Borders:" in line)
        assert "ASCII" in borders_line

    def test_density_change_not_applied_until_enter(self) -> None:
        screen = make_screen(current_density="compact")
        screen.handle_key(ord("\t"))
        screen.handle_key(curses.KEY_RIGHT)
        assert screen.result()["density"] == "compact"


class TestApplyAndCancel:
    def test_enter_applies_and_closes(self) -> None:
        screen = make_screen(
            current_columns=["flight", "alt"], current_density="compact", current_borders="ascii"
        )
        screen.handle_key(ord(" "))  # uncheck "flight"
        screen.handle_key(ord("\t"))
        screen.handle_key(curses.KEY_RIGHT)  # density -> normal
        screen.handle_key(ord("\t"))
        screen.handle_key(curses.KEY_RIGHT)  # borders -> none
        outcome = screen.handle_key(curses.KEY_ENTER)
        assert outcome == "close"
        result = screen.result()
        assert "flight" not in result["columns"]
        assert result["density"] == "normal"
        assert result["borders"] == "none"

    @pytest.mark.parametrize("enter_key", [10, 13, curses.KEY_ENTER, curses.KEY_F10])
    def test_all_apply_key_variants_work(self, enter_key: int) -> None:
        screen = make_screen(current_columns=["flight", "alt"])
        screen.handle_key(ord(" "))  # uncheck "flight"
        outcome = screen.handle_key(enter_key)
        assert outcome == "close"
        assert "flight" not in screen.result()["columns"]

    def test_applied_columns_reflect_selection_order(self) -> None:
        screen = make_screen(current_columns=["flight", "alt", "gs"])
        screen.handle_key(ord("-"))  # "flight" and "alt" swap -> alt, flight, gs
        outcome = screen.handle_key(curses.KEY_ENTER)
        assert outcome == "close"
        assert screen.result()["columns"] == ["alt", "flight", "gs"]

    def test_escape_closes_without_applying_toggle(self) -> None:
        screen = make_screen(current_columns=["flight", "alt"])
        screen.handle_key(ord(" "))  # would-be uncheck of "flight"
        outcome = screen.handle_key(27)
        assert outcome == "close"
        assert screen.result()["columns"] == ["flight", "alt"]

    def test_escape_closes_without_applying_density_or_borders(self) -> None:
        screen = make_screen(current_density="compact", current_borders="ascii")
        screen.handle_key(ord("\t"))
        screen.handle_key(curses.KEY_RIGHT)
        screen.handle_key(ord("\t"))
        screen.handle_key(curses.KEY_RIGHT)
        outcome = screen.handle_key(27)
        assert outcome == "close"
        result = screen.result()
        assert result["density"] == "compact"
        assert result["borders"] == "ascii"

    def test_escape_from_any_focus_cancels(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("\t"))
        screen.handle_key(ord("\t"))
        outcome = screen.handle_key(27)
        assert outcome == "close"


class TestRenderLines:
    def test_focus_indicator_moves_with_tab(self) -> None:
        screen = make_screen()
        before = screen.render_lines(80, 40)
        assert before[0].startswith(">")
        screen.handle_key(ord("\t"))
        after = screen.render_lines(80, 40)
        assert after[0].startswith(" ")
        density_line_index = next(i for i, line in enumerate(after) if "Density:" in line)
        assert after[density_line_index].startswith(">")

    def test_lines_clipped_to_width(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(10, 40)
        assert all(len(line) <= 10 for line in lines)

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
        truncated = screen.render_lines(80, 3)
        assert len(truncated) == 3
        assert truncated == full[:3]

    def test_all_lines_are_plain_strings(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)

    def test_shows_density_and_borders_values(self) -> None:
        screen = make_screen(current_density="wide", current_borders="none")
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert "Wide" in text
        assert "None" in text

    def test_shows_hint_line(self) -> None:
        screen = make_screen()
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)
        assert "apply" in text.lower()
        assert "cancel" in text.lower()

    def test_no_curses_window_or_input_needed(self) -> None:
        screen = make_screen()
        assert screen.render_lines(60, 30)
        assert screen.handle_key(ord("\t")) is None

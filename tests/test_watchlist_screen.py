"""Tests for adsbtui.ui.screens.watchlist.WatchlistScreen -- pure, no curses window
required.

Exercises handle_key() state transitions (list mode vs. add-form mode, add/delete/toggle/
quick-add actions, close keys) and render_lines() content only, matching the
pure-testability contract documented in ui/screens/__init__.py and
ui/screens/watchlist.py.
"""

from __future__ import annotations

import curses

import pytest

from adsbtui.model import Aircraft
from adsbtui.ui.screens.watchlist import WatchlistScreen
from adsbtui.watchlist import WatchEntry


def make_entries() -> list[WatchEntry]:
    return [
        WatchEntry(prefix="hex", pattern="A1B2C3", raw_line="hex:A1B2C3"),
        WatchEntry(prefix="reg", pattern="N123*", raw_line="reg:N123*", enabled=False),
        WatchEntry(prefix="call", pattern="UAL*", raw_line="call:UAL*"),
    ]


def make_screen(
    entries: list[WatchEntry] | None = None,
    selected_aircraft: Aircraft | None = None,
) -> WatchlistScreen:
    return WatchlistScreen(
        entries if entries is not None else make_entries(),
        selected_aircraft=selected_aircraft,
    )


def type_text(screen: WatchlistScreen, text: str) -> None:
    for ch in text:
        screen.handle_key(ord(ch))


class TestTitleAndConstruction:
    def test_title_is_watchlist(self) -> None:
        assert make_screen().title == "Watchlist"

    def test_starts_in_list_mode(self) -> None:
        assert make_screen().mode == "list"

    def test_result_starts_as_copy_of_constructor_entries(self) -> None:
        entries = make_entries()
        screen = make_screen(entries)
        assert screen.result() == entries
        assert screen.result() is not entries

    def test_mutating_screen_does_not_mutate_caller_original_list(self) -> None:
        entries = make_entries()
        screen = make_screen(entries)
        screen.handle_key(ord("d"))
        assert len(entries) == 3
        assert len(screen.result()) == 2


class TestCloseKeys:
    def test_escape_closes_from_list_mode(self) -> None:
        assert make_screen().handle_key(27) == "close"

    def test_f10_closes_from_list_mode(self) -> None:
        assert make_screen().handle_key(curses.KEY_F10) == "close"

    def test_escape_closes_from_add_form_mode(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        assert screen.mode == "add_form"
        assert screen.handle_key(27) == "close"

    def test_f10_closes_from_add_form_mode(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        assert screen.handle_key(curses.KEY_F10) == "close"

    def test_other_keys_leave_screen_open(self) -> None:
        screen = make_screen()
        assert screen.handle_key(curses.KEY_DOWN) is None
        assert screen.handle_key(ord("e")) is None


class TestAddFormFocus:
    def test_letter_a_enters_add_form_mode(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ord("a")) is None
        assert screen.mode == "add_form"

    def test_uppercase_a_also_enters_add_form_mode(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("A"))
        assert screen.mode == "add_form"

    def test_tab_moves_focus_between_prefix_and_pattern(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        lines_before = screen.render_lines(80, 40)
        prefix_line_before = next(line for line in lines_before if "Prefix" in line)
        assert prefix_line_before.lstrip().startswith(">")

        screen.handle_key(ord("\t"))
        lines_after = screen.render_lines(80, 40)
        prefix_line_after = next(line for line in lines_after if "Prefix" in line)
        pattern_line_after = next(line for line in lines_after if "Pattern" in line)
        assert not prefix_line_after.startswith(">")
        assert pattern_line_after.startswith(">")

    def test_shift_tab_moves_focus_backward(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        screen.handle_key(curses.KEY_BTAB)
        lines = screen.render_lines(80, 40)
        pattern_line = next(line for line in lines if "Pattern" in line)
        assert pattern_line.lstrip().startswith(">")

    def test_typing_letters_that_are_normally_shortcuts_edits_pattern_field(self) -> None:
        """While the add-form has focus, a/d/e/w must type into the focused field
        instead of triggering the list-mode single-key actions."""
        screen = make_screen()
        screen.handle_key(ord("a"))  # enter add-form mode (Prefix focused)
        screen.handle_key(ord("\t"))  # move focus to Pattern
        type_text(screen, "dawe")
        lines = screen.render_lines(80, 40)
        pattern_line = next(line for line in lines if "Pattern" in line)
        assert "dawe" in pattern_line
        # and no entries were deleted/added as a side effect
        assert len(screen.result()) == 3


class TestAddingEntries:
    def test_enter_commits_a_new_entry_and_returns_to_list_mode(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        screen.handle_key(ord("\t"))  # Prefix (default "hex") -> Pattern
        type_text(screen, "DEADBE")
        screen.handle_key(curses.KEY_ENTER)
        assert screen.mode == "list"
        result = screen.result()
        assert len(result) == 4
        assert result[-1] == WatchEntry(prefix="hex", pattern="DEADBE", raw_line="hex:DEADBE")

    def test_committed_entry_uses_selected_prefix(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        screen.handle_key(curses.KEY_RIGHT)  # Prefix: hex -> reg
        screen.handle_key(ord("\t"))
        type_text(screen, "N999XX")
        screen.handle_key(10)
        new_entry = screen.result()[-1]
        assert new_entry.prefix == "reg"
        assert new_entry.pattern == "N999XX"

    def test_pattern_field_is_cleared_after_a_successful_commit(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        screen.handle_key(ord("\t"))
        type_text(screen, "ABC123")
        screen.handle_key(10)
        lines = screen.render_lines(80, 40)
        pattern_line = next(line for line in lines if "Pattern" in line)
        assert "ABC123" not in pattern_line

    def test_enter_with_empty_pattern_does_not_add_an_entry_and_stays_open(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        screen.handle_key(ord("\t"))  # focus Pattern, leave it empty
        assert screen.handle_key(10) is None
        assert screen.mode == "add_form"
        assert len(screen.result()) == 3

    @pytest.mark.parametrize("key", [10, 13, curses.KEY_ENTER])
    def test_all_enter_key_variants_commit(self, key: int) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        screen.handle_key(ord("\t"))
        type_text(screen, "X")
        screen.handle_key(key)
        assert len(screen.result()) == 4


class TestDeleting:
    def test_d_deletes_the_currently_selected_entry(self) -> None:
        screen = make_screen()
        original = make_entries()
        screen.handle_key(ord("d"))
        result = screen.result()
        assert len(result) == 2
        assert result == [original[1], original[2]]

    def test_uppercase_d_also_deletes(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("D"))
        assert len(screen.result()) == 2

    def test_delete_key_constant_also_deletes(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_DC)
        assert len(screen.result()) == 2

    def test_delete_moves_cursor_down_first_then_deletes_that_entry(self) -> None:
        screen = make_screen()
        original = make_entries()
        screen.handle_key(curses.KEY_DOWN)  # cursor -> index 1
        screen.handle_key(ord("d"))
        result = screen.result()
        assert result == [original[0], original[2]]

    def test_deleting_from_empty_list_does_not_raise(self) -> None:
        screen = make_screen(entries=[])
        screen.handle_key(ord("d"))
        assert screen.result() == []

    def test_deleting_last_entry_repeatedly_empties_the_list(self) -> None:
        screen = make_screen()
        for _ in range(3):
            screen.handle_key(ord("d"))
        assert screen.result() == []
        # one more delete on an empty list must still be a no-op, not a crash
        screen.handle_key(ord("d"))
        assert screen.result() == []


class TestToggleEnabled:
    def test_e_toggles_enabled_flag_of_selected_entry(self) -> None:
        screen = make_screen()
        entries = screen.result()
        assert entries[0].enabled is True
        screen.handle_key(ord("e"))
        assert screen.result()[0].enabled is False
        screen.handle_key(ord("e"))
        assert screen.result()[0].enabled is True

    def test_uppercase_e_also_toggles(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("E"))
        assert screen.result()[0].enabled is False

    def test_toggle_follows_the_cursor(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_DOWN)  # cursor -> index 1 (starts disabled)
        screen.handle_key(ord("e"))
        result = screen.result()
        assert result[0].enabled is True
        assert result[1].enabled is True

    def test_toggle_on_empty_list_does_not_raise(self) -> None:
        screen = make_screen(entries=[])
        screen.handle_key(ord("e"))
        assert screen.result() == []


class TestQuickAdd:
    def test_w_is_ignored_when_no_aircraft_selected(self) -> None:
        screen = make_screen(selected_aircraft=None)
        screen.handle_key(ord("w"))
        assert len(screen.result()) == 3

    def test_w_quick_adds_hex_entry_when_aircraft_selected(self) -> None:
        aircraft = Aircraft(hex="a1b2c3", is_icao=True)
        screen = make_screen(selected_aircraft=aircraft)
        screen.handle_key(ord("w"))
        result = screen.result()
        assert len(result) == 4
        assert result[-1].prefix == "hex"
        assert result[-1].pattern == "a1b2c3"
        assert result[-1].enabled is True

    def test_uppercase_w_also_quick_adds(self) -> None:
        aircraft = Aircraft(hex="deadbe", is_icao=True)
        screen = make_screen(selected_aircraft=aircraft)
        screen.handle_key(ord("W"))
        assert screen.result()[-1].pattern == "deadbe"

    def test_w_footer_hint_only_shown_when_aircraft_selected(self) -> None:
        with_aircraft = make_screen(selected_aircraft=Aircraft(hex="abcdef", is_icao=True))
        without_aircraft = make_screen(selected_aircraft=None)
        text_with = "\n".join(with_aircraft.render_lines(80, 40))
        text_without = "\n".join(without_aircraft.render_lines(80, 40))
        assert "quick-add" in text_with
        assert "quick-add" not in text_without


class TestRenderLines:
    def test_title_appears_first(self) -> None:
        lines = make_screen().render_lines(80, 40)
        assert lines[0] == "Watchlist"

    def test_entry_lines_show_pattern_text(self) -> None:
        text = "\n".join(make_screen().render_lines(80, 40))
        assert "hex:A1B2C3" in text
        assert "reg:N123*" in text
        assert "call:UAL*" in text

    def test_enabled_and_disabled_entries_are_marked_differently(self) -> None:
        lines = make_screen().render_lines(80, 40)
        hex_line = next(line for line in lines if "hex:A1B2C3" in line)
        reg_line = next(line for line in lines if "reg:N123*" in line)
        assert "[x]" in hex_line
        assert "[ ]" in reg_line

    def test_entry_count_shown_in_header(self) -> None:
        text = "\n".join(make_screen().render_lines(80, 40))
        assert "Entries (3)" in text

    def test_empty_list_shows_zero_count_and_no_crash(self) -> None:
        lines = make_screen(entries=[]).render_lines(80, 40)
        text = "\n".join(lines)
        assert "Entries (0)" in text

    def test_add_form_labels_present(self) -> None:
        text = "\n".join(make_screen().render_lines(80, 40))
        assert "Prefix" in text
        assert "Pattern" in text

    def test_list_focus_marker_shown_in_list_mode(self) -> None:
        lines = make_screen().render_lines(80, 40)
        entries_header = next(line for line in lines if "Entries" in line)
        add_header = next(line for line in lines if "Add new entry" in line)
        assert entries_header.startswith(">")
        assert add_header.startswith(" ")

    def test_add_form_focus_marker_shown_in_add_form_mode(self) -> None:
        screen = make_screen()
        screen.handle_key(ord("a"))
        lines = screen.render_lines(80, 40)
        entries_header = next(line for line in lines if "Entries" in line)
        add_header = next(line for line in lines if "Add new entry" in line)
        assert entries_header.startswith(" ")
        assert add_header.startswith(">")

    def test_footer_hints_present(self) -> None:
        text = "\n".join(make_screen().render_lines(80, 40))
        assert "add" in text
        assert "delete" in text
        assert "Esc" in text

    def test_respects_width_clipping(self) -> None:
        lines = make_screen().render_lines(20, 40)
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

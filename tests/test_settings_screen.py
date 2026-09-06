"""Tests for adsbtui.ui.screens.settings.SettingsScreen -- pure, no curses window needed.

Exercises tab/field navigation, the apply / apply-and-save / cancel outcomes, the inline
validation error path, and the promise that the Config handed to the constructor is never
mutated. Only handle_key(), render_lines() and result() are touched, matching the
pure-testability contract in ui/screens/__init__.py.
"""

from __future__ import annotations

import curses
from dataclasses import fields

import pytest

from adsbtui.config import Config
from adsbtui.ui.screens.settings import _LABELS_AND_HELP, SettingsScreen

ESC = 27
ENTER = 10
TAB = ord("\t")
BACKSPACE = 127
CTRL_S = 19

SECTION_TABS = [
    "source",
    "home",
    "filter",
    "display",
    "alerts",
    "watchlist",
    "registry",
    "history",
    "logging",
]


def make_config() -> Config:
    cfg = Config()
    cfg.source.url = "http://192.168.1.50/skyaware/data/aircraft.json"
    cfg.home.lat = 37.7749
    cfg.home.lon = -122.4194
    return cfg


def make_screen(cfg: Config | None = None) -> SettingsScreen:
    return SettingsScreen(cfg if cfg is not None else make_config())


def goto_tab(screen: SettingsScreen, section: str) -> None:
    for _ in range(SECTION_TABS.index(section)):
        screen.handle_key(TAB)


def goto_field(screen: SettingsScreen, index: int) -> None:
    for _ in range(index):
        screen.handle_key(curses.KEY_DOWN)


def retype(screen: SettingsScreen, old_length: int, new_text: str) -> None:
    """Clear the focused text/number field and type new_text into it."""
    for _ in range(old_length):
        screen.handle_key(BACKSPACE)
    for ch in new_text:
        screen.handle_key(ord(ch))


class TestTitleAndCoverage:
    def test_title_is_settings(self) -> None:
        assert make_screen().title == "Settings"

    def test_every_config_key_in_every_section_is_editable(self) -> None:
        screen = make_screen()
        defaults = Config()
        for section_name in SECTION_TABS:
            section = getattr(defaults, section_name)
            for value_field in fields(section):
                dotted = f"{section_name}.{value_field.name}"
                assert dotted in screen._widgets, f"{dotted} has no widget"

    def test_every_config_key_has_a_label_and_help_string(self) -> None:
        defaults = Config()
        for section_name in SECTION_TABS:
            section = getattr(defaults, section_name)
            for value_field in fields(section):
                dotted = f"{section_name}.{value_field.name}"
                label, help_text = _LABELS_AND_HELP[dotted]
                assert label
                assert help_text


class TestTabs:
    def test_starts_on_the_first_section(self) -> None:
        screen = make_screen()
        assert screen.active_tab == 0
        assert screen.active_section == "source"

    def test_tab_moves_to_the_next_section(self) -> None:
        screen = make_screen()
        screen.handle_key(TAB)
        assert screen.active_section == "home"

    def test_tab_wraps_around_the_end(self) -> None:
        screen = make_screen()
        for _ in range(len(SECTION_TABS)):
            screen.handle_key(TAB)
        assert screen.active_section == "source"

    def test_shift_tab_moves_back(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_BTAB)
        assert screen.active_section == "logging"

    def test_switching_tabs_does_not_close_the_screen(self) -> None:
        screen = make_screen()
        assert screen.handle_key(TAB) is None


class TestFieldNavigation:
    def test_down_moves_between_fields_within_a_section(self) -> None:
        screen = make_screen()
        assert screen.focus_index == 0
        screen.handle_key(curses.KEY_DOWN)
        assert screen.focus_index == 1

    def test_up_moves_back_between_fields(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_DOWN)
        screen.handle_key(curses.KEY_UP)
        assert screen.focus_index == 0

    def test_each_section_keeps_its_own_field_focus(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_DOWN)
        screen.handle_key(TAB)
        assert screen.focus_index == 0


class TestApply:
    def test_enter_applies_and_closes(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ENTER) == "close"
        assert screen.applied is True
        assert screen.save_requested is False

    def test_f10_also_applies(self) -> None:
        screen = make_screen()
        assert screen.handle_key(curses.KEY_F10) == "close"
        assert screen.applied is True

    def test_editing_a_number_field_changes_the_returned_config(self) -> None:
        screen = make_screen()
        goto_tab(screen, "filter")
        retype(screen, len("15.0"), "25")
        screen.handle_key(ENTER)
        assert screen.applied is True
        assert screen.result().filter.radius == 25.0

    def test_editing_a_text_field_changes_the_returned_config(self) -> None:
        screen = make_screen()
        goto_tab(screen, "registry")
        retype(screen, 0, "/home/pi/MASTER.csv")
        screen.handle_key(ENTER)
        assert screen.result().registry.path == "/home/pi/MASTER.csv"

    def test_toggling_a_bool_changes_the_returned_config(self) -> None:
        screen = make_screen()
        goto_tab(screen, "filter")
        goto_field(screen, 2)  # hide_ground
        screen.handle_key(ord(" "))
        screen.handle_key(ENTER)
        assert screen.result().filter.hide_ground is True

    def test_cycling_a_select_changes_the_returned_config(self) -> None:
        screen = make_screen()
        goto_tab(screen, "display")
        screen.handle_key(ord(" "))  # units: imperial -> metric
        screen.handle_key(ENTER)
        assert screen.result().display.units == "metric"

    def test_editing_a_comma_separated_list_changes_the_returned_config(self) -> None:
        screen = make_screen()
        goto_tab(screen, "display")
        goto_field(screen, 3)  # columns
        retype(screen, len(", ".join(Config().display.columns)), "flight, reg, dist")
        screen.handle_key(ENTER)
        assert screen.result().display.columns == ["flight", "reg", "dist"]

    def test_editing_a_list_of_ints_changes_the_returned_config(self) -> None:
        screen = make_screen()
        goto_tab(screen, "alerts")
        goto_field(screen, 1)  # squawks
        retype(screen, len("7500, 7600, 7700"), "7700")
        screen.handle_key(ENTER)
        assert screen.result().alerts.squawks == [7700]

    def test_result_is_a_new_config_and_the_original_is_untouched(self) -> None:
        cfg = make_config()
        screen = SettingsScreen(cfg)
        goto_tab(screen, "filter")
        retype(screen, len("15.0"), "25")
        screen.handle_key(ENTER)
        result = screen.result()
        assert result is not cfg
        assert result.filter is not cfg.filter
        assert cfg.filter.radius == 15.0

    def test_result_carries_the_config_path_across_for_the_caller_to_save_to(self) -> None:
        cfg = make_config()
        cfg._config_path = "/home/pi/.config/adsbtui/config.toml"
        screen = SettingsScreen(cfg)
        screen.handle_key(ENTER)
        assert screen.result()._config_path == "/home/pi/.config/adsbtui/config.toml"


class TestCancel:
    def test_escape_closes_without_applying(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ESC) == "close"
        assert screen.applied is False
        assert screen.save_requested is False

    def test_escape_leaves_the_result_untouched_after_an_edit(self) -> None:
        cfg = make_config()
        screen = SettingsScreen(cfg)
        goto_tab(screen, "filter")
        retype(screen, len("15.0"), "99")
        screen.handle_key(ESC)
        assert screen.result().filter.radius == 15.0
        assert screen.result() == cfg

    def test_escape_does_not_mutate_the_config_it_was_given(self) -> None:
        cfg = make_config()
        screen = SettingsScreen(cfg)
        goto_tab(screen, "display")
        screen.handle_key(ord(" "))
        screen.handle_key(ESC)
        assert cfg.display.units == "imperial"


class TestSave:
    def test_f2_applies_and_asks_the_caller_to_save(self) -> None:
        screen = make_screen()
        assert screen.handle_key(curses.KEY_F2) == "close-and-save"
        assert screen.applied is True
        assert screen.save_requested is True

    def test_ctrl_s_also_saves(self) -> None:
        screen = make_screen()
        assert screen.handle_key(CTRL_S) == "close-and-save"
        assert screen.save_requested is True

    def test_saving_carries_the_edits(self) -> None:
        screen = make_screen()
        goto_tab(screen, "filter")
        retype(screen, len("15.0"), "40")
        screen.handle_key(curses.KEY_F2)
        assert screen.result().filter.radius == 40.0

    def test_invalid_input_does_not_ask_the_caller_to_save(self) -> None:
        screen = make_screen()
        goto_tab(screen, "home")
        retype(screen, len("37.7749"), "200")
        assert screen.handle_key(curses.KEY_F2) is None
        assert screen.save_requested is False


class TestValidationErrors:
    def test_out_of_range_latitude_keeps_the_screen_open(self) -> None:
        screen = make_screen()
        goto_tab(screen, "home")
        retype(screen, len("37.7749"), "200")
        assert screen.handle_key(ENTER) is None
        assert screen.applied is False

    def test_out_of_range_latitude_shows_the_error_inline(self) -> None:
        screen = make_screen()
        goto_tab(screen, "home")
        retype(screen, len("37.7749"), "200")
        screen.handle_key(ENTER)
        assert screen.error is not None
        assert "home.lat" in screen.error
        rendered = "\n".join(screen.render_lines(100, 30))
        assert "home.lat" in rendered

    def test_invalid_input_leaves_the_previous_result_in_place(self) -> None:
        screen = make_screen()
        goto_tab(screen, "home")
        retype(screen, len("37.7749"), "200")
        screen.handle_key(ENTER)
        assert screen.result().home.lat == 37.7749

    def test_empty_source_url_is_rejected(self) -> None:
        screen = make_screen()
        retype(screen, len("http://192.168.1.50/skyaware/data/aircraft.json"), "")
        assert screen.handle_key(ENTER) is None
        assert screen.error is not None
        assert "source.url" in screen.error

    def test_unparseable_int_list_is_reported_not_raised(self) -> None:
        screen = make_screen()
        goto_tab(screen, "alerts")
        goto_field(screen, 1)  # squawks
        retype(screen, len("7500, 7600, 7700"), "not-a-squawk")
        assert screen.handle_key(ENTER) is None
        assert screen.error is not None
        assert "alerts.squawks" in screen.error

    def test_a_fixed_error_can_then_be_applied(self) -> None:
        screen = make_screen()
        goto_tab(screen, "home")
        retype(screen, len("37.7749"), "200")
        screen.handle_key(ENTER)
        retype(screen, len("200"), "45.5")
        assert screen.handle_key(ENTER) == "close"
        assert screen.error is None
        assert screen.result().home.lat == 45.5


class TestRendering:
    def test_render_lines_is_empty_for_a_zero_sized_area(self) -> None:
        assert make_screen().render_lines(0, 10) == []
        assert make_screen().render_lines(80, 0) == []

    def test_every_line_is_clipped_to_width(self) -> None:
        lines = make_screen().render_lines(40, 20)
        assert lines
        assert all(len(line) <= 40 for line in lines)

    def test_never_returns_more_lines_than_height(self) -> None:
        for height in (1, 3, 8, 20, 60):
            assert len(make_screen().render_lines(80, height)) <= height

    def test_shows_the_tab_row_with_the_active_section_marked(self) -> None:
        screen = make_screen()
        assert "[Source]" in screen.render_lines(120, 30)[0]
        screen.handle_key(TAB)
        assert "[Home]" in screen.render_lines(120, 30)[0]

    def test_shows_the_current_value_of_a_field(self) -> None:
        rendered = "\n".join(make_screen().render_lines(120, 30))
        assert "http://192.168.1.50/skyaware/data/aircraft.json" in rendered

    def test_shows_the_help_string_for_a_field(self) -> None:
        rendered = "\n".join(make_screen().render_lines(120, 40))
        assert _LABELS_AND_HELP["source.refresh_s"][1] in rendered

    def test_scrolls_to_keep_the_focused_field_visible(self) -> None:
        screen = make_screen()
        goto_tab(screen, "alerts")
        goto_field(screen, 14)  # quiet_hours, the last field of the longest section
        rendered = "\n".join(screen.render_lines(120, 14))
        assert "Quiet hours" in rendered

    def test_footer_names_the_apply_save_and_cancel_keys(self) -> None:
        footer = make_screen().render_lines(120, 30)[-1]
        assert "Enter" in footer
        assert "F2" in footer
        assert "Esc" in footer


class TestUnhandledKeys:
    @pytest.mark.parametrize("key", [curses.KEY_F5, curses.KEY_MOUSE, curses.KEY_RESIZE])
    def test_unrelated_keys_are_swallowed_without_closing(self, key: int) -> None:
        screen = make_screen()
        assert screen.handle_key(key) is None
        assert screen.applied is False

"""Tests for adsbtui.ui.bars: pure single-line string builders for the title/key/status bars."""

from __future__ import annotations

from adsbtui.ui.bars import key_bar_text, status_line_text, title_bar_text

KEYMAP = {
    "help": "F1",
    "settings": "F2",
    "sort": "F3",
    "filter": "F4",
    "watchlist": "F5",
    "pause": "F6",
    "search": "F7",
    "columns": "F8",
    "units": "F9",
    "quit": "F10",
}


class TestTitleBarText:
    def test_never_exceeds_width(self):
        for width in range(0, 80, 3):
            text = title_bar_text(
                width, version="0.9.0", paused=False, units="imperial", config_path="/x/y.toml"
            )
            assert len(text) <= width

    def test_zero_width_is_empty(self):
        assert title_bar_text(0, "0.9.0", False, "imperial", "/x.toml") == ""

    def test_contains_version_when_room_allows(self):
        text = title_bar_text(200, "0.9.0", False, "imperial", "/x.toml")
        assert "0.9.0" in text
        assert "imperial" in text

    def test_paused_flag_shown_when_room_allows(self):
        text = title_bar_text(200, "0.9.0", True, "imperial", "/x.toml")
        assert "PAUSED" in text

    def test_paused_flag_absent_when_not_paused(self):
        text = title_bar_text(200, "0.9.0", False, "imperial", "/x.toml")
        assert "PAUSED" not in text

    def test_missing_config_path_does_not_raise(self):
        text = title_bar_text(200, "0.9.0", False, "imperial", None)
        assert len(text) <= 200


class TestKeyBarText:
    def test_never_exceeds_width(self):
        for width in range(0, 120, 4):
            text = key_bar_text(width, KEYMAP)
            assert len(text) <= width

    def test_zero_width_is_empty(self):
        assert key_bar_text(0, KEYMAP) == ""

    def test_all_entries_present_when_room_is_generous(self):
        text = key_bar_text(1000, KEYMAP)
        for action, key in KEYMAP.items():
            label = action.title()
            assert key in text
            assert label in text

    def test_drops_trailing_entries_that_do_not_fit(self):
        # Wide enough for the first entry ("F1 Help") but not the second.
        text = key_bar_text(len("F1 Help"), KEYMAP)
        assert text == "F1 Help"

    def test_never_truncates_mid_entry(self):
        # A width one short of fitting a second entry must not produce a partial entry.
        first = "F1 Help"
        second = f"{first}  F2 Settings"
        text = key_bar_text(len(second) - 1, KEYMAP)
        assert text == first

    def test_empty_keymap_is_empty(self):
        assert key_bar_text(80, {}) == ""

    def test_too_narrow_for_even_one_entry_is_empty(self):
        text = key_bar_text(3, KEYMAP)
        assert text == ""


class TestStatusLineText:
    def test_never_exceeds_width(self):
        for width in range(0, 100, 3):
            text = status_line_text(
                width,
                source_ok=True,
                source_message=None,
                last_success_age_s=2.0,
                aircraft_count=41,
                alert_count=2,
                msg_rate=120.0,
            )
            assert len(text) <= width

    def test_zero_width_is_empty(self):
        text = status_line_text(0, True, None, 2.0, 41, 2, 120.0)
        assert text == ""

    def test_shows_ok_and_counts_when_room_allows(self):
        text = status_line_text(200, True, None, 2.0, 41, 2, 120.0)
        assert "OK" in text
        assert "41 aircraft" in text
        assert "2 alerts" in text
        assert "120 msg/s" in text

    def test_shows_fail_and_message_when_room_allows(self):
        text = status_line_text(200, False, "connection refused", None, 0, 0, None)
        assert "FAIL" in text
        assert "connection refused" in text

    def test_none_msg_rate_does_not_raise(self):
        text = status_line_text(200, True, None, None, 0, 0, None)
        assert "N/A" in text
        assert len(text) <= 200

    def test_none_last_success_age_does_not_raise(self):
        text = status_line_text(200, True, None, None, 5, 0, 10.0)
        assert len(text) <= 200

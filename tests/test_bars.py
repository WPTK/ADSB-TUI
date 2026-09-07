"""Tests for adsbtui.ui.bars: pure single-line string builders for the key/status bars."""

from __future__ import annotations

from adsbtui.ui.bars import key_bar_text, status_line_text

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
        # Entries are separated by four spaces, so the second entry's true cost is
        # len("    F2 Settings") -- measuring it with a narrower gap would size the
        # terminal wide enough for both and stop testing anything.
        first = "F1 Help"
        second = f"{first}    F2 Settings"
        text = key_bar_text(len(second) - 1, KEYMAP)
        assert text == first

    def test_entries_are_separated_by_a_wide_gap(self):
        # The gap between pairs must be wider than the single space inside a pair,
        # otherwise "F1 Help F2 Settings" reads as one run-on string.
        text = key_bar_text(1000, KEYMAP)
        assert "F1 Help    F2 Settings" in text

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

    def test_shows_live_and_counts_when_room_allows(self):
        text = status_line_text(200, True, None, 2.0, 41, 2, 120.0)
        assert "LIVE 2s" in text
        assert "41 aircraft" in text
        assert "2 alerts" in text
        assert "120 msg/s" in text

    def test_shows_no_data_and_message_when_room_allows(self):
        text = status_line_text(200, False, "connection refused", None, 0, 0, None)
        assert "NO DATA (connection refused)" in text

    def test_multi_line_source_message_is_reduced_to_its_first_line(self):
        # A probe error with a traceback in it would otherwise wreck the single-line layout.
        text = status_line_text(200, False, "connection refused\nsecond line", None, 0, 0, None)
        assert "NO DATA (connection refused)" in text
        assert "second line" not in text

    def test_singular_alert_is_not_pluralized(self):
        assert "1 alert" in status_line_text(200, True, None, 2.0, 3, 1, None)
        assert "1 alerts" not in status_line_text(200, True, None, 2.0, 3, 1, None)

    def test_zero_alerts_are_omitted_entirely(self):
        # Nothing to report is quieter than "0 alerts" taking up a slot on a glanced-at line.
        text = status_line_text(200, True, None, 2.0, 41, 0, 120.0)
        assert "41 aircraft" in text
        assert "alert" not in text

    def test_none_msg_rate_is_omitted_rather_than_filled_in(self):
        text = status_line_text(200, True, None, None, 0, 0, None)
        assert "msg/s" not in text
        assert "N/A" not in text
        assert len(text) <= 200

    def test_none_last_success_age_does_not_raise(self):
        text = status_line_text(200, True, None, None, 5, 0, 10.0)
        assert "LIVE" in text
        assert "N/A" not in text
        assert len(text) <= 200

    def test_paused_is_flagged_ahead_of_the_link_state(self):
        text = status_line_text(200, True, None, 2.0, 41, 0, None, paused=True)
        assert text.startswith("PAUSED")
        assert "LIVE" in text

    def test_paused_flag_absent_when_not_paused(self):
        text = status_line_text(200, True, None, 2.0, 41, 0, None, paused=False)
        assert "PAUSED" not in text

    def test_clock_and_note_are_appended_when_supplied(self):
        text = status_line_text(
            200, True, None, 2.0, 41, 0, 120.0, clock="21:04:11", note="registry 45%"
        )
        assert "registry 45%" in text
        assert text.endswith("21:04:11")

    def test_clock_and_note_are_omitted_when_absent(self):
        text = status_line_text(200, True, None, 2.0, 41, 0, 120.0)
        assert text.endswith("120 msg/s")

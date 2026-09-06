"""Tests for adsbtui.ui.screens.setup.SetupScreen -- pure, no curses, no network.

Both side-effecting lookups the wizard performs (probing aircraft.json candidates and
reading a receiver.json) are injected as plain callables here, so nothing in this file
opens a socket or touches /run. The pure helpers (candidate_sources, receiver_json_for)
are tested directly.
"""

from __future__ import annotations

import curses

import pytest

from adsbtui.config import validate
from adsbtui.ui.screens.setup import (
    STEP_HOME,
    STEP_RECEIVER,
    STEP_SUMMARY,
    STEP_UNITS,
    ProbeResult,
    SetupScreen,
    candidate_sources,
    receiver_json_for,
)

ESC = 27
ENTER = 10
TAB = ord("\t")
F5 = curses.KEY_F5

HOST = "192.168.1.50"
#: The candidate probe_sources() would try for HOST, in order; index 2 is the "hit" used
#: throughout these tests (dump1090-fa's own alias).
CANDIDATES = candidate_sources(HOST)
HIT = CANDIDATES[2]


def fake_prober(hit_index: int | None = 2, count: int = 17):
    """A prober that reports every candidate as dead except hit_index."""

    def prober(host_or_url: str) -> list[ProbeResult]:
        results = []
        for i, candidate in enumerate(candidate_sources(host_or_url)):
            if i == hit_index:
                results.append(ProbeResult(candidate, True, count))
            else:
                results.append(ProbeResult(candidate, False, None, "connection refused"))
        return results

    return prober


def fixed_fetcher(location: tuple[float, float] | None):
    def fetcher(aircraft_url: str) -> tuple[float, float] | None:
        return location

    return fetcher


def exploding_lookup(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the real network lookup must never run in tests")


def make_screen(
    hit_index: int | None = 2,
    location: tuple[float, float] | None = (37.7749, -122.4194),
    initial_host: str = HOST,
) -> SetupScreen:
    return SetupScreen(
        prober=fake_prober(hit_index),
        location_fetcher=fixed_fetcher(location),
        initial_host=initial_host,
    )


def type_text(screen: SetupScreen, text: str) -> None:
    for ch in text:
        screen.handle_key(ord(ch))


def complete_receiver(screen: SetupScreen) -> None:
    screen.handle_key(F5)
    screen.handle_key(ENTER)


def complete_home(screen: SetupScreen) -> None:
    screen.handle_key(F5)
    screen.handle_key(ENTER)


class TestCandidateSources:
    def test_a_bare_host_expands_to_every_known_layout(self) -> None:
        candidates = candidate_sources("radio.local")
        assert candidates[:4] == [
            "http://radio.local/tar1090/data/aircraft.json",
            "http://radio.local/skyaware/data/aircraft.json",
            "http://radio.local/dump1090-fa/data/aircraft.json",
            "http://radio.local:8080/data/aircraft.json",
        ]

    def test_local_file_paths_are_always_offered(self) -> None:
        assert candidate_sources("radio.local")[4:] == [
            "/run/readsb/aircraft.json",
            "/run/dump1090-fa/aircraft.json",
        ]

    def test_a_full_url_is_taken_literally(self) -> None:
        candidates = candidate_sources("http://radio.local:8754/data/aircraft.json")
        assert candidates[0] == "http://radio.local:8754/data/aircraft.json"
        assert not any("tar1090" in candidate for candidate in candidates)

    def test_an_absolute_path_is_taken_literally(self) -> None:
        assert candidate_sources("/srv/feed/aircraft.json")[0] == "/srv/feed/aircraft.json"

    def test_duplicates_are_collapsed(self) -> None:
        candidates = candidate_sources("/run/readsb/aircraft.json")
        assert candidates.count("/run/readsb/aircraft.json") == 1

    def test_empty_input_still_offers_the_local_paths(self) -> None:
        assert candidate_sources("  ") == [
            "/run/readsb/aircraft.json",
            "/run/dump1090-fa/aircraft.json",
        ]


class TestReceiverJsonFor:
    def test_swaps_the_last_path_segment(self) -> None:
        assert (
            receiver_json_for("http://host/skyaware/data/aircraft.json")
            == "http://host/skyaware/data/receiver.json"
        )

    def test_works_for_a_local_path(self) -> None:
        assert receiver_json_for("/run/readsb/aircraft.json") == "/run/readsb/receiver.json"

    def test_bare_filename_has_no_directory_to_keep(self) -> None:
        assert receiver_json_for("aircraft.json") == "receiver.json"


class TestReceiverStep:
    def test_starts_on_the_receiver_step(self) -> None:
        screen = make_screen()
        assert screen.step == STEP_RECEIVER
        assert screen.title.startswith("Setup 1/4")

    def test_no_lookup_runs_until_the_probe_key_is_pressed(self) -> None:
        screen = SetupScreen(prober=exploding_lookup, location_fetcher=exploding_lookup)
        type_text(screen, "radio.local")
        assert screen.probe_results == []

    def test_probe_reports_every_candidate(self) -> None:
        screen = make_screen()
        screen.handle_key(F5)
        assert len(screen.probe_results) == len(CANDIDATES)
        assert [r.candidate for r in screen.probe_results] == CANDIDATES

    def test_probe_records_the_hit_and_its_aircraft_count(self) -> None:
        screen = make_screen()
        screen.handle_key(F5)
        hit = screen.probe_results[2]
        assert hit.ok is True
        assert hit.aircraft_count == 17

    def test_probe_parks_the_cursor_on_the_first_candidate_that_answered(self) -> None:
        screen = make_screen()
        screen.handle_key(F5)
        screen.handle_key(ENTER)
        assert screen.result().source.url == HIT

    def test_probe_reports_how_many_candidates_answered(self) -> None:
        screen = make_screen()
        screen.handle_key(F5)
        assert screen.message is not None
        assert "1 of 6" in screen.message

    def test_probe_results_are_rendered_with_their_counts(self) -> None:
        screen = make_screen()
        screen.handle_key(F5)
        rendered = "\n".join(screen.render_lines(120, 30))
        assert "17 aircraft" in rendered
        assert "connection refused" in rendered

    def test_a_dead_candidate_cannot_be_chosen(self) -> None:
        screen = make_screen()
        screen.handle_key(F5)
        screen.handle_key(curses.KEY_HOME)  # back to candidate 0, which did not answer
        assert screen.handle_key(ENTER) is None
        assert screen.step == STEP_RECEIVER
        assert screen.error is not None
        assert "did not return aircraft data" in screen.error

    def test_all_candidates_dead_is_reported(self) -> None:
        screen = make_screen(hit_index=None)
        screen.handle_key(F5)
        assert screen.error is not None
        assert "no candidate responded" in screen.error

    def test_typed_url_is_accepted_without_probing(self) -> None:
        screen = make_screen(initial_host="")
        type_text(screen, "http://radio.local/data/aircraft.json")
        screen.handle_key(ENTER)
        assert screen.step == STEP_HOME
        assert screen.result().source.url == "http://radio.local/data/aircraft.json"

    def test_an_empty_receiver_field_is_rejected(self) -> None:
        screen = make_screen(initial_host="")
        assert screen.handle_key(ENTER) is None
        assert screen.step == STEP_RECEIVER
        assert screen.error is not None

    def test_tab_switches_between_the_field_and_the_results(self) -> None:
        screen = make_screen()
        screen.handle_key(F5)
        screen.handle_key(TAB)  # back to the host field
        type_text(screen, "x")
        screen.handle_key(ENTER)
        assert screen.result().source.url == HOST + "x"


class TestHomeStep:
    def test_receiver_json_fills_in_the_coordinates(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        screen.handle_key(F5)
        assert screen.message is not None
        assert "receiver.json" in screen.message
        screen.handle_key(ENTER)
        assert screen.step == STEP_UNITS
        assert screen.result().home.lat == 37.7749
        assert screen.result().home.lon == -122.4194

    def test_the_fetcher_is_asked_about_the_chosen_aircraft_json(self) -> None:
        seen: list[str] = []

        def fetcher(aircraft_url: str) -> tuple[float, float] | None:
            seen.append(aircraft_url)
            return (1.0, 2.0)

        screen = SetupScreen(prober=fake_prober(), location_fetcher=fetcher, initial_host=HOST)
        complete_receiver(screen)
        screen.handle_key(F5)
        assert seen == [HIT]

    def test_a_receiver_without_a_location_is_reported(self) -> None:
        screen = make_screen(location=None)
        complete_receiver(screen)
        screen.handle_key(F5)
        assert screen.error is not None
        assert "receiver.json" in screen.error
        assert screen.step == STEP_HOME

    def test_coordinates_can_be_typed_by_hand(self) -> None:
        screen = make_screen(location=None)
        complete_receiver(screen)
        type_text(screen, "51.4700")
        screen.handle_key(curses.KEY_DOWN)
        type_text(screen, "-0.4543")
        screen.handle_key(ENTER)
        assert screen.step == STEP_UNITS
        assert screen.result().home.lat == 51.47
        assert screen.result().home.lon == -0.4543

    def test_the_zero_zero_placeholder_is_rejected(self) -> None:
        screen = make_screen(location=(0.0, 0.0))
        complete_receiver(screen)
        screen.handle_key(F5)
        assert screen.handle_key(ENTER) is None
        assert screen.step == STEP_HOME
        assert screen.error is not None
        assert "placeholder" in screen.error

    def test_typed_zero_zero_is_rejected_too(self) -> None:
        screen = make_screen(location=None)
        complete_receiver(screen)
        type_text(screen, "0")
        screen.handle_key(curses.KEY_DOWN)
        type_text(screen, "0")
        screen.handle_key(ENTER)
        assert screen.step == STEP_HOME
        assert screen.error is not None

    def test_an_empty_coordinate_is_rejected(self) -> None:
        screen = make_screen(location=None)
        complete_receiver(screen)
        type_text(screen, "51.47")
        screen.handle_key(ENTER)
        assert screen.step == STEP_HOME
        assert screen.error is not None
        assert "latitude and a longitude" in screen.error

    def test_an_out_of_range_latitude_is_rejected(self) -> None:
        screen = make_screen(location=(200.0, 10.0))
        complete_receiver(screen)
        screen.handle_key(F5)
        screen.handle_key(ENTER)
        assert screen.step == STEP_HOME
        assert screen.error is not None
        assert "latitude" in screen.error

    def test_an_out_of_range_longitude_is_rejected(self) -> None:
        screen = make_screen(location=(10.0, 200.0))
        complete_receiver(screen)
        screen.handle_key(F5)
        screen.handle_key(ENTER)
        assert screen.step == STEP_HOME
        assert screen.error is not None
        assert "longitude" in screen.error


class TestUnitsStep:
    def test_units_can_be_cycled_and_radius_typed(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        screen.handle_key(ord(" "))  # imperial -> metric
        screen.handle_key(curses.KEY_DOWN)
        for _ in range(len("15.0")):
            screen.handle_key(127)
        type_text(screen, "40")
        screen.handle_key(ENTER)
        assert screen.step == STEP_SUMMARY
        assert screen.result().display.units == "metric"
        assert screen.result().filter.radius == 40.0

    def test_a_non_positive_radius_is_rejected(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        screen.handle_key(curses.KEY_DOWN)
        for _ in range(len("15.0")):
            screen.handle_key(127)
        type_text(screen, "0")
        assert screen.handle_key(ENTER) is None
        assert screen.step == STEP_UNITS
        assert screen.error is not None


class TestSummaryAndResult:
    def test_the_summary_lists_the_four_answers_even_on_a_short_terminal(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        screen.handle_key(ENTER)
        rendered = "\n".join(screen.render_lines(120, 12))
        assert HIT in rendered
        assert "37.7749, -122.4194" in rendered
        assert "imperial" in rendered
        assert "15.0 mi" in rendered

    def test_the_summary_shows_the_config_that_will_be_written(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        screen.handle_key(ENTER)
        rendered = "\n".join(screen.render_lines(120, 60))
        assert "[source]" in rendered
        assert HIT in rendered
        assert "37.7749" in rendered

    def test_finishing_closes_the_wizard_with_applied_set(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        screen.handle_key(ENTER)
        assert screen.step == STEP_SUMMARY
        assert screen.handle_key(ENTER) == "close"
        assert screen.applied is True

    def test_the_assembled_config_is_valid(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        screen.handle_key(ENTER)
        screen.handle_key(ENTER)
        cfg = screen.result()
        validate(cfg)
        assert cfg.source.url == HIT
        assert cfg.home.lat == 37.7749
        assert cfg.filter.radius == 15.0
        assert cfg.display.units == "imperial"

    def test_result_is_a_fresh_config_each_time(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        assert screen.result() is not screen.result()

    def test_untouched_keys_keep_their_schema_defaults(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        cfg = screen.result()
        assert cfg.alerts.squawks == [7500, 7600, 7700]
        assert cfg.logging.level == "INFO"


class TestNavigation:
    def test_escape_closes_without_applying(self) -> None:
        screen = make_screen()
        assert screen.handle_key(ESC) == "close"
        assert screen.applied is False

    def test_shift_tab_steps_back(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        assert screen.step == STEP_HOME
        screen.handle_key(curses.KEY_BTAB)
        assert screen.step == STEP_RECEIVER

    def test_shift_tab_on_the_first_step_does_nothing(self) -> None:
        screen = make_screen()
        screen.handle_key(curses.KEY_BTAB)
        assert screen.step == STEP_RECEIVER

    def test_stepping_back_keeps_the_earlier_answer(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        screen.handle_key(curses.KEY_BTAB)
        screen.handle_key(ENTER)
        assert screen.step == STEP_HOME
        assert screen.result().source.url == HIT


class TestRendering:
    def test_render_lines_is_empty_for_a_zero_sized_area(self) -> None:
        assert make_screen().render_lines(0, 10) == []
        assert make_screen().render_lines(80, 0) == []

    @pytest.mark.parametrize("step_count", [0, 1, 2, 3])
    def test_every_line_is_clipped_and_bounded_at_each_step(self, step_count: int) -> None:
        screen = make_screen()
        for _ in range(step_count):
            screen.handle_key(F5)
            screen.handle_key(ENTER)
        for height in (1, 4, 12, 40):
            lines = screen.render_lines(50, height)
            assert len(lines) <= height
            assert all(len(line) <= 50 for line in lines)

    def test_the_title_names_the_current_step(self) -> None:
        screen = make_screen()
        assert "Receiver" in screen.title
        complete_receiver(screen)
        assert "Home location" in screen.title

    def test_the_receiver_step_lists_the_candidates_before_probing(self) -> None:
        rendered = "\n".join(make_screen().render_lines(120, 30))
        assert "tar1090" in rendered
        assert "/run/readsb/aircraft.json" in rendered

    def test_the_footer_names_the_probe_key(self) -> None:
        assert "F5" in make_screen().render_lines(120, 30)[-1]


class TestSummaryStepIsInert:
    def test_a_stray_key_on_the_summary_cannot_change_an_answer(self) -> None:
        screen = make_screen()
        complete_receiver(screen)
        complete_home(screen)
        screen.handle_key(ENTER)
        assert screen.step == STEP_SUMMARY
        screen.handle_key(ord(" "))
        assert screen.result().display.units == "imperial"

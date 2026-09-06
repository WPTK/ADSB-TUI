"""Tests for adsbtui.ui.screens.detail.DetailScreen.

Exercises handle_key() state transitions and render_lines() content only -- no real
curses window is required, matching the pure-testability contract documented in
ui/screens/__init__.py and ui/screens/detail.py.
"""

from __future__ import annotations

import curses

import pytest

from adsbtui.model import Aircraft
from adsbtui.ui.screens.detail import DetailScreen


def make_aircraft(**overrides) -> Aircraft:
    defaults = dict(hex="a1b2c3", is_icao=True)
    defaults.update(overrides)
    return Aircraft(**defaults)


class TestTitle:
    def test_title_uses_flight_when_present(self):
        ac = make_aircraft(flight="UAL123  ")
        screen = DetailScreen(ac, "imperial")
        assert screen.title == "Aircraft UAL123"

    def test_title_falls_back_to_hex_when_flight_missing(self):
        ac = make_aircraft(hex="abcdef")
        screen = DetailScreen(ac, "imperial")
        assert screen.title == "Aircraft ABCDEF"

    def test_title_falls_back_to_hex_when_flight_blank(self):
        ac = make_aircraft(hex="abcdef", flight="   ")
        screen = DetailScreen(ac, "imperial")
        assert screen.title == "Aircraft ABCDEF"


class TestConstructorValidation:
    def test_rejects_unknown_unit_system(self):
        ac = make_aircraft()
        with pytest.raises(ValueError):
            DetailScreen(ac, "furlongs")


class TestHandleKey:
    @pytest.mark.parametrize("key", [27, ord("q"), ord("Q"), 10, 13, curses.KEY_ENTER])
    def test_close_keys_return_close(self, key):
        screen = DetailScreen(make_aircraft(), "imperial")
        assert screen.handle_key(key) == "close"

    @pytest.mark.parametrize("key", [ord("a"), curses.KEY_UP, curses.KEY_DOWN, ord(" "), 0])
    def test_other_keys_return_none(self, key):
        screen = DetailScreen(make_aircraft(), "imperial")
        assert screen.handle_key(key) is None

    def test_screen_stays_reusable_after_a_non_close_key(self):
        screen = DetailScreen(make_aircraft(), "imperial")
        assert screen.handle_key(ord("x")) is None
        assert screen.handle_key(ord("q")) == "close"


class TestRenderLinesEmptyAircraft:
    """A fully-empty Aircraft (every optional field is None/default) must never raise, and
    every field should degrade to its documented fallback text.
    """

    def test_never_raises_and_renders_na_everywhere(self):
        ac = make_aircraft()
        screen = DetailScreen(ac, "imperial")
        lines = screen.render_lines(80, 40)
        text = "\n".join(lines)

        assert "Hex" in text
        assert "A1B2C3" in text
        assert "Flight" in text
        assert "N/A" in text

    def test_emergency_defaults_to_none_not_na(self):
        ac = make_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(80, 40))
        assert "Emergency" in text
        emergency_line = next(line for line in text.splitlines() if line.startswith("Emergency"))
        assert "None" in emergency_line

    def test_flags_defaults_to_dash(self):
        ac = make_aircraft(db_flags=0)
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(80, 40))
        flags_line = next(line for line in text.splitlines() if line.startswith("Flags"))
        assert flags_line.rstrip().endswith("-")

    def test_closest_approach_defaults_to_lowercase_na(self):
        ac = make_aircraft(cpa_distance_mi=None, cpa_seconds=None)
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(80, 40))
        cpa_line = next(line for line in text.splitlines() if line.startswith("Closest approach"))
        assert cpa_line.rstrip().endswith("n/a")


class TestRenderLinesPopulatedAircraft:
    def make_full_aircraft(self) -> Aircraft:
        return make_aircraft(
            hex="a1b2c3",
            flight="UAL123",
            registration="N12345",
            squawk="1200",
            emergency="general",
            category="A5",
            altitude_ft=35000,
            on_ground=False,
            baro_rate_fpm=1500,
            geom_rate_fpm=1400,
            ground_speed_kt=420,
            track_deg=270,
            source_type="adsb_icao",
            rssi=-12.3,
            type_code="B738",
            type_desc="Boeing 737-800",
            owner_operator="Some Airline",
            owner_name="Some Leasing LLC",
            db_flags=0x1 | 0x2,
            distance_mi=12.5,
            bearing_deg=95.0,
            cpa_distance_mi=3.2,
            cpa_seconds=185.0,
            seen_pos_s=4.0,
        )

    def test_hex_flight_registration(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        assert "A1B2C3" in text
        assert "UAL123" in text
        assert "N12345" in text

    def test_type_shows_code_and_description(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        type_line = next(line for line in text.splitlines() if line.startswith("Type"))
        assert "B738" in type_line
        assert "Boeing 737-800" in type_line

    def test_owner_prefers_owner_name_over_owner_operator(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        owner_line = next(line for line in text.splitlines() if line.startswith("Owner"))
        assert "Some Leasing LLC" in owner_line
        assert "Some Airline" not in owner_line

    def test_owner_falls_back_to_owner_operator(self):
        ac = self.make_full_aircraft()
        ac.owner_name = None
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        owner_line = next(line for line in text.splitlines() if line.startswith("Owner"))
        assert "Some Airline" in owner_line

    def test_altitude_shows_gnd_when_on_ground(self):
        ac = self.make_full_aircraft()
        ac.on_ground = True
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        alt_line = next(line for line in text.splitlines() if line.startswith("Altitude"))
        assert "GND" in alt_line

    def test_altitude_formatted_when_airborne(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        alt_line = next(line for line in text.splitlines() if line.startswith("Altitude"))
        assert "35,000" in alt_line
        assert "ft" in alt_line

    def test_vertical_rate_prefers_baro_over_geom(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        vs_line = next(line for line in text.splitlines() if line.startswith("Vertical rate"))
        assert "1500" in vs_line

    def test_vertical_rate_falls_back_to_geom_when_baro_missing(self):
        ac = self.make_full_aircraft()
        ac.baro_rate_fpm = None
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        vs_line = next(line for line in text.splitlines() if line.startswith("Vertical rate"))
        assert "1400" in vs_line

    def test_ground_speed_formatted(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        gs_line = next(line for line in text.splitlines() if line.startswith("Ground speed"))
        assert "mph" in gs_line

    def test_track_formatted_as_degrees(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        track_line = next(line for line in text.splitlines() if line.startswith("Track"))
        assert "270" in track_line

    def test_bearing_includes_compass_point(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        bearing_line = next(line for line in text.splitlines() if line.startswith("Bearing"))
        assert "095" in bearing_line
        assert "E" in bearing_line

    def test_distance_formatted(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        dist_line = next(line for line in text.splitlines() if line.startswith("Distance"))
        assert "12.5" in dist_line

    def test_closest_approach_shows_distance_and_time(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        cpa_line = next(line for line in text.splitlines() if line.startswith("Closest approach"))
        assert "3.2" in cpa_line
        assert "3m" in cpa_line

    def test_squawk_shown(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        squawk_line = next(line for line in text.splitlines() if line.startswith("Squawk"))
        assert "1200" in squawk_line

    def test_emergency_shown_when_present(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        emergency_line = next(line for line in text.splitlines() if line.startswith("Emergency"))
        assert "general" in emergency_line

    def test_category_decoded(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        cat_line = next(line for line in text.splitlines() if line.startswith("Category"))
        assert "Heavy" in cat_line

    def test_flags_joined(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        flags_line = next(line for line in text.splitlines() if line.startswith("Flags"))
        assert "MIL" in flags_line
        assert "INT" in flags_line

    def test_position_age_formatted(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        age_line = next(line for line in text.splitlines() if line.startswith("Position age"))
        assert "4s" in age_line

    def test_message_source_shown(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        source_line = next(line for line in text.splitlines() if line.startswith("Message source"))
        assert "adsb_icao" in source_line

    def test_signal_formatted(self):
        ac = self.make_full_aircraft()
        screen = DetailScreen(ac, "imperial")
        text = "\n".join(screen.render_lines(100, 40))
        signal_line = next(line for line in text.splitlines() if line.startswith("Signal"))
        assert "-12.3" in signal_line
        assert "dBFS" in signal_line


class TestRenderLinesUnitSystems:
    def test_metric_units_used_in_output(self):
        ac = make_aircraft(altitude_ft=10000, ground_speed_kt=300, distance_mi=10, on_ground=False)
        screen = DetailScreen(ac, "metric")
        text = "\n".join(screen.render_lines(100, 40))
        alt_line = next(line for line in text.splitlines() if line.startswith("Altitude"))
        gs_line = next(line for line in text.splitlines() if line.startswith("Ground speed"))
        dist_line = next(line for line in text.splitlines() if line.startswith("Distance"))
        assert "m" in alt_line
        assert "kmh" in gs_line
        assert "km" in dist_line

    def test_aviation_units_used_in_output(self):
        ac = make_aircraft(ground_speed_kt=300, distance_mi=10)
        screen = DetailScreen(ac, "aviation")
        text = "\n".join(screen.render_lines(100, 40))
        gs_line = next(line for line in text.splitlines() if line.startswith("Ground speed"))
        dist_line = next(line for line in text.splitlines() if line.startswith("Distance"))
        assert "kt" in gs_line
        assert "nm" in dist_line


class TestRenderLinesGeometry:
    def test_respects_width_clipping(self):
        ac = DetailScreen(make_aircraft(owner_name="A" * 200, source_type="adsb_icao"), "imperial")
        lines = ac.render_lines(20, 40)
        assert all(len(line) <= 20 for line in lines)

    def test_respects_height_limit(self):
        screen = DetailScreen(make_aircraft(), "imperial")
        lines = screen.render_lines(100, 3)
        assert len(lines) == 3

    def test_zero_or_negative_dimensions_return_empty(self):
        screen = DetailScreen(make_aircraft(), "imperial")
        assert screen.render_lines(0, 40) == []
        assert screen.render_lines(100, 0) == []
        assert screen.render_lines(-5, 40) == []
        assert screen.render_lines(100, -5) == []

    def test_full_height_returns_all_fields(self):
        screen = DetailScreen(make_aircraft(), "imperial")
        lines = screen.render_lines(100, 100)
        # 19 documented fields.
        assert len(lines) == 19

    def test_all_lines_are_plain_strings_no_curses(self):
        screen = DetailScreen(make_aircraft(), "imperial")
        lines = screen.render_lines(100, 40)
        assert all(isinstance(line, str) for line in lines)

"""Tests for adsbtui.alerts: pure alert grading, vertical trend, and enrichment decoding."""

from __future__ import annotations

from adsbtui.alerts import (
    classify_trend,
    decode_category,
    decode_db_flags,
    grade,
    trend_glyph,
)
from adsbtui.model import Aircraft, AlertLevel

DEFAULT_SQUAWKS = [7500, 7600, 7700]


def make_aircraft(**overrides) -> Aircraft:
    """Build a minimal Aircraft with sane defaults, overridden per test."""
    defaults = dict(hex="a1b2c3", is_icao=True)
    defaults.update(overrides)
    return Aircraft(**defaults)


def grade_default(aircraft: Aircraft, **overrides) -> AlertLevel:
    """Call grade() with the [alerts]/[filter] defaults from the config schema, overridden
    per test so each call only has to spell out what it cares about."""
    kwargs = dict(
        squawks=DEFAULT_SQUAWKS,
        emergency_enabled=True,
        proximity_mi=5.0,
        cpa_enabled=True,
        cpa_distance_mi=1.0,
        cpa_horizon_s=600.0,
        cpa_min_gs_kt=30.0,
    )
    kwargs.update(overrides)
    return grade(aircraft, **kwargs)


class TestGradeEmergency:
    def test_emergency_squawk_wins(self):
        ac = make_aircraft(squawk="7700", distance_mi=100.0)
        assert grade_default(ac) == AlertLevel.EMERGENCY

    def test_emergency_squawk_matches_int_config_via_str_compare(self):
        # squawks in config are ints like [7500, 7600, 7700]; aircraft.squawk is a string.
        ac = make_aircraft(squawk="7600")
        assert grade_default(ac, squawks=[7500, 7600, 7700]) == AlertLevel.EMERGENCY

    def test_emergency_state_field_wins_even_with_ordinary_squawk(self):
        ac = make_aircraft(squawk="1200", emergency="unlawful")
        assert grade_default(ac) == AlertLevel.EMERGENCY

    def test_plain_squawk_with_emergency_disabled_is_not_emergency(self):
        # A 7700 squawk should NOT trigger EMERGENCY when emergency_enabled is False, and
        # with nothing else set up the aircraft should grade as clear.
        ac = make_aircraft(squawk="7700")
        assert grade_default(ac, emergency_enabled=False) == AlertLevel.NONE

    def test_plain_squawk_not_in_watchlist_is_not_emergency(self):
        ac = make_aircraft(squawk="1200")
        assert grade_default(ac) == AlertLevel.NONE


class TestGradeOverhead:
    def test_within_proximity_radius_is_overhead(self):
        ac = make_aircraft(distance_mi=3.0)
        assert grade_default(ac, proximity_mi=5.0) == AlertLevel.OVERHEAD

    def test_exactly_at_proximity_radius_is_overhead(self):
        ac = make_aircraft(distance_mi=5.0)
        assert grade_default(ac, proximity_mi=5.0) == AlertLevel.OVERHEAD

    def test_just_outside_proximity_radius_is_not_overhead(self):
        ac = make_aircraft(distance_mi=5.01)
        assert grade_default(ac, proximity_mi=5.0) == AlertLevel.NONE


class TestGradeInbound:
    def test_qualifying_cpa_is_inbound(self):
        ac = make_aircraft(
            distance_mi=20.0,
            cpa_distance_mi=0.5,
            cpa_seconds=120.0,
            ground_speed_kt=200.0,
        )
        assert grade_default(ac) == AlertLevel.INBOUND

    def test_cpa_disabled_falls_through(self):
        ac = make_aircraft(
            distance_mi=20.0,
            cpa_distance_mi=0.5,
            cpa_seconds=120.0,
            ground_speed_kt=200.0,
        )
        assert grade_default(ac, cpa_enabled=False) == AlertLevel.NONE

    def test_cpa_too_far_is_not_inbound(self):
        ac = make_aircraft(
            distance_mi=20.0,
            cpa_distance_mi=5.0,
            cpa_seconds=120.0,
            ground_speed_kt=200.0,
        )
        assert grade_default(ac, cpa_distance_mi=1.0) == AlertLevel.NONE

    def test_cpa_beyond_horizon_is_not_inbound(self):
        ac = make_aircraft(
            distance_mi=20.0,
            cpa_distance_mi=0.5,
            cpa_seconds=900.0,
            ground_speed_kt=200.0,
        )
        assert grade_default(ac, cpa_horizon_s=600.0) == AlertLevel.NONE

    def test_cpa_seconds_zero_is_not_inbound(self):
        # cpa_seconds must be strictly positive (0 means "right now", not "in the future").
        ac = make_aircraft(
            distance_mi=20.0,
            cpa_distance_mi=0.5,
            cpa_seconds=0.0,
            ground_speed_kt=200.0,
        )
        assert grade_default(ac) == AlertLevel.NONE

    def test_too_slow_is_not_inbound(self):
        ac = make_aircraft(
            distance_mi=20.0,
            cpa_distance_mi=0.5,
            cpa_seconds=120.0,
            ground_speed_kt=5.0,
        )
        assert grade_default(ac, cpa_min_gs_kt=30.0) == AlertLevel.NONE

    def test_missing_ground_speed_treated_as_zero_and_fails_min_gs(self):
        ac = make_aircraft(
            distance_mi=20.0,
            cpa_distance_mi=0.5,
            cpa_seconds=120.0,
            ground_speed_kt=None,
        )
        assert grade_default(ac, cpa_min_gs_kt=30.0) == AlertLevel.NONE


class TestGradeOutbound:
    def test_receding_target_within_double_proximity_is_outbound(self):
        ac = make_aircraft(distance_mi=8.0, cpa_distance_mi=0.5, cpa_seconds=None)
        assert grade_default(ac, proximity_mi=5.0) == AlertLevel.OUTBOUND

    def test_receding_target_beyond_double_proximity_is_not_outbound(self):
        ac = make_aircraft(distance_mi=11.0, cpa_distance_mi=0.5, cpa_seconds=None)
        assert grade_default(ac, proximity_mi=5.0) == AlertLevel.NONE

    def test_no_cpa_solution_at_all_is_not_outbound(self):
        ac = make_aircraft(distance_mi=8.0, cpa_distance_mi=None, cpa_seconds=None)
        assert grade_default(ac, proximity_mi=5.0) == AlertLevel.NONE


class TestGradeNone:
    def test_fully_clear_aircraft_is_none(self):
        ac = make_aircraft(
            squawk="1200",
            distance_mi=50.0,
            cpa_distance_mi=None,
            cpa_seconds=None,
            ground_speed_kt=400.0,
        )
        assert grade_default(ac) == AlertLevel.NONE

    def test_no_position_at_all_is_none(self):
        ac = make_aircraft()
        assert grade_default(ac) == AlertLevel.NONE


class TestGradePriorityOrdering:
    def test_emergency_beats_overhead(self):
        ac = make_aircraft(squawk="7500", distance_mi=1.0)
        assert grade_default(ac) == AlertLevel.EMERGENCY

    def test_overhead_beats_inbound(self):
        ac = make_aircraft(
            distance_mi=2.0,
            cpa_distance_mi=0.5,
            cpa_seconds=120.0,
            ground_speed_kt=200.0,
        )
        assert grade_default(ac, proximity_mi=5.0) == AlertLevel.OVERHEAD


class TestClassifyTrend:
    def test_strongly_positive_baro_rate_is_climb(self):
        assert classify_trend(1000.0, None, 256.0) == "climb"

    def test_strongly_negative_baro_rate_is_descend(self):
        assert classify_trend(-1000.0, None, 256.0) == "descend"

    def test_near_zero_baro_rate_is_level(self):
        assert classify_trend(50.0, None, 256.0) == "level"

    def test_exactly_at_threshold_is_climb(self):
        assert classify_trend(256.0, None, 256.0) == "climb"

    def test_exactly_at_negative_threshold_is_descend(self):
        assert classify_trend(-256.0, None, 256.0) == "descend"

    def test_both_none_is_unknown(self):
        assert classify_trend(None, None, 256.0) == "unknown"

    def test_falls_back_to_geom_rate_when_baro_missing(self):
        assert classify_trend(None, 1000.0, 256.0) == "climb"

    def test_baro_preferred_over_geom_when_both_present(self):
        # baro says descend, geom says climb -- baro should win.
        assert classify_trend(-1000.0, 1000.0, 256.0) == "descend"


class TestTrendGlyph:
    def test_unicode_glyphs(self):
        assert trend_glyph("climb") == "↑"
        assert trend_glyph("descend") == "↓"
        assert trend_glyph("level") == "→"
        assert trend_glyph("unknown") == ""

    def test_ascii_glyphs(self):
        assert trend_glyph("climb", ascii_only=True) == "^"
        assert trend_glyph("descend", ascii_only=True) == "v"
        assert trend_glyph("level", ascii_only=True) == "-"
        assert trend_glyph("unknown", ascii_only=True) == ""


class TestDecodeDbFlags:
    def test_zero_is_empty(self):
        assert decode_db_flags(0) == []

    def test_bit0_is_military(self):
        assert decode_db_flags(1) == ["MIL"]

    def test_military_and_pia(self):
        assert decode_db_flags(5) == ["MIL", "PIA"]

    def test_all_four_bits(self):
        assert decode_db_flags(15) == ["MIL", "INT", "PIA", "LADD"]


class TestDecodeCategory:
    def test_known_codes(self):
        assert decode_category("A1") == "Light"
        assert decode_category("A5") == "Heavy"
        assert decode_category("A7") == "Rotor"
        assert decode_category("B6") == "UAV"

    def test_unknown_code_passes_through(self):
        assert decode_category("D7") == "D7"

    def test_none_is_none(self):
        assert decode_category(None) is None

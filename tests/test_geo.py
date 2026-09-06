"""Tests for adsbtui.geo: pure great-circle, elevation, compass, and CPA math."""

from __future__ import annotations

import math

import pytest

from adsbtui.geo import (
    closest_point_of_approach,
    compass_point,
    elevation_angle_deg,
    haversine_mi,
    initial_bearing_deg,
)

# San Francisco City Hall and downtown Oakland -- a well-known, easily checked pair roughly
# 8-9 statute miles apart across the bay.
SF_LAT, SF_LON = 37.7749, -122.4194
OAK_LAT, OAK_LON = 37.8044, -122.2712


class TestHaversineMi:
    def test_sf_to_oakland_known_distance(self):
        d = haversine_mi(SF_LAT, SF_LON, OAK_LAT, OAK_LON)
        assert 8.0 <= d <= 9.0

    def test_same_point_is_zero(self):
        assert haversine_mi(37.0, -122.0, 37.0, -122.0) == pytest.approx(0.0, abs=1e-9)

    def test_symmetric(self):
        d1 = haversine_mi(SF_LAT, SF_LON, OAK_LAT, OAK_LON)
        d2 = haversine_mi(OAK_LAT, OAK_LON, SF_LAT, SF_LON)
        assert d1 == pytest.approx(d2, abs=1e-9)

    def test_one_degree_latitude_is_about_69_miles(self):
        # A classic sanity check independent of the SF/Oakland fixture: one degree of latitude
        # anywhere on Earth is close to 69 statute miles, which only holds if the radius constant
        # (statute miles, not nautical) is correct.
        d = haversine_mi(0.0, 0.0, 1.0, 0.0)
        assert 68.9 <= d <= 69.2

    def test_antipodal_quarter_globe(self):
        # Equator to pole is a quarter of the Earth's circumference.
        d = haversine_mi(0.0, 0.0, 90.0, 0.0)
        expected = math.pi / 2 * 3958.8
        assert d == pytest.approx(expected, rel=1e-6)


class TestInitialBearingDeg:
    def test_due_north(self):
        b = initial_bearing_deg(0.0, 0.0, 1.0, 0.0)
        assert b == pytest.approx(0.0, abs=1e-6)

    def test_due_east_at_equator(self):
        b = initial_bearing_deg(0.0, 0.0, 0.0, 1.0)
        assert b == pytest.approx(90.0, abs=1e-6)

    def test_due_south(self):
        b = initial_bearing_deg(1.0, 0.0, 0.0, 0.0)
        assert b == pytest.approx(180.0, abs=1e-6)

    def test_due_west_at_equator(self):
        b = initial_bearing_deg(0.0, 0.0, 0.0, -1.0)
        assert b == pytest.approx(270.0, abs=1e-6)

    def test_result_in_range(self):
        b = initial_bearing_deg(SF_LAT, SF_LON, OAK_LAT, OAK_LON)
        assert 0.0 <= b < 360.0


class TestComparePointBoundaries:
    def test_zero_is_north(self):
        assert compass_point(0.0) == "N"

    def test_just_below_first_boundary_is_north(self):
        assert compass_point(11.24) == "N"

    def test_at_first_boundary_is_nne(self):
        assert compass_point(11.25) == "NNE"

    def test_just_below_wraparound_boundary_is_nnw(self):
        assert compass_point(348.74) == "NNW"

    def test_at_wraparound_boundary_is_north(self):
        assert compass_point(348.75) == "N"

    def test_near_360_is_north(self):
        assert compass_point(359.9) == "N"

    def test_east(self):
        assert compass_point(90.0) == "E"

    def test_south(self):
        assert compass_point(180.0) == "S"

    def test_west(self):
        assert compass_point(270.0) == "W"

    def test_negative_degrees_wrap(self):
        assert compass_point(-11.0) == "N"

    def test_over_360_wraps(self):
        assert compass_point(360.0 + 11.25) == "NNE"

    def test_only_sixteen_distinct_labels(self):
        labels = {compass_point(d) for d in range(0, 360)}
        assert len(labels) == 16


class TestElevationAngleDeg:
    def test_directly_overhead_is_ninety(self):
        assert elevation_angle_deg(0.0, 1000.0) == pytest.approx(90.0, abs=1e-9)

    def test_far_away_is_a_small_angle(self):
        angle = elevation_angle_deg(100.0, 35000.0)
        assert 0.0 < angle < 10.0

    def test_closer_and_higher_gives_larger_angle_than_farther_and_lower(self):
        near_high = elevation_angle_deg(5.0, 30000.0)
        far_low = elevation_angle_deg(100.0, 5000.0)
        assert near_high > far_low

    def test_on_horizon_is_zero(self):
        assert elevation_angle_deg(10.0, 0.0, observer_alt_ft=0.0) == pytest.approx(0.0, abs=1e-9)

    def test_observer_altitude_is_subtracted(self):
        angle = elevation_angle_deg(10.0, 1000.0, observer_alt_ft=1000.0)
        assert angle == pytest.approx(0.0, abs=1e-9)

    def test_target_below_observer_is_negative(self):
        angle = elevation_angle_deg(10.0, 0.0, observer_alt_ft=1000.0)
        assert angle < 0.0


class TestClosestPointOfApproach:
    HOME_LAT, HOME_LON = 37.0, -122.0

    def test_flying_directly_at_home_gives_small_distance_and_future_time(self):
        # Aircraft due north of home, heading due south straight at it.
        ac_lat, ac_lon = self.HOME_LAT + 0.1, self.HOME_LON
        d_cpa, t_cpa = closest_point_of_approach(
            self.HOME_LAT, self.HOME_LON, ac_lat, ac_lon, track_deg=180.0, ground_speed_kt=120.0
        )
        assert d_cpa is not None and t_cpa is not None
        assert d_cpa == pytest.approx(0.0, abs=1e-6)
        assert t_cpa > 0.0

    def test_flying_directly_away_returns_none(self):
        # Same geometry, but heading further away (due north) instead of at home.
        ac_lat, ac_lon = self.HOME_LAT + 0.1, self.HOME_LON
        d_cpa, t_cpa = closest_point_of_approach(
            self.HOME_LAT, self.HOME_LON, ac_lat, ac_lon, track_deg=0.0, ground_speed_kt=120.0
        )
        assert d_cpa is None
        assert t_cpa is None

    def test_missing_ground_speed_returns_none(self):
        d_cpa, t_cpa = closest_point_of_approach(
            self.HOME_LAT,
            self.HOME_LON,
            self.HOME_LAT + 0.1,
            self.HOME_LON,
            track_deg=180.0,
            ground_speed_kt=None,
        )
        assert d_cpa is None
        assert t_cpa is None

    def test_missing_track_returns_none(self):
        d_cpa, t_cpa = closest_point_of_approach(
            self.HOME_LAT,
            self.HOME_LON,
            self.HOME_LAT + 0.1,
            self.HOME_LON,
            track_deg=None,
            ground_speed_kt=120.0,
        )
        assert d_cpa is None
        assert t_cpa is None

    def test_stationary_aircraft_returns_none(self):
        d_cpa, t_cpa = closest_point_of_approach(
            self.HOME_LAT,
            self.HOME_LON,
            self.HOME_LAT + 0.1,
            self.HOME_LON,
            track_deg=180.0,
            ground_speed_kt=0.0,
        )
        assert d_cpa is None
        assert t_cpa is None

    def test_crossing_path_gives_nonzero_but_finite_distance(self):
        # Aircraft north-east of home, heading due south -- it passes to the east of home rather
        # than straight through it, so cpa_distance_mi should be positive but still small relative
        # to the aircraft's starting distance from home.
        ac_lat, ac_lon = self.HOME_LAT + 0.1, self.HOME_LON + 0.1
        d_cpa, t_cpa = closest_point_of_approach(
            self.HOME_LAT, self.HOME_LON, ac_lat, ac_lon, track_deg=180.0, ground_speed_kt=120.0
        )
        start_distance = haversine_mi(self.HOME_LAT, self.HOME_LON, ac_lat, ac_lon)
        assert d_cpa is not None and t_cpa is not None
        assert 0.0 < d_cpa < start_distance
        assert t_cpa > 0.0

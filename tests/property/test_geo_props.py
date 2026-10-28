"""Property tests for geo: bearings stay in range, distances are non-negative."""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from adsbtui import geo

lats = st.floats(min_value=-90, max_value=90)
lons = st.floats(min_value=-180, max_value=180)


@given(lats, lons, lats, lons)
def test_bearing_is_in_zero_to_360(lat1, lon1, lat2, lon2):
    b = geo.initial_bearing_deg(lat1, lon1, lat2, lon2)
    assert 0 <= b < 360


@given(lats, lons, lats, lons)
def test_distance_is_finite_and_non_negative(lat1, lon1, lat2, lon2):
    d = geo.haversine_mi(lat1, lon1, lat2, lon2)
    assert math.isfinite(d)
    assert d >= 0


@given(lats, lons, lats, lons)
def test_distance_is_symmetric(lat1, lon1, lat2, lon2):
    a = geo.haversine_mi(lat1, lon1, lat2, lon2)
    b = geo.haversine_mi(lat2, lon2, lat1, lon1)
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


@given(
    lats,
    st.floats(min_value=179.0, max_value=180.0),
    st.floats(min_value=-180.0, max_value=-179.0),
)
def test_distance_across_the_antimeridian_is_short(lat, east, west):
    # 2 degrees of longitude is at most ~140 miles at the equator.
    assert geo.haversine_mi(lat, east, lat, west) < 150


@given(st.floats(min_value=0, max_value=360, exclude_max=True))
def test_compass_point_accepts_any_bearing(deg):
    assert geo.compass_point(deg)

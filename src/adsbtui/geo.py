"""Pure geometry helpers: great-circle distance/bearing, elevation angle, compass labels,
and closest-point-of-approach projection.

All distances are in statute miles, all angles in degrees, all speeds in knots unless a
parameter name says otherwise. Nothing in this module does I/O or touches curses -- it is
plain math over floats so it can be unit-tested without a terminal or a network feed.
"""

from __future__ import annotations

import math

#: Mean Earth radius in statute miles, used by haversine_mi. This value (not the more commonly
#: quoted 3959 mi rounding, and not the nautical-mile-derived 3440.065 nm figure) is what the
#: original tool got wrong -- it used a nautical radius with a statute-mile label, which quietly
#: overstated every displayed distance by about 15%.
EARTH_RADIUS_MI = 3958.8

#: Local tangent-plane scale factors used by closest_point_of_approach. One degree of latitude is
#: very close to a fixed distance everywhere on Earth (average ~69.0 statute miles); one degree of
#: longitude shrinks toward the poles by a factor of cos(latitude), so it is scaled by the home
#: observer's latitude. This is only valid for the small (tens-of-miles) scale a hobby receiver
#: covers -- it is not a projection appropriate for long-range navigation.
MI_PER_DEG_LAT = 69.0
MI_PER_DEG_LON_AT_EQUATOR = 69.17

#: 16-point compass rose, index 0 = N, going clockwise in 22.5-degree steps.
_COMPASS_POINTS = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in statute miles."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_MI * c


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, in degrees [0, 360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    theta = math.atan2(x, y)
    return math.degrees(theta) % 360.0


def elevation_angle_deg(
    distance_mi: float, altitude_ft: float, observer_alt_ft: float = 0.0
) -> float:
    """Angle above the horizon from a ground observer to a target at the given ground-track
    distance and altitude.

    This is a flat-earth right-triangle approximation (horizontal leg = distance_mi, vertical
    leg = altitude difference) -- it ignores Earth curvature and atmospheric refraction, which is
    fine at the ranges a hobby receiver sees but would need correcting for a survey instrument.
    """
    height_diff_ft = altitude_ft - observer_alt_ft
    distance_ft = distance_mi * 5280.0
    if distance_ft == 0:
        return 90.0 if height_diff_ft > 0 else (0.0 if height_diff_ft == 0 else -90.0)
    return math.degrees(math.atan2(height_diff_ft, distance_ft))


def compass_point(deg: float) -> str:
    """16-point compass label (N, NNE, NE, ...) for a bearing in degrees.

    Boundaries are centered on each of the 16 directions, e.g. N covers [348.75, 360) and
    [0, 11.25), NNE covers [11.25, 33.75), and so on -- so 11.25 degrees is NNE, not N.
    """
    index = int((deg % 360 + 11.25) // 22.5) % 16
    return _COMPASS_POINTS[index]


def closest_point_of_approach(
    home_lat: float,
    home_lon: float,
    ac_lat: float,
    ac_lon: float,
    track_deg: float | None,
    ground_speed_kt: float | None,
) -> tuple[float | None, float | None]:
    """Project the aircraft's current position forward along its current track/ground speed and
    find the closest future approach to the home point.

    Uses a local flat-earth tangent-plane projection (see MI_PER_DEG_LAT/MI_PER_DEG_LON_AT_EQUATOR
    above) rather than great-circle math -- adequate at the tens-of-miles scale this is used at,
    much cheaper to differentiate, and the aircraft's own track/speed inputs are themselves only
    instantaneous snapshots, not exact future promises.

    Returns (cpa_distance_mi, cpa_seconds_from_now). Returns (None, None) if track_deg or
    ground_speed_kt is missing, if the aircraft is effectively stationary (velocity so small the
    projection is meaningless), or if the closest approach on this heading is already in the past.
    """
    if track_deg is None or ground_speed_kt is None:
        return (None, None)

    mi_per_deg_lon = MI_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(home_lat))
    px = (ac_lon - home_lon) * mi_per_deg_lon
    py = (ac_lat - home_lat) * MI_PER_DEG_LAT

    speed_mi_per_s = ground_speed_kt * 1.150779448 / 3600.0
    track_rad = math.radians(track_deg)
    vx = speed_mi_per_s * math.sin(track_rad)
    vy = speed_mi_per_s * math.cos(track_rad)

    vv = vx * vx + vy * vy
    if vv < 1e-6:
        return (None, None)

    pv = px * vx + py * vy
    t_cpa = -pv / vv
    if t_cpa < 0:
        return (None, None)

    cx = px + vx * t_cpa
    cy = py + vy * t_cpa
    d_cpa = math.hypot(cx, cy)
    return (d_cpa, t_cpa)

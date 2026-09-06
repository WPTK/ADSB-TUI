"""Alert grading: pure logic that decides how alarmed the UI should be about an aircraft.

This module only grades -- it never rings a bell, sends a webhook, or writes a log line.
Delivery/dispatch (cooldowns, quiet hours, webhook formatting, etc.) belongs in a later
module that sits on top of grade()'s output. Like model.py and geo.py, this module is pure:
no curses calls, no network calls, no global state, so it can be unit-tested without a
terminal or a feed.

Functions take plain keyword arguments mirroring the [alerts] and [filter] TOML config
keys rather than importing config.py directly, to avoid a circular dependency between the
config loader and the alert grader.
"""

from __future__ import annotations

from adsbtui.model import Aircraft, AlertLevel

#: Emitter category -> short display label, for the subset of ADS-B categories that are
#: interesting enough in a terminal-width table to abbreviate. Anything not in this table
#: is passed through as-is by decode_category() rather than being dropped.
_CATEGORY_LABELS = {
    "A1": "Light",
    "A2": "Small",
    "A3": "Large",
    "A4": "HVortex",
    "A5": "Heavy",
    "A6": "HiPerf",
    "A7": "Rotor",
    "B1": "Glider",
    "B2": "LTA",
    "B4": "UltraLt",
    "B6": "UAV",
    "B7": "Space",
    "C1": "Vehicle",
}

#: dbFlags bitfield -> short tag, in bit order (bit0 first). decode_db_flags() emits tags
#: in this order regardless of which bits are set.
_DB_FLAG_TAGS = [
    (0x1, "MIL"),
    (0x2, "INT"),
    (0x4, "PIA"),
    (0x8, "LADD"),
]

#: Unicode and ASCII glyphs for each vertical trend, keyed by the strings classify_trend()
#: returns. "unknown" is deliberately blank in both sets so the UI can render nothing
#: instead of a misleading "level" arrow when there is no rate data at all.
_TREND_GLYPHS_UNICODE = {
    "climb": "↑",  # ^
    "descend": "↓",  # v
    "level": "→",  # >
    "unknown": "",
}
_TREND_GLYPHS_ASCII = {
    "climb": "^",
    "descend": "v",
    "level": "-",
    "unknown": "",
}


def grade(
    aircraft: Aircraft,
    squawks: list[int] | list[str],
    emergency_enabled: bool,
    proximity_mi: float,
    cpa_enabled: bool,
    cpa_distance_mi: float,
    cpa_horizon_s: float,
    cpa_min_gs_kt: float,
) -> AlertLevel:
    """The single source of truth for how alarmed the UI should be about this aircraft.

    Replaces the original tool's only signal ("is it closer than N miles?") with a
    priority-ordered set of checks; the first one that matches wins. Kept deliberately
    simple -- this is a heuristic for a terminal UI, not a collision-avoidance system.

    Priority order:
      1. EMERGENCY -- squawking one of the watched codes, or reporting an emergency state.
      2. OVERHEAD  -- currently within the proximity radius.
      3. INBOUND   -- not close yet, but its current track/speed will bring it within the
                      CPA distance inside the CPA horizon, and it's moving fast enough for
                      that projection to be meaningful.
      4. OUTBOUND  -- was recently close and is now moving away (a receding CPA solution).
      5. NONE      -- nothing above applies.
    """
    # 1. EMERGENCY: a watched squawk code, or any reported emergency state, if enabled.
    if emergency_enabled:
        watched_squawks = {str(s) for s in squawks}
        if aircraft.squawk is not None and aircraft.squawk in watched_squawks:
            return AlertLevel.EMERGENCY
        if aircraft.emergency is not None:
            return AlertLevel.EMERGENCY

    # 2. OVERHEAD: currently inside the proximity radius.
    if aircraft.distance_mi is not None and aircraft.distance_mi <= proximity_mi:
        return AlertLevel.OVERHEAD

    # 3. INBOUND: projected to close within cpa_distance_mi inside cpa_horizon_s, moving
    # fast enough (cpa_min_gs_kt) that the projection isn't just ground-track noise from an
    # aircraft that is essentially parked. cpa_seconds must be strictly in the future (> 0)
    # -- 0 or negative means "now or already past", which OVERHEAD or OUTBOUND handle instead.
    if (
        cpa_enabled
        and aircraft.cpa_distance_mi is not None
        and aircraft.cpa_seconds is not None
        and aircraft.cpa_distance_mi <= cpa_distance_mi
        and 0 < aircraft.cpa_seconds <= cpa_horizon_s
        and (aircraft.ground_speed_kt or 0) >= cpa_min_gs_kt
    ):
        return AlertLevel.INBOUND

    # 4. OUTBOUND: still within twice the proximity radius, and geo.closest_point_of_approach
    # returned a cpa_distance_mi with no cpa_seconds -- that combination means the aircraft's
    # closest approach on its current track is already behind it (a receding target), i.e. it
    # was recently close and is now heading away rather than about to arrive.
    if (
        aircraft.distance_mi is not None
        and aircraft.distance_mi <= proximity_mi * 2
        and aircraft.cpa_distance_mi is not None
        and aircraft.cpa_seconds is None
    ):
        return AlertLevel.OUTBOUND

    # 5. Nothing above applies.
    return AlertLevel.NONE


def classify_trend(
    baro_rate_fpm: float | None,
    geom_rate_fpm: float | None,
    threshold_fpm: float,
) -> str:
    """Classify vertical motion as "climb", "descend", "level", or "unknown".

    Prefers barometric rate (baro_rate_fpm); falls back to geometric rate (geom_rate_fpm)
    only when baro is missing. Returns "unknown" -- not "level" -- when neither rate is
    available, so the UI can render a blank cell instead of a false "level" arrow.
    """
    rate = baro_rate_fpm if baro_rate_fpm is not None else geom_rate_fpm
    if rate is None:
        return "unknown"
    if rate >= threshold_fpm:
        return "climb"
    if rate <= -threshold_fpm:
        return "descend"
    return "level"


def trend_glyph(trend: str, ascii_only: bool = False) -> str:
    """Single-character glyph for a classify_trend() result.

    Unicode set: up-arrow for climb, down-arrow for descend, right-arrow for level, empty
    for unknown. ASCII set (ascii_only=True): "^", "v", "-", and empty for unknown.
    """
    glyphs = _TREND_GLYPHS_ASCII if ascii_only else _TREND_GLYPHS_UNICODE
    return glyphs.get(trend, "")


def decode_db_flags(db_flags: int) -> list[str]:
    """Decode the dbFlags bitfield into its present tags: MIL, INT, PIA, LADD.

    bit0=military(1), bit1=interesting(2), bit2=PIA(4), bit3=LADD(8). Tags are returned in
    that bit order; bits outside this table (and a falsy/None input) are simply ignored.
    """
    if not db_flags:
        return []
    return [tag for bit, tag in _DB_FLAG_TAGS if db_flags & bit]


def decode_category(category: str | None) -> str | None:
    """Map a common ADS-B emitter category (e.g. "A5") to a short display label.

    Returns None if category is None. Returns the raw category string unchanged for any
    code not in the table below, rather than dropping it -- an unrecognized but present
    category is still more useful to show than nothing.
    """
    if category is None:
        return None
    return _CATEGORY_LABELS.get(category, category)

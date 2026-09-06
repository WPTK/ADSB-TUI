"""Unit conversion for display.

Internal storage is always native units (see `model.py`): knots, feet, feet/minute, statute miles,
degrees. This module converts those to whichever unit system the user has selected and formats them
as strings. Nothing outside this module (and the UI layer that calls it) should know about mph, km,
or any other display unit.
"""

from __future__ import annotations

KT_TO_MPH = 1.150779448
KT_TO_KMH = 1.852
MI_TO_KM = 1.609344
MI_TO_NM = 0.868976111
FT_TO_M = 0.3048

#: unit-system name -> (distance unit, altitude unit, speed unit, vertical-speed unit)
UNIT_SYSTEMS: dict[str, dict[str, str]] = {
    "imperial": {"distance": "mi", "altitude": "ft", "speed": "mph", "vspeed": "fpm"},
    "metric": {"distance": "km", "altitude": "m", "speed": "kmh", "vspeed": "mpm"},
    "aviation": {"distance": "nm", "altitude": "ft", "speed": "kt", "vspeed": "fpm"},
}


def speed_kt_to(value_kt: float | None, unit: str) -> float | None:
    if value_kt is None:
        return None
    if unit == "kt":
        return value_kt
    if unit == "mph":
        return value_kt * KT_TO_MPH
    if unit == "kmh":
        return value_kt * KT_TO_KMH
    raise ValueError(f"unknown speed unit {unit!r}")


def distance_mi_to(value_mi: float | None, unit: str) -> float | None:
    if value_mi is None:
        return None
    if unit == "mi":
        return value_mi
    if unit == "km":
        return value_mi * MI_TO_KM
    if unit == "nm":
        return value_mi * MI_TO_NM
    raise ValueError(f"unknown distance unit {unit!r}")


def altitude_ft_to(value_ft: float | None, unit: str) -> float | None:
    if value_ft is None:
        return None
    if unit == "ft":
        return value_ft
    if unit == "m":
        return value_ft * FT_TO_M
    raise ValueError(f"unknown altitude unit {unit!r}")


def vspeed_fpm_to(value_fpm: float | None, unit: str) -> float | None:
    if value_fpm is None:
        return None
    if unit == "fpm":
        return value_fpm
    if unit == "mpm":
        return value_fpm * FT_TO_M
    raise ValueError(f"unknown vertical-speed unit {unit!r}")


def format_speed(value_kt: float | None, unit: str) -> str:
    v = speed_kt_to(value_kt, unit)
    return "N/A" if v is None else f"{v:.0f} {unit}"


def format_distance(value_mi: float | None, unit: str, decimals: int = 1) -> str:
    v = distance_mi_to(value_mi, unit)
    return "N/A" if v is None else f"{v:.{decimals}f} {unit}"


def format_altitude(value_ft: float | None, unit: str, on_ground: bool = False) -> str:
    if on_ground:
        return "GND"
    v = altitude_ft_to(value_ft, unit)
    return "N/A" if v is None else f"{v:,.0f}{unit}"


def format_vspeed(value_fpm: float | None, unit: str) -> str:
    v = vspeed_fpm_to(value_fpm, unit)
    return "N/A" if v is None else f"{v:+.0f} {unit}"

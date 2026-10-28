"""A built-in Source for ``--demo``: a dozen scripted aircraft moving around the home point.

No server, no network, no files. Positions are a pure function of the clock passed to
``fetch``, so the same instant always gives the same payload, and the tests can drive it
with a fake clock. The scripted traffic covers every row style the table has: an inbound,
overhead and outbound pass, a 7700 squawk, a military aircraft, one on the ground, one whose
position has gone stale, and cruising traffic at several altitudes.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from adsbtui.config import Config

#: Where the demo sits when the config has no home (the repository's fixture location).
DEFAULT_HOME = (37.7749, -122.4194)

_MI_PER_DEG_LAT = 69.0
_MI_PER_DEG_LON_AT_EQUATOR = 69.17
_MPH_PER_KT = 1.150779


@dataclass(frozen=True)
class _Plan:
    """One aircraft flying back and forth along a straight line through the home point's
    neighbourhood: ``heading`` degrees, ``offset_mi`` to the right of the home point, over
    a ``span_mi``-long leg, wrapping to the start when it reaches the end."""

    hex: str
    flight: str
    heading: float
    offset_mi: float
    span_mi: float
    gs_kt: float
    alt_ft: int | str
    squawk: str = "1200"
    extra: tuple[tuple[str, Any], ...] = ()


_PLANS = (
    # The pass: in from the south-west, over the house, out to the north-east.
    _Plan("a10001", "UAL123", 45.0, 0.0, 24.0, 330.0, 4200, extra=(("baro_rate", -900),)),
    _Plan("a10002", "SWA2210", 90.0, 6.0, 30.0, 420.0, 32000),
    _Plan("a10003", "DAL88", 180.0, -9.0, 30.0, 450.0, 36000),
    _Plan("a10004", "N172SP", 270.0, 3.0, 16.0, 110.0, 2400, extra=(("category", "A1"),)),
    _Plan("a10005", "FDX771", 0.0, 12.0, 28.0, 380.0, 21000),
    _Plan("a10006", "JBU404", 135.0, -4.0, 26.0, 300.0, 9500, extra=(("baro_rate", 1800),)),
    _Plan("a10007", "ASA19", 225.0, 8.0, 28.0, 360.0, 14000),
    # The ones with something to say.
    _Plan("a10008", "MAYDAY1", 300.0, 5.0, 20.0, 240.0, 7000, squawk="7700"),
    _Plan("ae0001", "RCH345", 60.0, -7.0, 30.0, 400.0, 26000, extra=(("dbFlags", 1),)),
    _Plan("a10009", "TAXI9", 90.0, 1.5, 0.0, 12.0, "ground"),
    _Plan("a1000a", "OLD10", 0.0, -11.0, 20.0, 280.0, 18000, extra=(("seen_pos", 45.0),)),
    _Plan("a1000b", "N5Q", 200.0, 2.0, 18.0, 150.0, 5500),
)


class DemoSource:
    """Implements the Source protocol with scripted traffic around ``home``."""

    def __init__(
        self,
        home_lat: float = DEFAULT_HOME[0],
        home_lon: float = DEFAULT_HOME[1],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.home_lat = home_lat
        self.home_lon = home_lon
        self._clock = clock

    def fetch(self, timeout: float) -> dict:
        now = self._clock()
        aircraft = [self._record(plan, now) for plan in _PLANS]
        return {"now": now, "messages": int(now * 400) % 10**9, "aircraft": aircraft}

    def _record(self, plan: _Plan, now: float) -> dict[str, Any]:
        travelled = (plan.gs_kt * _MPH_PER_KT / 3600.0) * now
        along = (travelled % plan.span_mi) - plan.span_mi / 2 if plan.span_mi else 0.0
        theta = math.radians(plan.heading)
        # East and north components of "along the heading" plus "to the right of it".
        east = along * math.sin(theta) + plan.offset_mi * math.cos(theta)
        north = along * math.cos(theta) - plan.offset_mi * math.sin(theta)
        mi_per_deg_lon = _MI_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(self.home_lat))
        record: dict[str, Any] = {
            "hex": plan.hex,
            "type": "adsb_icao",
            "flight": plan.flight,
            "alt_baro": plan.alt_ft,
            "gs": plan.gs_kt,
            "track": plan.heading,
            "lat": self.home_lat + north / _MI_PER_DEG_LAT,
            "lon": self.home_lon + east / mi_per_deg_lon,
            "squawk": plan.squawk,
            "seen": 0.2,
            "seen_pos": 0.2,
            "messages": int(now) % 100000,
        }
        record.update(dict(plan.extra))
        return record


def apply_demo_settings(cfg: Config) -> None:
    """Make a config safe for a demo: nothing is sent, downloaded or written.

    A 7700 in the demo must never reach a real pager, and a demo must not leave files in
    the home directory. Every channel and every path that does either is switched off,
    whatever the config file says. A home position that was never set becomes the fixture's.
    """
    if cfg.home.lat == 0.0 and cfg.home.lon == 0.0:
        cfg.home.lat, cfg.home.lon = DEFAULT_HOME
    cfg.alerts.bell = False
    cfg.alerts.desktop = False
    cfg.alerts.webhook_url = ""
    cfg.alerts.command = ""
    cfg.registry.auto_update = False
    cfg.registry.db = ""
    cfg.history.db = ""
    cfg.watchlist.path = ""
    cfg.logging.file = ""

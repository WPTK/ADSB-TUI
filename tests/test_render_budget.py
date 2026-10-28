"""Render budget (A22): 2,000 aircraft on a 200x60 screen with the radar on.

Measured as CPU time, not wall time, so a busy shared runner does not fail it. The budgets
are about 5x what a laptop needs, so only a real regression (an accidental O(n^2), the
radar plotted per frame again) trips them.
"""

from __future__ import annotations

import curses
import random
import time

import pytest

from adsbtui.config import Config
from adsbtui.model import Aircraft
from adsbtui.ui.app import App

FRAMES = 20
#: CPU seconds per frame when every frame has a new aircraft list (a fetch just landed).
COLD_BUDGET_S = 0.040
#: CPU seconds per frame when only the clock ticked or a key moved the cursor.
WARM_BUDGET_S = 0.008


class _Source:
    stale_s = 15.0

    def fetch(self):  # pragma: no cover - never called
        raise AssertionError


class _Window:
    def getmaxyx(self):
        return (60, 200)

    def erase(self):
        pass

    def noutrefresh(self):
        pass

    def addstr(self, *args):
        pass


@pytest.fixture
def drawing(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    rng = random.Random(1)
    cfg = Config()
    cfg.home.lat, cfg.home.lon = 37.77, -122.42
    cfg.filter.radius = 150.0
    fleet = [
        Aircraft(
            hex=f"{i:06x}",
            is_icao=True,
            flight=f"AB{i}",
            registration=f"N{i}",
            type_code="B738",
            altitude_ft=float(rng.randint(1000, 40000)),
            ground_speed_kt=rng.uniform(100, 500),
            distance_mi=rng.uniform(1, 140),
            bearing_deg=rng.uniform(0, 360),
        )
        for i in range(2000)
    ]
    app = App(cfg, _Source())
    app._attrs = {}  # noqa: SLF001 - plain attributes: no curses colour setup needed
    return app, _Window(), fleet


def _cpu_per_frame(frame) -> float:
    frame()  # warm imports and caches
    start = time.process_time()
    for _ in range(FRAMES):
        frame()
    return (time.process_time() - start) / FRAMES


def test_a_frame_after_a_fetch_is_within_budget(drawing):
    app, window, fleet = drawing
    cost = _cpu_per_frame(lambda: app._draw(window, list(fleet), None, time.time(), False))  # noqa: SLF001
    assert cost < COLD_BUDGET_S, f"{cost * 1000:.1f} ms per frame"


def test_a_redraw_of_the_same_data_is_cheap(drawing):
    app, window, fleet = drawing
    cost = _cpu_per_frame(lambda: app._draw(window, fleet, None, time.time(), False))  # noqa: SLF001
    assert cost < WARM_BUDGET_S, f"{cost * 1000:.1f} ms per frame"


def test_the_radar_is_plotted_once_per_update(drawing, monkeypatch):
    from adsbtui.ui import radar

    app, window, fleet = drawing
    calls = []
    real = radar.render
    monkeypatch.setattr(radar, "render", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(5):
        app._draw(window, fleet, None, time.time(), False)  # noqa: SLF001
    assert len(calls) == 1

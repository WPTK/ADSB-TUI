"""Characterization test for ``App._draw``: the final screen, cell by cell, in golden files.

The UAT flows run the real program in a pty and stay the acceptance test. This one is for
refactors that must not change a single character (the D5 split): it calls the draw code
in-process with a fake window that records every ``addstr``, replays them onto a grid the
way curses would, and compares the result with ``tests/goldens/draw_log.jsonl``.

One line per screen row: ``[case, y, [[x, text, style], ...]]``, so a diff names the row and
the run that moved. Styles are logged by their theme name, not by curses attribute number.
A change that is meant (new layout, new glyph) rewrites the file with
``pytest tests/test_draw_log.py --update-goldens``; a refactor that needs that is wrong.
"""

from __future__ import annotations

import curses
import json
import re
from pathlib import Path

import pytest

from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel
from adsbtui.ui import theme
from adsbtui.ui.app import App
from adsbtui.ui.frame import Frame

GOLDEN = Path(__file__).parent / "goldens" / "draw_log.jsonl"
NOW = 1_700_000_000.0
SIZES = [(30, 6), (40, 8), (80, 24), (120, 30), (200, 60)]
STATES = [
    "empty",
    "fixture",
    "error",
    "stale",
    "paused",
    "ascii",
    "help",
    "detail",
    "sort",
    "settings",
]

#: Style name <-> a stand-in attribute number, so the log says "emergency", not "2097153".
_STYLE_ATTR = {name: i + 1 for i, name in enumerate(theme.ROW_STYLES)}
_ATTR_STYLE = {v: k for k, v in _STYLE_ATTR.items()} | {0: "-"}
_CLOCK = re.compile(r"\d\d:\d\d:\d\d")


class _Source:
    stale_s = 15.0

    def fetch(self, timeout):  # pragma: no cover - never called
        raise AssertionError


class RecordingWindow:
    """The part of a curses window ``_draw`` uses, with curses' wrap-at-the-edge behaviour."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cells: list[list[tuple[str, int]]] = []
        self.refreshes = 0
        self.erase()

    def getmaxyx(self):
        return (self.height, self.width)

    def erase(self):
        self.cells = [[(" ", 0)] * self.width for _ in range(self.height)]

    def noutrefresh(self):
        self.refreshes += 1

    def addstr(self, y, x, text, attr=0):
        if not (0 <= y < self.height and 0 <= x < self.width):
            raise curses.error("addstr() returned ERR")
        if "\0" in text:
            raise ValueError("embedded null character")
        for ch in text:
            if y >= self.height:
                raise curses.error("addstr() returned ERR")
            self.cells[y][x] = (ch, attr)
            x += 1
            if x == self.width:
                x, y = 0, y + 1

    def rows(self):
        """Per non-blank row: runs of (x, text, style name), merged where the style matches."""
        for y, row in enumerate(self.cells):
            runs: list[list] = []
            for x, (ch, attr) in enumerate(row):
                if ch == " " and attr == 0:
                    continue
                style = _ATTR_STYLE[attr]
                if runs and runs[-1][0] + len(runs[-1][1]) == x and runs[-1][2] == style:
                    runs[-1][1] += ch
                else:
                    runs.append([x, ch, style])
            if runs:
                yield y, runs


def _fleet() -> list[Aircraft]:
    def ac(hex_, flight, alt, dist, brg, **kw):
        return Aircraft(
            hex=hex_,
            is_icao=True,
            flight=flight,
            registration=kw.pop("registration", "N" + hex_[:4].upper()),
            type_code=kw.pop("type_code", "B738"),
            altitude_ft=alt,
            ground_speed_kt=kw.pop("gs", 250.0),
            track_deg=kw.pop("track", 90.0),
            distance_mi=dist,
            bearing_deg=brg,
            lat=37.77,
            lon=-122.4,
            seen_pos_s=kw.pop("seen_pos", 0.5),
            **kw,
        )

    return [
        ac("a00001", "OVHD1", 3000.0, 0.8, 10.0, alert_level=AlertLevel.OVERHEAD),
        ac("a00002", "MAYDAY", 8000.0, 4.0, 45.0, alert_level=AlertLevel.EMERGENCY),
        ac("ae0003", "RCH123", 28000.0, 12.0, 90.0, db_flags=1, type_code="C17"),
        ac("a00004", "WATCH4", 12000.0, 20.0, 135.0, is_watched=True),
        ac(
            "a00005",
            "INBD5",
            6000.0,
            30.0,
            180.0,
            alert_level=AlertLevel.INBOUND,
            baro_rate_fpm=-900,
        ),
        ac("a00006", "OLD6", 35000.0, 45.0, 225.0, is_stale=True, seen_pos=40.0),
        ac("a00007", "TAXI7", 0.0, 2.0, 270.0, on_ground=True, gs=12.0),
        ac(
            "a00008",
            "HIGH8",
            41000.0,
            90.0,
            315.0,
            owner_operator="Example Air",
            baro_rate_fpm=1500,
        ),
        ac("a00009", "LOW9", 900.0, 140.0, 350.0, type_code="C172", registration="N9LOW"),
    ]


def _app() -> App:
    cfg = Config()
    cfg.home.lat, cfg.home.lon = 37.7749, -122.4194
    cfg.filter.radius = 150.0
    cfg.registry.db = ""
    cfg.watchlist.path = ""
    app = App(cfg, _Source())
    app.renderer.attrs = dict(_STYLE_ATTR)
    return app


def render(app: App, window: RecordingWindow, state: str) -> None:
    """Put the app into ``state`` and draw once. The one place the test touches App."""
    fleet = [] if state in ("empty", "error") else _fleet()
    error = "connection refused" if state == "error" else None
    last_ok = None if state in ("empty", "error") else NOW - 3
    app.view.now = NOW
    if state == "stale":
        app.view.last_ok = Frame(1, NOW - 3, None, fleet, None, True)
    if state == "ascii":
        app.cfg.display.borders = "ascii"
    app.view.msg_rate = 321.0 if fleet else None
    app.view.cursor = 6 if fleet else 0
    if state in ("help", "detail", "sort", "settings"):
        app.view.rows = fleet
        app.controller.screens.open(state)
    app.view.rows = fleet
    app.view.paused = state == "paused"
    if error:
        app.view.frame = Frame(0, NOW, None, [], error, False)
    if last_ok is not None and app.view.last_ok is None:
        app.view.last_ok = Frame(0, last_ok, None, fleet, None, False)
    app._paint(window)  # noqa: SLF001


def _log() -> list[str]:
    lines = []
    for width, height in SIZES:
        for state in STATES:
            window = RecordingWindow(width, height)
            render(_app(), window, state)
            case = f"{width}x{height}/{state}"
            lines.append(json.dumps([case, "refresh", window.refreshes], ensure_ascii=False))
            for y, runs in window.rows():
                runs = [[x, _CLOCK.sub("HH:MM:SS", text), style] for x, text, style in runs]
                lines.append(json.dumps([case, y, runs], ensure_ascii=False))
    return lines


@pytest.fixture(autouse=True)
def _fixed_time(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)


def test_draw_matches_the_golden_log(request):
    lines = _log()
    if request.config.getoption("--update-goldens"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    want = GOLDEN.read_text(encoding="utf-8").splitlines()
    for i, (got, exp) in enumerate(zip(lines, want, strict=False)):
        assert got == exp, f"first difference at golden line {i + 1}:\n got  {got}\n want {exp}"
    assert len(lines) == len(want)


def test_the_golden_covers_every_case_and_style():
    text = GOLDEN.read_text(encoding="utf-8")
    for width, height in SIZES:
        for state in STATES:
            assert f'"{width}x{height}/{state}"' in text
    for style in ("emergency", "military", "watchlist", "overhead", "inbound", "stale", "selected"):
        assert f'"{style}"' in text, style

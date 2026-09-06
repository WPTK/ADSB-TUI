"""Tests for the App glue: filtering, alert dispatch, history recording, and the screen
stack.

These exercise App's methods directly without ever starting curses. That is possible
because the wiring deliberately keeps decisions (which aircraft to keep, whether an alert
should fire, when a track has closed, which screen to open) separate from drawing --
tests/test_tui_smoke.py covers the drawing half against a real terminal.
"""

from __future__ import annotations

import sqlite3

import pytest

from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel
from adsbtui.ui.app import App


class _StubSource:
    """A Source that never gets called: these tests drive App's methods directly."""

    def fetch(self, timeout):  # pragma: no cover - never invoked
        raise AssertionError("the stub source must not be fetched from")


def _config(**overrides) -> Config:
    cfg = Config()
    cfg.source.url = "tests/fixtures/aircraft.json"
    cfg.home.lat, cfg.home.lon = 37.7749, -122.4194
    cfg.watchlist.path = ""  # do not touch the developer's real watchlist file
    cfg.logging.file = ""  # keep tests from writing a log
    for dotted, value in overrides.items():
        section, key = dotted.split(".", 1)
        setattr(getattr(cfg, section), key, value)
    return cfg


def _app(cfg: Config | None = None) -> App:
    return App(cfg or _config(), _StubSource())


def _ac(hex_id="abc123", **kw) -> Aircraft:
    defaults = dict(is_icao=True, distance_mi=5.0, altitude_ft=10000.0)
    defaults.update(kw)
    return Aircraft(hex=hex_id, **defaults)


# ----------------------------------------------------------------------------------
# _apply_filters
# ----------------------------------------------------------------------------------


def test_filters_drop_aircraft_beyond_the_radius():
    app = _app(_config(**{"filter.radius": 10.0}))
    kept = app._apply_filters([_ac("aa", distance_mi=5.0), _ac("bb", distance_mi=25.0)])
    assert [a.hex for a in kept] == ["aa"]


def test_aircraft_with_no_position_is_dropped():
    app = _app()
    assert app._apply_filters([_ac("aa", distance_mi=None)]) == []


def test_emergency_bypasses_the_radius_when_configured():
    """A 7700 squawk twenty miles out is exactly what must not be filtered away."""
    cfg = _config(**{"filter.radius": 10.0})
    cfg.alerts.emergency_ignore_radius = True
    app = _app(cfg)
    far_emergency = _ac("aa", distance_mi=50.0, squawk="7700")
    assert [a.hex for a in app._apply_filters([far_emergency])] == ["aa"]


def test_emergency_does_not_bypass_the_radius_when_disabled():
    cfg = _config(**{"filter.radius": 10.0})
    cfg.alerts.emergency_ignore_radius = False
    app = _app(cfg)
    assert app._apply_filters([_ac("aa", distance_mi=50.0, squawk="7700")]) == []


def test_hide_ground_filter():
    app = _app(_config(**{"filter.hide_ground": True}))
    kept = app._apply_filters([_ac("air"), _ac("gnd", on_ground=True, altitude_ft=None)])
    assert [a.hex for a in kept] == ["air"]


def test_include_nonicao_false_drops_tisb_addresses():
    app = _app(_config(**{"filter.include_nonicao": False}))
    kept = app._apply_filters([_ac("aa"), _ac("~bb", is_icao=False)])
    assert [a.hex for a in kept] == ["aa"]


def test_altitude_band_filter():
    app = _app(_config(**{"filter.min_alt_ft": 5000, "filter.max_alt_ft": 20000}))
    kept = app._apply_filters(
        [
            _ac("low", altitude_ft=1000.0),
            _ac("mid", altitude_ft=10000.0),
            _ac("hi", altitude_ft=40000.0),
        ]
    )
    assert [a.hex for a in kept] == ["mid"]


def test_aircraft_without_an_altitude_survives_the_altitude_band():
    """Mode S targets often report no altitude at all; that is not a reason to hide them."""
    app = _app(_config(**{"filter.min_alt_ft": 5000, "filter.max_alt_ft": 20000}))
    assert [a.hex for a in app._apply_filters([_ac("aa", altitude_ft=None)])] == ["aa"]


@pytest.mark.parametrize(
    "needle,expected",
    [("ual", ["aa"]), ("N12", ["bb"]), ("boeing", []), ("", ["aa", "bb"])],
)
def test_search_text_matches_across_fields(needle, expected):
    app = _app()
    app._search_text = needle
    aircraft = [_ac("aa", flight="UAL123"), _ac("bb", registration="N12345")]
    assert [a.hex for a in app._apply_filters(aircraft)] == expected


# ----------------------------------------------------------------------------------
# _dispatch_alerts
# ----------------------------------------------------------------------------------


def test_alert_fires_once_on_transition_then_stays_quiet():
    app = _app()
    fired = []
    app._dispatcher.maybe_fire = lambda event: fired.append(event.event_name) or True

    ac = _ac("aa", alert_level=AlertLevel.OVERHEAD)
    app._dispatch_alerts([ac])
    app._dispatch_alerts([ac])  # same level: no new transition, so no second alert

    assert fired == ["proximity"]


def test_watchlist_match_fires_its_own_event():
    app = _app()
    fired = []
    app._dispatcher.maybe_fire = lambda event: fired.append(event.event_name) or True

    app._dispatch_alerts([_ac("aa", is_watched=True, alert_level=AlertLevel.NONE)])
    assert fired == ["watchlist"]


def test_dispatch_failure_does_not_propagate():
    """A broken webhook must never take down the fetch thread."""
    app = _app()

    def boom(event):
        raise RuntimeError("webhook exploded")

    app._dispatcher.maybe_fire = boom
    app._dispatch_alerts([_ac("aa", alert_level=AlertLevel.OVERHEAD)])  # must not raise


def test_departed_aircraft_are_forgotten_so_they_alert_again_on_return():
    app = _app()
    app._dispatch_alerts([_ac("aa", alert_level=AlertLevel.OVERHEAD)])
    assert "aa" in app._prev_levels
    app._dispatch_alerts([])
    assert app._prev_levels == {}


# ----------------------------------------------------------------------------------
# _record_tracks
# ----------------------------------------------------------------------------------


def test_track_is_written_to_history_only_once_it_drops(tmp_path):
    db = str(tmp_path / "history.sqlite")
    app = _app(_config(**{"history.db": db}))

    ac = _ac("aa", flight="UAL123", distance_mi=8.0, altitude_ft=10000.0, ground_speed_kt=400.0)
    ac.first_seen = ac.last_seen = 1000.0
    app._record_tracks({"aa": ac}, now=1000.0)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 0

    closer = _ac("aa", flight="UAL123", distance_mi=2.5, altitude_ft=12000.0, ground_speed_kt=450.0)
    closer.first_seen, closer.last_seen = 1000.0, 1010.0
    app._record_tracks({"aa": closer}, now=1010.0)
    app._record_tracks({}, now=1020.0)  # the aircraft aged out: the track closed

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT hex, min_distance_mi, max_altitude_ft, max_gs_kt FROM sightings"
        ).fetchone()
    assert row[0] == "aa"
    assert row[1] == pytest.approx(2.5)  # the closest approach across the whole track
    assert row[2] == pytest.approx(12000.0)  # the highest altitude seen
    assert row[3] == pytest.approx(450.0)


def test_history_is_a_no_op_when_disabled(tmp_path):
    app = _app(_config(**{"history.db": ""}))
    app._record_tracks({"aa": _ac("aa")}, now=1.0)
    app._record_tracks({}, now=2.0)
    assert app._track_stats == {}


def test_emergency_is_remembered_on_the_closed_track(tmp_path):
    db = str(tmp_path / "history.sqlite")
    app = _app(_config(**{"history.db": db}))
    ac = _ac("aa", squawk="7700", alert_level=AlertLevel.EMERGENCY)
    ac.first_seen = ac.last_seen = 5.0
    app._record_tracks({"aa": ac}, now=5.0)
    app._record_tracks({}, now=6.0)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT had_emergency FROM sightings").fetchone()[0] == 1


# ----------------------------------------------------------------------------------
# cursor / scrolling
# ----------------------------------------------------------------------------------


def test_cursor_and_scroll_clamp_to_the_list():
    app = _app()
    app._cursor = 99
    app._clamp_view(total=10, table_height=5)
    assert app._cursor == 9
    assert app._scroll == 5  # the window followed the cursor to the end

    app._cursor = 0
    app._clamp_view(total=10, table_height=5)
    assert app._scroll == 0


def test_empty_list_resets_the_view():
    app = _app()
    app._cursor, app._scroll = 4, 4
    app._clamp_view(total=0, table_height=5)
    assert (app._cursor, app._scroll) == (0, 0)


# ----------------------------------------------------------------------------------
# screen stack
# ----------------------------------------------------------------------------------


def test_opening_and_closing_the_sort_screen_applies_its_result():
    app = _app()
    app._open_screen("sort", [])
    assert app._screen is not None

    # Drive the screen the way a user would: pick the next sort key, then apply.
    app._screen._key_select.handle_key(ord(" "))
    app._screen.handle_key(ord("\n"))
    app._close_screen()

    assert app._screen is None
    assert app.cfg.display.sort_key == "altitude"


def test_detail_screen_needs_a_selected_aircraft():
    app = _app()
    app._open_screen("detail", [])
    assert app._screen is None  # nothing selected, so nothing to show

    app._open_screen("detail", [_ac("aa", flight="UAL123")])
    assert app._screen is not None
    assert "UAL123" in app._screen.title


def test_filters_screen_result_lands_on_the_live_config():
    app = _app()
    app._open_screen("filters", [])
    app._screen._hide_ground.handle_key(ord(" "))  # flip the toggle
    app._screen.handle_key(ord("\n"))  # apply
    app._close_screen()
    assert app.cfg.filter.hide_ground is True


def test_escaping_a_screen_changes_nothing():
    app = _app()
    before = app.cfg.display.sort_key
    app._open_screen("sort", [])
    app._screen._key_select.handle_key(ord(" "))  # move the selection...
    app._screen.handle_key(27)  # ...then escape instead of applying
    app._close_screen()
    assert app.cfg.display.sort_key == before


def test_unknown_screen_name_is_ignored():
    app = _app()
    app._open_screen("does-not-exist", [])
    assert app._screen is None

"""The shared per-tick pipeline, the dispatcher worker, and the TUI fetch loop's scheduling.

The filter, alert and history tests used to run against `App` and again (differently)
against `HeadlessRunner`; they now run against the one `Pipeline` both of them call.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time

import pytest

from adsbtui import dispatch, enrich, output
from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel
from adsbtui.pipeline import Pipeline, apply_filters
from adsbtui.ui.app import App


def _config(**overrides) -> Config:
    cfg = Config()
    cfg.source.url = "tests/fixtures/aircraft.json"
    cfg.home.lat, cfg.home.lon = 37.7749, -122.4194
    cfg.watchlist.path = ""
    cfg.logging.file = ""
    cfg.history.db = ""
    for dotted, value in overrides.items():
        section, key = dotted.split(".", 1)
        setattr(getattr(cfg, section), key, value)
    return cfg


def _ac(hex_id="abc123", **kw) -> Aircraft:
    defaults = dict(is_icao=True, distance_mi=5.0, altitude_ft=10000.0)
    defaults.update(kw)
    return Aircraft(hex=hex_id, **defaults)


def _pipeline(cfg: Config | None = None, notify=None) -> Pipeline:
    return Pipeline(cfg or _config(), enricher=enrich.Enricher([]), notify=notify)


# ----------------------------------------------------------------------------------
# apply_filters
# ----------------------------------------------------------------------------------


def test_filters_drop_aircraft_beyond_the_radius():
    cfg = _config(**{"filter.radius": 10.0})
    kept = apply_filters([_ac("aa", distance_mi=5.0), _ac("bb", distance_mi=25.0)], cfg)
    assert [a.hex for a in kept] == ["aa"]


def test_aircraft_with_no_position_is_dropped():
    assert apply_filters([_ac("aa", distance_mi=None)], _config()) == []


def test_emergency_bypasses_the_radius_when_configured():
    """A 7700 squawk twenty miles out is exactly what must not be filtered away."""
    cfg = _config(**{"filter.radius": 10.0})
    cfg.alerts.emergency_ignore_radius = True
    far_emergency = _ac("aa", distance_mi=50.0, squawk="7700")
    assert [a.hex for a in apply_filters([far_emergency], cfg)] == ["aa"]


def test_emergency_does_not_bypass_the_radius_when_disabled():
    cfg = _config(**{"filter.radius": 10.0})
    cfg.alerts.emergency_ignore_radius = False
    assert apply_filters([_ac("aa", distance_mi=50.0, squawk="7700")], cfg) == []


def test_hide_ground_filter():
    cfg = _config(**{"filter.hide_ground": True})
    kept = apply_filters([_ac("air"), _ac("gnd", on_ground=True, altitude_ft=None)], cfg)
    assert [a.hex for a in kept] == ["air"]


def test_include_nonicao_false_drops_tisb_addresses():
    cfg = _config(**{"filter.include_nonicao": False})
    kept = apply_filters([_ac("aa"), _ac("~bb", is_icao=False)], cfg)
    assert [a.hex for a in kept] == ["aa"]


def test_altitude_band_filter():
    cfg = _config(**{"filter.min_alt_ft": 5000, "filter.max_alt_ft": 20000})
    kept = apply_filters(
        [
            _ac("low", altitude_ft=1000.0),
            _ac("mid", altitude_ft=10000.0),
            _ac("hi", altitude_ft=40000.0),
        ],
        cfg,
    )
    assert [a.hex for a in kept] == ["mid"]


def test_aircraft_without_an_altitude_survives_the_altitude_band():
    """Mode S targets often report no altitude at all; that is not a reason to hide them."""
    cfg = _config(**{"filter.min_alt_ft": 5000, "filter.max_alt_ft": 20000})
    assert [a.hex for a in apply_filters([_ac("aa", altitude_ft=None)], cfg)] == ["aa"]


@pytest.mark.parametrize(
    "needle,expected",
    [("ual", ["aa"]), ("N12", ["bb"]), ("boeing", []), ("", ["aa", "bb"])],
)
def test_search_text_matches_across_fields(needle, expected):
    aircraft = [_ac("aa", flight="UAL123"), _ac("bb", registration="N12345")]
    assert [a.hex for a in apply_filters(aircraft, _config(), needle)] == expected


# ----------------------------------------------------------------------------------
# alert dispatch
# ----------------------------------------------------------------------------------


def test_alert_fires_once_on_transition_then_stays_quiet():
    fired: list[str] = []
    pipe = _pipeline(notify=lambda event: fired.append(event.event_name))

    ac = _ac("aa", alert_level=AlertLevel.OVERHEAD)
    pipe._dispatch_alerts([ac])
    pipe._dispatch_alerts([ac])  # same level: no new transition, so no second alert

    assert fired == ["proximity"]


def test_watchlist_match_fires_its_own_event():
    fired: list[str] = []
    pipe = _pipeline(notify=lambda event: fired.append(event.event_name))
    pipe._dispatch_alerts([_ac("aa", is_watched=True, alert_level=AlertLevel.NONE)])
    assert fired == ["watchlist"]


def test_a_failing_notify_does_not_propagate():
    """A broken webhook must never end the tick."""

    def boom(event):
        raise RuntimeError("webhook exploded")

    pipe = _pipeline(notify=boom)
    events = pipe._dispatch_alerts([_ac("aa", alert_level=AlertLevel.OVERHEAD)])  # must not raise
    assert len(events) == 1


def test_departed_aircraft_are_forgotten_so_they_alert_again_on_return():
    pipe = _pipeline()
    pipe._dispatch_alerts([_ac("aa", alert_level=AlertLevel.OVERHEAD)])
    assert "aa" in pipe._prev_levels
    pipe._dispatch_alerts([])
    assert pipe._prev_levels == {}


# ----------------------------------------------------------------------------------
# sighting history
# ----------------------------------------------------------------------------------


def test_track_is_written_to_history_only_once_it_drops(tmp_path):
    db = str(tmp_path / "history.sqlite")
    pipe = _pipeline(_config(**{"history.db": db}))

    ac = _ac("aa", flight="UAL123", distance_mi=8.0, altitude_ft=10000.0, ground_speed_kt=400.0)
    ac.first_seen = ac.last_seen = 1000.0
    assert pipe._record_tracks({"aa": ac}, now=1000.0) == 0

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 0

    closer = _ac("aa", flight="UAL123", distance_mi=2.5, altitude_ft=12000.0, ground_speed_kt=450.0)
    closer.first_seen, closer.last_seen = 1000.0, 1010.0
    pipe._record_tracks({"aa": closer}, now=1010.0)
    assert pipe._record_tracks({}, now=1020.0) == 1  # the aircraft aged out: the track closed

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT hex, min_distance_mi, max_altitude_ft, max_gs_kt FROM sightings"
        ).fetchone()
    assert row[0] == "aa"
    assert row[1] == pytest.approx(2.5)  # the closest approach across the whole track
    assert row[2] == pytest.approx(12000.0)  # the highest altitude seen
    assert row[3] == pytest.approx(450.0)


def test_history_is_a_no_op_when_disabled():
    pipe = _pipeline()
    pipe._record_tracks({"aa": _ac("aa")}, now=1.0)
    pipe._record_tracks({}, now=2.0)
    assert pipe._track_stats == {}


def test_emergency_is_remembered_on_the_closed_track(tmp_path):
    db = str(tmp_path / "history.sqlite")
    pipe = _pipeline(_config(**{"history.db": db}))
    ac = _ac("aa", squawk="7700", alert_level=AlertLevel.EMERGENCY)
    ac.first_seen = ac.last_seen = 5.0
    pipe._record_tracks({"aa": ac}, now=5.0)
    pipe._record_tracks({}, now=6.0)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT had_emergency FROM sightings").fetchone()[0] == 1


# ----------------------------------------------------------------------------------
# one pipeline, two front ends
# ----------------------------------------------------------------------------------


def _feed(*aircraft: dict) -> dict:
    return {"now": 1000.0, "messages": 5, "aircraft": list(aircraft)}


def _plane(hex_id: str, lat: float, **extra) -> dict:
    base = {"hex": hex_id, "type": "adsb_icao", "flight": "TEST1", "lat": lat}
    return {**base, "lon": -122.4194, "alt_baro": 9000, "seen": 0.1, "seen_pos": 0.1, **extra}


class _OneShot:
    def __init__(self, payload):
        self.payload = payload

    def fetch(self, timeout):
        return self.payload


def test_the_tui_and_headless_runner_agree_on_the_same_payload():
    """The two front ends used to carry separate copies of this logic (A34)."""
    payload = _feed(
        _plane("a1b2c1", 37.78),
        _plane("a1b2c2", 40.0),
        _plane("a1b2c3", 37.79, squawk="7700"),
    )

    headless = output.HeadlessRunner(_config(), _OneShot(payload)).tick()

    app = App(_config(), _OneShot(payload), clock=lambda: 1000.0)
    tui = app._pipeline.tick(payload, 1000.0)

    assert sorted(a.hex for a in tui.aircraft) == ["a1b2c1", "a1b2c3"]
    assert sorted(a.hex for a in headless.aircraft) == sorted(a.hex for a in tui.aircraft)
    assert [e.event_name for e in headless.events] == [e.event_name for e in tui.events]
    assert [e.event_name for e in tui.events]  # the 7700 squawk raised an emergency


def test_tick_is_deterministic_for_a_given_clock():
    """The clock is an argument, so a tick far in the "future" ages tracks out at once."""
    cfg = _config(**{"display.linger_s": 5.0})
    pipe = _pipeline(cfg)
    first = pipe.tick(_feed(_plane("a1b2c1", 37.78)), 1000.0)
    assert [a.hex for a in first.aircraft] == ["a1b2c1"]
    later = pipe.tick(_feed(), 1100.0)
    assert later.aircraft == []


def test_a_narrower_radius_drops_the_tracks_it_filters_out_at_once():
    """Without the prune a filtered-out aircraft lingers for display.linger_s (A52)."""
    pipe = _pipeline(_config(**{"display.linger_s": 30.0}))
    payload = _feed(_plane("a1b2c1", 37.78), _plane("a1b2c2", 37.9))
    assert len(pipe.tick(payload, 1000.0).aircraft) == 2
    pipe.cfg.filter.radius = 5.0
    still = pipe.tick(payload, 1001.0)
    assert sorted(a.hex for a in still.aircraft) == ["a1b2c1", "a1b2c2"]  # lingering, is_lost
    pipe.drop_filtered_lingerers()
    assert [a.hex for a in pipe.tick(payload, 1002.0).aircraft] == ["a1b2c1"]


# ----------------------------------------------------------------------------------
# DispatchWorker (A23)
# ----------------------------------------------------------------------------------


def _event(hex_id="aa") -> dispatch.AlertEvent:
    return dispatch.AlertEvent(hex_id, AlertLevel.OVERHEAD, _ac(hex_id), "proximity")


def test_a_slow_channel_does_not_block_submit():
    release = threading.Event()
    done: list[str] = []

    def slow(event):
        release.wait(5)
        done.append(event.hex)

    worker = dispatch.DispatchWorker(slow)
    try:
        started = time.monotonic()
        for h in ("a", "b", "c"):
            worker.submit(_event(h))
        assert time.monotonic() - started < 0.5  # enqueue only; the 5 s wait is on the worker
        assert done == []
    finally:
        release.set()
        worker.close()
    assert done == ["a", "b", "c"]


def test_a_failing_channel_does_not_stop_the_worker():
    seen: list[str] = []

    def fire(event):
        if event.hex == "bad":
            raise RuntimeError("down")
        seen.append(event.hex)

    worker = dispatch.DispatchWorker(fire)
    worker.submit(_event("bad"))
    worker.submit(_event("good"))
    worker.close()
    assert seen == ["good"]


def test_a_full_queue_drops_rather_than_blocks():
    release = threading.Event()
    worker = dispatch.DispatchWorker(lambda event: release.wait(5), maxsize=1)
    try:
        started = time.monotonic()
        for i in range(10):
            worker.submit(_event(str(i)))
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        worker.close()


# ----------------------------------------------------------------------------------
# fetch-loop scheduling (A24)
# ----------------------------------------------------------------------------------


def test_the_poll_period_is_refresh_s_not_fetch_time_plus_refresh_s():
    cfg = _config(**{"source.refresh_s": 0.4})
    stamps: list[float] = []

    class Slow:
        def fetch(self, timeout):
            stamps.append(time.monotonic())
            time.sleep(0.25)  # a fetch that eats most of the period
            return _feed()

    app = App(cfg, Slow())
    stop, out = threading.Event(), queue.Queue()
    thread = threading.Thread(target=app._fetch_loop, args=(stop, out), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while len(stamps) < 4 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(timeout=5)
        app._dispatch_worker.close()
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert len(gaps) >= 3
    assert all(g < 0.55 for g in gaps), gaps  # 0.25 + 0.4 = 0.65 under the old scheme


# ----------------------------------------------------------------------------------
# stale feed (A08)
# ----------------------------------------------------------------------------------


def test_a_frozen_feed_is_reported_stale_after_stale_s():
    pipe = _pipeline(_config(**{"source.stale_s": 30.0}))
    payload = _feed(_plane("a1b2c1", 37.78))
    assert not pipe.tick(payload, 1000.0).feed_stale
    assert not pipe.tick(payload, 1020.0).feed_stale  # same `now`, but within stale_s
    late = pipe.tick(payload, 1031.0)
    assert late.feed_stale
    assert all(ac.is_stale for ac in late.aircraft)


def test_an_advancing_feed_is_never_stale():
    pipe = _pipeline(_config(**{"source.stale_s": 30.0}))
    for i in range(5):
        payload = {**_feed(_plane("a1b2c1", 37.78)), "now": 1000.0 + i * 100}
        assert not pipe.tick(payload, 1000.0 + i * 100).feed_stale


def test_a_feed_that_resumes_stops_being_stale():
    pipe = _pipeline(_config(**{"source.stale_s": 30.0}))
    payload = _feed(_plane("a1b2c1", 37.78))
    pipe.tick(payload, 1000.0)
    assert pipe.tick(payload, 1100.0).feed_stale
    assert not pipe.tick({**payload, "now": 1101.0}, 1101.0).feed_stale

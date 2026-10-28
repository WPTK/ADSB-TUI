"""ViewState: the main-thread state, driven with a fake clock and hand-made frames."""

from __future__ import annotations

from adsbtui.config import Config
from adsbtui.model import Aircraft, Snapshot
from adsbtui.ui.frame import Frame
from adsbtui.ui.view import ViewState


def _ac(hex_: str) -> Aircraft:
    return Aircraft(hex=hex_, is_icao=True)


def _frame(seq, at, rows=(), error=None, stale=False, messages=None):
    snap = None if error else Snapshot(now=at, messages=messages, raw_aircraft=[], fetched_at=at)
    return Frame(seq, at, snap, list(rows), error, stale)


def test_a_healthy_frame_replaces_the_rows_and_an_error_frame_keeps_them():
    view = ViewState(Config())
    view.apply(_frame(1, 100.0, [_ac("a")]))
    view.apply(_frame(2, 105.0, error="down"))
    assert [a.hex for a in view.rows] == ["a"]
    assert view.error == "down" and view.last_ok.seq == 1


def test_paused_keeps_the_rows_but_still_records_the_frame():
    view = ViewState(Config())
    view.apply(_frame(1, 100.0, [_ac("a")]))
    view.paused = True
    view.apply(_frame(2, 105.0, [_ac("b")]))
    assert [a.hex for a in view.rows] == ["a"]
    assert view.last_ok.seq == 2


def test_the_age_comes_from_the_injected_time_only():
    view = ViewState(Config())
    assert view.last_success_age() is None
    view.apply(_frame(1, 100.0))
    view.tick(107.5)
    assert view.last_success_age() == 7.5


def test_feed_stale_survives_an_error_frame_like_the_old_attribute():
    view = ViewState(Config())
    view.apply(_frame(1, 100.0, stale=True))
    view.apply(_frame(2, 105.0, error="down"))
    assert view.feed_stale is True


def test_message_rate_resets_when_the_counter_goes_backwards():
    view = ViewState(Config())
    view.apply(_frame(1, 100.0, messages=1000))
    view.apply(_frame(2, 110.0, messages=2000))
    assert view.msg_rate == 100.0
    view.apply(_frame(3, 120.0, messages=5))
    assert view.msg_rate is None


def test_a_flash_lasts_six_seconds_of_view_time_then_the_registry_note_shows():
    view = ViewState(Config())
    view.registry_note = "registry 40%"
    view.tick(1000.0)
    view.flash_for("settings saved")
    view.tick(1005.9)
    assert view.note() == "settings saved"
    view.tick(1006.0)
    assert view.note() == "registry 40%" and view.flash is None


def test_dirty_is_set_when_the_second_changes():
    view = ViewState(Config())
    view.tick(50.2)
    view.dirty = False
    view.tick(50.9)
    assert view.dirty is False
    view.tick(51.0)
    assert view.dirty is True

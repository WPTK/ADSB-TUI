"""Tests for the App glue: the screen stack, sorting, settings and the fetch loop.

These exercise App's methods directly without ever starting curses. That is possible
because the wiring deliberately keeps decisions (which screen to open, how to sort)
separate from drawing -- tests/uat covers the drawing half against a real terminal.
"""

from __future__ import annotations

import pytest

from adsbtui.config import Config
from adsbtui.model import Aircraft, AlertLevel
from adsbtui.pipeline import enrich_all
from adsbtui.ui.app import App
from adsbtui.ui.sorting import SORT_KEYS, sort_aircraft


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


def _open(app: App, name: str, rows: list) -> None:
    app.view.rows = rows
    app.controller.screens.open(name)


def _ac(hex_id="abc123", **kw) -> Aircraft:
    defaults = dict(is_icao=True, distance_mi=5.0, altitude_ft=10000.0)
    defaults.update(kw)
    return Aircraft(hex=hex_id, **defaults)


# ----------------------------------------------------------------------------------
# cursor / scrolling
# ----------------------------------------------------------------------------------


def test_cursor_and_scroll_clamp_to_the_list():
    app = _app()
    app.view.cursor = 99
    app.view.clamp(total=10, table_height=5)
    assert app.view.cursor == 9
    assert app.view.scroll == 5  # the window followed the cursor to the end

    app.view.cursor = 0
    app.view.clamp(total=10, table_height=5)
    assert app.view.scroll == 0


def test_empty_list_resets_the_view():
    app = _app()
    app.view.cursor, app.view.scroll = 4, 4
    app.view.clamp(total=0, table_height=5)
    assert (app.view.cursor, app.view.scroll) == (0, 0)


# ----------------------------------------------------------------------------------
# screen stack
# ----------------------------------------------------------------------------------


def test_opening_and_closing_the_sort_screen_applies_its_result():
    app = _app()
    _open(app, "sort", [])
    assert app.view.screen is not None

    # Drive the screen the way a user would: pick the next sort key, then apply.
    app.view.screen._key_select.handle_key(ord(" "))
    app.view.screen.handle_key(ord("\n"))
    app.controller.screens.close()

    assert app.view.screen is None
    assert app.cfg.display.sort_key == "altitude"


def test_detail_screen_needs_a_selected_aircraft():
    app = _app()
    _open(app, "detail", [])
    assert app.view.screen is None  # nothing selected, so nothing to show

    _open(app, "detail", [_ac("aa", flight="UAL123")])
    assert app.view.screen is not None
    assert "UAL123" in app.view.screen.title


def test_filters_screen_result_lands_on_the_live_config():
    app = _app()
    _open(app, "filters", [])
    app.view.screen._hide_ground.handle_key(ord(" "))  # flip the toggle
    app.view.screen.handle_key(ord("\n"))  # apply
    app.controller.screens.close()
    assert app.cfg.filter.hide_ground is True


def test_escaping_a_screen_changes_nothing():
    app = _app()
    before = app.cfg.display.sort_key
    _open(app, "sort", [])
    app.view.screen._key_select.handle_key(ord(" "))  # move the selection...
    app.view.screen.handle_key(27)  # ...then escape instead of applying
    app.controller.screens.close()
    assert app.cfg.display.sort_key == before


def test_unknown_screen_name_is_ignored():
    app = _app()
    _open(app, "does-not-exist", [])
    assert app.view.screen is None


# ----------------------------------------------------------------------------------
# Sorting
# ----------------------------------------------------------------------------------

#: One (lower, higher) pair of Aircraft kwargs per SORT_KEYS entry. "aaaaaa" always gets
#: the lower-kwargs half and "zzzzzz" the higher-kwargs half, so a correct ascending sort
#: always produces ["aaaaaa", "zzzzzz"] regardless of which field is under test -- "hex"
#: needs no override at all, since aaaaaa < zzzzzz is already the fact being sorted on.
_SORT_FIELD_CASES: dict[str, tuple[dict, dict]] = {
    "distance": (dict(distance_mi=1.0), dict(distance_mi=9.0)),
    "altitude": (dict(altitude_ft=1000.0), dict(altitude_ft=9000.0)),
    "callsign": (dict(flight="AAL1"), dict(flight="UAL1")),
    "reg": (dict(registration="N1"), dict(registration="N9")),
    "type": (dict(type_code="A320"), dict(type_code="B738")),
    "gs": (dict(ground_speed_kt=100.0), dict(ground_speed_kt=400.0)),
    "vs": (dict(baro_rate_fpm=-500.0), dict(baro_rate_fpm=500.0)),
    "brg": (dict(bearing_deg=10.0), dict(bearing_deg=200.0)),
    "cpa": (dict(cpa_seconds=30.0), dict(cpa_seconds=300.0)),
    "owner": (dict(owner_name="AAA Co"), dict(owner_name="ZZZ Co")),
    "flags": (dict(db_flags=0x1), dict(db_flags=0x1 | 0x4)),  # "MIL" < "MIL PIA"
    "age": (dict(seen_pos_s=1.0), dict(seen_pos_s=100.0)),
    "alert": (dict(alert_level=AlertLevel.OUTBOUND), dict(alert_level=AlertLevel.EMERGENCY)),
    "hex": ({}, {}),
}


def test_sort_field_cases_cover_every_sort_key():
    """Guards the test data itself: every key SORT_KEYS offers must have a case above, and
    vice versa, or this file's coverage of "every field is sortable" silently rots."""
    assert set(_SORT_FIELD_CASES) == {key for key, _label in SORT_KEYS}


@pytest.mark.parametrize("sort_key", sorted(_SORT_FIELD_CASES))
def test_every_sort_key_orders_ascending_and_descending(sort_key):
    lower_kwargs, higher_kwargs = _SORT_FIELD_CASES[sort_key]
    lower = _ac("aaaaaa", **lower_kwargs)
    higher = _ac("zzzzzz", **higher_kwargs)

    ascending = sort_aircraft([higher, lower], sort_key, reverse=False)
    assert [ac.hex for ac in ascending] == ["aaaaaa", "zzzzzz"]

    descending = sort_aircraft([higher, lower], sort_key, reverse=True)
    assert [ac.hex for ac in descending] == ["zzzzzz", "aaaaaa"]


@pytest.mark.parametrize("sort_key", ["distance", "reg", "owner", "flags", "cpa"])
def test_aircraft_with_no_sort_value_always_sorts_last(sort_key):
    with_value_kwargs, _ = _SORT_FIELD_CASES[sort_key]
    with_value = _ac("has", **with_value_kwargs)
    without_value = _ac(
        "none",
        distance_mi=None,
        registration=None,
        owner_name=None,
        owner_operator=None,
        db_flags=0,
        cpa_seconds=None,
    )

    for reverse in (False, True):
        ordered = sort_aircraft([without_value, with_value], sort_key, reverse=reverse)
        assert [ac.hex for ac in ordered] == ["has", "none"], (sort_key, reverse)


def test_alert_level_none_sorts_by_rank_not_shoved_to_the_end():
    """Unlike reg/owner/flags/cpa, alert_level is never missing -- it defaults to NONE,
    which is a real, ranked value (rank 0, the lowest) and must sort accordingly rather
    than being treated as "no value" the way the fields above are."""
    none_level = _ac("none-level", alert_level=AlertLevel.NONE)
    emergency = _ac("emergency", alert_level=AlertLevel.EMERGENCY)

    ascending = sort_aircraft([emergency, none_level], "alert", reverse=False)
    assert [ac.hex for ac in ascending] == ["none-level", "emergency"]

    descending = sort_aircraft([emergency, none_level], "alert", reverse=True)
    assert [ac.hex for ac in descending] == ["emergency", "none-level"]


def test_cpa_sort_hides_ground_aircraft_like_the_column_does():
    airborne = _ac("air", cpa_seconds=120.0, on_ground=False)
    # A shorter cpa_seconds would otherwise sort first -- but a taxiing aircraft's
    # extrapolated closest approach is exactly what the "cpa" column itself hides.
    taxiing = _ac("taxi", cpa_seconds=10.0, on_ground=True)
    ordered = sort_aircraft([taxiing, airborne], "cpa", reverse=False)
    assert [ac.hex for ac in ordered] == ["air", "taxi"]


def test_sort_keys_keeps_distance_then_altitude_first():
    """test_opening_and_closing_the_sort_screen_applies_its_result (above) presses the
    key picker's "next" key exactly once from the default ("distance") and expects to
    land on "altitude" -- this only holds if the two stay adjacent in that order."""
    assert [key for key, _label in SORT_KEYS[:2]] == ["distance", "altitude"]


# ----------------------------------------------------------------------------------
# Enrichment and the settings/data screens
# ----------------------------------------------------------------------------------


def test_enricher_fills_registration_and_country_with_no_databases_at_all():
    """The derived provider needs no files, so a bare install still identifies US
    aircraft rather than showing a hex code and nothing else."""
    app = _app()
    ac = _ac("a004b3")  # a real US civil hex
    enrich_all([ac], app._pipeline.enricher)
    assert ac.registration is not None


def test_enrichment_never_overwrites_what_the_receiver_sent():
    """The feed is the most authoritative source: a receiver running its own database
    knows better than anything we derive."""
    app = _app()
    ac = _ac("a004b3", registration="N-FROM-FEED", type_code="C172")
    enrich_all([ac], app._pipeline.enricher)
    assert ac.registration == "N-FROM-FEED"
    assert ac.type_code == "C172"


def test_a_failing_lookup_cannot_break_the_fetch_loop():
    app = _app()

    class Boom:
        def lookup(self, aircraft):
            raise RuntimeError("provider exploded")

    app._pipeline.enricher = Boom()
    enrich_all([_ac("a004b3")], app._pipeline.enricher)  # must not raise


def test_settings_screen_opens_and_applying_adopts_the_new_config():
    app = _app()
    _open(app, "settings", [])
    assert app.view.screen is not None

    edited = app.view.screen.result()
    edited.filter.radius = 42.0
    app.view.screen.result = lambda: edited
    app.view.screen.applied = True
    app.view.screen.save_requested = False
    app.controller.screens.close()

    assert app.cfg.filter.radius == 42.0


def test_cancelled_settings_screen_changes_nothing():
    app = _app()
    before = app.cfg.filter.radius
    _open(app, "settings", [])
    app.view.screen.applied = False
    app.controller.screens.close()
    assert app.cfg.filter.radius == before


def test_saving_settings_writes_a_config_file_that_loads_back(tmp_path):
    import tomllib

    app = _app()
    target = tmp_path / "config.toml"
    app.cfg._config_path = str(target)

    _open(app, "settings", [])
    edited = app.view.screen.result()
    edited.filter.radius = 23.0
    app.view.screen.result = lambda: edited
    app.view.screen.applied = True
    app.view.screen.save_requested = True
    app.controller.screens.close()

    assert target.exists()
    with open(target, "rb") as handle:
        written = tomllib.load(handle)
    assert written["filter"]["radius"] == 23.0


def test_data_screen_success_rebuilds_the_enricher():
    """A finished download changes what can be resolved, so the session must not keep
    querying the databases it started with."""
    app = _app()
    _open(app, "data", [])
    before = app._pipeline.enricher

    from adsbtui.ui.screens.data import UpdateOutcome

    app.view.screen.take_finished = lambda: [
        UpdateOutcome(source=None, ok=True, row_count=10, message="done")
    ]
    app.controller.screens.close()
    assert app._pipeline.enricher is not before


def test_data_screen_failure_leaves_the_enricher_alone():
    app = _app()
    _open(app, "data", [])
    before = app._pipeline.enricher

    from adsbtui.ui.screens.data import UpdateOutcome

    app.view.screen.take_finished = lambda: [
        UpdateOutcome(source=None, ok=False, row_count=None, message="download failed")
    ]
    app.controller.screens.close()
    assert app._pipeline.enricher is before


def test_a_bug_in_the_fetch_loop_becomes_an_error_banner_not_a_dead_thread(monkeypatch):
    """The catch-all in _fetch_loop is reachable (it used to hide behind `pragma: no cover`)."""
    import queue
    import threading

    from adsbtui import normalize

    class Source:
        def fetch(self, timeout):
            return {"now": 1.0, "aircraft": [{"hex": "abc123"}]}

    def boom(raw):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(normalize, "parse_aircraft", boom)
    app = App(_config(), Source())
    stop, out = threading.Event(), queue.Queue()
    thread = threading.Thread(target=app._fetch_loop, args=(stop, out), daemon=True)
    thread.start()
    try:
        frame = out.get(timeout=5)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert frame.snapshot is None and frame.aircraft == []
    assert "parser exploded" in frame.error


def _save_settings(app, edit):
    _open(app, "settings", [])
    edited = app.view.screen.result()
    edit(edited)
    app.view.screen.result = lambda: edited
    app.view.screen.applied = True
    app.view.screen.save_requested = True
    app.controller.screens.close()


def test_a_failed_settings_save_is_shown_in_the_status_line(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", "utf-8")
    app = _app()
    app.cfg._config_path = str(blocker / "config.toml")  # parent is a file: unwritable
    _save_settings(app, lambda c: setattr(c.filter, "radius", 23.0))
    assert "could not save settings" in (app.view.note() or "")
    assert app.cfg.filter.radius == 23.0  # applied for the session anyway


def test_a_successful_settings_save_says_so(tmp_path):
    app = _app()
    app.cfg._config_path = str(tmp_path / "config.toml")
    _save_settings(app, lambda c: setattr(c.filter, "radius", 23.0))
    assert app.view.note() == "settings saved"


def test_settings_save_writes_only_the_file_layer_and_the_edit(tmp_path):
    import tomllib

    target = tmp_path / "config.toml"
    target.write_text('[source]\nurl = "http://file/aircraft.json"\n', "utf-8")
    app = _app()  # its source.url is a different, command-line-style value
    app.cfg._config_path = str(target)
    app.cfg._file_layer = {"source": {"url": "http://file/aircraft.json"}}
    _save_settings(app, lambda c: setattr(c.display, "units", "metric"))
    written = tomllib.loads(target.read_text("utf-8"))
    assert written["source"] == {"url": "http://file/aircraft.json"}
    assert written["display"] == {"units": "metric"}
    assert "home" not in written

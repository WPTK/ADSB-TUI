"""Controller: keys in, view changes and Effects calls out, with a fake Effects and no curses."""

from __future__ import annotations

from adsbtui.config import Config
from adsbtui.model import Aircraft
from adsbtui.ui.controller import _HANDLERS, Controller, Outcome
from adsbtui.ui.keys import BINDINGS
from adsbtui.ui.view import ViewState

KEY = {b.action: b.keys[0] for b in BINDINGS}


class FakeEffects:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.watch_entries: list = []
        self.saved: list = []
        self.save_error: str | None = None

    def refresh_now(self) -> None:
        self.calls.append("refresh_now")

    def set_enricher(self) -> None:
        self.calls.append("set_enricher")

    def set_dispatcher(self) -> None:
        self.calls.append("set_dispatcher")

    def set_watch_entries(self, entries) -> None:
        self.calls.append("set_watch_entries")
        self.watch_entries = entries

    def set_pipeline_cfg(self, cfg) -> None:
        self.calls.append("set_pipeline_cfg")

    def save_config(self, cfg, edited):
        self.saved.append(set(edited))
        return self.save_error


class _Layout:
    table_height = 5


def _rows(n: int) -> list[Aircraft]:
    return [Aircraft(hex=f"{i:06x}", is_icao=True) for i in range(n)]


def _controller(rows: int = 12):
    view = ViewState(Config())
    view.rows = _rows(rows)
    effects = FakeEffects()
    return Controller(view, effects), view, effects


def _press(controller: Controller, key: int) -> Outcome:
    return controller.handle_key(key, _Layout())  # type: ignore[arg-type]


def test_every_binding_has_a_handler_and_no_handler_is_orphaned():
    assert set(_HANDLERS) == {b.action for b in BINDINGS}


def test_quit_is_the_only_action_that_ends_the_loop():
    controller, _, _ = _controller()
    outcomes = {
        action: _press(controller, key)
        for action, key in KEY.items()
        if action not in ("quit",) and controller.view.screen is None
    }
    assert Outcome.QUIT not in outcomes.values()
    controller, _, _ = _controller()
    assert _press(controller, KEY["quit"]) is Outcome.QUIT


def test_cursor_keys_move_the_cursor_by_the_visible_page():
    controller, view, _ = _controller()
    _press(controller, KEY["down"])
    assert view.cursor == 1
    _press(controller, KEY["page_down"])
    assert view.cursor == 6
    _press(controller, KEY["page_up"])
    assert view.cursor == 1
    _press(controller, KEY["up"])
    _press(controller, KEY["bottom"])
    assert view.cursor == 11
    _press(controller, KEY["top"])
    assert view.cursor == 0


def test_toggles_flip_the_view_and_the_live_config():
    controller, view, _ = _controller()
    cfg = view.cfg
    _press(controller, KEY["pause"])
    _press(controller, KEY["radar"])
    _press(controller, KEY["reverse"])
    assert view.paused and not view.radar_on and cfg.display.sort_reverse
    seen = []
    for _ in range(3):
        _press(controller, KEY["units"])
        seen.append(cfg.display.units)
    assert seen == ["metric", "aviation", "imperial"]


def test_radius_keys_widen_and_narrow_the_range_but_not_below_one():
    controller, view, _ = _controller()
    view.cfg.filter.radius = 2.0
    _press(controller, ord("+"))
    assert view.cfg.filter.radius == 3.0
    for _ in range(5):
        _press(controller, ord("-"))
    assert view.cfg.filter.radius == 1.0


def test_a_key_goes_to_the_open_screen_before_the_key_table():
    controller, view, _ = _controller()
    _press(controller, KEY["help"])
    assert view.screen_name == "help"
    assert _press(controller, KEY["quit"]) is Outcome.CONTINUE  # the screen has the keyboard
    assert view.screen_name in ("help", None)


def test_detail_needs_a_row_under_the_cursor():
    controller, view, _ = _controller(rows=0)
    _press(controller, KEY["detail"])
    assert view.screen is None
    controller, view, _ = _controller(rows=3)
    _press(controller, KEY["detail"])
    assert view.screen_name == "detail"


def test_a_saved_settings_screen_tells_the_effects_in_order():
    controller, view, effects = _controller()
    controller.screens.open("settings")
    screen = view.screen
    edited = screen.result()
    edited.filter.radius = 42.0
    screen.result = lambda: edited
    screen.applied = True
    screen.save_requested = True
    controller.screens.close()
    assert view.cfg is edited and view.cfg.filter.radius == 42.0
    assert effects.calls == ["set_pipeline_cfg", "set_enricher", "set_dispatcher", "refresh_now"]
    assert effects.saved and view.note() == "settings saved"
    assert controller.screens.edited == set()


def test_a_failed_save_keeps_the_edits_and_shows_the_error():
    controller, view, effects = _controller()
    effects.save_error = "could not save settings: disk full"
    controller.screens.open("settings")
    screen = view.screen
    edited = screen.result()
    edited.filter.radius = 42.0
    screen.result = lambda: edited
    screen.applied = True
    screen.save_requested = True
    controller.screens.close()
    assert view.note() == "could not save settings: disk full"
    assert ("filter", "radius") in controller.screens.edited


def test_escape_in_filters_leaves_the_filter_alone():
    controller, view, _ = _controller()
    before = view.cfg.filter.hide_ground
    controller.screens.open("filters")
    view.screen._hide_ground.handle_key(ord(" "))
    assert view.screen.handle_key(27) == "close"
    controller.screens.close()
    assert view.cfg.filter.hide_ground is before and view.search_text == ""


def test_a_radius_change_asks_for_a_fresh_fetch():
    controller, _, effects = _controller()
    _press(controller, ord("+"))
    assert effects.calls == ["refresh_now"]

"""--demo: scripted traffic that never sends or writes anything."""

from __future__ import annotations

from adsbtui import normalize
from adsbtui.config import Config, load_config
from adsbtui.model import AlertLevel
from adsbtui.sources import Source
from adsbtui.sources.demo import DEFAULT_HOME, DemoSource, apply_demo_settings
from adsbtui.ui.app import App


def _records(source: DemoSource, now: float) -> list[dict]:
    source._clock = lambda: now  # noqa: SLF001
    return source.fetch(1.0)["aircraft"]


def test_the_demo_is_a_source_and_gives_the_same_payload_for_the_same_instant():
    source = DemoSource(clock=lambda: 1_700_000_000.0)
    assert isinstance(source, Source)
    assert source.fetch(1.0) == source.fetch(1.0)


def test_the_demo_traffic_covers_every_kind_of_row():
    records = _records(DemoSource(), 1_700_000_000.0)
    parsed = [normalize.parse_aircraft(r) for r in records]
    assert all(parsed) and len(parsed) == 12
    assert any(ac.squawk == "7700" for ac in parsed)
    assert any(ac.is_military for ac in parsed)
    assert any(ac.on_ground for ac in parsed)
    assert any((ac.seen_pos_s or 0) > 30 for ac in parsed)


def test_the_aircraft_move_and_the_pass_goes_over_the_home_point():
    source = DemoSource()
    first = {r["hex"]: r for r in _records(source, 1_700_000_000.0)}
    later = {r["hex"]: r for r in _records(source, 1_700_000_060.0)}
    assert first["a10001"]["lat"] != later["a10001"]["lat"]
    assert first["a10009"]["lat"] == later["a10009"]["lat"]  # the one on the ground stays put
    lats = [_records(source, 1_700_000_000.0 + 10 * i)[0]["lat"] for i in range(200)]
    assert min(lats) < DEFAULT_HOME[0] < max(lats)


def test_demo_settings_switch_off_everything_that_sends_or_writes():
    cfg = Config()
    cfg.alerts.webhook_url = "https://example.invalid/hook"
    cfg.alerts.command = "echo hi"
    apply_demo_settings(cfg)
    assert (cfg.home.lat, cfg.home.lon) == DEFAULT_HOME
    assert not (
        cfg.alerts.bell or cfg.alerts.desktop or cfg.alerts.webhook_url or cfg.alerts.command
    )
    assert not cfg.registry.auto_update
    assert cfg.history.db == cfg.watchlist.path == cfg.logging.file == ""


def test_the_flag_needs_no_config_file_and_no_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg = load_config(["--demo"])
    assert cfg._args.demo and cfg.source.url == "demo"


def test_a_demo_app_has_no_alert_channel_and_cannot_save():
    cfg = Config()
    apply_demo_settings(cfg)
    app = App(cfg, DemoSource())
    assert app._pipeline.notify is None  # noqa: SLF001
    assert app.view.demo
    assert "not saved" in (app.save_config(cfg, set()) or "")
    result = app._pipeline.tick(app.source.fetch(1.0), 1_700_000_000.0)  # noqa: SLF001
    assert any(ac.alert_level == AlertLevel.EMERGENCY for ac in result.aircraft)
    assert result.events  # the emergency is graded, and still goes nowhere

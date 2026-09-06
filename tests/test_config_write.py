"""Tests for adsbtui.config_write -- the hand-rolled TOML emitter.

The whole point of this module is that what it writes, tomllib reads back identically, so
almost every test here is a round trip: build a Config, dump it, parse it with tomllib,
and compare. The rest cover the atomic-write behaviour of dump_config() on the filesystem.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from adsbtui.config import Config, build_config
from adsbtui.config_write import dump_config, dump_config_str


def make_config() -> Config:
    """A Config with every section pushed off its defaults, so a round trip has to carry
    real values rather than just re-deriving the schema defaults."""
    cfg = Config()
    cfg.source.url = "http://192.168.1.50/skyaware/data/aircraft.json"
    cfg.source.refresh_s = 2.5
    cfg.source.timeout_s = 3.0
    cfg.source.stale_s = 45.0
    cfg.source.backoff_max_s = 60.0
    cfg.source.max_bytes = 4_000_000
    cfg.home.lat = 37.7749
    cfg.home.lon = -122.4194
    cfg.filter.radius = 25.0
    cfg.filter.proximity = 3.5
    cfg.filter.hide_ground = True
    cfg.filter.include_nonicao = False
    cfg.filter.min_alt_ft = 500
    cfg.filter.max_alt_ft = 45000
    cfg.display.units = "metric"
    cfg.display.theme = "default"
    cfg.display.color = "never"
    cfg.display.columns = ["flight", "reg", "dist"]
    cfg.display.owner_width = 24
    cfg.display.borders = "ascii"
    cfg.display.density = "compact"
    cfg.display.sort_key = "altitude"
    cfg.display.sort_reverse = True
    cfg.display.stale_after_s = 20.0
    cfg.display.linger_s = 12.5
    cfg.display.status_bar = False
    cfg.display.vs_threshold_fpm = 300.0
    cfg.alerts.emergency = False
    cfg.alerts.squawks = [7700]
    cfg.alerts.emergency_ignore_radius = False
    cfg.alerts.cpa_enabled = False
    cfg.alerts.cpa_distance = 2.0
    cfg.alerts.cpa_horizon_s = 300.0
    cfg.alerts.cpa_min_gs_kt = 45.0
    cfg.alerts.events = ["emergency"]
    cfg.alerts.bell = False
    cfg.alerts.desktop = True
    cfg.alerts.webhook_url = "https://ntfy.sh/my-topic"
    cfg.alerts.webhook_format = "ntfy"
    cfg.alerts.command = "logger -t adsbtui"
    cfg.alerts.cooldown_s = 120.0
    cfg.alerts.quiet_hours = "23:00-07:00"
    cfg.watchlist.path = "/home/pi/watchlist.txt"
    cfg.watchlist.pin_top = False
    cfg.watchlist.ignore_radius = False
    cfg.registry.path = "/home/pi/MASTER.csv"
    cfg.history.db = "/home/pi/history.sqlite3"
    cfg.history.retention_days = 30
    cfg.logging.file = "/var/log/adsbtui.log"
    cfg.logging.level = "DEBUG"
    cfg.logging.max_bytes = 2_097_152
    cfg.logging.backup_count = 5
    cfg.logging.redact_home = False
    return cfg


def round_trip(cfg: Config) -> dict:
    return tomllib.loads(dump_config_str(cfg))


class TestStructure:
    def test_every_section_gets_a_header(self) -> None:
        parsed = round_trip(Config())
        assert set(parsed) == {
            "source",
            "home",
            "filter",
            "display",
            "alerts",
            "watchlist",
            "registry",
            "history",
            "logging",
        }

    def test_each_section_has_a_comment_line_above_it(self) -> None:
        lines = dump_config_str(Config()).splitlines()
        for index, line in enumerate(lines):
            if line.startswith("["):
                assert lines[index - 1].startswith("# ")

    def test_private_bookkeeping_attributes_are_not_written(self) -> None:
        cfg = Config()
        cfg._config_path = "/somewhere/config.toml"
        text = dump_config_str(cfg)
        assert "_config_path" not in text
        assert "_args" not in text


class TestRoundTrip:
    def test_defaults_round_trip_through_tomllib(self) -> None:
        cfg = Config()
        parsed = round_trip(cfg)
        for section_name, section in parsed.items():
            original = getattr(cfg, section_name)
            for key, value in section.items():
                assert value == getattr(original, key), f"{section_name}.{key}"

    def test_populated_config_round_trips_through_tomllib(self) -> None:
        cfg = make_config()
        parsed = round_trip(cfg)
        for section_name, section in parsed.items():
            original = getattr(cfg, section_name)
            for key, value in section.items():
                assert value == getattr(original, key), f"{section_name}.{key}"

    def test_round_trip_through_build_config_rebuilds_an_equal_config(self) -> None:
        cfg = make_config()
        rebuilt = build_config(round_trip(cfg))
        assert rebuilt == cfg

    def test_types_survive_and_do_not_collapse_into_each_other(self) -> None:
        parsed = round_trip(Config())
        assert isinstance(parsed["display"]["status_bar"], bool)
        assert isinstance(parsed["source"]["max_bytes"], int)
        assert not isinstance(parsed["source"]["max_bytes"], bool)
        assert isinstance(parsed["source"]["refresh_s"], float)
        assert isinstance(parsed["display"]["columns"], list)
        assert isinstance(parsed["alerts"]["squawks"][0], int)


class TestEdgeCaseValues:
    def test_empty_string_round_trips(self) -> None:
        cfg = Config()
        cfg.registry.path = ""
        assert round_trip(cfg)["registry"]["path"] == ""

    def test_backslash_path_round_trips(self) -> None:
        cfg = Config()
        cfg.logging.file = "C:\\Users\\pi\\adsbtui.log"
        assert round_trip(cfg)["logging"]["file"] == "C:\\Users\\pi\\adsbtui.log"

    def test_double_quote_in_string_round_trips(self) -> None:
        cfg = Config()
        cfg.alerts.command = 'notify-send "aircraft overhead"'
        assert round_trip(cfg)["alerts"]["command"] == 'notify-send "aircraft overhead"'

    def test_control_characters_round_trip(self) -> None:
        cfg = Config()
        cfg.alerts.command = "echo one\ttwo\nthree"
        assert round_trip(cfg)["alerts"]["command"] == "echo one\ttwo\nthree"

    def test_non_ascii_string_round_trips(self) -> None:
        cfg = Config()
        cfg.registry.path = "/home/pi/Aufzeichnungen/flugzeuge.csv"
        assert round_trip(cfg)["registry"]["path"].endswith("flugzeuge.csv")

    def test_empty_list_round_trips(self) -> None:
        cfg = Config()
        cfg.display.columns = []
        cfg.alerts.squawks = []
        parsed = round_trip(cfg)
        assert parsed["display"]["columns"] == []
        assert parsed["alerts"]["squawks"] == []

    def test_whole_number_float_stays_a_float(self) -> None:
        cfg = Config()
        cfg.filter.radius = 20.0
        value = round_trip(cfg)["filter"]["radius"]
        assert isinstance(value, float)
        assert value == 20.0

    def test_negative_numbers_round_trip(self) -> None:
        cfg = Config()
        cfg.home.lat = -33.8688
        cfg.home.lon = -151.2093
        cfg.filter.min_alt_ft = -500
        parsed = round_trip(cfg)
        assert parsed["home"]["lat"] == -33.8688
        assert parsed["home"]["lon"] == -151.2093
        assert parsed["filter"]["min_alt_ft"] == -500

    def test_high_precision_float_round_trips_exactly(self) -> None:
        cfg = Config()
        cfg.home.lat = 37.123456789012345
        assert round_trip(cfg)["home"]["lat"] == 37.123456789012345

    def test_unsupported_value_type_raises_rather_than_writing_garbage(self) -> None:
        cfg = Config()
        cfg.registry.path = {"not": "a string"}  # type: ignore[assignment]
        with pytest.raises(TypeError):
            dump_config_str(cfg)


class TestDumpConfigFile:
    def test_writes_a_file_that_tomllib_can_read(self, tmp_path: Path) -> None:
        target = tmp_path / "config.toml"
        cfg = make_config()
        dump_config(cfg, target)
        with open(target, "rb") as handle:
            parsed = tomllib.load(handle)
        assert parsed["source"]["url"] == cfg.source.url
        assert parsed["home"]["lat"] == cfg.home.lat

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deeper" / "config.toml"
        dump_config(Config(), target)
        assert target.is_file()

    def test_replaces_an_existing_file_and_leaves_no_temp_files(self, tmp_path: Path) -> None:
        target = tmp_path / "config.toml"
        target.write_text("# stale contents that must not survive\nbogus = true\n")
        cfg = make_config()
        dump_config(cfg, target)
        text = target.read_text()
        assert "bogus" not in text
        assert cfg.source.url in text
        assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]

    def test_expands_a_leading_tilde(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        dump_config(Config(), "~/adsbtui/config.toml")
        assert (tmp_path / "adsbtui" / "config.toml").is_file()

    def test_a_failed_write_leaves_the_original_file_intact(self, tmp_path: Path) -> None:
        target = tmp_path / "config.toml"
        original = "# the config the user already had\n"
        target.write_text(original)
        cfg = Config()
        cfg.registry.path = {"unwritable": True}  # type: ignore[assignment]
        with pytest.raises(TypeError):
            dump_config(cfg, target)
        assert target.read_text() == original
        assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]

    def test_accepts_a_string_path(self, tmp_path: Path) -> None:
        target = tmp_path / "config.toml"
        dump_config(Config(), str(target))
        assert target.is_file()
